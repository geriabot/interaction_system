# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A reply whose every word is in one of the answers the robot expects ("Cancel." to "open an
account or cancel a card?") is that answer: never a request to stop, and the model is not asked.
At the read-back of the data, a reply that opens with a yes is the confirmation."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
DRINKS = {"slots": [{"name": "drink", "type": "string", "required": True,
                     "description": "The drink"}]}
LANGUAGES = pytest.mark.parametrize("language", ["English", "Spanish"])


def client(answers, language="English"):
    """A real client whose model gives these answers in turn (the last one again after them)."""
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    pb = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"],
                       language=language)
    calls = []

    def infer(session, prompt, max_tokens, schema, **kwargs):
        if schema and "urgency" in schema.get("properties", {}):
            return '{"urgency": false}'  # the urgency question, where it is asked first
        calls.append(prompt)
        if not answers:
            return None
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language), calls


def begin(llm, goal):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def drinks(llm):
    return begin(llm, {"dialog_mode": "slot_filling", "task_description": "Drink order",
                       "frame_schema_json": json.dumps(DRINKS)})


cancel_check = get_skill("cancel_check")

from interaction_intelligence.skills import get_mode  # noqa: E402

ACTIONS = {"slots": [{"name": "action", "type": "str",
                      "canonical_values": ["open an account", "cancel a card"]}]}


def bank(llm):
    env, state = begin(llm, {"dialog_mode": "slot_filling", "task_description": "Bank desk",
                             "frame_schema_json": json.dumps(ACTIONS)})
    get_skill("slot_filling.prepare")(env, state, SkillInput())
    return env, state


def test_a_command_that_is_an_option_is_the_answer_without_the_model():
    # A model that reads every utterance as a request to stop
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'])
    env, state = bank(llm)
    assert get_mode("slot_filling").expected_answers(state) == ["open an account", "cancel a card"]
    n = len(calls)
    result = cancel_check(env, state, SkillInput(user_text="Cancel.", option="Which one?"))
    assert result.label == "none" and len(calls) == n  # the model was not asked
    assert not state.get("awaiting_cancel")


@pytest.mark.parametrize("said", ["Cancel a card.", "cancel"])
def test_any_reply_within_an_option_is_the_answer(said):
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'])
    env, state = bank(llm)
    assert cancel_check(env, state, SkillInput(user_text=said, option="Which one?")).label \
        == "none"


@pytest.mark.parametrize("said", ["I'd like to cancel a card, please.",
                                  "I need to cancel a card, I've lost it."])
def test_a_whole_option_named_in_a_sentence_is_the_answer(said):
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'])
    env, state = bank(llm)
    n = len(calls)
    assert cancel_check(env, state, SkillInput(user_text=said, option="Which one?")).label \
        == "none"
    assert len(calls) == n


def test_a_longer_request_to_stop_still_goes_to_the_model():
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'])
    env, state = bank(llm)
    n = len(calls)
    result = cancel_check(env, state, SkillInput(user_text="Cancel, I want to leave.",
                                                 option="Which one?"))
    assert result.label == "ask" and len(calls) == n + 1


@pytest.mark.parametrize("language,said", [
    ("English", "Yes, that's correct."), ("English", "Yes, that's me. Please cancel it."),
    ("English", "Correct."), ("Spanish", "Sí, es correcto."), ("Spanish", "Vale, perfecto."),
])
def test_at_the_read_back_a_yes_is_the_confirmation(language, said):
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'], language)
    env, state = bank(llm)
    state["phase"] = "confirming"
    n = len(calls)
    result = cancel_check(env, state, SkillInput(
        user_text=said, option="You want to cancel a card, is that correct?"))
    assert result.label == "none" and len(calls) == n


@pytest.mark.parametrize("said", ["Okay let's stop here.", "No, cancel everything.",
                                  "Cancel the card thing, I want to leave."])
def test_at_the_read_back_other_replies_still_go_to_the_model(said):
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'])
    env, state = bank(llm)
    state["phase"] = "confirming"
    n = len(calls)
    assert cancel_check(env, state, SkillInput(user_text=said, option="Is that correct?")).label \
        == "ask"
    assert len(calls) == n + 1


def test_a_yes_while_gathering_still_goes_to_the_model():
    assert not get_mode("slot_filling").yes_is_an_answer({"phase": "gathering"})
    for name in ("procedure", "quiz", "questionnaire", "grounded_qa", "explanation", "roleplay"):
        assert not get_mode(name).yes_is_an_answer({"phase": "confirming"}), name


def test_with_the_model_down_it_does_not_end_the_dialogue():
    llm, _ = client(['{"about": "task", "request": "none"}'])
    env, state = bank(llm)
    state["model_down"] = True
    result = cancel_check(env, state, SkillInput(user_text="Cancel.", option="Which one?"))
    assert result.label == "none" and not state["user_cancelled"]


def test_a_command_that_is_no_option_is_still_decided_locally():
    llm, calls = client([])
    env, state = bank(llm)
    n = len(calls)
    assert cancel_check(env, state, SkillInput(user_text="Stop.", option="Which one?")).label \
        == "ask"
    assert len(calls) == n
