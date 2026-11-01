# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
A simulated user and speech capability for the interaction system, to run dialogues without
audio.

DialogueHarness is one ROS node that stands for the speech capability and the user:
  - It serves /tts_action (Say) and /stt_action (Listen) as simple_hri does. Each robot
    utterance "lasts" speech_time(text) seconds. When the robot finishes one, the user policy
    decides what the user says next; the next Listen goal returns it (and publishes it on
    /listened_text, as simple_hri), or times out if the user stays silent.
  - The user can barge in: /user_speaking goes True while the robot speaks (lip activity).
  - A conversational filler ("Hmm…", said while the system prepares its reply; announced on
    /robot_filler, the lines of the dialogue on /robot_utterance) is recorded as a robot line
    with "filler": True, in order and with its time, but it is not a turn: the user does not
    answer it, it is not passed to the user policy, and a planned barge-in waits for the reply.
  - It sends the DialogTask goal and collects the feedback, the result and the transcript.

A user policy is any callable (robot_utterance: str, transcript: list[dict]) -> UserTurn or
None. ScriptedUser replays a list of lines; the experiments add users driven by an LLM.
"""

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, String

from ros2_dialog_interfaces.action import DialogTask
from ros2_dialog_interfaces.msg import BargeIn
from simple_hri_interfaces.action import Listen, Say


@dataclass
class UserTurn:
    """What the simulated user does after a robot utterance."""

    text: str | None  # None: stay silent
    barge_in_after: float | None = None  # cut the NEXT robot utterance after this many seconds


class ScriptedUser:
    """Says the given lines in order, one after each robot utterance. A line None stays
    silent; a UserTurn is used as is. After the last line, silence."""

    def __init__(self, lines: list):
        self._lines = list(lines)

    def __call__(self, robot_utterance: str, transcript: list[dict]):
        if not self._lines:
            return None
        line = self._lines.pop(0)
        return line if isinstance(line, UserTurn) else UserTurn(line)


@dataclass
class DialogueRun:
    """What happened in one dialogue."""

    result: object = None  # DialogTask.Result
    status: str = ""  # succeeded | aborted | canceled | rejected | timeout
    transcript: list[dict] = field(default_factory=list)  # {role, text, t[, filler: True]}
    feedback: list[dict] = field(default_factory=list)
    barge_ins: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)  # /interaction/skill_trace, with t
    duration_s: float = 0.0

    def robot_lines(self) -> list[str]:
        """The robot's lines of the dialogue (not the fillers)."""
        return [t["text"] for t in self.transcript
                if t["role"] == "robot" and not t.get("filler")]

    def fillers(self) -> list[dict]:
        """The conversational fillers the robot said: {role, text, t, filler}."""
        return [t for t in self.transcript if t.get("filler")]

    def user_lines(self) -> list[str]:
        return [t["text"] for t in self.transcript if t["role"] == "user"]


def no_speech_time(text: str) -> float:
    return 0.0


def dialogue_lines(transcript: list[dict]) -> list[dict]:
    """The transcript without the conversational fillers (what the user policy sees)."""
    return [t for t in transcript if not t.get("filler")]


# Longest wait for the announcement of a robot utterance (/robot_utterance or /robot_filler,
# published just before the Say goal) that tells a filler from a line of the dialogue
ANNOUNCE_WAIT_S = 0.5


