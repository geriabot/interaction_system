# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Model failures injected into the interaction system, as the scenario plans them (E3).

The scenario lists its failures under `faults` (a sibling of `events`): what the system's model
does wrong while the system handles a given user turn, whatever the user said in it. Each one:

    {turn: 2, kind: error, questions: [detect_cancel_intent], turns: 1}

  turn       the user turn (1-based, counted as the events count them: one per robot utterance
             the user answers) whose handling fails; 0 = the opening, before the user speaks
  kind       error    the call to the model fails (the gateway raises or returns an error)
             invalid  the model answers, but not what was asked: prose instead of the JSON of an
                      understanding question; an empty text for a generated line (too short to
                      be said); `answer` replaces it (e.g. "{}", JSON without the fields)
             hang     the model does not answer: the call blocks until the client stops
                      waiting for it (its time limit for the call: llm_timeout, or what is left
                      of the reply's time budget) and returns nothing, as a timed-out call does;
                      hang_s caps the block (default: none); it ends earlier when the harness
                      moves to another turn
             crash    the question raises outside the client's own handling, so the skill
                      fails (the "a skill fails" branch of the `error` outcome)
             answer   not a failure: the model's answer is replaced by `answer`, the raw text the
                      client receives (a list: one per call of the question in that turn, in
                      order, the last one repeated): the oracle replay of the debugging
                      (debug/replay.py pins the answers a dialogue got, and replaces a wrong one
                      by the correct one, to see what the tree then does)
             freeze   the question blocks for hang_s seconds (default 60) outside the client's
                      own time limits, then raises: the intelligence does not answer (a fault of
                      the system, not of the model; the "no answer within intelligence_timeout"
                      branch of the `error` outcome); it ends earlier when the harness moves on
  questions  the questions that fail (methods of LLMDialogClient: detect_cancel_intent,
             classify_cancel_reply, evaluate_quiz_answer, verify_answer...); empty = every one
  turns      consecutive user turns from `turn` (default 1); 0 = until the end of the dialogue

error, invalid and hang replace the call to the model (the model is not asked), so the client's
own parsing, retries, time limits and fallbacks run as they would; crash and freeze replace the
whole question.

How a plan reaches the system. The harness side (FaultPlan) writes the faults active for the
current user turn to a small JSON file (the switch) before the user's line is delivered, and
clears it when the dialogue ends; the intelligence side reads it at every call of the model and
appends each failed call to <switch>.log (JSON lines), which FaultPlan.finish() returns for the
record of the dialogue. The path of the switch is the environment variable INTERACTION_SIM_FAULTS
of the interaction system's process.

The intelligence side is loaded through an extension point that already exists, so the method's
code is not modified:
  - with the real model (the experiments): skill_modules: ["interaction_sim.fault_injection"],
    whose import calls install(): the questions of LLMDialogClient and its call to the gateway
    are wrapped (the client's own code, prompts, parsing and fallbacks are unchanged);
  - with the scripted LLM (the system tests): llm_client_factory:
    "interaction_sim.llm_faults:make_scripted", the scripted LLM behind a proxy that, for a
    failed question, runs the real client's method with the failing call, so that the fallback
    under test is the method's own and not a copy of it.
"""

import functools
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

ENV = "INTERACTION_SIM_FAULTS"
KINDS = ("error", "invalid", "hang", "crash", "freeze", "answer")
# A hung model never answers: the call blocks until the client stops waiting (hang_s unset)
DEFAULT_HANG_S = None
# A frozen question: longer than the default intelligence_timeout (30 s)
DEFAULT_FREEZE_S = 60.0
# What a model says when it ignores the format asked: prose instead of JSON
INVALID_UNDERSTANDING = "Sorry, I cannot help with that."
# A generated line that is too short to be said (the client asks again, then uses its phrase)
INVALID_GENERATION = ""
# How often a hung call checks whether the harness moved on
_POLL_S = 0.1


class InjectedFault(RuntimeError):
    """A failure injected by the simulation (never raised by the method itself)."""


@dataclass
class LlmFault:
    """A failure of the system's model while it handles a user turn, planned by the scenario."""

    turn: int
    kind: str
    questions: list = field(default_factory=list)
    turns: int = 1
    hang_s: float | None = DEFAULT_HANG_S
    answer: str | list | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(
                f"Unknown model failure kind '{self.kind}': one of {', '.join(KINDS)}")
        if int(self.turn) < 0 or int(self.turns) < 0:
            raise ValueError("A model failure needs turn >= 0 (0: the opening) and turns >= 0 "
                             "(0: until the end)")
        if self.hang_s is not None and float(self.hang_s) <= 0:
            raise ValueError("hang_s must be positive")
        if self.kind == "answer" and not (
                isinstance(self.answer, str) or (isinstance(self.answer, list) and self.answer
                                                 and all(isinstance(a, str)
                                                         for a in self.answer))):
            raise ValueError("A fault of kind answer needs `answer`: a text, or a list of texts "
                             "(one per call)")
        if isinstance(self.questions, str):
            self.questions = [self.questions]
        if not all(isinstance(q, str) and q for q in self.questions):
            raise ValueError("questions: a list of question names (methods of LLMDialogClient)")
        self.turn, self.turns = int(self.turn), int(self.turns)
        if self.hang_s is not None:
            self.hang_s = float(self.hang_s)
        self.questions = list(self.questions)

    def covers(self, turn: int) -> bool:
        if turn < self.turn:
            return False
        return self.turns == 0 or turn < self.turn + self.turns


def known_questions() -> set[str] | None:
    """The questions a failure can name (None when the interaction intelligence is not
    importable: the names are then not checked)."""
    try:
        from interaction_intelligence.llm_client import LLMDialogClient
    except ImportError:
        return None
    return LLMDialogClient.questions() | set(LLMDialogClient.ROUTE_ALIASES)


def validate_faults(faults) -> list[LlmFault]:
    """The faults of a scenario as LlmFault (dicts or LlmFault); ValueError if one is malformed
    or names a question the client does not ask."""
    out = [f if isinstance(f, LlmFault) else LlmFault(**f) for f in (faults or [])]
    known = known_questions()
    if known is not None:
        unknown = sorted({q for f in out for q in f.questions if q not in known})
        if unknown:
            raise ValueError(f"Unknown questions in the model failures: {unknown}. "
                             f"Known: {', '.join(sorted(known))}")
    return out


def _write_json(path: str, data: dict) -> None:
    """Atomically: the intelligence never reads half a file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ==== THE HARNESS SIDE ====


class FaultPlan:
    """The model failures of one scenario, applied turn by turn from the harness side.

        plan = FaultPlan(scenario["faults"], switch_path)
        plan.start()                          # before the goal is sent (turn 0: the opening)
        run = harness.run(goal, plan.user(user))
        applied = plan.finish()               # the failed calls of this dialogue
    """

    def __init__(self, faults, path: str):
        self.faults = validate_faults(faults)
        self.path = path
        self.log_path = path + ".log"
        self.seq = ""
        self._calls = 0

    def start(self) -> None:
        self.seq = uuid.uuid4().hex[:12]
        self._calls = 0
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.arm(0)

    def arm(self, turn: int) -> None:
        """The faults active while the system handles this user turn."""
        active = [{"i": i, **asdict(f)} for i, f in enumerate(self.faults) if f.covers(turn)]
        _write_json(self.path, {"seq": self.seq, "turn": turn, "faults": active})

    def user(self, policy):
        """The user policy with the faults armed: the k-th call (the user's turn k, as the
        events count it) arms the faults of turn k before the user's line is delivered."""

        def wrapped(robot_utterance: str, transcript: list[dict]):
            self._calls += 1
            self.arm(self._calls)
            return policy(robot_utterance, transcript)

        return wrapped

    def finish(self) -> list[dict]:
        """Disarm, and return the failed calls of this dialogue: {fault, turn, question, kind,
        t} (fault: the index of the planned failure)."""
        _write_json(self.path, {"seq": "", "turn": 0, "faults": []})
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
        return out


# ==== THE INTELLIGENCE SIDE ====


class FaultSwitch:
    """The switch written by FaultPlan, read at every call of the model."""

    def __init__(self, path: str):
        self.path = path
        self.log_path = path + ".log"
        self._lock = threading.Lock()
        self._answered: dict = {}  # (seq, turn, fault) -> calls answered by a kind answer

    def next_answer(self, state: dict, fault: dict) -> str:
        """The answer of a fault of kind answer for its next call in this turn."""
        answers = fault.get("answer")
        if isinstance(answers, str):
            return answers
        key = (state.get("seq"), state.get("turn"), fault.get("i"))
        with self._lock:
            n = self._answered.get(key, 0)
            self._answered[key] = n + 1
        return answers[min(n, len(answers) - 1)]

    def read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def active(self, questions: list[str], kinds) -> tuple[dict, dict] | None:
        """The first active fault of one of these kinds for any of these questions (the
        questions in progress, innermost last), with the state of the switch; None if none."""
        state = self.read()
        for fault in state.get("faults") or []:
            if fault.get("kind") not in kinds:
                continue
            wanted = fault.get("questions") or []
            if not wanted or any(q in wanted for q in questions):
                return fault, state
        return None

    def armed(self, state: dict) -> bool:
        """Whether the harness is still at the turn of this state (a hung call stops waiting
        when the dialogue moved on or ended)."""
        now = self.read()
        return now.get("seq") == state.get("seq") and now.get("turn") == state.get("turn")

    def record(self, state: dict, fault: dict, question: str) -> None:
        entry = {"seq": state.get("seq", ""), "fault": fault.get("i"), "turn": state.get("turn"),
                 "question": question, "kind": fault.get("kind"), "t": time.time()}
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass


_local = threading.local()


def _in_progress() -> list[str]:
    """The questions in progress in this thread, innermost last."""
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


def _block(seconds: float | None, switch: FaultSwitch, state: dict) -> None:
    """Block for `seconds` (None: until the harness moves on), or until the harness moves to
    another turn or ends the dialogue."""
    end = None if seconds is None else time.monotonic() + seconds
    while (end is None or time.monotonic() < end) and switch.armed(state):
        time.sleep(_POLL_S)


def fail(fault: dict, state: dict, switch: FaultSwitch, question: str, schema,
         wait: float | None = None) -> str | None:
    """One failed call of the model (error, invalid or hang), recorded. wait: how long the
    client waits for this call (CallLimits.wait; None: no limit), which ends a hang."""
    switch.record(state, fault, question)
    kind = fault["kind"]
    if kind == "answer":
        return switch.next_answer(state, fault)
    if kind == "error":
        raise InjectedFault(f"Injected model failure (error) in {question}")
    if kind == "invalid":
        if fault.get("answer") is not None:
            return fault["answer"]
        return INVALID_UNDERSTANDING if schema else INVALID_GENERATION
    if kind == "hang":
        limits = [t for t in (fault.get("hang_s"), wait) if t is not None]
        _block(min(limits) if limits else None, switch, state)
        return None
    raise InjectedFault(f"Injected model failure ({kind}) in {question}")


def freeze(fault: dict, state: dict, switch: FaultSwitch, question: str) -> None:
    """A frozen question: blocks for hang_s (default DEFAULT_FREEZE_S), whatever the client's
    limits, then raises (recorded)."""
    switch.record(state, fault, question)
    _block(float(fault.get("hang_s") or DEFAULT_FREEZE_S), switch, state)
    raise InjectedFault(f"Injected failure (freeze) in {question}")


# The client's own functions between a question and its call to the model
_TRANSPORT = {"call", "_infer_limited", "_call_nlu", "_call_nlg"}


def _caller_question() -> str:
    """The function that asked the model: the first caller outside the client's transport."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_name in _TRANSPORT:
        frame = frame.f_back
    return frame.f_code.co_name if frame is not None else ""


def faulty_infer(infer, switch: FaultSwitch, limits=lambda: None):
    """The client's call to the model (sid, prompt, max_tokens, schema) -> text | None, failing
    when the switch says so. The question is the one in progress (the wrapped questions), else
    the function that called _call_nlu/_call_nlg (e.g. the LLM-led baseline's). limits() gives
    the client's CallLimits, whose `wait` (the time the client waits for this call) ends a
    hang."""

    def call(session_id, prompt, max_tokens, schema):
        names = list(_in_progress()) or [_caller_question()]
        hit = switch.active(names, ("error", "invalid", "hang", "answer"))
        if hit is None:
            return infer(session_id, prompt, max_tokens, schema)
        fault, state = hit
        current = limits()
        return fail(fault, state, switch, names[-1], schema,
                    current.wait if current is not None else None)

    return call


def _question(name: str, method):
    """A question of the client: it records itself as in progress, and a crash or a freeze
    fails it."""

    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        stack = _in_progress()
        stack.append(name)
        try:
            switch = getattr(self, "_fault_switch", None)
            if switch is not None:
                hit = switch.active(stack, ("crash", "freeze"))
                if hit is not None:
                    fault, state = hit
                    if fault["kind"] == "freeze":
                        freeze(fault, state, switch, name)
                    switch.record(state, fault, name)
                    raise InjectedFault(f"Injected model failure (crash) in {name}")
            return method(self, *args, **kwargs)
        finally:
            stack.pop()

    return wrapped


_installed: dict = {}


def install(path: str | None = None) -> bool:
    """Wrap LLMDialogClient (its questions and its call to the gateway) with the switch at
    `path` (default: $INTERACTION_SIM_FAULTS). Called when the intelligence imports
    interaction_sim.fault_injection. False (and nothing changed) without a path."""
    path = path or os.environ.get(ENV, "")
    if not path:
        print(f"[interaction_sim.llm_faults] {ENV} is not set: no model failure can be "
              f"injected", file=sys.stderr)
        return False
    from interaction_intelligence.llm_client import LLMDialogClient

    switch = FaultSwitch(path)
    if _installed:
        _installed["switch"] = switch
        return True
    names = LLMDialogClient.questions() | set(LLMDialogClient.ROUTE_ALIASES)
    originals = {"__init__": LLMDialogClient.__init__}
    for name in sorted(names):
        if name not in LLMDialogClient.__dict__:
            continue
        originals[name] = LLMDialogClient.__dict__[name]
        setattr(LLMDialogClient, name, _question(name, originals[name]))
    init = originals["__init__"]

    @functools.wraps(init)
    def __init__(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self._fault_switch = _installed["switch"]
        self._infer = faulty_infer(self._infer, self._fault_switch,
                                   lambda: getattr(self, "limits", None))

    LLMDialogClient.__init__ = __init__
    _installed.update({"switch": switch, "originals": originals, "cls": LLMDialogClient})
    return True


def uninstall() -> None:
    """Undo install() (for the tests)."""
    if not _installed:
        return
    cls = _installed["cls"]
    for name, original in _installed["originals"].items():
        setattr(cls, name, original)
    _installed.clear()


# ==== THE SCRIPTED LLM (system tests) ====


_templates: dict = {}


def prompt_templates() -> dict:
    """The prompt templates of the installed interaction intelligence (prompts.yaml)."""
    if not _templates:
        import yaml
        from ament_index_python.packages import get_package_share_directory

        path = os.path.join(get_package_share_directory("interaction_intelligence"), "config",
                            "prompts.yaml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _templates.update(data["interaction_intelligence"]["ros__parameters"]["prompts"])
    return _templates


class FaultyClient:
    """A client (the scripted LLM) whose questions fail as the switch says: a crash raises, a
    freeze blocks and raises; an error, invalid answer or hang runs the real LLMDialogClient's
    method with the failing call, so that what the skill gets back is the method's own
    fallback. With the limits of the request (`limits`, set by the intelligence: CallLimits),
    the real client bounds the failing calls as the method does (a hang ends when the client
    stops waiting), and once the model is taken as unresponsive or the budget is spent, the
    other questions of the request get the real client's fallback too, without a call."""

    def __init__(self, inner, switch: FaultSwitch | None, language: str = "English"):
        self._inner = inner
        self._switch = switch
        self.language = language  # set by the intelligence: the language of the dialogue
        self._questions = known_questions() or set()
        self._questions.add("_call_nlu")  # the LLM-led baseline asks it directly
        self.limits = None
        # As LLMDialogClient.model_status: whether the last question that would call the model
        # got a usable answer (False after a failed one), read by the intelligence (model_down)
        self.model_status = None

    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        # The scripted LLM reads the exact stop commands of the language too
        self._language = value
        self._inner.language = value

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if self._switch is None or name not in self._questions or not callable(attr):
            return attr

        def call(*args, **kwargs):
            stack = _in_progress()
            stack.append(name)
            try:
                hit = self._switch.active(stack, KINDS)
                if hit is None:
                    if self.limits is not None and self.limits.refusal():
                        # The method would not call the model: its fallback, without a call
                        return self._real(self._real_client(None, None, name), name, args,
                                          kwargs)
                    if not self._local(name, args):
                        self.model_status = True
                    return attr(*args, **kwargs)
                fault, state = hit
                if fault["kind"] == "freeze":
                    freeze(fault, state, self._switch, name)
                if fault["kind"] in ("crash", "freeze"):
                    self._switch.record(state, fault, name)
                    self.model_status = False
                    raise InjectedFault(f"Injected model failure (crash) in {name}")
                return self._real(self._real_client(fault, state, name), name, args, kwargs)
            finally:
                stack.pop()

        return call

    def _real(self, client, name: str, args, kwargs):
        """The real client's method (the method's own fallback), with its model status."""
        try:
            return getattr(client, name)(*args, **kwargs)
        finally:
            if client.model_status is not None:
                self.model_status = client.model_status

    def _local(self, name: str, args) -> bool:
        """Whether the real client would answer this question without the model: an exact
        stop command is decided locally (detect_cancel_intent)."""
        if name != "detect_cancel_intent" or not args:
            return False
        from interaction_intelligence.utils import local_cancel_check
        return bool(local_cancel_check(args[0], self._language))

    def _real_client(self, fault: dict | None, state: dict | None, question: str):
        from interaction_intelligence.llm_client import CallLimits, LLMDialogClient
        from interaction_intelligence.prompt_builder import PromptBuilder

        limits = self.limits if self.limits is not None else CallLimits()

        def infer(session_id, prompt, max_tokens, schema):
            if fault is None:
                raise InjectedFault(f"{question}: no call was expected")
            return fail(fault, state, self._switch, question, schema, limits.wait)

        return LLMDialogClient("nlu", "nlg", infer,
                               PromptBuilder(prompt_templates(), language=self._language),
                               language=self._language, limits=limits)


def make_scripted():
    """llm_client_factory for the system tests: the scripted LLM with the injected failures of
    the switch at $INTERACTION_SIM_FAULTS."""
    from .scripted_llm import ScriptedLLM

    path = os.environ.get(ENV, "")
    return FaultyClient(ScriptedLLM(), FaultSwitch(path) if path else None)


def make_scripted_localized():
    """As make_scripted, with the scripted LLM that says the fixed phrases of the dialogue's
    language file (scripted_llm.LocalizedScriptedLLM), for the tests in English and Spanish."""
    from .scripted_llm import LocalizedScriptedLLM

    path = os.environ.get(ENV, "")
    return FaultyClient(LocalizedScriptedLLM(), FaultSwitch(path) if path else None)
