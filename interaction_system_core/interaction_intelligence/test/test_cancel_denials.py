# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""After a denied request to stop: the same words, normalised, are not asked about again while
the user is on the same element; an exact stop command is never set aside."""

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

QUIZ = [{"name": "quiz", "content": json.dumps([{"question": "Two plus two?", "answer": "4"},
                                               {"question": "Three plus three?", "answer": "6"}])}]


def quiz(llm):
    return begin(llm, {"dialog_mode": "quiz", "task_description": "Sums",
                       "resources_json": json.dumps(QUIZ)})


@LANGUAGES
def test_an_exact_command_is_heard_again_after_a_denial(language):
    llm, calls = client(['{"answer": "no"}'], language)
    env, state = drinks(llm)
    stop = "Stop." if language == "English" else "Para."
    ask = cancel_check(env, state, SkillInput(user_text=stop, option="Your drink?"))
    assert ask.label == "ask"
    assert cancel_check(env, state, SkillInput(user_text="No.", option=ask.reply)).label == "denied"
    again = "Stop!" if language == "English" else "¡Para!"
    assert cancel_check(env, state, SkillInput(user_text=again, option="Your drink?")).label \
        == "ask"


@LANGUAGES
def test_the_same_words_differently_written_are_set_aside_on_the_same_element(language):
    llm, calls = client(['{"about": "conversation", "request": "cancel"}', '{"answer": "no"}'],
                        language)
    env, state = quiz(llm)
    ask = cancel_check(env, state, SkillInput(user_text="Done, I've pressed cancel.",
                                              option="Two plus two?"))
    assert ask.label == "ask"
    assert cancel_check(env, state, SkillInput(user_text="No.", option=ask.reply)).label == "denied"
    n = len(calls)
    assert cancel_check(env, state, SkillInput(user_text="Done I've pressed CANCEL!",
                                              option="Two plus two?")).label == "none"
    assert len(calls) == n  # no model call for it


def test_on_another_element_the_words_are_asked_about_again():
    llm, calls = client(['{"about": "conversation", "request": "cancel"}', '{"answer": "no"}',
                         '{"about": "conversation", "request": "cancel"}'])
    env, state = quiz(llm)
    said = "Done, I've pressed cancel."
    ask = cancel_check(env, state, SkillInput(user_text=said, option="Two plus two?"))
    cancel_check(env, state, SkillInput(user_text="No.", option=ask.reply))
    state["idx"] = 1  # the next question
    assert cancel_check(env, state, SkillInput(user_text=said, option="Three plus three?")).label \
        == "ask"
