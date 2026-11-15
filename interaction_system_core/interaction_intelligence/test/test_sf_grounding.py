# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: what grounds a value of a closed slot (canonical values) in the user's words.
Said, said in numbers or words ("six in the evening" for 18:00), a word only that value has, a
"yes" to a question that named only it, or an ordinal just after the options were read; a word
several values share is ambiguous (the options are read again). The last asking of a closed slot
is its options, composed by code."""

import json

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.languages import load_language
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.slot_filling import _grounded

TIMES = ["18:00", "18:30", "09:00"]
CARS = ["car rental", "car wash", "bike"]
SATISFACTION = ["very satisfied", "satisfied", "not satisfied"]


def frame(canon, kind="str"):
    return DialogFrame.from_slots_json(json.dumps({"slots": [
        {"name": "x", "type": kind, "canonical_values": canon}]}))


def ground(canon, value, user, robot="", language="English"):
    kept, ambiguous = _grounded(frame(canon), [{"slot": "x", "value": value}], user, robot,
                                language)
    return (kept[0]["value"] if kept else None), ambiguous


@pytest.mark.parametrize("value,user", [
    ("18:00", "At six in the evening, please."),
    ("18:00", "six"),
    ("18:30", "half past six, so six thirty"),
    ("09:00", "a las nueve"),
    ("car rental", "I want to rent a car rental"),
    ("bike", "the bike"),
])
def test_values_said_also_in_words_are_taken(value, user):
    language = "Spanish" if "las" in user else "English"
    assert ground(TIMES + CARS, value, user, language=language)[0] == value


def test_a_word_shared_by_several_values_is_ambiguous():
    assert ground(CARS, "car rental", "a car") == (None, ["x"])


def test_a_value_said_inside_a_longer_one_is_not_taken():
    assert ground(SATISFACTION, "satisfied", "very satisfied")[0] is None
    assert ground(SATISFACTION, "very satisfied", "very satisfied")[0] == "very satisfied"


def test_a_value_the_user_did_not_say_is_not_taken():
    assert ground(CARS, "bike", "Yellow window seven the river.") == (None, [])


def test_a_yes_to_a_question_that_named_only_that_value():
    assert ground(TIMES, "18:00", "Yes.", "Is it 18:00?")[0] == "18:00"
    assert ground(TIMES, "18:00", "Sí, vale.", "¿Es a las 18:00?", "Spanish")[0] == "18:00"
    # Not when the question named several values, nor without a yes
    assert ground(TIMES, "18:00", "Yes.", "Is it 18:00 or 09:00?")[0] is None
    assert ground(TIMES, "18:00", "Hmm, something warm.", "Is it 18:00?")[0] is None


def test_an_ordinal_just_after_the_options_were_read():
    read = "Is it car rental, car wash or bike?"
    assert ground(CARS, "car wash", "The second one.", read)[0] == "car wash"
    assert ground(CARS, "bike", "the last", read)[0] == "bike"
    assert ground(CARS, "car rental", "La primera.", "¿Es car rental, car wash o bike?",
                  "Spanish")[0] == "car rental"
    # Two ordinals: not a single pick (the options are asked again)
    assert ground(CARS, "car wash", "not the first, the second", read)[0] is None
    assert ground(CARS, "bike", "first or second", read)[0] is None


class Llm:
    language = "English"

    def __init__(self, turns=()):
        self.turns = list(turns)
        self.asked = []

    def phrase(self, key):
        return load_language(self.language).phrases[key]

    def extract_turn(self, user_text, frame, ctx=None):
        return self.turns.pop(0)

    def generate_response(self, task, frame, user_text, extracted_slots, attempts_exceeded,
                          ctx=None, rejected=None):
        self.asked.append(attempts_exceeded)
        return "[llm question]"


def run(llm, state, text):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    label = get_skill("slot_filling.update")(env, state, SkillInput(user_text=text, number=3))
    reply = get_skill("slot_filling.next_question")(env, state, SkillInput(number=3)).reply
    return label.label, reply


def state_of(canon):
    f = frame(canon)
    f.add_turn("assistant", "Which one would you like?")
    return {"task": "Order", "frame": f.to_state(), "ack_idx": 0, "acknowledgments": 0}


def test_the_last_asking_of_a_closed_slot_reads_its_options():
    llm = Llm([("other", []), ("other", [])])
    state = state_of(CARS)
    assert run(llm, state, "hmm what")[1] == "[llm question]"
    assert run(llm, state, "the weather")[1] == "Is it car rental, car wash or bike?"
    assert llm.asked == [[]]   # never the model's yes/no question for a closed slot


def test_an_ambiguous_answer_reads_the_options_again_and_an_ordinal_then_picks():
    llm = Llm([("values", [{"slot": "x", "value": "car rental", "source_span": "a car"}]),
               ("other", [])])
    state = state_of(CARS)
    label, reply = run(llm, state, "a car")
    assert label == "not_understood" and reply == "Is it car rental, car wash or bike?"
    # The model extracted nothing; the ordinal picks the value at that position
    llm.turns = [("other", [])]
    label, _ = run(llm, state, "The second one, please.")
    assert DialogFrame.from_state(state["frame"]).to_dict() == {"x": "car wash"}


def test_spanish_options_question():
    llm = Llm([("other", []), ("other", [])])
    llm.language = "Spanish"
    state = state_of(CARS)
    run(llm, state, "eh qué")
    assert run(llm, state, "el tiempo")[1] == "¿Es car rental, car wash o bike?"
