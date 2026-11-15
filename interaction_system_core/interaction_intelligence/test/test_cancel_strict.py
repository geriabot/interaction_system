# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Questionnaires and role plays tell the cancel detector where the conversation is (the items
are closed-choice questions; lines said in character are part of the practice), without strict
mode: a hesitant wish to stop is still asked about."""

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

INSTRUMENT = {"name": "Feedback", "options": ["Yes", "No"], "items": ["I liked it."]}
SCENE = {"scenario": "A hotel desk", "role": "The receptionist", "criteria": ["Polite"]}


@pytest.mark.parametrize("goal", [
    {"dialog_mode": "questionnaire", "task_description": "Feedback form",
     "resources_json": json.dumps([{"name": "instrument", "content": json.dumps(INSTRUMENT)}])},
    {"dialog_mode": "roleplay", "task_description": "Practise a check-in",
     "resources_json": json.dumps([{"name": "scene", "content": json.dumps(SCENE)}])},
])
def test_the_detector_is_told_where_the_conversation_is(goal):
    llm, calls = client(['{"about": "task", "request": "none"}'])
    env, state = begin(llm, goal)
    assert cancel_check(env, state, SkillInput(user_text="Cancel my reservation, please.",
                                              option="Welcome!")).label == "none"
    prompt = " ".join(calls[-1].split())
    assert "STRICT MODE: false" in prompt
    assert "WHERE THE CONVERSATION IS: (not given)" not in prompt
