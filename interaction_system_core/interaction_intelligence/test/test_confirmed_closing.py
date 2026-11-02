# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The closing of a confirmed form ends the dialogue, so it never asks anything: a closing from
the model that asks again ("... is that correct?", found with small models in the debugging) is
replaced by the fixed one. The same for the closing of a confirmed cancel (round 9 of the
debugging: "Lo siento, ¿quieres que te ayudemos con algo más?"; decided by the author,
2026-11-02)."""

import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

FRAME = DialogFrame.from_slots_json('{"slots": [{"name": "day", "type": "str"}]}')


class Model:
    def __init__(self, closing):
        self.closing = closing

    def phrase(self, key):
        return f"[{key}]"

    def response_to_intent(self, task, intent, corrections, frame, ctx=None):
        return self.closing


@pytest.mark.parametrize("closing, said", [
    ("Your table is booked.", "Your table is booked."),
    ("You booked Saturday. Is that correct?", "[confirmed_closing]"),
    ("Reservado el sábado. ¿Es correcto", "[confirmed_closing]"),
])
def test_the_confirmed_closing_never_asks(closing, said):
    env = SkillEnv(llm=Model(closing), ctx=DialogContext())
    state = {"task": "Book a table", "frame": FRAME.to_state()}
    result = get_skill("slot_filling.confirmed")(env, state, SkillInput())
    assert result.reply == said
    assert state["completed"] is True


_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def cancel_client(answer, language):
    with open(_PROMPTS) as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]
    return LLMDialogClient("nlu", "nlg", lambda session, prompt, max_tokens, schema: answer,
                           PromptBuilder(prompts, language=language), language=language)


@pytest.mark.parametrize("language, closing, fixed", [
    ("English", "No problem, we can pick this up whenever you like.", False),
    ("Spanish", "De acuerdo, lo dejamos aquí; seguimos cuando quieras.", False),
    ("English", "Okay, stopping here. Need anything else?", True),
    ("Spanish", "Lo siento, ¿quieres que te ayudemos con algo más?", True),
    ("Spanish", "Vale, paramos. ¿Cuándo te viene bien volver", True),
])
def test_the_cancel_closing_never_asks(language, closing, fixed):
    llm = cancel_client(closing, language)
    said = llm.cancel_confirmed_response("Book a taxi")
    assert said == (llm.phrase("cancel_ok") if fixed else closing)
