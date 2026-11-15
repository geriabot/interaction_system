# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: the generated read-back is checked against the frame (every value said, no
number the frame does not have), else the read-back built by code is said; free-text values
lose the short words that never end a value ("Marta the")."""

import json

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.languages import load_language
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.slot_filling import _readback_ok, _trimmed

SLOTS = [{"name": "name", "type": "str"},
         {"name": "drink", "type": "str", "canonical_values": ["latte", "tea"]},
         {"name": "tickets", "type": "int"},
         {"name": "time", "type": "str", "canonical_values": ["18:00", "09:00"]},
         {"name": "phone", "type": "str"}]


def frame():
    f = DialogFrame.from_slots_json(json.dumps({"slots": SLOTS}))
    f.try_update({"name": "Marta", "drink": "latte", "tickets": 2, "time": "18:00"})
    f.mark_unanswered("phone")
    return f


@pytest.mark.parametrize("line,ok", [
    ("So: Marta, a latte, two tickets at 18:00. Is that right?", True),
    ("Marta, latte, 2 tickets, six in the evening; no phone. Correct?", True),
    ("Marta, a latte and two tickets. Is that right?", False),            # the time left out
    ("Marta, latte, 2 tickets at 18:00, ready at 5 at the counter. OK?", False),  # a number added
    ("Is this information correct?", False),                               # the fallback
])
def test_the_generated_read_back_is_checked(line, ok):
    assert _readback_ok(line, frame(), "English") is ok


class Llm:
    language = "English"

    def __init__(self, line):
        self.line = line

    def phrase(self, key):
        return load_language(self.language).phrases[key]

    def get_confirmation_question(self, task, frame, ctx=None):
        return self.line


def test_a_wrong_read_back_is_replaced_by_the_one_built_by_code():
    state = {"task": "Tickets", "frame": frame().to_state(), "last": None}
    env = SkillEnv(llm=Llm("Is this information correct?"), ctx=DialogContext())
    reply = get_skill("slot_filling.confirmation_question")(env, state, SkillInput()).reply
    assert reply == "Let me check: Marta; latte; 2; 18:00; left blank: phone. Is that right?"
    env.llm.language = "Spanish"
    reply = get_skill("slot_filling.correction_reply")(env, state, SkillInput()).reply
    assert reply.endswith("Repasemos: Marta; latte; 2; 18:00; sin rellenar: phone. ¿Es correcto?")


def test_a_right_read_back_is_said_as_generated():
    state = {"task": "Tickets", "frame": frame().to_state(), "last": None}
    line = "Marta, a latte, two tickets at 18:00, phone left blank. Is that right?"
    env = SkillEnv(llm=Llm(line), ctx=DialogContext())
    assert get_skill("slot_filling.confirmation_question")(env, state, SkillInput()).reply == line


@pytest.mark.parametrize("value,kept,language", [
    ("Marta the", "Marta", "English"),
    ("eh Marta García", "Marta García", "English"),   # recognizer noise at the start
    ("The Grand Hotel", "The Grand Hotel", "English"),
    ("Marta de", "Marta", "Spanish"),
])
def test_free_text_values_lose_trailing_short_words(value, kept, language):
    out = _trimmed(frame(), [{"slot": "name", "value": value}], language)
    assert out == [{"slot": "name", "value": kept}]


def test_closed_values_are_not_trimmed():
    assert _trimmed(frame(), [{"slot": "drink", "value": "latte"}], "English") == [
        {"slot": "drink", "value": "latte"}]
