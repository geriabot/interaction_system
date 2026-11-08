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
    end (the role play: its evaluation, once the user said a line of the scene), or "" for the
    ending user_silent. When it closes, the outcome is the strategy's own (E1 development,
    2026-11-07: a user who leaves the practice in silence after the scene still gets, and the
    record still keeps, its evaluation, as after a cancellation)."""

    init: Callable[[SkillEnv, dict], None]
    phase: Callable[[dict], str]
    data: Callable[[dict], dict]
    succeeded: Callable[[dict], bool]
    strict_cancel: Callable[[dict], bool] = lambda state: False
    outcome: Callable[[dict], tuple[str, str]] | None = None
    silence_grace: Callable[[dict], float] = lambda state: 0.0
    on_cancel: Callable[[SkillEnv, dict], str] | None = None
    on_finished: Callable[[SkillEnv, dict], str] | None = None
    on_silent_end: Callable[[SkillEnv, dict], str] | None = None


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
    slot_filling, explanation, quiz, procedure, roleplay, grounded_qa, questionnaire, common,
)
