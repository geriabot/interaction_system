# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling at the read-back: a "yes" that names values the data lack ("Yes, free swim and
aquagym" when only free swim was read back) is read as a correction of them, applied, and the
data are read back again (the model took the yes and left out the added value)."""

import json

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

POOL = {"slots": [
    {"name": "activities", "type": "list_str",
     "canonical_values": ["swimming lessons", "aquagym", "free swim", "sauna"]},
    {"name": "day", "type": "str", "canonical_values": ["saturday", "sunday"]}]}
BIKE = {"slots": [
    {"name": "bike", "type": "str", "canonical_values": ["city bike", "electric bike", "tandem"]},
    {"name": "hours", "type": "int", "minimum": 1, "maximum": 8}]}
SPA = {"slots": [
    {"name": "relleno", "type": "list_str", "canonical_values": ["queso", "jamón", "lechuga"]},
    {"name": "pan", "type": "str", "canonical_values": ["blanco", "integral"]}]}


class Llm:
    def __init__(self, language="English"):
        self.language = language

    def classify_intent(self, user_text, frame, ctx=None):
        return "confirms", {}


def run(schema, values, text, language="English"):
    frame = DialogFrame.from_slots_json(json.dumps(schema))
    frame.try_update(values)
    state = {"task": "Booking", "frame": frame.to_state(), "phase": "confirming"}
    env = SkillEnv(llm=Llm(language), ctx=DialogContext())
    label = get_skill("slot_filling.classify_confirmation")(env, state, SkillInput(
        user_text=text)).label
    return label, DialogFrame.from_state(state["frame"]).filled_slots()


def test_a_yes_that_adds_a_list_value_is_a_correction():
    label, slots = run(POOL, {"activities": ["free swim"], "day": "saturday"},
                       "Yes, free swim and aquagym on Saturday.")
    assert label == "corrects_applied"
    assert slots["activities"] == ["free swim", "aquagym"]


def test_a_yes_that_changes_a_value_is_a_correction():
    label, slots = run(BIKE, {"bike": "electric bike", "hours": 3}, "Yes, a city bike for three hours.")
    assert label == "corrects_applied"
    assert slots["bike"] == "city bike"


@pytest.mark.parametrize("text", [
    "Yes, free swim on Saturday.",
    "Yes, that's right.",
    "Yes, free swim, not the sauna.",
    "Yes, free swim without the sauna, on Saturday.",
    "Yes, free swim instead of the sauna.",
])
def test_a_yes_that_names_nothing_new_still_confirms(text):
    label, slots = run(POOL, {"activities": ["free swim"], "day": "saturday"}, text)
    assert label == "confirms"
    assert slots["activities"] == ["free swim"]


def test_two_values_of_a_one_value_slot_are_left_to_the_model():
    label, _ = run(POOL, {"activities": ["free swim"], "day": "saturday"},
                   "Yes, Saturday or Sunday, either is fine.")
    assert label == "confirms"


def test_spanish():
    label, slots = run(SPA, {"relleno": ["queso"], "pan": "integral"},
                       "Sí, queso y lechuga en pan integral.", "Spanish")
    assert label == "corrects_applied"
    assert slots["relleno"] == ["queso", "lechuga"]
    label, _ = run(SPA, {"relleno": ["queso"], "pan": "integral"},
                   "Sí, queso, sin jamón, en pan integral.", "Spanish")
    assert label == "confirms"
