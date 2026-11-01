# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The sessions with real participants: their plans, and ONE trial (one conversation of a
participant with the robot, in one condition and scenario) per call.

    python3 real_sessions.py --campaign C plan P07     # create (or show) the plan of P07
    python3 real_sessions.py --campaign C card P07 2   # the task card of its trial 2
    python3 real_sessions.py balance 24                # how balanced the plans of P01-P24 are
    python3 real_sessions.py --campaign C trial P07 2 [--try] [--allow-cloud] [--keep]
                                                      [--barge-in on|off]
    python3 real_sessions.py [--campaign C] check speak | listen | llm | system | perception
                                                  | rehearsal | presession [--config NAME]
    python3 real_sessions.py check speak [--phrase TEXT] [--config NAME] [--voice V]
                                         [--length-scale L] [--noise-scale N] [--noise-w-scale W]
    python3 real_sessions.py --campaign C review P07 --by <name> [--k 2] [--again]
    python3 real_sessions.py try-scenario pr_tea [--scenarios FILE] [--control bounded|llm_led]
                                                 [--model M] [--allow-cloud] [--barge-in on|off]
                                                 [--config NAME] [--voice V] [--length-scale L]
                                                 [--noise-scale N] [--noise-w-scale W]
                                                 [--filler-after S]

