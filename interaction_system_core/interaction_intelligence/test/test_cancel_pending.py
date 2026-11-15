# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The question whether to stop never stays pending by mistake: a left-over question (the
request that answered it failed) is dropped, a failed reading of the answer falls back to the
exact yes and no words, and a user who asked to stop and then says nothing ends the dialogue as a
silence ends it: with the strategy's own closing when it has one, else as cancelled."""

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


class Broken(LLMDialogClient):
    def classify_cancel_reply(self, *args, **kwargs):
        raise RuntimeError("no answer")

    def detect_cancel_intent(self, user_text, *args, **kwargs):
        if kwargs.get("reading") and user_text == "Explode.":
            raise RuntimeError("no answer")
        return super().detect_cancel_intent(user_text, *args, **kwargs)


def broken(language="English"):
    llm, _ = client(['{"about": "conversation", "request": "cancel"}'], language)
    llm.__class__ = Broken
    return llm


def test_a_left_over_question_is_dropped():
    llm, calls = client(['{"about": "conversation", "request": "cancel"}',
                         '{"about": "task", "request": "none"}'])
    env, state = drinks(llm)
    ask = cancel_check(env, state, SkillInput(user_text="I'd rather stop.", option="Your drink?"))
    assert ask.label == "ask"
    # That turn's answer was lost; the strategy took it and the robot asked something else
    later = cancel_check(env, state, SkillInput(user_text="Yes.",
                                                option="Is this information correct?"))
    assert later.label == "none" and not state["awaiting_cancel"] and not state["user_cancelled"]


@LANGUAGES
def test_a_failed_reading_of_the_answer_falls_back_to_the_exact_words(language):
    llm = broken(language)
    env, state = drinks(llm)
    ask = cancel_check(env, state, SkillInput(user_text="I'd rather stop.", option="Your drink?"))
    yes = "Yes." if language == "English" else "Sí."
    result = cancel_check(env, state, SkillInput(user_text=yes, option=ask.reply))
    assert result.label == "confirmed" and state["user_cancelled"]
    env, state = drinks(llm)
    ask = cancel_check(env, state, SkillInput(user_text="I'd rather stop.", option="Your drink?"))
    result = cancel_check(env, state, SkillInput(user_text="Maybe later.", option=ask.reply))
    assert result.label == "denied" and not state["awaiting_cancel"]


def test_a_failed_detection_is_not_a_request_to_stop():
    llm = broken()
    env, state = drinks(llm)
    assert cancel_check(env, state, SkillInput(user_text="Explode.", option="x")).label == "none"


@LANGUAGES
def test_silence_after_the_question_asks_it_again_then_ends_as_cancelled(language):
    llm, _ = client(['{"about": "conversation", "request": "cancel"}'], language)
    env, state = drinks(llm)
    ask = cancel_check(env, state, SkillInput(user_text="I'd rather stop.", option="Your drink?"))
    silence = get_skill("silence")
    first = silence(env, state, SkillInput(number=0, option=ask.reply))
    assert (first.label, first.reply) == ("prompt", ask.reply)
    second = silence(env, state, SkillInput(number=1, option=ask.reply))
    assert second.label == "closed" and second.reply == llm.phrase("cancel_ok")
    result = get_skill("result")(env, state, SkillInput())
    assert result.label == "cancelled" and "said nothing" in result.reply


@LANGUAGES
def test_silence_after_the_question_ends_with_the_strategys_own_closing_when_it_has_one(
        language, monkeypatch):
    # "Goodbye" at the end of a role play is read as a request to stop; the user has left: the
    # dialogue ends as a silence ends it (the strategy's closing, its own outcome), not cancelled
    from interaction_intelligence.skills import get_mode
    llm, _ = client(['{"about": "conversation", "request": "cancel"}'], language)
    env, state = drinks(llm)
    monkeypatch.setattr(get_mode("slot_filling"), "on_silent_end",
                        lambda env, state: "Here is how it went.")
    ask = cancel_check(env, state, SkillInput(user_text="Goodbye.", option="Your drink?"))
    silence = get_skill("silence")
    assert silence(env, state, SkillInput(number=0, option=ask.reply)).label == "prompt"
    second = silence(env, state, SkillInput(number=1, option=ask.reply))
    assert (second.label, second.reply) == ("closed", "Here is how it went.")
    assert not state["user_cancelled"] and not state["awaiting_cancel"]
    assert get_skill("result")(env, state, SkillInput()).label != "cancelled"
