# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: a value out of range is answered with the value and its whole allowed range,
said by code from the schema (a model left a bound out), in the dialogue's language."""

import json

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.languages import load_language
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.slot_filling import _range_words, _rejected_line


class Llm:
    def __init__(self, language="English"):
        self.language = language
        self.prompts = []

    def phrase(self, key):
        return load_language(self.language).phrases[key]

    def generate_response(self, task, frame, user_text, extracted_slots, attempts_exceeded,
                          ctx=None, rejected=None):
        self.prompts.append(rejected)
        return "How many tickets?"


def env(llm):
    return SkillEnv(llm=llm, ctx=DialogContext())


def frame(**bounds):
    return DialogFrame.from_slots_json(json.dumps({"slots": [
        {"name": "n", "type": bounds.pop("type", "int"), **bounds}]}))


@pytest.mark.parametrize("bounds,said", [
    ({"minimum": 1, "maximum": 8}, "between 1 and 8"),
    ({"minimum": 18}, "at least 18"),
    ({"maximum": 8}, "at most 8"),
    ({"minimum": 18, "exclusive_maximum": 120}, "between 18 and 119"),
    ({"type": "float", "exclusive_minimum": 0}, "more than 0"),
    ({"type": "float", "minimum": 0.5, "exclusive_maximum": 2.5}, "at least 0.5, less than 2.5"),
])
def test_every_bound_is_said(bounds, said):
    assert _range_words(env(Llm()), frame(**bounds), "n") == said


def test_spanish_words():
    llm = env(Llm("Spanish"))
    assert _range_words(llm, frame(minimum=1, maximum=8), "n") == "entre 1 y 8"
    assert _rejected_line(llm, frame(minimum=1, maximum=8), {"n": 20}) == (
        "20 no es posible: tiene que ser entre 1 y 8.")


def test_the_reply_says_the_value_and_the_range_before_the_question():
    llm = Llm()
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {"task": "Tickets", "frame": frame(minimum=1, maximum=8).to_state(),
             "last": {"extractions": [], "rejected": {"n": 20}, "ack": "", "corrections": {}}}
    reply = get_skill("slot_filling.next_question")(env, state, SkillInput(number=3)).reply
    assert reply == "20 is not possible: it must be between 1 and 8. How many tickets?"