Campaigns and configurations (campaigns.py; doc/REAL_SESSIONS.md#campaigns-and-configurations):
a trial belongs to a campaign (--campaign: real/campaigns/<C>.yaml), which names the sessions file
and the configuration (real/configs/<name>.yaml: the Piper voice and its settings, the filler
delay, the microphone and the speech-recognition model); its participants are stored in
<out>/<C>/<P07>/. The trial, its test (--try) and the checks of a campaign run with its
configuration: the stack starts without its speech nodes (speech:=none) and simple_hri is started
here with the configuration. A campaign whose configuration file changed after it was associated
(its hash) refuses its trials and checks and says why; every stored record keeps the name and the
hash of its configuration. --config runs a check or try-scenario with any configuration, drafts
included (nothing stored). check presession (the pre-session check of the control panel): speak,
listen and the model, in sequence, with the configuration; with --campaign, the date of its last
pass is kept in <out>/<C>/presession.json, and a trial that counts warns when it has not passed
that day.

review: before anything of a session goes to the judge or the annotators, a member of the team
reads each stored trial and replaces any real personal data the participant said by mistake
(the ethics protocol). The replacement runs over the whole record and the logs of the trial;
what was replaced is not kept, and the record is marked as reviewed (by whom, when).
analysis/judge_runs.py refuses the records of real sessions that are not reviewed.

try-scenario: any scenario of any scenario file (default experiments/scenarios/e4_es.yaml), with
a person as the user, as a trial --try (nothing is stored). The card is the scenario's user_goal
(what the simulated user is asked to do); the language follows the file (*_es.yaml: Spanish;
otherwise English). The Tests tab of the control panel runs it from a web page. Options of a test
only (nothing is changed): a configuration (--config, any, drafts included), or ad-hoc values over
it: the Piper voice, --voice (another voice of the catalog: NAME, or NAME:SPEAKER for a voice with
several speakers) and its settings --length-scale (speed), --noise-scale (expressiveness) and
--noise-w-scale (rhythm variation), with which the stack runs without its speech nodes and
simple_hri is started here; and --filler-after, the seconds the robot may take to answer before
its conversational filler (filler_after_s of the request; 0: none). check speak with them says the
phrase with that voice and settings (only the voice is started, no recognition).

The checks before a session (nothing stored; doc/REAL_SESSIONS.md): the voice says a phrase,
one transcription, the model of every condition answers, the stack starts in every condition,
the lip activity for barge-in (only when barge-in is on), and a rehearsal: a whole conversation, as a trial --try,
in a scenario and condition (--scenario, --condition), before any participant exists. Their last
line is the verdict, read by the control panel: CHECK <what>: OK | FAILED | SKIPPED - <why>.

The conditions, the scenarios and their task cards are in sessions_es.yaml; the plan of a
participant (the order of the conditions and the scenario of each, counterbalanced from the
participant's number: sessions_common.py) is written to ~/.ros/interaction_real/<C>/<P07>/plan.yaml
(--out to change the folder). What the trial does:

  1. launches the whole stack in the trial's condition, in its own process group: bounded,
     main.launch.py; LLM-led, interaction_ablations' llm_led.launch.py; both with the condition's
     system model (a parameter file generated from app_params.yaml, as run_experiment.py does),
     and the speech capability on this computer (simple_hri, Spanish) with the campaign's
     configuration;
  2. shows the task card while the stack starts, and waits until it is ready (the orchestrator
     and simple_hri's actions);
  3. waits for the operator (Enter: start the conversation; q: quit without running it);
  4. sends the scenario's request (the DialogTask goal, as run_experiment.make_goal) and records
     the transcript (/robot_utterance, /interaction/user_turn, and the conversational fillers of
     /robot_filler marked "filler": true), what the speech recognition heard
     (/listened_text), the feedback, the result, the skill trace (/interaction/skill_trace) and
     the barge-ins, printing the conversation as it goes;
  5. stops the stack and stores the record in ~/.ros/interaction_real/<C>/<P07>/
     trial_<k>_<scenario>_<condition>.json (the logs of the stack in the folder of the same
     name), and a row in ~/.ros/interaction_real/<C>/trials.csv.

The record has the keys of run_experiment.py's (scenario, strategy, condition, model, status,
success, data, expect, goal, transcript...) plus participant, trial, the card, the campaign and
its configuration (name, hash, values), so that analysis/score_runs.py and analysis/judge_runs.py
score the real sessions as the simulated ones (give them ~/.ros/interaction_real/<C>, one
campaign at a time). With --try nothing is stored: the trial runs into a
temporary folder deleted at the end (--keep keeps it). Ctrl-C cancels the conversation, stops
the stack and stores nothing (trials.csv notes it). Running a stored trial again replaces it.

Barge-in (the robot stops talking when the participant starts speaking) is optional: with
--barge-in on (or `barge_in: true` in sessions_es.yaml) the trial also starts the lip activity
detector of interaction_perception on the camera (live and local, no images stored, never the
emotion detector) and the orchestrator listens to /user_speaking; off, no camera is used and the
robot finishes its utterance before listening. It must be the same in every stored trial of a
participant (a trial with the other setting is refused). The state of the user (emotion) is
never used in the sessions.

The participant's code never reaches the system: the stack runs in a temporary folder whose name
says nothing of the participant, the request is the scenario's, and only the stored record
carries the code.

A condition with a cloud model sends what the participant says, transcribed on this computer
(never the audio), to the provider: such a trial needs --allow-cloud, and the consent form must
say so. Needs a sourced workspace (pixi shell + install/setup.bash), the microphone and speaker
of simple_hri, and the LLM server (Ollama) or the cloud provider's API key.
"""

import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sessions_common import (API_KEYS, OUT, PIPER_SETTINGS, PIPER_VOICE,  # noqa: E402,F401
                             PLACEHOLDER, SESSIONS, balance, card_text, details, is_cloud,
                             is_reviewed, load, load_sessions, parse_voice, participant, read_plan,
                             record_path, replace_in_text, review_record, split_model, stored,
                             write_plan)
import campaigns  # noqa: E402

EVAL = os.path.dirname(HERE)
PROMPT = "Enter: start the conversation"
TRIALS_CSV = ["time", "participant", "trial", "scenario", "condition", "model", "status",
              "success", "outcome", "turns", "duration_s", "stored", "barge_in", "configuration",
              "notes"]


class NotRun(Exception):
    """The operator chose not to run the trial (q)."""


def say(text: str, bell: bool = False) -> None:
    print(("\a" if bell else "") + f"\n>>> {text}", flush=True)


# ------------------------------------------------------------------ the stack
def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = deep_merge(out.get(k) or {}, v) if isinstance(v, dict) else v
    return out


def barge_in_of(args, sessions: dict) -> bool:
    """Barge-in by lip activity: --barge-in on|off, or else the sessions file's `barge_in`."""
    choice = getattr(args, "barge_in", None)
    return bool(sessions.get("barge_in", False)) if choice is None else choice == "on"


def perception_command(sessions: dict) -> list[str]:
    """The perception of the user for barge-in only: the lip activity detector on the camera
    (local, live, no images stored), never the emotion detector."""
    return ["ros2", "launch", "interaction_perception", "interaction_perception.launch.py",
            "emotion:=false", "lips:=true",
            f"image_topic:={sessions.get('camera_topic', '/camera/image_raw')}"]


def stack_params(sessions: dict, model: str, trial_dir: str, barge_in: bool = False) -> str:
    """The parameter file of the trial: app_params.yaml with the condition's system model, the
    language, the transcript log in the trial's folder and the limits of the sessions file (as
    run_experiment.System), barge-in on /user_speaking or none, and never the state of the user
    (no emotion in the prompts). Returns its path."""
    from ament_index_python.packages import get_package_share_directory
    app = load(sessions.get("params_file") or os.path.join(
        get_package_share_directory("interaction_system"), "config", "app_params.yaml"))
    llm = sessions.get("ollama", {})
    provider, name = split_model(model)
    if provider == "ollama":
        backend = {"enable": True, "host": llm.get("host", "127.0.0.1"),
                   "port": llm.get("port", 11434), "model": name,
                   "timeout": float(llm.get("timeout", 120.0))}
    else:
        # The key comes from the environment (the gateway reads it when api_key is empty)
        backend = {"enable": True, "model": name, "timeout": float(llm.get("timeout", 120.0))}
    gateway = {"default_provider": provider, provider: backend}
    if provider != "ollama":
        # Otherwise the gateway loads app_params' Ollama model on the GPU at start, for nothing
        gateway["ollama"] = {"enable": False}
    override = {
        "llm_manager_node": {"ros__parameters": gateway},
        "interaction_intelligence": {"ros__parameters": {
            "language": sessions.get("language", "Spanish"),
            "conversation_log_path": os.path.join(trial_dir, "transcripts.log"),
            # llm_timeout is not set: the system's own limits on the wait for the model
            # (llm_timeout, reply_budget) are part of the method, and E4 runs with its defaults
            # (only E1 opens them, in run_experiment.py's model_wait); ollama.timeout above is only the gateway's limit for one request to the server
            "user_state_service": "",
        }},
        "interaction_orchestrator": {"ros__parameters": {
            **dict(sessions.get("limits", {})),
            "user_speaking_topic": "/user_speaking" if barge_in else ""}},
    }
    params = deep_merge(deep_merge(deep_merge(app, override), sessions.get("params", {})),
                        model_params(sessions, model))
    path = os.path.join(trial_dir, "params.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(params, f, sort_keys=False, allow_unicode=True)
    return path


def stack_command(sessions: dict, control: str, params_file: str) -> list[str]:
    """bounded: interaction_system's main.launch.py; llm_led: interaction_ablations'
    llm_led.launch.py (the same, with the monolithic ablation). With a configuration (or a voice
    of a test) speech is none: real_sessions.py starts simple_hri itself (speech_commands)."""
    launch = {"speech": "simple_hri", "language": "es", **campaigns.SPEECH_DEFAULTS,
              **sessions.get("launch", {})}
    package, file = (("interaction_system", "main.launch.py") if control == "bounded"
                     else ("interaction_ablations", "llm_led.launch.py"))
    return (["ros2", "launch", package, file] + [f"{k}:={v}" for k, v in launch.items()]
            + [f"params_file:={params_file}"])


def start_stack(command: list[str], log_path: str) -> subprocess.Popen:
    """The stack in its own process group (stopped with everything it started)."""
    log = open(log_path, "a")
    log.write("$ " + " ".join(command) + "\n\n")
    log.flush()
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    process.log = log
    return process


def stop_stack(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    # A second Ctrl+C (or a second Stop of the panel) must not cut the stop short: the stack is
    # in its own session and would be left running (holding the microphone, serving its actions)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        signal.signal(signal.SIGINT, previous)
    process.log.close()


def model_params(sessions: dict, model: str) -> dict:
    """The parameter overrides for one system model (model_params of the sessions file: e.g.
    the routing of the questions of the local model, which a cloud model does not use)."""
    return (sessions.get("model_params") or {}).get(model) or {}


def routed_models(sessions: dict, model: str) -> list[str]:
    """The other models that the routing of this system model sends questions to."""
    routes = (((model_params(sessions, model).get("interaction_intelligence") or {})
               .get("ros__parameters") or {}).get("routes") or [])
    return sorted({r.partition("=")[2].strip() for r in routes if "=" in r})


def check_model(sessions: dict, model: str, allow_cloud: bool, force: bool = False) -> str:
    """Refuse what cannot run (an unpinned model, a cloud model without --allow-cloud or its
    key, an Ollama server that does not answer, lacks the model or holds models of other work,
    which would slow the robot down; --force skips the last); returns the version of the model
    when it is known before the trial (the digest of an Ollama model; a cloud model is
    identified by its dated name)."""
    provider, name = split_model(model)
    if "<pin>" in model:
        sys.exit(f"Write the dated version of the model in the sessions file: {model}")
    if provider != "ollama":
        if not allow_cloud:
            sys.exit(f"This trial sends what the participant says (transcribed text) to {provider} "
                     f"({model}): run it with --allow-cloud")
        if not any(os.environ.get(k) for k in API_KEYS[provider]):
            sys.exit(f"No API key for {provider}: set {' or '.join(API_KEYS[provider])}")
        return ""
    llm = sessions.get("ollama", {})
    base = f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=10) as r:
            have = {m["name"]: m.get("digest", "") for m in json.load(r)["models"]}
    except OSError as exc:
        sys.exit(f"The Ollama server does not answer at {base} ({exc}): open the SSH tunnel first")
    digest = have.get(name) or have.get(f"{name}:latest")
    needed = [name] + routed_models(sessions, model)
    for n in needed:
        if (have.get(n) or have.get(f"{n}:latest")) is None:
            sys.exit(f"{n} is not on the Ollama server at {base}: pull it first (ollama pull {n})")
    with urllib.request.urlopen(f"{base}/api/ps", timeout=10) as r:
        others = [m["name"] for m in json.load(r).get("models", [])
                  if m["name"] not in needed and m["name"].removesuffix(":latest") not in needed]
    if others and not force:
        sys.exit(f"The Ollama server at {base} holds models of other work ({others}): the robot "
                 "would answer slower. Wait until that work ends (--force to run anyway)")
    return digest


def warm_up(sessions: dict, model: str) -> None:
    """Load the Ollama model, so that the first turn of the participant does not pay for it."""
    provider, name = split_model(model)
    if provider != "ollama":
        return
    llm = sessions.get("ollama", {})
    for n in [name] + routed_models(sessions, model):
        body = json.dumps({"model": n, "prompt": "Hola", "stream": False,
                           "options": {"num_predict": 1}}).encode()
        request = urllib.request.Request(
            f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}/api/generate",
            data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=600).read()
        except OSError as exc:
            say(f"(the model {n} could not be loaded in advance: {exc}; the first turn will be "
                "slower)")


# ------------------------------------------------------------------ the recorder
def make_recorder():
    """The ROS node of a trial (imported here, so that plan, card and balance need no ROS)."""
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.node import Node
    from std_msgs.msg import String

    from ros2_dialog_interfaces.action import DialogTask
    from ros2_dialog_interfaces.msg import BargeIn
    from simple_hri_interfaces.action import Listen, Say

    class SessionRecorder(Node):
        """The client side of the simulation harness, without serving the speech: sends the
        request, records the conversation and prints it as it goes."""

        def __init__(self):
            super().__init__("real_session_recorder")
            cb = ReentrantCallbackGroup()
            self._lock = threading.Lock()
            self.recording = False
            self.t0 = 0.0
            self.transcript, self.heard, self.skills, self.barge_ins, self.feedback = \
                [], [], [], [], []
            self.create_subscription(String, "/robot_utterance",
                                     lambda m: self._line("robot", m.data), 10, callback_group=cb)
            # A conversational filler: a robot line marked as such, not a line of the dialogue
            self.create_subscription(String, "/robot_filler",
                                     lambda m: self._line("robot", m.data, filler=True), 10,
                                     callback_group=cb)
            self.create_subscription(String, "/interaction/user_turn",
                                     lambda m: self._line("user", m.data), 10, callback_group=cb)
            self.create_subscription(String, "/listened_text", self._on_heard, 10,
                                     callback_group=cb)
            self.create_subscription(String, "/interaction/skill_trace", self._on_skill, 50,
                                     callback_group=cb)
            self.create_subscription(BargeIn, "/barge_in", self._on_barge_in, 10,
                                     callback_group=cb)
            self.dialog = ActionClient(self, DialogTask, "/dialog/execute_task", callback_group=cb)
            self.say_action = ActionClient(self, Say, "/tts_action", callback_group=cb)
            self.listen_action = ActionClient(self, Listen, "/stt_action", callback_group=cb)

        def now(self) -> float:
            return time.monotonic() - self.t0

        def _line(self, role: str, text: str, filler: bool = False) -> None:
            with self._lock:
                if self.recording:
                    line = {"role": role, "text": text, "t": self.now()}
                    if filler:
                        line["filler"] = True
                    self.transcript.append(line)
                    print(f"{role.upper()}{' (filler)' if filler else ''}: {text}", flush=True)

        def _on_heard(self, msg) -> None:
            with self._lock:
                if self.recording:
                    self.heard.append({"text": msg.data, "t": self.now()})

        def _on_skill(self, msg) -> None:
            with self._lock:
                if self.recording:
                    entry = json.loads(msg.data)
                    entry["t"] = self.now()
                    self.skills.append(entry)

        def _on_barge_in(self, msg) -> None:
            with self._lock:
                if self.recording:
                    self.barge_ins.append({"utterance": msg.utterance, "spoken_ms": msg.spoken_ms,
                                           "t": self.now()})
                    print(f"   (barge-in after {msg.spoken_ms} ms)", flush=True)

        def wait_ready(self, timeout: float) -> str:
            """'' when the orchestrator and simple_hri's actions are up, else what is missing."""
            end = time.monotonic() + timeout
            for client, what in ((self.dialog, "/dialog/execute_task (the interaction system)"),
                                 (self.say_action, "/tts_action (simple_hri)"),
                                 (self.listen_action, "/stt_action (simple_hri)")):
                if not client.wait_for_server(timeout_sec=max(1.0, end - time.monotonic())):
                    return what
            return ""

        def converse(self, goal, timeout: float) -> dict:
            """One conversation to its end (Ctrl-C cancels it and raises KeyboardInterrupt)."""
            done = threading.Event()
            out = {"status": "", "result": None}
            handle_box = [None]

            def on_feedback(msg):
                fb = msg.feedback
                self.feedback.append({"state": fb.state, "frame": fb.current_frame_json,
                                      "utterance": fb.last_utterance, "turns": fb.turns,
                                      "t": self.now()})

            def on_result(future):
                res = future.result()
                out["result"] = res.result
                out["status"] = {4: "succeeded", 5: "canceled", 6: "aborted"}.get(
                    res.status, str(res.status))
                done.set()

            def on_accepted(future):
                handle = future.result()
                if not handle.accepted:
                    out["status"] = "rejected"
                    done.set()
                    return
                handle_box[0] = handle
                handle.get_result_async().add_done_callback(on_result)

            with self._lock:
                self.t0 = time.monotonic()
                self.recording = True
            self.dialog.send_goal_async(goal, feedback_callback=on_feedback) \
                .add_done_callback(on_accepted)
            try:
                if not done.wait(timeout):
                    out["status"] = "timeout"
                    say(f"TIMEOUT: the conversation lasted more than {timeout:g} s; it is cancelled")
                    if handle_box[0] is not None:
                        handle_box[0].cancel_goal_async()
                        done.wait(10.0)
            except KeyboardInterrupt:
                if handle_box[0] is not None:
                    handle_box[0].cancel_goal_async()
                    done.wait(5.0)
                raise
            finally:
                with self._lock:
                    self.recording = False
                out["duration_s"] = time.monotonic() - self.t0
            return out

    return rclpy, SessionRecorder


# ------------------------------------------------------------------ commands
def show_plan(sessions: dict, plan: dict, pid: str, out: str) -> None:
    done = stored(pid, out)
    print(f"{pid}: order {' -> '.join(plan['order'])} (row {plan['latin_square_row']} of the "
          f"Latin square); {len(done)}/{len(plan['trials'])} trials stored. Plan in "
          f"{os.path.join(out, pid, 'plan.yaml')}")
    for t in plan["trials"]:
        model = sessions["enabled"].get(t["condition"], {}).get("model", "(not enabled now)")
        print(f"  {t['k']}. {t['scenario']:14s} {t['condition']:14s} {model:22s} "
              f"{'stored' if t['k'] in done else ''}")


def append_row(out: str, row: dict) -> None:
    path = os.path.join(out, "trials.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRIALS_CSV)
        if new:
            writer.writeheader()
        writer.writerow(row)


def current_trial(sessions: dict, plan: dict, k: int) -> dict:
    """Trial k of a plan, with the current details of its condition and scenario."""
    try:
        return details(sessions, plan["trials"][k - 1])
    except ValueError as exc:
        sys.exit(f"{exc}: the plan of {plan['participant']} no longer matches the sessions file")


def store(record: dict, work_dir: str, target: str) -> None:
    """The record at target and the logs of the stack in the folder of the same name; a trial
    stored before (run again) is moved to replaced/<time>/ first."""
    final_dir = target[:-len(".json")]
    if os.path.exists(target) or os.path.isdir(final_dir):
        old = os.path.join(os.path.dirname(target), "replaced", time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(old, exist_ok=True)
        for path in (target, final_dir):
            if os.path.exists(path):
                shutil.move(path, old)
        say(f"The trial stored before is kept in {old}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(work_dir, final_dir)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    say(f"Stored: {target}")


def planned_trial(args, sessions: dict) -> dict | None:
    """Trial k of a participant's plan (real_sessions.py trial P07 2)."""
    pid, _ = participant(args.participant)
    plan = read_plan(pid, args.out)
    if plan is None:
        sys.exit(f"{pid} has no plan: real_sessions.py plan {pid}")
    if not 1 <= args.k <= len(plan["trials"]):
        sys.exit(f"{pid} has trials 1 to {len(plan['trials'])}")
    return run_trial(args, sessions, pid, plan, current_trial(sessions, plan, args.k))


def run_trial(args, sessions: dict, pid: str, plan: dict, trial: dict) -> dict | None:
    """One conversation (a trial of a plan, or the rehearsal of `check rehearsal`); returns its
    record, or None when it did not run. With args.just_try nothing is stored."""
    model = trial["model"]
    configuration = sessions.get("configuration") or {}
    version = check_model(sessions, model, args.allow_cloud, args.force)
    target = record_path(pid, trial, args.out)
    barge_in = barge_in_of(args, sessions)
    if not args.just_try:
        # Barge-in is the same in every trial of a participant (in every condition)
        for path in stored(pid, args.out).values():
            with open(path, encoding="utf-8") as f:
                before = json.load(f).get("barge_in", False)
            if before != barge_in:
                sys.exit(f"{pid}'s stored trials ran with barge-in {'on' if before else 'off'}: "
                         f"run this one the same way (--barge-in {'on' if before else 'off'})")
    if configuration:
        print(f"Configuration {configuration['name']} ({configuration['hash']}): "
              f"{campaigns.describe(configuration['values'])}", flush=True)
    if args.just_try:
        print(f"TRY {pid} trial {trial['k']} ({trial['scenario']}, {trial['condition']}): a test, "
              "nothing is stored", flush=True)
    else:
        again = os.path.exists(target)
        print(f"{pid}: {len(stored(pid, args.out))}/{len(plan['trials'])} trials stored. Now trial "
              f"{trial['k']}{' (again: the new one replaces the stored one)' if again else ''}",
              flush=True)
    # The stack runs in a temporary folder whose name says nothing of the participant: the
    # participant's code never reaches the system (its parameters, its logs, the request) nor
    # what it sends to an LLM; only the stored record carries it. Once the trial ends, the
    # folder is stored with the record (or deleted, with --try)
    work_dir = tempfile.mkdtemp(prefix="real_trial_")
    scenario = next(s for s in sessions["scenarios"] if s["id"] == trial["scenario"])["definition"]
    if sessions.get("filler_after_s") is not None:
        # The filler delay of the configuration (or of try-scenario --filler-after), over the
        # scenario's own
        scenario = {**scenario, "goal": {**scenario.get("goal", {}),
                                         "filler_after_s": sessions["filler_after_s"]}}

    # run_experiment.make_goal: the same request as in the simulated experiments
    sys.path.insert(0, os.path.join(EVAL, "scripts"))
    from run_experiment import make_goal
    rclpy, SessionRecorder = make_recorder()
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.signals import SignalHandlerOptions
    # Ctrl-C must reach this script (it cancels the conversation and stops the stack), not shut
    # ROS down underneath it
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = SessionRecorder()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    process, perception, record, row = None, None, None, None
    speech = []   # simple_hri started here (try-scenario --voice...), the stack without it
    try:
        say(f"TRIAL {pid} {trial['k']}/{len(plan['trials'])}: {trial['scenario']} "
            f"({trial['strategy']}), condition {trial['condition']} ({trial['control']}, {model})\n"
            f"    The card for the participant (the control panel shows it full screen):\n\n"
            + "\n".join("      " + line for line in card_text(trial, sessions).splitlines()))
        warm_up(sessions, model)
        if barge_in:
            say("Barge-in on: starting the lip activity detector (camera, live, no images stored)")
            perception = start_stack(perception_command(sessions),
                                     os.path.join(work_dir, "perception.log"))
        if sessions.get("piper"):
            say(f"Voice {piper_text(sessions['piper'])}: simple_hri starts here with it (the "
                "first time, the voice is downloaded)")
            speech = [start_stack(c, os.path.join(work_dir, f"speech_{i}.log"))
                      for i, c in enumerate(speech_commands(sessions, sessions["piper"]))]
        command = stack_command(sessions, trial["control"],
                                stack_params(sessions, model, work_dir, barge_in))
        process = start_stack(command, os.path.join(work_dir, "system.log"))
        say("Starting the stack (speech recognition, voice, LLM gateway, interaction system)...")
        missing = node.wait_ready(180.0)
        if missing:
            raise RuntimeError(f"{missing} did not start in 180 s; see system.log")
        time.sleep(3.0)
        say("READY: the participant has read the card and sits in front of the robot")
        if input(f"{PROMPT}   q: quit without running it > ").strip().lower() == "q":
            raise NotRun
        say("CONVERSATION: the robot starts")
        t0 = time.time()
        run = node.converse(make_goal(scenario, sessions),
                            float(sessions.get("dialogue_timeout", 900)))
        result = run["result"]
        record = {
            "participant": pid, "trial": trial["k"], "order": plan["order"],
            "scenario": trial["scenario"], "strategy": trial["strategy"],
            "persona": "real", "rep": trial["k"], "seed": None,
            "model": model, "condition": trial["condition"], "control": trial["control"],
            # The version that answered is not reported by the gateway: the digest of an Ollama
            # model; a cloud model is identified by its dated name
            "provider": split_model(model)[0], "model_version": version,
            "barge_in": barge_in,
            # The campaign and how the robot sounded (campaigns.py): a campaign refuses a trial
            # whose configuration file no longer has this hash
            "campaign": sessions.get("campaign", ""),
            "configuration": configuration.get("name", ""),
            "configuration_hash": configuration.get("hash", ""),
            "configuration_values": configuration.get("values", {}),
            "status": run["status"],
            "success": bool(result.success) if result else False,
            "outcome": result.outcome if result else "",
            "failure_reason": result.failure_reason if result else "",
            "data": json.loads(result.final_frame_json or "{}") if result else {},
            "total_turns": result.total_turns if result else 0,
            "duration_s": round(run["duration_s"], 2),
            "expect": scenario.get("expect", {}),
            "goal": scenario.get("goal", {}),
            "transcript": node.transcript, "heard": node.heard, "feedback": node.feedback,
            "skills": node.skills, "barge_ins": node.barge_ins, "user_log": [],
            "goal": scenario["goal"], "user_goal": scenario["user_goal"],
            "title": trial["title"], "card": trial["card"], "detour": trial["detour"],
            "language": sessions.get("language", "Spanish"),
            "launch": " ".join(command[:-1]),     # without the parameter file (in the logs)
            "sessions_file": plan.get("sessions_file"), "sessions_hash": sessions["hash"],
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "started_at": t0,
        }
        say(f"END: {record['status']}, {record['outcome'] or 'no outcome'}, "
            f"{record['total_turns']} turns, {record['duration_s']:.0f} s"
            + (f" ({record['failure_reason']})" if record["failure_reason"] else ""), bell=True)
        row = {"status": record["status"], "success": record["success"],
               "outcome": record["outcome"],
               "turns": record["total_turns"], "duration_s": record["duration_s"],
               "stored": "no (a test)" if args.just_try else "yes",
               "barge_in": "on" if barge_in else "off",
               "configuration": configuration.get("name", ""),
               "notes": record["failure_reason"]}
    except NotRun:
        say("Not run: nothing stored")
        shutil.rmtree(work_dir, ignore_errors=True)
    except KeyboardInterrupt:
        say("CANCELLED: the conversation and the stack are stopped; nothing stored", bell=True)
        row = {"status": "cancelled", "stored": "no", "notes": "cancelled by the operator",
               "configuration": configuration.get("name", "")}
    except RuntimeError as exc:
        say(f"NOT RUN: {exc}. Nothing stored", bell=True)
        row = {"status": "not run", "stored": "no", "notes": str(exc),
               "configuration": configuration.get("name", "")}
    finally:
        say("Stopping the stack...")
        stop_stack(process)
        stop_stack(perception)
        for p in speech:
            stop_stack(p)
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
    if args.just_try:
        if args.keep:
            print(f"Test kept in {work_dir} (delete it when done)")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)
        return record
    if record is not None:
        store(record, work_dir, target)
    elif row is not None:
        print(f"The logs of the stack are in {work_dir} (not stored; delete them when done)")
    if row is not None:
        append_row(args.out, {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "participant": pid,
                              "trial": trial["k"], "scenario": trial["scenario"],
                              "condition": trial["condition"], "model": model, **row})
    say(f"{pid}: {len(stored(pid, args.out))}/{len(plan['trials'])} trials stored")
    return record


# ------------------------------------------------------------------ checks before a session
CHECKS = ("speak", "listen", "llm", "system", "perception", "rehearsal", "presession")


def verdict(what: str, status: str, detail: str) -> int:
    """The last line of a check (the control panel shows it): CHECK <what>: OK | FAILED |
    SKIPPED - <detail>. Returns the exit status."""
    print(f"\nCHECK {what}: {status} - {' '.join(str(detail).split())}", flush=True)
    return 1 if status == "FAILED" else 0


class CheckFailed(Exception):
    """A check that cannot go on: why."""


def ros_node():
    """(rclpy, a node spun in a background thread, its executor)."""
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.signals import SignalHandlerOptions
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = rclpy.create_node("real_session_check")
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    return rclpy, node, executor


# PIPER_VOICE, PIPER_SETTINGS and parse_voice are in sessions_common.py (the configurations of
# campaigns.py use them without ROS)


def piper_of(args) -> dict:
    """The voice and settings given (--voice, --length-scale, --noise-scale, --noise-w-scale):
    {voice?, length_scale?, ...}; empty when none."""
    piper = {"voice": args.voice} if getattr(args, "voice", "") else {}
    for key in PIPER_SETTINGS:
        if getattr(args, key, None) is not None:
            piper[key] = getattr(args, key)
    return piper


def piper_text(piper: dict) -> str:
    """The voice and settings in words, e.g. "es_ES-sharvard-medium:1, noise_scale 0.9"."""
    return ", ".join([piper.get("voice") or "the default voice"]
                     + [f"{k} {piper[k]:g}" for k in PIPER_SETTINGS if k in piper])


def piper_parameters(piper: dict) -> list[str]:
    """The parameters (-p ...) of tts_piper for a voice and its settings; ValueError if wrong."""
    params = []
    if piper.get("voice"):
        name, speaker = parse_voice(piper["voice"])
        params += ["-p", f"voice:={name}"]
        params += ["-p", f"speaker:={speaker}"] if speaker is not None else []
    for key, (low, high) in PIPER_SETTINGS.items():
        if piper.get(key) is None:
            continue
        value = float(piper[key])
        if not low <= value <= high:
            raise ValueError(f"--{key.replace('_', '-')} {value:g}: from {low:g} to {high:g}")
        # Always with a decimal point: ROS reads it as a double, the parameter's type
        params += ["-p", f"{key}:={value:.4f}"]
    return params


def speech_commands(sessions: dict, piper: dict | None = None) -> list[list[str]]:
    """simple_hri's two nodes as main.launch.py starts them (the settings of `launch`, the
    speech settings of the language file of its language); with a Piper voice and/or its
    settings (piper_of, or a configuration's), those instead of the language's voice and the
    voice's defaults. The microphone and the speech-recognition model are those of the
    configuration (campaigns.apply puts them in `launch`), else campaigns.SPEECH_DEFAULTS."""
    from interaction_intelligence.languages import load_language

    launch = {"stt": "faster_whisper", "tts": "piper", "language": "es",
              **campaigns.SPEECH_DEFAULTS, **sessions.get("launch", {})}
    speech = load_language(launch["language"]).speech
    stt_code = speech["stt_language"]
    if launch["tts"] == "piper":
        tts = ["-p", f"lang_code:={stt_code}"]
        if not (piper or {}).get("voice"):
            tts += ["-p", f"voice:={speech['piper_voice']}"]
            if speech["piper_speaker"] >= 0:
                tts += ["-p", f"speaker:={speech['piper_speaker']}"]
    else:
        tts = ["-p", f"lang_code:={speech['mms_voice']}", "-p", "use_gpu:=true"]
    microphone = (["-p", f"input_device:={launch['input_device']}"]
                  if launch.get("input_device") else [])
    if piper:
        if launch["tts"] != "piper":
            raise ValueError(f"--voice and its settings are of Piper: the sessions file uses tts "
                             f"{launch['tts']}")
        tts += piper_parameters(piper)
    return [["ros2", "run", "simple_hri", f"stt_{launch['stt']}", "--ros-args", "-r",
             "__node:=stt_service_node", "-p", f"model:={launch['whisper_model']}",
             "-p", f"language:={stt_code}"] + microphone,
            ["ros2", "run", "simple_hri", f"tts_{launch['tts']}", "--ros-args", "-r",
             "__node:=tts_service_node", "-p", "play_sound:=true"] + tts]


def ensure_speech(node, sessions: dict, log_dir: str, processes: list,
                  piper: dict | None = None) -> tuple:
    """simple_hri's action clients (Say, Listen); its nodes are started (into processes) unless
    a stack already serves them. With a Piper voice or settings (check speak --voice...) only the
    voice is started, with them, and a running simple_hri is refused (its voice cannot be
    changed)."""
    from rclpy.action import ActionClient
    from simple_hri_interfaces.action import Listen, Say
    say_client = ActionClient(node, Say, "/tts_action")
    listen_client = ActionClient(node, Listen, "/stt_action")
    if piper:
        if say_client.wait_for_server(timeout_sec=3.0):
            raise CheckFailed(f"simple_hri is already running: stop it to try {piper_text(piper)}")
        say(f"Starting simple_hri's voice: {piper_text(piper)} (the first time, the voice is "
            "downloaded)...")
        processes.append(start_stack(speech_commands(sessions, piper)[1],
                                     os.path.join(log_dir, "speech_1.log")))
        if not say_client.wait_for_server(timeout_sec=180.0):
            raise CheckFailed(f"/tts_action did not start in 180 s (see {log_dir})")
        return say_client, listen_client
    if say_client.wait_for_server(timeout_sec=3.0) and listen_client.wait_for_server(timeout_sec=1.0):
        say("simple_hri is already running: it is used as it is")
        return say_client, listen_client
    say("Starting simple_hri (speech recognition and voice; the first time it loads its models)...")
    for i, command in enumerate(speech_commands(sessions)):
        processes.append(start_stack(command, os.path.join(log_dir, f"speech_{i}.log")))
    for client, name in ((say_client, "/tts_action"), (listen_client, "/stt_action")):
        if not client.wait_for_server(timeout_sec=180.0):
            raise CheckFailed(f"{name} did not start in 180 s (see {log_dir})")
    return say_client, listen_client


def call_action(client, name: str, goal, timeout: float):
    """The result of one goal of an action (Ctrl-C cancels it)."""
    done = threading.Event()
    box = {}

    def on_result(future):
        box["result"] = future.result().result
        done.set()

    def on_accepted(future):
        handle = future.result()
        box["handle"] = handle
        if not handle.accepted:
            done.set()
            return
        handle.get_result_async().add_done_callback(on_result)

    client.send_goal_async(goal).add_done_callback(on_accepted)
    try:
        if not done.wait(timeout):
            raise CheckFailed(f"no answer from {name} in {timeout:g} s")
    except KeyboardInterrupt:
        if box.get("handle") is not None:
            box["handle"].cancel_goal_async()
        raise
    if "result" not in box:
        raise CheckFailed(f"{name} rejected the goal")
    return box["result"]


def ask(prompt: str) -> bool:
    """The operator's answer: Enter (True) or q (False)."""
    return input(f"{prompt} > ").strip().lower() != "q"


def check_speak(node, sessions, log_dir, processes, phrase: str = "",
                piper: dict | None = None) -> tuple[str, str]:
    say_client, _ = ensure_speech(node, sessions, log_dir, processes, piper)
    return speak(say_client, sessions, phrase, piper)


def speak(say_client, sessions, phrase: str = "", piper: dict | None = None) -> tuple[str, str]:
    """The voice says the phrase (default: check_phrase of the sessions file); the operator
    confirms that it was heard."""
    from simple_hri_interfaces.action import Say
    phrase = phrase.strip() or sessions.get("check_phrase", "Hola.")
    say(f'SPEAKING{f" with {piper_text(piper)}" if piper else ""}: "{phrase}"')
    t0 = time.monotonic()
    result = call_action(say_client, "/tts_action", Say.Goal(text=phrase), 60.0)
    if not result.success:
        return "FAILED", f"/tts_action failed: {result.message}"
    took = time.monotonic() - t0
    if ask("Enter: I heard it   q: I did not hear it"):
        return "OK", f"the phrase was heard ({took:.1f} s)"
    return "FAILED", "the voice played, but it was not heard: check the speaker and its volume"


def check_listen(node, sessions, log_dir, processes) -> tuple[str, str]:
    _, listen_client = ensure_speech(node, sessions, log_dir, processes)
    return listen(node, listen_client)


def listen(node, listen_client) -> tuple[str, str]:
    """One transcription of what someone says; the operator confirms the words and the
    language."""
    from simple_hri_interfaces.action import Listen
    from std_msgs.msg import String
    cleaned = []
    node.create_subscription(String, "/interaction/user_utterance", lambda m: cleaned.append(m.data), 10)
    if not ask("Enter: listen now (then say a sentence in Spanish in front of the microphone)   q: skip"):
        return "SKIPPED", "not listened"
    say("LISTENING: speak now (up to 15 s to start)")
    t0 = time.monotonic()
    result = call_action(listen_client, "/stt_action", Listen.Goal(max_wait=15.0), 90.0)
    if result.timed_out:
        return "FAILED", "nobody was heard in 15 s: check the microphone, its level and the VAD"
    if not result.success or not result.text.strip():
        return "FAILED", f"no transcription ({result.message})"
    say(f'HEARD: "{result.text}" ({time.monotonic() - t0:.1f} s)')
    time.sleep(2.0)
    extra = (f'; the intelligence took it as "{cleaned[-1]}"' if cleaned else
             "; the intelligence is not running (no cleaned utterance)")
    # As the Speak check: only the person who spoke knows whether the words are right
    if not ask("Enter: the words and the language are right   q: they are wrong"):
        return "FAILED", f'heard "{result.text}"{extra}: the words or the language are wrong'
    return "OK", f'heard "{result.text}"{extra}; confirmed by the operator'


def ping_model(sessions: dict, model: str) -> str:
    """One call of one token to the model; returns what it took (raises on failure)."""
    provider, name = split_model(model)
    t0 = time.monotonic()
    if provider == "ollama":
        llm = sessions.get("ollama", {})
        base = f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"
        with urllib.request.urlopen(f"{base}/api/tags", timeout=10) as r:
            have = {m["name"] for m in json.load(r)["models"]}
        if name not in have and f"{name}:latest" not in have:
            raise CheckFailed(f"{name} is not on the Ollama server at {base} (ollama pull {name})")
        url = f"{base}/api/generate"
        body = {"model": name, "prompt": "Hola", "stream": False, "options": {"num_predict": 1}}
        headers = {}
    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        body = {"model": name, "messages": [{"role": "user", "content": "Hola"}],
                "max_completion_tokens": 1}
        headers = {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": "Hola"}]}],
                "generationConfig": {"maxOutputTokens": 1}}
        headers = {"x-goog-api-key": os.environ.get("GEMINI_API_KEY")
                   or os.environ.get("GOOGLE_API_KEY", "")}
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=600) as r:
            answer = json.load(r)
    except urllib.error.HTTPError as exc:
        raise CheckFailed(f"HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}")
    except OSError as exc:
        raise CheckFailed(f"no answer: {exc}")
    version = answer.get("model") or answer.get("modelVersion") or ""
    return f"{time.monotonic() - t0:.1f} s" + (f", version {version}" if version and version != name else "")


def check_llm(sessions, allow_cloud) -> tuple[str, str]:
    """The model of every enabled condition answers."""
    results, failed, skipped = [], False, False
    for model in dict.fromkeys(c["model"] for c in sessions["enabled"].values()):
        provider = split_model(model)[0]
        if "<pin>" in model:
            results.append(f"{model}: write its dated version")
            failed = True
            continue
        if provider != "ollama" and not allow_cloud:
            results.append(f"{model}: skipped (a cloud model: confirm the cloud calls)")
            skipped = True
            continue
        if provider != "ollama" and not any(os.environ.get(k) for k in API_KEYS[provider]):
            results.append(f"{model}: no API key ({' or '.join(API_KEYS[provider])})")
            failed = True
            continue
        try:
            took = ping_model(sessions, model)
            results.append(f"{model}: answers ({took}, the first call loads it)"
                           if provider == "ollama" else f"{model}: answers ({took})")
            say(results[-1])
        except CheckFailed as exc:
            results.append(f"{model}: {exc}")
            failed = True
    return ("FAILED" if failed else "SKIPPED" if skipped and len(results) == 1 else "OK"), "; ".join(results)


def check_system(node, sessions, log_dir, allow_cloud, barge_in=False) -> tuple[str, str]:
    """The stack starts in every enabled condition (its control and model) and serves
    /dialog/execute_task and simple_hri's actions; it is stopped after each."""
    from rclpy.action import ActionClient
    from ros2_dialog_interfaces.action import DialogTask
    from simple_hri_interfaces.action import Say
    client = ActionClient(node, DialogTask, "/dialog/execute_task")
    say_client = ActionClient(node, Say, "/tts_action")
    if client.wait_for_server(timeout_sec=3.0):
        return "FAILED", "an interaction system is already running in this ROS domain: stop it first"
    results, failed = [], False
    for name, condition in sessions["enabled"].items():
        model = condition["model"]
        if "<pin>" in model or (is_cloud(model) and not allow_cloud):
            results.append(f"{name}: skipped ({'unpinned model' if '<pin>' in model else 'a cloud model: confirm the cloud calls'})")
            failed = failed or "<pin>" in model
            continue
        work = os.path.join(log_dir, name)
        os.makedirs(work, exist_ok=True)
        say(f"Starting the stack of {name} ({condition['control']}, {model})...")
        t0 = time.monotonic()
        # With a configuration the stack runs without its speech nodes: simple_hri with it here
        speech = [start_stack(c, os.path.join(work, f"speech_{i}.log"))
                  for i, c in enumerate(speech_commands(sessions, sessions["piper"]))] \
            if sessions.get("piper") else []
        process = start_stack(stack_command(sessions, condition["control"],
                                            stack_params(sessions, model, work, barge_in)),
                              os.path.join(work, "system.log"))
        try:
            up = client.wait_for_server(timeout_sec=180.0) and \
                say_client.wait_for_server(timeout_sec=max(1.0, 180.0 - (time.monotonic() - t0)))
            results.append(f"{name}: {'up in %.0f s' % (time.monotonic() - t0) if up else 'did not start in 180 s (see its system.log)'}")
            failed = failed or not up
            say(results[-1])
        finally:
            say("Stopping it...")
            stop_stack(process)
            for p in speech:
                stop_stack(p)
            # Until its servers are gone, so that the next start is not taken for this one
            end = time.monotonic() + 20.0
            while (client.server_is_ready() or say_client.server_is_ready()) and time.monotonic() < end:
                time.sleep(0.5)
    return ("FAILED" if failed else "OK"), "; ".join(results)


def check_perception(node, sessions, log_dir, processes, barge_in) -> tuple[str, str]:
    """Only with barge-in: the lip activity detector (started here, camera live, no images
    stored, no emotion) publishes /user_speaking, which goes true when someone talks in front of
    the camera."""
    from std_msgs.msg import Bool
    if not barge_in:
        return "SKIPPED", "barge-in is off: the camera is not used"
    seen = []
    node.create_subscription(Bool, "/user_speaking", lambda m: seen.append(m.data), 10)
    say("Starting the lip activity detector...")
    processes.append(start_stack(perception_command(sessions),
                                 os.path.join(log_dir, "perception.log")))
    end = time.monotonic() + 60.0
    while not seen and time.monotonic() < end:
        time.sleep(0.2)
    if not seen:
        return "FAILED", ("/user_speaking was not published in 60 s: check the camera topic "
                          f"({sessions.get('camera_topic', '/camera/image_raw')}) and perception.log")
    if not ask("Enter: watch the lips for 15 s (someone talks in front of the camera)   q: skip"):
        return "OK", "the lip activity detector publishes /user_speaking (not watched)"
    seen.clear()
    say("WATCHING: talk in front of the camera now (15 s)")
    time.sleep(15.0)
    if True in seen:
        return "OK", "/user_speaking went true while talking"
    return "FAILED", "/user_speaking stayed false: check the camera and the face in view"


PRESESSION = ("speak", "listen", "llm")


def check_presession(node, sessions, log_dir, processes, phrase: str = "",
                     allow_cloud: bool = False) -> dict:
    """The pre-session check of a campaign, in sequence: the loudspeaker (the voice of the
    configuration says the phrase), the microphone (one transcription with its microphone and
    speech-recognition model) and the model of every condition. simple_hri is started here with
    the configuration (a running one is refused: its voice and microphone are not known). Every
    part prints its verdict; returns {part: {status, detail}}."""
    from rclpy.action import ActionClient
    from simple_hri_interfaces.action import Listen, Say
    say_client = ActionClient(node, Say, "/tts_action")
    listen_client = ActionClient(node, Listen, "/stt_action")
    if say_client.wait_for_server(timeout_sec=3.0) or listen_client.wait_for_server(timeout_sec=1.0):
        raise CheckFailed("simple_hri is already running: stop it, so that the check runs with "
                          "the configuration")
    piper = sessions.get("piper") or None
    say("Starting simple_hri" + (f" with {piper_text(piper)}" if piper else "")
        + " (the first time it loads its models and downloads the voice)...")
    for i, command in enumerate(speech_commands(sessions, piper)):
        processes.append(start_stack(command, os.path.join(log_dir, f"speech_{i}.log")))
    for client, name in ((say_client, "/tts_action"), (listen_client, "/stt_action")):
        if not client.wait_for_server(timeout_sec=180.0):
            raise CheckFailed(f"{name} did not start in 180 s (see {log_dir})")
    results = {}
    for part in PRESESSION:
        say(f"PRE-SESSION CHECK: {part}")
        try:
            if part == "speak":
                status, detail = speak(say_client, sessions, phrase, piper)
            elif part == "listen":
                status, detail = listen(node, listen_client)
            else:
                status, detail = check_llm(sessions, allow_cloud)
        except CheckFailed as exc:
            status, detail = "FAILED", str(exc)
        verdict(part, status, detail)
        results[part] = {"status": status, "detail": " ".join(str(detail).split())}
    return results


def run_check(args, sessions: dict) -> int:
    """One of the checks before a session (nothing is stored); its verdict is the last line."""
    piper = piper_of(args)
    if piper:
        if args.what != "speak":
            sys.exit("--voice and its settings: only for check speak")
    # The voice of the configuration (--campaign, --config), with the ad-hoc values over it
    piper = {**(sessions.get("piper") or {}), **piper} or None
    if piper or sessions.get("piper"):
        try:
            speech_commands(sessions, piper or sessions.get("piper"))
        except ValueError as exc:
            sys.exit(str(exc))
    if args.what == "rehearsal":
        if args.condition not in sessions["enabled"]:
            sys.exit(f"--condition: one of {list(sessions['enabled'])}")
        trial = details(sessions, {"k": 1, "scenario": args.scenario, "condition": args.condition})
        args.just_try = True
        try:
            record = run_trial(args, sessions, "REHEARSAL",
                               {"order": [args.condition], "trials": [trial]}, trial)
        except SystemExit as exc:   # refused before it started (check_model)
            return verdict("rehearsal", "FAILED", str(exc.code))
        if record is None:
            return verdict("rehearsal", "FAILED", "the conversation did not run (see above)")
        return verdict("rehearsal", "OK", f"{trial['scenario']} in {trial['condition']}: "
                       f"{record['status']}, {record['total_turns']} turns, "
                       f"{record['duration_s']:.0f} s (nothing stored)")
    if args.what == "llm":
        status, detail = check_llm(sessions, args.allow_cloud)
        return verdict("llm", status, detail)
    log_dir = tempfile.mkdtemp(prefix=f"real_check_{args.what}_")
    rclpy, node, executor = ros_node()
    processes = []
    results = {}
    try:
        if args.what == "presession":
            results = check_presession(node, sessions, log_dir, processes,
                                       getattr(args, "phrase", ""), args.allow_cloud)
            status = "OK" if all(r["status"] == "OK" for r in results.values()) else "FAILED"
            detail = ", ".join(f"{k} {r['status']}" for k, r in results.items())
        elif args.what == "speak":
            status, detail = check_speak(node, sessions, log_dir, processes, args.phrase,
                                         piper)
        elif args.what == "listen":
            status, detail = check_listen(node, sessions, log_dir, processes)
        elif args.what == "system":
            status, detail = check_system(node, sessions, log_dir, args.allow_cloud,
                                          barge_in_of(args, sessions))
        else:
            status, detail = check_perception(node, sessions, log_dir, processes,
                                              barge_in_of(args, sessions))
    except CheckFailed as exc:
        status, detail = "FAILED", str(exc)
    except KeyboardInterrupt:
        status, detail = "FAILED", "cancelled by the operator"
    finally:
        for process in reversed(processes):
            stop_stack(process)
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
    campaign = getattr(args, "campaign_info", None)
    if args.what == "presession" and campaign:
        # The date of the last pass of this campaign (the counting trial warns without one today)
        if not results:
            results = {"speak": {"status": "FAILED", "detail": detail}}
        state = campaigns.record_presession(campaign, results, args.out_root)
        print(f"Pre-session check of {campaign['name']}: "
              + ("PASSED" if state["last"]["passed"] else "not passed")
              + (f"; last pass {state['last_pass']['time']}" if state.get("last_pass") else ""),
              flush=True)
    if status == "FAILED" and not args.keep:
        print(f"The logs are in {log_dir}")
    elif args.keep:
        print(f"Kept in {log_dir}")
    else:
        shutil.rmtree(log_dir, ignore_errors=True)
    return verdict(args.what, status, detail)


def review(args) -> int:
    """Review the stored trials of a participant (or one, --k): show each transcript, read the
    replacements ("real text => replacement", or "real text" alone for [DATO]; an empty line ends),
    apply them to the record and to the logs of the trial, and mark the record as reviewed."""
    pid, _ = participant(args.participant)
    paths = stored(pid, args.out)
    if args.k:
        paths = {k: v for k, v in paths.items() if k == args.k}
    if not paths:
        sys.exit(f"{pid} has no stored trial{f' {args.k}' if args.k else ''}")
    for k, path in sorted(paths.items()):
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        if is_reviewed(record) and not args.again:
            print(f"Trial {k}: already reviewed by {record['reviewed']['by']} "
                  f"({record['reviewed']['time']}); --again to review it again")
            continue
        print(f"\n=== {pid} trial {k}: {record.get('scenario')} ===")
        for i, turn in enumerate(record.get("transcript", []), 1):
            role = turn.get('role', '?').upper() + (" (filler)" if turn.get("filler") else "")
            print(f"{i:3d} {role:6s} {turn.get('text', '')}")
        heard = [h.get("text", h) if isinstance(h, dict) else h for h in record.get("heard", [])]
        if heard:
            print("    What the speech recognition heard (it may hold more than the turns):")
            for h in heard:
                print(f"      {h}")
        print("Personal data the participant said that is not on the card (a name, an address, a "
              "phone...): write 'real text => replacement' (or the real text alone: "
              f"{PLACEHOLDER}); an empty line ends.")
        pairs = []
        while True:
            line = input("> ").strip()
            if not line:
                break
            old, _, new = line.partition("=>")
            if old.strip():
                pairs.append((old.strip(), new.strip() or PLACEHOLDER))
        when = time.strftime("%Y-%m-%d %H:%M")
        reviewed, count = review_record(record, pairs, args.by, when)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(reviewed, f, ensure_ascii=False, indent=1)
        logs = path[:-len(".json")]
        in_logs = 0
        for root, _, files in os.walk(logs):
            for name in files:
                file = os.path.join(root, name)
                try:
                    with open(file, encoding="utf-8") as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                text, n = replace_in_text(text, pairs)
                if n:
                    with open(file, "w", encoding="utf-8") as f:
                        f.write(text)
                    in_logs += n
        print(f"Trial {k}: reviewed by {args.by}; {count} replacements in the record, "
              f"{in_logs} in its logs")
    return 0


def try_scenario(args, sessions: dict) -> int:
    """A scenario of any scenario file, with a person as the user, as a trial --try: nothing is
    stored. The conditions, models and limits are those of the sessions file, the speech settings
    those of the configuration (--config or --campaign, else the defaults) with the ad-hoc values
    over them; the language follows the scenario file."""
    path = os.path.abspath(args.scenarios)
    scenario = next((s for s in load(path)["scenarios"] if s["id"] == args.scenario), None)
    if scenario is None:
        sys.exit(f"{args.scenario} is not in {path}")
    spanish = os.path.basename(path).endswith("_es.yaml")
    model = args.model or next((c["model"] for c in sessions["conditions"].values()
                                if c.get("control") == args.control), "")
    if not model:
        sys.exit(f"--model: no condition of the sessions file has control {args.control}")
    test = dict(sessions)
    test["language"] = "Spanish" if spanish else "English"
    test["launch"] = {**sessions.get("launch", {}), "language": "es" if spanish else "en"}
    piper = {**(sessions.get("piper") or {}), **piper_of(args)}
    if piper:
        # The stack without its speech nodes: run_trial starts simple_hri with the voice
        try:
            speech_commands(test, piper)
            if piper.get("voice") and \
                    campaigns.voice_language(piper["voice"]) != test["launch"]["language"]:
                raise ValueError(f"The voice {piper['voice']} is not of the language of "
                                 f"{os.path.basename(path)}")
        except ValueError as exc:
            sys.exit(str(exc))
        test["piper"] = piper
        test["launch"]["speech"] = "none"
    if args.filler_after is not None:
        if args.filler_after < 0:
            sys.exit("--filler-after: seconds, 0 or more (0: no fillers)")
        scenario = {**scenario, "goal": {**scenario.get("goal", {}),
                                         "filler_after_s": args.filler_after}}
        test["filler_after_s"] = args.filler_after     # over the configuration's
    test["scenarios"] = [{"id": scenario["id"], "definition": scenario}]
    test["card_footer"] = []
    goal_lines = [line.strip() for line in str(scenario.get("user_goal", "")).splitlines() if line.strip()]
    trial = {"k": 1, "scenario": scenario["id"], "title": scenario["id"],
             "strategy": scenario.get("strategy"), "condition": "test", "control": args.control,
             "model": model,
             "card": [scenario.get("goal", {}).get("task_description", ""), ""] + goal_lines,
             "detour": {"kind": "none", "text": ""}}
    test["detour_label"] = ""
    args.just_try = True
    record = run_trial(args, test, "TEST", {"order": ["test"], "trials": [trial]}, trial)
    return 0 if record is not None else 1


def piper_arguments(p, prefix: str) -> None:
    """--length-scale, --noise-scale, --noise-w-scale (PIPER_SETTINGS)."""
    words = {"length_scale": "speed: < 1 faster, > 1 slower (usually 0.8-1.3)",
             "noise_scale": "expressiveness: the variation of the voice (usually 0.3-1.0)",
             "noise_w_scale": "rhythm: the variation of the phoneme durations (usually 0.3-1.2)"}
    for key, (low, high) in PIPER_SETTINGS.items():
        p.add_argument(f"--{key.replace('_', '-')}", dest=key, type=float, default=None,
                       help=f"{prefix}the voice's {key}, {words[key]}; from {low:g} to {high:g}. "
                            "Default: the voice's own")


def configured(args) -> tuple[dict, dict | None, str]:
    """(the sessions file with the configuration of the command, the campaign or None, the
    folder of the sessions' data). With --campaign: its sessions file, its configuration and its
    folder (args.out becomes <out>/<campaign>); a trial, a check or a test of a campaign that
    fails campaigns.integrity is refused, saying why. Else --config (any configuration), or none
    (the defaults; a trial and a plan need a campaign)."""
    root, campaign, configuration = args.out, None, None
    if args.campaign:
        try:
            campaign = campaigns.load_campaign(args.campaign)
        except ValueError as exc:
            sys.exit(str(exc))
        if getattr(args, "config", ""):
            sys.exit(f"--config: campaign {campaign['name']} runs with its own configuration "
                     f"({campaign['configuration']})")
        args.out = campaigns.campaign_out(campaign["name"], root)
        args.sessions = campaign["sessions_path"]
        if args.command in ("trial", "check", "try-scenario"):
            why = campaigns.integrity(campaign, root)
            if why:
                sys.exit(f"Campaign {campaign['name']} refuses its trials and checks: {why}")
            configuration = campaign["config"]
    elif args.command in ("plan", "trial"):
        sys.exit("A participant belongs to a campaign: --campaign <name> (real/campaigns/; "
                 "doc/REAL_SESSIONS.md#campaigns-and-configurations)")
    elif getattr(args, "config", ""):
        try:
            configuration = campaigns.load_config(args.config)
        except ValueError as exc:
            sys.exit(str(exc))
    sessions = load_sessions(args.sessions)
    if configuration:
        sessions = campaigns.apply(sessions, configuration, campaign["name"] if campaign else "")
        if args.command == "trial" and not args.just_try:
            state = campaigns.presession_state(campaign, root)
            if not state["passed_today"]:
                last = (state.get("last_pass") or {}).get("time")
                say(f"WARNING: the pre-session check of {campaign['name']} has not passed today "
                    f"with its configuration ({'last pass ' + last if last else 'never passed'}): "
                    f"real_sessions.py --campaign {campaign['name']} check presession", bell=True)
    return sessions, campaign, root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions", default=SESSIONS,
                        help="The sessions file (with --campaign: the campaign's)")
    parser.add_argument("--out", default=OUT,
                        help="The folder of the sessions' data (a campaign's participants are in "
                             "<out>/<campaign>/)")
    parser.add_argument("--campaign", default="",
                        help="The campaign (real/campaigns/<name>.yaml): its sessions file, its "
                             "configuration and its participants. Needed by plan and trial")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="Create (or show) the plan of a participant")
    p.add_argument("participant", help="P01, P02...")
    p.add_argument("--replace", action="store_true",
                   help="Replace an existing plan with what the sessions file gives now "
                        "(refused once a trial is stored)")
    p = sub.add_parser("card", help="Print the task card of a trial")
    p.add_argument("participant")
    p.add_argument("k", type=int)
    p = sub.add_parser("balance", help="How balanced the plans of P01...PN are")
    p.add_argument("count", type=int)
    p = sub.add_parser("trial", help="Run one trial of a participant")
    p.add_argument("participant")
    p.add_argument("k", type=int, help="The trial (1, 2...) of the participant's plan")
    p.add_argument("--try", dest="just_try", action="store_true",
                   help="A test: nothing is stored (a temporary folder, deleted at the end)")
    p.add_argument("--keep", action="store_true", help="With --try: keep the temporary folder")
    p.add_argument("--allow-cloud", action="store_true",
                   help="Allow a condition with a cloud model (the transcribed text goes to it)")
    p.add_argument("--force", action="store_true",
                   help="Run even if the Ollama server holds models of other work")
    p.add_argument("--barge-in", choices=("on", "off"), default=None,
                   help="Barge-in by lip activity (camera); default: barge_in of the sessions file")
    p = sub.add_parser("check", help="A check before a session (nothing is stored)")
    p.add_argument("what", choices=CHECKS,
                   help="speak: the voice says a phrase; listen: one transcription; llm: the model "
                        "of every condition answers; system: the stack starts in every condition; "
                        "perception: the lip activity for barge-in (only with barge-in on); "
                        "rehearsal: a whole conversation with a person, not stored; "
                        "presession: speak, listen and llm in sequence, with the configuration "
                        "(with --campaign, its pass is kept)")
    p.add_argument("--phrase", default="",
                   help="speak: the phrase to say (default: check_phrase of the sessions file)")
    p.add_argument("--scenario", default="sf_coffee", help="rehearsal: the scenario (default sf_coffee)")
    p.add_argument("--condition", default="bounded", help="rehearsal: the condition (default bounded)")
    p.add_argument("--allow-cloud", action="store_true",
                   help="llm, system, rehearsal: also the conditions with a cloud model")
    p.add_argument("--force", action="store_true", help="rehearsal: as trial --force")
    p.add_argument("--keep", action="store_true", help="Keep the logs of the check")
    p.add_argument("--config", default="",
                   help="A configuration (real/configs/<name>.yaml, drafts included) for this "
                        "check; with --campaign, the campaign's is used")
    p.add_argument("--voice", default="",
                   help="speak: a Piper voice of the catalog, NAME or NAME:SPEAKER (e.g. "
                        "es_ES-davefx-medium, es_ES-sharvard-medium:1); only the voice is started. "
                        "Default: the configuration's, else the voice of the sessions file's "
                        "language")
    piper_arguments(p, "speak: ")
    p.add_argument("--barge-in", choices=("on", "off"), default=None,
                   help="system, perception, rehearsal: barge-in by lip activity (camera); "
                        "default: barge_in of the sessions file")
    p = sub.add_parser("review", help="Review the stored trials of a participant before the "
                                      "judge (replace real personal data said by mistake)")
    p.add_argument("participant")
    p.add_argument("--by", required=True, help="Who reviews (recorded in the record)")
    p.add_argument("--k", type=int, default=0, help="Only this trial")
    p.add_argument("--again", action="store_true", help="Review trials already reviewed")
    p = sub.add_parser("try-scenario",
                       help="Any scenario, with a person as the user (a test: nothing is stored)")
    p.add_argument("scenario", help="The scenario id, e.g. pr_tea")
    p.add_argument("--scenarios", default=os.path.join(EVAL, "experiments", "scenarios", "e4_es.yaml"),
                   help="The scenario file (default experiments/scenarios/e4_es.yaml)")
    p.add_argument("--control", choices=("bounded", "llm_led"), default="bounded")
    p.add_argument("--model", default="",
                   help="The system model (default: that of the sessions file's condition with "
                        "this control)")
    p.add_argument("--allow-cloud", action="store_true",
                   help="Allow a cloud model (the transcribed text goes to it)")
    p.add_argument("--force", action="store_true",
                   help="Run even if the Ollama server holds models of other work")
    p.add_argument("--keep", action="store_true", help="Keep the temporary folder")
    p.add_argument("--barge-in", choices=("on", "off"), default=None)
    p.add_argument("--config", default="",
                   help="A configuration (real/configs/<name>.yaml, drafts included) for this "
                        "test; the options below go over it")
    p.add_argument("--voice", default="",
                   help="A Piper voice of the catalog for this test, NAME or NAME:SPEAKER (e.g. "
                        "es_ES-davefx-medium, es_ES-sharvard-medium:1). Default: the "
                        "configuration's, else the voice of the language")
    piper_arguments(p, "")
    p.add_argument("--filler-after", type=float, default=None,
                   help="filler_after_s of the request for this test: the seconds the robot may "
                        "take to answer before a conversational filler (0: none). Default: the "
                        "scenario's, else the system's")
    args = parser.parse_args()

    sessions, args.campaign_info, args.out_root = configured(args)
    if args.command == "review":
        sys.exit(review(args))
    if args.command == "try-scenario":
        sys.exit(try_scenario(args, sessions))
    if args.command == "balance":
        print(balance(sessions, args.count))
    elif args.command == "plan":
        try:
            plan, what = write_plan(sessions, args.participant, args.out, args.replace)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"{what}.")
        show_plan(sessions, plan, plan["participant"], args.out)
    elif args.command == "card":
        pid, _ = participant(args.participant)
        plan = read_plan(pid, args.out)
        if plan is None or not 1 <= args.k <= len(plan["trials"]):
            sys.exit(f"{pid} has no plan or no trial {args.k}: real_sessions.py plan {pid}")
        print(card_text(current_trial(sessions, plan, args.k), sessions))
    elif args.command == "trial":
        planned_trial(args, sessions)
    else:
        sys.exit(run_check(args, sessions))


if __name__ == "__main__":
    main()