class DialogueHarness(Node):

    def __init__(self, speech_time: Callable[[str], float] = no_speech_time):
        super().__init__("interaction_sim_harness")
        self._speech_time = speech_time
        self._cb = ReentrantCallbackGroup()
        self._lock = threading.Lock()
        self._user = None
        self._run: DialogueRun | None = None
        self._t0 = 0.0
        self._pending_barge_in: float | None = None
        self._user_lines: queue.Queue = queue.Queue()  # what the user will say
        # The robot utterances announced and not yet said: [(filler?, text)]
        self._announced: list[tuple[bool, str]] = []
        self._announced_cv = threading.Condition(self._lock)

        self._user_speaking_pub = self.create_publisher(Bool, "/user_speaking", 10)
        self.create_subscription(
            BargeIn, "/barge_in", self._on_barge_in, 10, callback_group=self._cb
        )
        self.create_subscription(
            String, "/interaction/skill_trace", self._on_skill, 50, callback_group=self._cb
        )
        self.create_subscription(
            String, "/robot_utterance", lambda m: self._announce(False, m.data), 50,
            callback_group=self._cb
        )
        self.create_subscription(
            String, "/robot_filler", lambda m: self._announce(True, m.data), 50,
            callback_group=self._cb
        )
        self._listened_pub = self.create_publisher(String, "/listened_text", 10)
        self._action_server(Say, "/tts_action", self._say)
        self._action_server(Listen, "/stt_action", self._listen)
        self._dialog_client = ActionClient(
            self, DialogTask, "/dialog/execute_task", callback_group=self._cb
        )

    def _action_server(self, action_type, name, execute):
        ActionServer(
            self, action_type, name,
            execute_callback=execute,
            goal_callback=lambda _: GoalResponse.ACCEPT,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=self._cb,
        )

    # ==== THE VOICE ====

    def _announce(self, filler: bool, text: str) -> None:
        with self._announced_cv:
            self._announced.append((filler, text))
            self._announced_cv.notify_all()

    def _is_filler(self, text: str) -> bool:
        """Whether the utterance to say is a filler: from its announcement, which normally came
        before the Say goal (else it is waited for briefly; none: a line of the dialogue)."""
        deadline = time.monotonic() + ANNOUNCE_WAIT_S
        with self._announced_cv:
            while True:
                for i, (filler, announced) in enumerate(self._announced):
                    if announced == text:
                        del self._announced[: i + 1]
                        return filler
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._announced_cv.wait(left)

    def _play(self, goal_handle, text: str, filler: bool = False) -> bool:
        """Play an utterance: record it, last speech_time(text), let the user barge in (not
        during a filler). Returns whether it was interrupted."""
        start = time.monotonic()
        with self._lock:
            barge_after = None
            if not filler:
                barge_after = self._pending_barge_in
                self._pending_barge_in = None
            if self._run is not None:
                line = {"role": "robot", "text": text, "t": start - self._t0}
                if filler:
                    line["filler"] = True
                self._run.transcript.append(line)
        duration = self._speech_time(text)
        while True:
            elapsed = time.monotonic() - start
            if goal_handle.is_cancel_requested:
                return True
            if barge_after is not None and elapsed >= barge_after:
                self._user_speaking_pub.publish(Bool(data=True))
                deadline = time.monotonic() + 2.0
                while not goal_handle.is_cancel_requested and time.monotonic() < deadline:
                    time.sleep(0.01)
                self._user_speaking_pub.publish(Bool(data=False))
                return goal_handle.is_cancel_requested
            if barge_after is None and elapsed >= duration:
                return False
            time.sleep(0.01)

    def _say(self, goal_handle):
        text = goal_handle.request.text
        filler = self._is_filler(text)
        interrupted = self._play(goal_handle, text, filler)
        result = Say.Result(success=not interrupted, message="canceled" if interrupted else "")
        # The user's reply is ready when the robot finishes (the simulator's latency is not the
        # user's silence). The terminal state is set only then: rclpy keeps a finished goal's
        # result for result_timeout (10 s), so a slower simulator would expire it first. A
        # filler is not a turn: the user answers the reply that follows it
        if not filler:
            self._after_robot_spoke(text)
        goal_handle.canceled() if goal_handle.is_cancel_requested else goal_handle.succeed()
        return result

    # ==== THE USER ====

    def _listen(self, goal_handle):
        """simple_hri's /stt_action: the next line of the user, or a timeout."""
        max_wait = goal_handle.request.max_wait or 10.0
        deadline = time.monotonic() + max_wait
        result = Listen.Result()
        while time.monotonic() < deadline:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result.message = "canceled"
                return result
            try:
                text = self._user_lines.get(timeout=0.05)
            except queue.Empty:
                continue
            self._record_user(text)
            self._listened_pub.publish(String(data=text))
            result.success, result.text = True, text
            goal_handle.succeed()
            return result
        result.timed_out = True
        result.message = "timeout"
        goal_handle.abort()
        return result

    def _record_user(self, text: str) -> None:
        with self._lock:
            if self._run is not None:
                self._run.transcript.append(
                    {"role": "user", "text": text, "t": time.monotonic() - self._t0}
                )

    def _after_robot_spoke(self, text: str) -> None:
        with self._lock:
            user, run = self._user, self._run
        if user is None or run is None:
            return
        turn = user(text, dialogue_lines(run.transcript))
        if turn is None:
            return
        if turn.barge_in_after is not None:
            with self._lock:
                self._pending_barge_in = turn.barge_in_after
        if turn.text is not None:
            self._user_lines.put(turn.text)

    def _on_skill(self, msg: String) -> None:
        with self._lock:
            if self._run is not None:
                entry = json.loads(msg.data)
                entry["t"] = time.monotonic() - self._t0
                self._run.skills.append(entry)

    def _on_barge_in(self, msg: BargeIn) -> None:
        with self._lock:
            if self._run is not None:
                self._run.barge_ins.append(
                    {"utterance": msg.utterance, "spoken_ms": msg.spoken_ms}
                )

    # ==== A DIALOGUE ====

    def wait_for_system(self, timeout: float = 30.0) -> bool:
        return self._dialog_client.wait_for_server(timeout_sec=timeout)

    def run(self, goal: DialogTask.Goal, user, timeout: float = 120.0,
            cancel_after: float | None = None) -> DialogueRun:
        """Run one dialogue to its end (blocking; the node must be spun by another thread)."""
        run = DialogueRun()
        while not self._user_lines.empty():
            self._user_lines.get_nowait()
        with self._lock:
            self._announced.clear()
            self._user, self._run = user, run
            self._t0 = time.monotonic()
        done = threading.Event()
        handle_box = [None]

        def on_feedback(msg):
            fb = msg.feedback
            run.feedback.append({
                "state": fb.state, "frame": fb.current_frame_json,
                "utterance": fb.last_utterance, "turns": fb.turns,
            })

        def on_result(future):
            res = future.result()
            run.result = res.result
            run.status = {4: "succeeded", 5: "canceled", 6: "aborted"}.get(
                res.status, str(res.status))
            done.set()

        def on_accepted(future):
            handle = future.result()
            if not handle.accepted:
                run.status = "rejected"
                done.set()
                return
            handle_box[0] = handle
            handle.get_result_async().add_done_callback(on_result)

        self._dialog_client.send_goal_async(goal, feedback_callback=on_feedback) \
            .add_done_callback(on_accepted)
        if cancel_after is not None:
            if not done.wait(cancel_after) and handle_box[0] is not None:
                handle_box[0].cancel_goal_async()
        if not done.wait(timeout):
            run.status = "timeout"
            if handle_box[0] is not None:
                handle_box[0].cancel_goal_async()
                done.wait(5.0)
        run.duration_s = time.monotonic() - self._t0
        with self._lock:
            self._user, self._run = None, None
        return run


class HarnessRunner:
    """The harness spinning in a background thread (rclpy initialised if needed)."""

    def __init__(self, speech_time: Callable[[str], float] = no_speech_time):
        if not rclpy.ok():
            rclpy.init()
        self.node = DialogueHarness(speech_time)
        self._executor = MultiThreadedExecutor(num_threads=8)
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._executor.shutdown()
        self.node.destroy_node()
