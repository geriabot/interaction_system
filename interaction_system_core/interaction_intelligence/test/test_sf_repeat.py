# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: a request to hear the question again has its own ladder per slot (literal,
simpler words, then the options of a closed slot or an example for an open one), is never a
failed attempt on the slot, and starts again after a turn with progress. At the read-back,
"repeat" is an intent of its own."""

import json
import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.languages import load_language
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


class Llm:
    language = "English"

    def __init__(self, turns):
        self.turns = list(turns)
        self.rephrased = []

    def phrase(self, key):
        return load_language(self.language).phrases[key]

    def extract_turn(self, user_text, frame, ctx=None):
        return self.turns.pop(0)

    def rephrase_slot_question(self, task, frame, slot, question, example=False, ctx=None):
        self.rephrased.append((slot, question, example))
        return f"Simpler {slot}{' e.g.' if example else ''}?"


def state_of(slots):
    frame = DialogFrame.from_slots_json(json.dumps({"slots": slots}))
    frame.add_turn("assistant", "Hello! Welcome. Which drink would you like?")
    return {"task": "Order", "frame": frame.to_state(), "ack_idx": 0, "acknowledgments": 0}


def turn(llm, state, text):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    label = get_skill("slot_filling.update")(env, state, SkillInput(user_text=text, number=3))
    if label.label != "repeat":
        return label.label, None
    return "repeat", get_skill("slot_filling.repeat_question")(env, state, SkillInput()).reply


DRINK = [{"name": "drink", "type": "str", "canonical_values": ["coffee", "tea"]}]
NAME = [{"name": "name", "type": "str"}]


def test_the_ladder_of_a_closed_slot_ends_with_its_options():
    llm = Llm([("repeat", [])] * 4)
    state = state_of(DRINK)
    replies = [turn(llm, state, "Sorry?")[1] for _ in range(4)]
    assert replies == ["Which drink would you like?", "Simpler drink?", "Is it coffee or tea?",
                       "Is it coffee or tea?"]
    frame = DialogFrame.from_state(state["frame"])
    assert frame.attempts("drink") == 0 and not frame.is_unanswered("drink")


def test_an_open_slot_gets_an_example_from_the_third_time():
    llm = Llm([("repeat", [])] * 3)
    state = state_of(NAME)
    replies = [turn(llm, state, "What?")[1] for _ in range(3)]
    assert replies[1:] == ["Simpler name?", "Simpler name e.g.?"]
    assert [example for _, _, example in llm.rephrased] == [False, True]


def test_progress_starts_the_ladder_again():
    llm = Llm([("repeat", []), ("question", []), ("repeat", [])])
    state = state_of(DRINK)
    turn(llm, state, "Sorry?")
    turn(llm, state, "What do you have?")
    assert "repeats" not in state
    assert turn(llm, state, "Pardon?")[0] == "repeat" and state["repeats"] == {"drink": 1}


def test_repeat_is_an_intent_of_the_read_back():
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(schema)
        return json.dumps({"intent": "repeat", "corrections": {}})

    llm = LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts()))
    frame = DialogFrame.from_slots_json(json.dumps({"slots": DRINK}))
    assert llm.classify_intent("Can you say that again?", frame) == ("repeat", {})
    assert "repeat" in calls[-1]["properties"]["intent"]["enum"]
    text = " ".join(prompts()["classify_intent"].split())
    assert '"repeat": asks to hear the data again' in text


def test_the_rephrase_prompt_never_proposes_a_value():
    llm_calls = []

    def infer(session, prompt, max_tokens, schema):
        llm_calls.append(prompt)
        return "What would you like to drink?"

    llm = LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts()))
    frame = DialogFrame.from_slots_json(json.dumps({"slots": DRINK}))
    assert llm.rephrase_slot_question("Order", frame, "drink", "Which drink?", True) == (
        "What would you like to drink?")
    assert "Never turn it into a yes/no question" in " ".join(llm_calls[-1].split())
    assert "only an example" in llm_calls[-1]
