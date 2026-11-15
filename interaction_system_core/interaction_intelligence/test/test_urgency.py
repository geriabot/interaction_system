# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Urgencies in the user's turn: their own question (detect_urgency), asked on every user turn
before the request to stop; the shared check hands the dialogue over with a fixed line, and the
outcome is escalated. No keywords: without an answer of the model, a turn is never an
urgency."""

import json
import os

import yaml

from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput
from interaction_intelligence.skills.common import begin, cancel_check, result

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def client(answer):
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    builder = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"])
    prompts = []

    def infer(session, prompt, max_tokens, schema):
        prompts.append((prompt, schema))
        return answer

    return LLMDialogClient("nlu", "nlg", infer, builder), prompts


# ==== THE QUESTION ====


def test_the_question_reads_an_urgency():
    c, prompts = client('{"urgency": true}')
    assert c.detect_urgency("I've slipped and I can't stand up", task="Stretching") is True
    prompt, schema = prompts[0]
    assert schema["properties"]["urgency"]["type"] == "boolean"
    assert "URGENCY" in prompt and "Stretching" in prompt and "figurative" in prompt


def test_no_urgency():
    c, _ = client('{"urgency": false}')
    assert c.detect_urgency("Four of us, at nine.") is False


def test_no_answer_is_never_an_urgency():
    for answer in (None, "not json", '{"urgency": "yes"}'):
        c, _ = client(answer)
        assert c.detect_urgency("Help!") is False


def test_the_question_can_be_routed():
    assert "detect_urgency" in LLMDialogClient.questions()


# ==== THE SHARED CHECK AND THE OUTCOME ====


class FakeLLM:
    language = "English"

    def __init__(self, urgent=(), cancel=False, reply="other"):
        self.urgent, self.cancel, self.reply = set(urgent), cancel, reply
        self.asked = []

    def detect_urgency(self, user_text, ctx=None, task=""):
        self.asked.append(user_text)
        return user_text in self.urgent

    def detect_cancel_intent(self, user_text, ctx=None, strict=False, task="", stage="",
                             robot_line="", local=True, reading=False):
        if reading:
            return "cancel" if self.cancel else "none"
        return self.cancel

    def classify_cancel_reply(self, user_text, ctx=None, task="", request=""):
        return self.reply

    def ask_cancel_confirmation(self, task, ctx=None):
        return "[cancel?]"

    def phrase(self, key):
        return f"[{key}]"


def env_of(llm):
    return SkillEnv(llm=llm, ctx=None, ack_phrases=[], ack_enabled=False, filler_phrases=[],
                    timeout_prompt="", history_max_turns=4)


def started(llm, goal=None):
    env, state = env_of(llm), {}
    goal = goal or {"dialog_mode": "grounded_qa", "task_description": "Museum desk",
                    "resources_json": '[{"name": "m", "content": "Opens at 10."}]'}
    begin(env, state, SkillInput("", "", 0, goal))
    return env, state


def test_an_urgency_hands_the_dialogue_over():
    env, state = started(FakeLLM(urgent={"I've fallen"}))
    turn = SkillInput("I've fallen", "What would you like to know?", 0, {})
    out = cancel_check(env, state, turn)
    assert out.label == "escalated" and out.reply == "[close_escalated]"
    assert state["escalated"] and state["urgency_text"] == "I've fallen"
    end = result(env, state, SkillInput("", "", 0, {}))
    assert end.label == "escalated"
    assert end.reply == "Urgency, handed over to a person: I've fallen"


def test_an_urgency_wins_over_an_ending_of_the_tree():
    env, state = started(FakeLLM(urgent={"Help me"}))
    cancel_check(env, state, SkillInput("Help me", "", 0, {}))
    assert result(env, state, SkillInput("", "turn_limit", 0, {})).label == "escalated"


def test_an_urgency_in_answer_to_the_confirmation():
    # (in slot filling: in grounded QA a request to stop is the normal end, with no confirmation)
    llm = FakeLLM(urgent={"No, my chest hurts a lot"}, cancel=True)
    schema = {"slots": [{"name": "drink", "type": "string", "required": True,
                         "description": "The drink"}]}
    env, state = started(llm, {"dialog_mode": "slot_filling", "task_description": "Drink order",
                               "frame_schema_json": json.dumps(schema)})
    asked = cancel_check(env, state, SkillInput("I'd rather stop", "Anything else?", 0, {}))
    assert asked.label == "ask"
    out = cancel_check(env, state, SkillInput("No, my chest hurts a lot", "", 0, {}))
    assert out.label == "escalated" and not state.get("user_cancelled")


def test_an_exact_stop_command_asks_no_urgency_question():
    llm = FakeLLM(cancel=True)
    env, state = started(llm)
    cancel_check(env, state, SkillInput("Stop", "", 0, {}))
    assert llm.asked == []


def test_no_urgency_leaves_the_turn_to_the_strategy():
    env, state = started(FakeLLM())
    assert cancel_check(env, state, SkillInput("When does it open?", "", 0, {})).label == "none"
    assert not state["escalated"]
