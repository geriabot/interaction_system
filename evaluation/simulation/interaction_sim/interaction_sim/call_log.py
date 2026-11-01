# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The record of every call of the system's model in a dialogue, for the debugging before E0a
(evaluation/doc/DEBUGGING.md): what was asked (the question, its prompt and schema), what the
model answered (or the error), how long it took, at which user turn and in which skill, so that a
reviewer can find the first wrong turn of a dialogue and tell a program error from a prompt, a
simulator or a model error (evaluation/debug/triage.py).

It works like the injector of model failures (llm_faults.py) and is loaded the same way, so the
method's code is not modified:

  - the harness side (CallLog) writes the current user turn to a small JSON file (the marker)
    before the user's line is delivered (turn 0: the opening), and at the end of the dialogue
    returns the calls of that dialogue;
  - the intelligence side, loaded as one more of its skill_modules
    ("interaction_sim.call_recording", whose import calls install()), wraps the call of
    LLMDialogClient to the gateway and the registered skills: each call is appended to
    <marker>.jsonl with the turn of the marker, the skill in progress and the question.

The path of the marker is the environment variable INTERACTION_SIM_CALL_LOG of the interaction
system's process. run_experiment.py sets it up for the experiment files with
`record_model_calls: true` (the debugging files of experiments/debug/; off in the experiments:
the prompts make the records several times larger) and stores the calls of each dialogue in its
record (`model_calls`). With the injector also loaded, the recorder wraps it from outside: an
injected answer is recorded as the answer the client received (faults_applied says which).

Each call: {turn, skill, question, prompt, schema, max_tokens, answer, error, latency_s, t}
(question: the method of LLMDialogClient that asked, e.g. extract_turn; skill: the skill of the
intelligence in progress, e.g. slot_filling.update, or "choose_behavior"; answer: the raw text the
client received, None when nothing came back; error: the exception of a failed call; t: the
time it started, epoch seconds).
"""

import functools
import json
import os
import sys
import threading
import time
import uuid

ENV = "INTERACTION_SIM_CALL_LOG"


def _write_json(path: str, data: dict) -> None:
    """Atomically: the intelligence never reads half a file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ==== THE HARNESS SIDE ====


class CallLog:
    """The calls of the model in one dialogue, collected from the harness side.

        log = CallLog(marker_path)
        log.start()                         # before the goal is sent (turn 0: the opening)
        run = harness.run(goal, log.user(user))
        calls = log.finish()                # the calls of this dialogue
    """

    def __init__(self, path: str):
        self.path = path
        self.log_path = path + ".jsonl"
        self.seq = ""
        self._calls = 0

    def start(self) -> None:
        self.seq = uuid.uuid4().hex[:12]
        self._calls = 0
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.mark(0)

    def mark(self, turn: int) -> None:
        _write_json(self.path, {"seq": self.seq, "turn": turn})

    def user(self, policy):
        """The user policy with the turn marked: the k-th call (the user's turn k, as the events
        and the faults count it) marks turn k before the user's line is delivered."""

        def wrapped(robot_utterance: str, transcript: list[dict]):
            self._calls += 1
            self.mark(self._calls)
            return policy(robot_utterance, transcript)

        return wrapped

    def finish(self) -> list[dict]:
        """Stop marking, and return the calls of this dialogue in the order they started."""
        _write_json(self.path, {"seq": "", "turn": 0})
        out = []
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("seq") == self.seq:
                        out.append({k: v for k, v in entry.items() if k != "seq"})
        except FileNotFoundError:
            pass
        return sorted(out, key=lambda e: e.get("t", 0.0))


# ==== THE INTELLIGENCE SIDE ====


class Recorder:
    """Reads the marker and appends each call to <marker>.jsonl."""

    def __init__(self, path: str):
        self.path = path
        self.log_path = path + ".jsonl"
        self._lock = threading.Lock()

    def marker(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def record(self, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


_local = threading.local()


def current_skill() -> str:
    return getattr(_local, "skill", "")


def _skill(name: str, fn):
    @functools.wraps(fn)
    def wrapped(env, state, inp):
        previous = current_skill()
        _local.skill = name
        try:
            return fn(env, state, inp)
        finally:
            _local.skill = previous

    wrapped.__call_log_wrapped__ = True
    return wrapped


# The client's own functions between a question and its call to the model, and the wrappers
_TRANSPORT = {"call", "_infer_limited", "_call_nlu", "_call_nlg", "recorded_call", "wrapped"}


def question_of(questions: set[str]) -> str:
    """The question that called the model: the innermost question in progress of the injector
    when it is loaded, else the first caller that is a question of the client (or, failing that,
    the first one outside the transport)."""
    try:
        from .llm_faults import _in_progress
        stack = _in_progress()
        if stack:
            return stack[-1]
    except ImportError:
        pass
    frame = sys._getframe(1)
    first = ""
    while frame is not None:
        name = frame.f_code.co_name
        if name in questions:
            return name
        if not first and name not in _TRANSPORT:
            first = name
        frame = frame.f_back
    return first


def recorded_infer(infer, recorder: Recorder, questions: set[str]):
    """The client's call to the model (sid, prompt, max_tokens, schema) -> text | None,
    recorded with the turn of the marker, the skill in progress and the question."""

    def recorded_call(session_id, prompt, max_tokens, schema):
        mark = recorder.marker()
        question = question_of(questions)
        entry = {"seq": mark.get("seq", ""), "turn": mark.get("turn"),
                 "skill": current_skill() or ("choose_behavior"
                                              if question == "choose_dialogue_behavior" else ""),
                 "question": question, "prompt": str(prompt), "schema": schema,
                 "max_tokens": max_tokens, "answer": None, "error": None, "t": time.time()}
        started = time.monotonic()
        try:
            answer = infer(session_id, prompt, max_tokens, schema)
            entry["answer"] = answer
            return answer
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            entry["latency_s"] = round(time.monotonic() - started, 3)
            if entry["seq"]:  # only while a dialogue of the harness is marked
                recorder.record(entry)

    return recorded_call


_installed: dict = {}


def install(path: str | None = None) -> bool:
    """Record the calls of LLMDialogClient to the gateway, with the marker at `path` (default:
    $INTERACTION_SIM_CALL_LOG). Called when the intelligence imports
    interaction_sim.call_recording. False (and nothing changed) without a path."""
    path = path or os.environ.get(ENV, "")
    if not path:
        print(f"[interaction_sim.call_log] {ENV} is not set: the calls of the model are not "
              f"recorded", file=sys.stderr)
        return False
    from interaction_intelligence import skills
    from interaction_intelligence.llm_client import LLMDialogClient

    recorder = Recorder(path)
    if _installed:
        _installed["recorder"] = recorder
        return True
    questions = LLMDialogClient.questions()
    init = LLMDialogClient.__init__

    @functools.wraps(init)
    def __init__(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self._infer = recorded_infer(self._infer, _installed["recorder"], questions)

    LLMDialogClient.__init__ = __init__
    originals = dict(skills._SKILLS)
    for name, fn in originals.items():
        if not getattr(fn, "__call_log_wrapped__", False):
            skills._SKILLS[name] = _skill(name, fn)
    _installed.update({"recorder": recorder, "init": init, "cls": LLMDialogClient,
                       "skills": originals})
    return True


def uninstall() -> None:
    """Undo install() (for the tests)."""
    if not _installed:
        return
    from interaction_intelligence import skills

    _installed["cls"].__init__ = _installed["init"]
    skills._SKILLS.clear()
    skills._SKILLS.update(_installed["skills"])
    _installed.clear()
