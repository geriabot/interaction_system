# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Teach-back repairs: given the points the person had already said right, the model restated
them in the repair (E2 pre-run verification), which lengthens it and buries the points to
explain again. The repair is told only how many there are, and never restates them."""

import json
import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
POINTS = ["Use a counter with a blue ear sign", "Switch the hearing aid to the T position"]


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers):
    """A real client whose model gives these answers in turn (the prompts are recorded)."""
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts())), calls


def explanation(llm):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": "explanation", "task_description": "The hearing loop",
        "resources_json": json.dumps([{"name": "key points",
                                       "content": json.dumps({"key_points": POINTS})}])}))
    return env, state


def test_the_repair_gets_only_the_count_of_the_points_right():
    prompt = PromptBuilder(prompts()).repair_explanation(
        "The hearing loop", [{"point": POINTS[1], "status": "pending", "said": ""}], [POINTS[0]])
    assert POINTS[0] not in prompt
    assert "POINTS THE PERSON ALREADY SAID RIGHT: 1" in prompt
    assert "Never restate or repeat the points the person already said right" in " ".join(
        prompt.split())


def test_without_points_right_the_repair_says_none():
    prompt = PromptBuilder(prompts()).repair_explanation(
        "The hearing loop", [{"point": POINTS[1], "status": "pending", "said": ""}], [])
    assert "POINTS THE PERSON ALREADY SAID RIGHT: none" in prompt
