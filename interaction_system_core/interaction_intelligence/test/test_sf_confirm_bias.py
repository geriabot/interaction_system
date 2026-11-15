# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling at the read-back: a reply that starts with "no" is never a confirmation, and after
answering a question the robot asks for the confirmation again (an "okay" to the answer was
taken as a confirmation of the data)."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.languages import load_language
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


class Llm:
    language = "English"

    def __init__(self, intent="confirms"):
        self.intent = intent

    def phrase(self, key):
        return load_language(self.language).phrases[key]

    def classify_intent(self, user_text, frame, ctx=None):
        return self.intent, {}

    def answer_confirmation_question(self, user_text, task, frame, ctx=None):
        return "It takes five to eight days."


def state_of():
    frame = DialogFrame.from_slots_json(json.dumps({"slots": [{"name": "name", "type": "str"}]}))
    frame.try_update({"name": "Ana"})
    return {"task": "Parcel", "frame": frame.to_state(), "phase": "confirming"}


def classify(llm, text):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    return get_skill("slot_filling.classify_confirmation")(env, state_of(), SkillInput(
        user_text=text)).label


@pytest.mark.parametrize("text", ["No, the the Marta.", "Nope.", "no"])
def test_a_reply_that_starts_with_no_never_confirms(text):
    assert classify(Llm("confirms"), text) == "unclear"


def test_a_plain_yes_still_confirms():
    assert classify(Llm("confirms"), "Yes, no changes.") == "confirms"


def test_the_confirmation_is_asked_again_after_an_answer():
    llm = Llm()
    env = SkillEnv(llm=llm, ctx=DialogContext())
    reply = get_skill("slot_filling.answer_question")(env, state_of(), SkillInput(
        user_text="How long does it take?")).reply
    assert reply == "It takes five to eight days. Are the details I read out correct?"
    llm.language = "Spanish"
    reply = get_skill("slot_filling.answer_question")(env, state_of(), SkillInput()).reply
    assert reply.endswith("¿Son correctos los datos que te he leído?")


def test_the_prompt_never_confirms_a_no_without_a_value():
    with open(_PROMPTS) as f:
        text = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"][
            "classify_intent"]
    assert "but gives no new value you can read, is unclear: NEVER confirms" in " ".join(
        text.split())
