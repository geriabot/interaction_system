# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The skills of the interaction intelligence: what the Behavior Trees ask it.

A skill is one narrow question to the LLM (understand an utterance, phrase a reply) or one
deterministic operation on the data of the dialogue (apply the extracted slots, record a quiz
answer). The trees own the control: the phases, the turn loop, the counters and the limits.
The intelligence keeps no state between calls: the state of the dialogue travels in the
blackboard of the tree (a JSON object), and every skill receives it and returns it updated.

Every skill has the signature

    skill(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult

and mutates `state` in place. A skill that cannot run raises ValueError with the reason, which
the tree reports as the failure of the dialogue.

The strategy modules register their skills as "<mode>.<name>" (e.g. "slot_filling.update") and
describe their dialogue with a DialogueMode (phase, data, success, cancel strictness).
"""

from dataclasses import dataclass, field
from typing import Callable

from ..dialog_context import DialogContext


@dataclass
class SkillEnv:
    """What a skill uses besides the state: the LLM client and the context of the dialogue."""

    llm: object  # LLMDialogClient (or a test double with the same methods)
    ctx: DialogContext
    ack_phrases: tuple[str, ...] = ()
    ack_enabled: bool = True  # the system's default; a request may set its own
    filler_phrases: tuple[str, ...] = ()
    timeout_prompt: str = ""
    history_max_turns: int = 200


@dataclass
class SkillInput:
    user_text: str = ""
    option: str = ""
    number: int = 0
    args: dict = field(default_factory=dict)


@dataclass
class SkillResult:
    reply: str = ""
    label: str = ""


@dataclass
class DialogueMode:
    """How the state of one strategy is reported: its phase, its data (the result of the
    dialogue), its outcome, whether a cancel request must be explicit, and how long the user
    may stay silent on the current element without being prompted.

    outcome(state) -> (outcome, reason) when the dialogue ended by the strategy itself:
    "completed", "partial" (some element left unfinished; the reason says which) or "failed".
    Without it, the outcome is "completed" if succeeded(state), else "failed".

    on_cancel(env, state) -> the closing line when the user's cancellation is confirmed and
    the strategy has something to give for what was done (the role play: its evaluation), or
    "" for the generic cancel closing. With its own closing the stop is a normal end and the
    outcome is the strategy's own (decided by the author, 2026-11-08); with the generic one it
    is "cancelled".

    on_finished(env, state) -> the strategy's own closing line when the user, asked whether
    they want to stop, says instead that they have finished (nothing more to ask or add), for
    the strategies whose end the user decides (grounded question answering); it records the
    normal end (the outcome is the strategy's own, e.g. completed). None: the turn goes to the
    strategy like any other (decided by the author, 2026-10-31: finishing is not cancelling).

    on_silent_end(env, state) -> the strategy's own closing line when the user's silence would
    end the dialogue (max_silent_waits) and the strategy can close what was done as a normal
    end (the role play: its evaluation, once the user said a line of the scene; grounded
    question answering: its closing, once a question within the task got its reply), or "" for
    the ending user_silent. When it closes, the outcome is the strategy's own (E1 development,
    2026-11-07: a user who leaves the practice in silence after the scene still gets, and the
    record still keeps, its evaluation, as after a cancellation).

    cancel_stage(state) -> where the strategy is, for the cancel detector, in one sentence (slot
    filling: the details are being collected or read back, and nothing has been booked, ordered
    or sent yet), so that dropping what the conversation is preparing ("cancel the booking") is
    read as a request to stop; "" when it adds nothing."""

    init: Callable[[SkillEnv, dict], None]
    phase: Callable[[dict], str]
    data: Callable[[dict], dict]
    succeeded: Callable[[dict], bool]
    strict_cancel: Callable[[dict], bool] = lambda state: False
    cancel_stage: Callable[[dict], str] = lambda state: ""
    outcome: Callable[[dict], tuple[str, str]] | None = None
    silence_grace: Callable[[dict], float] = lambda state: 0.0
    on_cancel: Callable[[SkillEnv, dict], str] | None = None
    on_finished: Callable[[SkillEnv, dict], str] | None = None
    on_silent_end: Callable[[SkillEnv, dict], str] | None = None
    # on_turn_limit(env, state) -> what the strategy says for what was done when the dialogue
    # reaches its turn limit (max_turns) before its own end, after the user's last turn was
    # handled (the quiz: the score of the questions answered; the procedure: how the steps
    # went; the role play: its evaluation); said before the fixed closing line. "" or None:
    # the fixed closing line alone. The ending stays turn_limit
    on_turn_limit: Callable[[SkillEnv, dict], str] | None = None
    # pending(env, state) -> the element the user is expected to answer now, as the robot asks
    # it (the slot's question, the quiz question, the questionnaire item with its options, the
    # step, the teach-back request, the character's last line, "anything else?"): what the
    # robot repeats after a silence and resumes with after a denied request to stop, instead
    # of its whole last utterance (acknowledgments, feedback, introductions). "": the last
    # utterance
    pending: Callable[[SkillEnv, dict], str] = lambda env, state: ""
    # With on_finished: whether a request to stop is the strategy's normal end now, without a
    # confirmation (grounded question answering: once a question within the task got its
    # reply); otherwise it is asked about, and a confirmed stop is a cancellation
    stop_is_finishing: Callable[[dict], bool] = lambda state: True
    # The answers the robot expects now (the options of the slot or item, the quiz answer): a
    # reply whose every word is in one of them ("Cancel." to "open an account or cancel a
    # card?") is that answer, never a request to stop; the cancel detector is not asked
    expected_answers: Callable[[dict], list[str]] = lambda state: []
    # Whether the question pending now is answered yes or no (slot filling's read-back of the
    # data): a reply that opens with a yes ("Yes, that's correct.") is then that answer, never a
    # request to stop, and the cancel detector is not asked
    yes_is_an_answer: Callable[[dict], bool] = lambda state: False
    # Whether the user saying they have finished ("done", "I'm done", "that's all") is a normal
    # reply to the element the strategy is on, for its own turn to handle: in a procedure it
    # reports or skips the step, in an explanation it answers the check, in a role play it belongs
    # to the scene (whose classifier ends it with its evaluation), in slot filling it goes to the
    # reading of the frame. False (quiz, questionnaire: "I'm done, that's enough" is no answer to
    # a question or an item): without on_finished, the detector's "finished" is a request to
    # stop and is asked about. Either way, "I'm done" said in answer to that question agrees
    finishing_is_an_answer: bool = False
    # Whether a reply that is exactly one of the language's step_done_words ("Done.", "Ready.",
    # "Hecho.") answers the element and is never read as a request to stop (the procedure: the
    # detector, given the robot's last line "Let me know when you're done", read a bare "done"
    # as one in about one turn in ten of a scenario)
    done_words_are_answers: bool = False


Skill = Callable[[SkillEnv, dict, SkillInput], SkillResult]

_SKILLS: dict[str, Skill] = {}
_MODES: dict[str, DialogueMode] = {}


def skill(name: str) -> Callable[[Skill], Skill]:
    """Register a skill under `name`. Raises at import time on duplicates."""

    def decorator(fn: Skill) -> Skill:
        if name in _SKILLS:
            raise ValueError(f"Skill '{name}' already registered")
        _SKILLS[name] = fn
        return fn

    return decorator


def register_mode(name: str, mode: DialogueMode) -> None:
    if name in _MODES:
        raise ValueError(f"Dialogue mode '{name}' already registered")
    _MODES[name] = mode


def get_skill(name: str) -> Skill:
    if name not in _SKILLS:
        raise ValueError(f"Unknown skill '{name}'. Available: {sorted(_SKILLS)}")
    return _SKILLS[name]


def get_mode(name: str) -> DialogueMode:
    if name not in _MODES:
        raise ValueError(f"Unknown dialog mode '{name}'. Available: {available_modes()}")
    return _MODES[name]


def available_modes() -> list[str]:
    return sorted(_MODES)


def available_skills() -> list[str]:
    return sorted(_SKILLS)


# Register the skills on package import (the strategies first: common uses their modes)
from . import (  # noqa: E402,F401
    slot_filling, explanation, quiz, procedure, roleplay, grounded_qa, questionnaire, topic_chat,
    common,
)
