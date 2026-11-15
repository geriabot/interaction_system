# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: when the LLM cannot design the frame (no schema), the robot says the closing
line of an error instead of ending in silence; an invalid schema still fails at once."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def client(answer):
    with open(_PROMPTS) as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]

    def infer(session, prompt, max_tokens, schema):
        if isinstance(answer, Exception):
            raise answer
        return answer

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts))


def prepare(llm, schema=None):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    goal = {"dialog_mode": "slot_filling", "task_description": "Visitor desk"}
    if schema is not None:
        goal["frame_schema_json"] = json.dumps(schema)
    get_skill("begin")(env, state, SkillInput(args=goal))
    return get_skill("slot_filling.prepare")(env, state, SkillInput())


@pytest.mark.parametrize("answer", ["not a frame", RuntimeError("model down")])
def test_a_frame_the_llm_cannot_design_ends_with_the_error_closing_said(answer):
    llm = client(answer)
    result = prepare(llm)
    assert result.label == "design_failed"
    assert result.reply == llm.phrase("close_error")


def test_an_invalid_schema_still_fails_at_once():
    with pytest.raises(Exception):
        prepare(client("{}"), schema={"slots": []})
