# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The choice of the dialogue behavior for a request without dialog_mode.

The orchestrator sends its catalog: each behavior with its explanation and what it needs from
the request. A behavior is offered only if the request provides what it needs (a quiz needs
a question bank, a procedure its steps, a roleplay its scene, grounded question answering a
plain-text material, a questionnaire its instrument); this is decided here, by
parsing the resources, without the LLM. If one behavior is offered it is chosen; if several
are, the LLM chooses among them from their explanations.
"""

import logging

from .dialog_context import DialogContext
from .skills.grounded_qa import parse_material
from .skills.procedure import parse_steps
from .skills.questionnaire import parse_instrument
from .skills.quiz import parse_questions
from .skills.roleplay import parse_scene

_log = logging.getLogger("InteractionIntelligence")

# What a request can provide, and how to tell from its resources
NEEDS = {
    "question_bank": lambda resources: bool(parse_questions(resources)),
    "steps": lambda resources: bool(parse_steps(resources)),
    "scene": lambda resources: parse_scene(resources)[0] is not None,
    "material": lambda resources: bool(parse_material(resources)[0]),
    "instrument": lambda resources: parse_instrument(resources) is not None,
}


def provided_needs(resources: list[dict]) -> set[str]:
    return {need for need, test in NEEDS.items() if test(resources)}


def offered_behaviors(behaviors: list[dict], resources: list[dict]) -> list[dict]:
    """The behaviors whose needs the request provides. Unknown needs are never provided."""
    provided = provided_needs(resources)
    return [b for b in behaviors if set(b.get("needs", [])) <= provided]


def choose_behavior(
    llm, task: str, behaviors: list[dict], resources: list[dict], ctx: DialogContext | None
) -> tuple[int, list[int]]:
    """(chosen behavior id or -1, offered ids). Each behavior: {behavior_id, name, mode,
    explanation, needs}."""
    offered = offered_behaviors(behaviors, resources)
    offered_ids = [b["behavior_id"] for b in offered]
    if not offered:
        return -1, offered_ids
    if len(offered) == 1:
        return offered[0]["behavior_id"], offered_ids
    chosen = llm.choose_dialogue_behavior(task, offered, ctx)
    if chosen in offered_ids:
        return chosen, offered_ids
    # The LLM did not answer one of the offered: the default of the original design (collect
    # data) when offered, otherwise the first one
    fallback = next((b for b in offered if b.get("mode") == "slot_filling"), offered[0])
    _log.warning(
        f"No valid behavior chosen ({chosen}); using {fallback['name']} as the fallback."
    )
    return fallback["behavior_id"], offered_ids
