# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: an answer between values of the closed slot asked ("about 3, or 4" for 2, 4 or
6; "between morning and afternoon") takes no value, counts as a failed attempt on the slot and
the robot reads the options, composed by code ("I need one of these: 2, 4 or 6."). The model says
so (act "between"); the code too, when two or more values are said in full with a hedge word and
no "no" (a correction)."""

import json

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.languages import load_language
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.slot_filling import _hedged

TABLE = ["2", "4", "6"]
TIME = ["morning", "afternoon", "evening"]
DAYS = ["martes", "miércoles", "jueves"]


def frame(canon, kind="str", extra=False):
    slots = [{"name": "x", "type": kind, "canonical_values": canon}]
    if extra:
        slots.append({"name": "name", "type": "str"})
    return DialogFrame.from_slots_json(json.dumps({"slots": slots}))


def test_the_model_says_the_answer_is_between_values():
    assert _hedged(frame(TABLE), "x", "between", "about 3, or 4", "English")
    # Not for an open slot, nor for a list slot (it takes several values)
    assert not _hedged(frame([]), "x", "between", "about 3, or 4", "English")
    assert not _hedged(frame(TIME, "list_str"), "x", "between", "morning or afternoon",
                       "English")


def test_two_values_with_a_hedge_word_are_between_by_code():
    assert _hedged(frame(TABLE), "x", "values", "2 or 4, I'm not sure", "English")
    assert _hedged(frame(TIME), "x", "values", "Between morning and afternoon.", "English")
    assert _hedged(frame(DAYS), "x", "values", "El martes o el miércoles.", "Spanish")
    assert _hedged(frame(DAYS), "x", "values", "Tal vez el martes, tal vez el jueves", "Spanish")


def test_one_value_or_a_correction_is_not_between():
    assert not _hedged(frame(TABLE), "x", "values", "4, please.", "English")
    assert not _hedged(frame(TIME), "x", "values", "Morning, no, afternoon.", "English")
    assert not _hedged(frame(TIME), "x", "values", "In the morning and in the afternoon",
                       "English")   # no hedge word: left to the extraction


class Llm:
    language = "English"

    def __init__(self, turns=()):
        self.turns = list(turns)

    def phrase(self, key):
        return load_language(self.language).phrases[key]

    def extract_turn(self, user_text, frame, ctx=None):
        return self.turns.pop(0)

    def generate_response(self, task, frame, user_text, extracted_slots, attempts_exceeded,
                          ctx=None, rejected=None):
        return "[llm question]"


def run(llm, state, text):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    label = get_skill("slot_filling.update")(env, state, SkillInput(user_text=text, number=3))
    reply = get_skill("slot_filling.next_question")(env, state, SkillInput(number=3)).reply
    return label.label, reply


def state_of(f):
    f.add_turn("assistant", "A table for how many?")
    return {"task": "Book a table", "frame": f.to_state(), "ack_idx": 0, "acknowledgments": 0}


def test_an_answer_between_values_takes_none_and_reads_the_options():
    llm = Llm([("between", [{"slot": "x", "value": "4", "source_span": "or 4"}])])
    state = state_of(frame(TABLE))
    label, reply = run(llm, state, "About 3, or 4.")
    assert label == "not_understood"
    assert reply == "I need one of these: 2, 4 or 6."
    f = DialogFrame.from_state(state["frame"])
    assert f.to_dict().get("x") is None and f.attempts("x") == 1   # a failed attempt


def test_by_code_even_when_the_model_picked_one():
    llm = Llm([("values", [{"slot": "x", "value": "morning", "source_span": "morning"}])])
    llm.language = "English"
    state = state_of(frame(TIME))
    label, reply = run(llm, state, "Morning or afternoon, either is fine.")
    assert label == "not_understood"
    assert reply == "I need one of these: morning, afternoon or evening."


def test_spanish_phrase():
    llm = Llm([("between", [])])
    llm.language = "Spanish"
    state = state_of(frame(DAYS))
    assert run(llm, state, "El martes o el miércoles")[1] == (
        "Necesito una de estas: martes, miércoles o jueves.")


def test_another_slot_given_with_the_hedge_is_kept_and_no_attempt_is_counted():
    llm = Llm([("between", [{"slot": "name", "value": "Ana", "source_span": "Ana"}])])
    state = state_of(frame(TABLE, extra=True))
    label, _ = run(llm, state, "For Ana, 2 or 4 people.")
    f = DialogFrame.from_state(state["frame"])
    assert label == "incomplete" and f.to_dict().get("name") == "Ana"
    assert f.to_dict().get("x") is None and f.attempts("x") == 0
