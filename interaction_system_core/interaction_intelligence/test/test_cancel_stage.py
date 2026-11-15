# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Dropping what the conversation is preparing is a request to stop it: "cancel the booking,
I've changed my mind" in a booking task is about the conversation (the robot carries nothing out
until it ends), while "I need to cancel my card" in a card task stays about the task. Read as
about the task, the words went to the read-back as an unclear answer, and a following "yes"
confirmed the booking. The detector is given the strategy's stage (DialogueMode.cancel_stage)
and the robot's last line."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
TICKETS = {"slots": [{"name": "tickets", "type": "int", "required": True,
                      "description": "How many tickets"}]}
LANGUAGES = pytest.mark.parametrize("language", ["English", "Spanish"])


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers, language="English"):
    pb = PromptBuilder(prompts(), language=language)
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language), calls


def begin(llm):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": "slot_filling", "task_description": "Theatre booking",
        "frame_schema_json": json.dumps(TICKETS)}))
    return env, state


def test_the_prompt_says_dropping_the_request_is_about_the_conversation():
    text = " ".join(prompts()["detect_cancel_intent"].split())
    assert "cancel the booking, I've changed my mind" in text
    assert "Dropping what this conversation is preparing is about the conversation" in text
    assert "I need to cancel my card" in text  # still about the task


def test_the_stage_and_the_robot_line_reach_the_prompt():
    c, calls = client(['{"about": "conversation", "request": "cancel"}'])
    assert c.detect_cancel_intent("Cancel the booking.", task="Theatre booking",
                                  stage="Nothing has been booked yet.",
                                  robot_line="Two tickets for Friday. Is that right?") is True
    assert "WHERE THE CONVERSATION IS: Nothing has been booked yet." in calls[-1]
    assert "ROBOT'S LAST LINE: \"Two tickets for Friday. Is that right?\"" in calls[-1]


def test_without_stage_or_line_the_prompt_says_so():
    c, calls = client(['{"about": "task", "request": "none"}'])
    assert c.detect_cancel_intent("I need to cancel my card", task="Cards") is False
    assert "WHERE THE CONVERSATION IS: (not given)" in calls[-1]
    assert "ROBOT'S LAST LINE: \"(none)\"" in calls[-1]


def test_slot_filling_says_nothing_is_booked_in_both_phases():
    stage = get_mode("slot_filling").cancel_stage
    assert "nothing has been booked" in stage({"phase": "gathering"})
    confirming = stage({"phase": "confirming"})
    assert "read the details back" in confirming and "nothing has been booked" in confirming
    assert get_mode("quiz").cancel_stage({}) == ""


@LANGUAGES
def test_cancel_check_gives_the_detector_the_stage_and_the_last_line(language):
    llm, calls = client(['{"about": "conversation", "request": "cancel"}'], language)
    env, state = begin(llm)
    state["phase"] = "confirming"
    last = "Two tickets for Friday. Is that right?"
    result = get_skill("cancel_check")(env, state, SkillInput(
        user_text="Actually, cancel the whole booking, I've changed my mind.", option=last))
    assert result.label == "ask"
    assert "read the details back" in calls[-1] and last in calls[-1]
