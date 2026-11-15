# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The question whether to stop is about this conversation, and its answer is read with the task
and the user's words that led to it: a "yes" about the task's own object is not a yes to
stopping."""

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


def test_the_question_is_about_the_conversation():
    llm, _ = client([])
    assert "conversation" in llm.ask_cancel_confirmation("Cancel a bank card").lower()


def test_the_reply_is_read_with_the_task_and_the_request():
    llm, calls = client(['{"about": "conversation", "request": "cancel"}', '{"answer": "other"}'])
    env, state = drinks(llm)
    ask = cancel_check(env, state, SkillInput(user_text="I'd rather stop this.",
                                              option="Your drink?"))
    reply = cancel_check(env, state, SkillInput(user_text="Yes, I need to cancel my card.",
                                                option=ask.reply))
    assert reply.label == "denied" and not state["user_cancelled"]
    assert "Drink order" in calls[-1] and "I'd rather stop this." in calls[-1]
    assert "about something the task deals with" in " ".join(calls[-1].split())
