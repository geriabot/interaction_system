# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The first phase of every request: deciding the dialogue behavior, before any conversation.

The orchestrator sends its catalog: each behavior with its explanation and what it needs from
the request. The decision has two steps:

  1. Only when the request does not name the behavior (dialog_mode empty): the LLM reads the
     task and chooses, over the whole catalog, the behavior the task needs, or none when no
     behavior of the catalog fits it. The LLM does not see the resources: it decides what the
     task needs, not what can run with what was attached, so that a request without the
     material of its behavior is refused instead of being served by another behavior that
     happens to run with it.
  2. Always, also when the request names the behavior: a deterministic check, without the LLM,
     that the request carries the material of that behavior (a quiz needs a question bank, a
     procedure its steps, a roleplay its scene, grounded question answering a plain-text
     material, a questionnaire its instrument, an explanation a reference text or its key
     points; only slot filling needs none).

When no behavior fits, or the request lacks the material of the behavior it needs, the request
is not feasible: it ends before any conversation, with the reason.
"""

from dataclasses import dataclass
import logging

from .dialog_context import DialogContext
from .skills.explanation import parse_key_points
from .skills.grounded_qa import parse_material
from .skills.procedure import parse_steps
from .skills.questionnaire import parse_instrument
from .skills.quiz import parse_questions
from .skills.roleplay import parse_scene

_log = logging.getLogger("InteractionIntelligence")

# The answer of the LLM when no behavior of the catalog fits the task
NONE = -1

# What a request can provide, and how to tell from its resources
NEEDS = {
    "question_bank": lambda resources: bool(parse_questions(resources)),
    "steps": lambda resources: bool(parse_steps(resources)),
    "scene": lambda resources: parse_scene(resources)[0] is not None,
    "material": lambda resources: bool(parse_material(resources)[0]),
    "instrument": lambda resources: parse_instrument(resources) is not None,
    # An explanation explains given content: its key points, or a reference text the key
    # points are listed from. Without either, the model would explain what it believes, which
    # nobody has checked
    "reference": lambda resources: bool(parse_key_points(resources))
    or bool(parse_material(resources)[0]),
}

# How the reason of a request that is not feasible names what is missing
MISSING = {
    "question_bank": "no question bank was provided",
    "steps": "no steps were provided",
    "scene": "no scene was provided",
    "material": "no material was provided",
    "instrument": "no instrument was provided",
    "reference": "no reference text or key points were provided",
}

# How that reason names the behavior the task needs (its mode)
BEHAVIOR_NOUNS = {
    "slot_filling": "slot filling",
    "explanation": "an explanation",
    "quiz": "a quiz",
    "procedure": "a procedure",
    "roleplay": "a role play",
    "grounded_qa": "grounded question answering",
    "questionnaire": "a questionnaire",
}

NO_BEHAVIOR_FITS = "No dialogue behavior fits the task"


@dataclass
class Decision:
    """The first phase of a request: the behavior the task needs (behavior_id, NONE when no
    behavior fits) and whether the request carries its material (feasible). reason: why the
    request is not feasible ("" when it is)."""

    behavior_id: int
    feasible: bool
    reason: str = ""


def provided_needs(resources: list[dict]) -> set[str]:
    return {need for need, test in NEEDS.items() if test(resources)}


def missing_needs(behavior: dict, resources: list[dict]) -> list[str]:
    """The needs of the behavior that the request does not provide, in the catalog's order.
    Unknown needs are never provided."""
    provided = provided_needs(resources)
    return [n for n in behavior.get("needs", []) if n and n not in provided]


def not_feasible_reason(behavior: dict, missing: list[str]) -> str:
    noun = BEHAVIOR_NOUNS.get(behavior.get("mode", ""), f"the behavior {behavior.get('name')}")
    lacks = " and ".join(MISSING.get(n, f"no {n} was provided") for n in missing)
    return f"The task needs {noun}, but {lacks}"


def check_material(behavior: dict, resources: list[dict]) -> Decision:
    """Step 2: whether the request carries the material of the behavior."""
    missing = missing_needs(behavior, resources)
    if missing:
        return Decision(behavior["behavior_id"], False, not_feasible_reason(behavior, missing))
    return Decision(behavior["behavior_id"], True)


def choice_context(domain: str) -> DialogContext:
    """The context of the question of step 1: the domain of the request, without its
    resources (the LLM decides what the task needs, not what was attached)."""
    return DialogContext(domain=domain)


def decide_behavior(
    llm, task: str, behaviors: list[dict], resources: list[dict], ctx: DialogContext | None,
    mode: str = "",
) -> Decision:
    """The decision of the first phase. Each behavior: {behavior_id, name, mode, explanation,
    needs}. mode: the dialog_mode of the request ("" to choose it from the task). Raises when
    the request names a mode the catalog does not have, or the LLM gives no valid answer (a
    failure of the system, not of the request)."""
    mode = (mode or "").strip()
    if mode:
        behavior = next((b for b in behaviors if b.get("mode") == mode), None)
        if behavior is None:
            raise ValueError(f"Unknown dialog mode '{mode}'")
    else:
        chosen = llm.choose_dialogue_behavior(task, behaviors, ctx)
        if chosen == NONE:
            return Decision(NONE, False, NO_BEHAVIOR_FITS)
        behavior = next((b for b in behaviors if b["behavior_id"] == chosen), None)
        if behavior is None:
            raise RuntimeError(f"the model gave no valid behavior ({chosen})")
    decision = check_material(behavior, resources)
    if not decision.feasible:
        _log.info(f"Request not feasible: {decision.reason}")
    return decision
