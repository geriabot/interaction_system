# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""In grounded QA, once a question was answered, stopping is finishing: a request to stop, also
one only the model read as one ("No, that's all, thank you"), gets the normal end without a
confirmation; before any answer, it is asked about."""

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

MATERIAL = [{"name": "information", "content": "Tickets cost 18 euros. The play starts at 20:00."}]


@LANGUAGES
def test_a_request_to_stop_after_an_answer_is_the_normal_end_without_a_question(language):
    llm, _ = client(['{"about": "conversation", "request": "cancel"}'], language)
    env, state = begin(llm, {"dialog_mode": "grounded_qa", "task_description": "Theatre desk",
                             "resources_json": json.dumps(MATERIAL)})
    state["questions"].append({"question": "When?", "resolution": "answered"})
    closed = cancel_check(env, state, SkillInput(user_text="No, that's all, thank you.",
                                                 option="Anything else?"))
    assert (closed.label, closed.reply) == ("finished", llm.phrase("qa_bye"))
    assert not state.get("awaiting_cancel")
    assert get_skill("result")(env, state, SkillInput()).label == "completed"


@LANGUAGES
def test_a_request_to_stop_before_any_answer_is_asked_about(language):
    llm, _ = client(['{"about": "conversation", "request": "cancel"}'], language)
    env, state = begin(llm, {"dialog_mode": "grounded_qa", "task_description": "Theatre desk",
                             "resources_json": json.dumps(MATERIAL)})
    ask = cancel_check(env, state, SkillInput(user_text="Hmm, maybe I'll leave it.",
                                              option="What would you like to know?"))
    assert ask.label == "ask"
