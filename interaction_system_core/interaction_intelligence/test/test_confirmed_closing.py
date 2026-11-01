# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The closing of a confirmed form ends the dialogue, so it never asks anything: a closing from
the model that asks again ("... is that correct?", found with small models in the debugging) is
replaced by the fixed one."""

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
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
