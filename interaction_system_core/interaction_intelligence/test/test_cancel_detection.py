# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The spoken cancel request: an exact stop command is decided without the LLM; anything else
goes to the LLM, also in strict mode (a closed-choice question), where the question tells it to
accept only an explicit request. Deciding "no cancel" locally in strict mode made a closed-choice
slot impossible to cancel in Spanish."""

import os

import yaml

from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def client(answer):
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    builder = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"])
    prompts = []

    def infer(session, prompt, max_tokens, schema):
        prompts.append(prompt)
        return answer

    return LLMDialogClient("nlu", "nlg", infer, builder), prompts


def test_an_exact_command_cancels_without_the_llm():
    c, prompts = client('{"cancel": false}')
    assert c.detect_cancel_intent("Stop!", strict=True) is True
    assert prompts == []


def test_a_request_in_strict_mode_goes_to_the_llm():
    c, prompts = client('{"cancel": true}')
    assert c.detect_cancel_intent("Para, déjalo.", strict=True) is True
    assert len(prompts) == 1 and "STRICT MODE: true" in prompts[0]


def test_strict_mode_keeps_a_choice_answer():
    c, prompts = client('{"cancel": false}')
    assert c.detect_cancel_intent("Para llevar.", strict=True) is False
    assert "STRICT MODE: true" in prompts[0]


def test_an_unreadable_answer_is_no_cancel():
    c, _ = client("not json")
    assert c.detect_cancel_intent("Wait, stop.", strict=True) is False
