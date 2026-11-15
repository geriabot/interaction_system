# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Teach-back: a word salad with one word of a key point ("Kettle along the north blue" for a
blue ear sign) was judged an incomplete restatement, which counted as content and reset the
count of turns without progress (E2 pre-run verification). The judge first says whether the
words make sense; a reply that does not is "other"."""

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


def judged(sense, *statuses):
    return json.dumps({"sense": sense, "kind": "restates", "points": [
        {"id": i + 1, "status": s, "said": said} for i, (s, said) in enumerate(statuses)]})


def test_a_reply_without_sense_is_other_whatever_the_points_say():
    llm, _ = client([judged("no", ("incomplete", "blue"), ("missing", ""))])
    out = llm.judge_teach_back("Kettle along the north blue.", [{"point": p} for p in POINTS], "")
    assert out == {"kind": "other", "points": []}


def test_a_reply_with_sense_is_read_as_before():
    llm, _ = client([judged("yes", ("correct", ""), ("missing", ""))])
    out = llm.judge_teach_back("The counter with the blue ear.", [{"point": p} for p in POINTS],
                               "")
    assert out["kind"] == "restates" and out["points"][0] == (0, "correct", "")


def test_the_skill_gives_a_turn_without_progress():
    llm, _ = client([judged("no", ("incomplete", "blue"), ("missing", ""))])
    env, state = explanation(llm)
    result = get_skill("explanation.check")(env, state, SkillInput(
        user_text="Kettle along the north blue."))
    assert result.label == "other"
    assert all(p["status"] == "pending" for p in state["key_points"])


def test_the_prompt_and_the_schema_ask_for_sense():
    prompt, schema = PromptBuilder(prompts()).judge_teach_back("x", [{"point": "a"}], "")
    assert schema["properties"]["sense"]["enum"] == ["yes", "no"]
    assert "sense" in schema["required"]
    assert 'First decide "sense"' in prompt and '{"sense": "yes" | "no"' in prompt
