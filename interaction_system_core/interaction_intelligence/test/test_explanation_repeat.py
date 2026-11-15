# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Explanation: a request to hear it again was "not_understood", a failed check that explained
the whole topic again (E2 pre-run verification): three "say it again?" ended the explanation as
failed. It is now "repeat": the robot's last line again (literally, then in other words), a turn
without progress for the tree, not a failed check."""

import xml.etree.ElementTree as ET

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

_TREE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "interaction_system_behaviors",
                     "behavior_tree_xml", "explanation.xml")
REPEAT = json.dumps({"sense": "yes", "kind": "repeat", "points": []})
OTHER = json.dumps({"sense": "yes", "kind": "other", "points": []})


def test_the_judge_reads_a_request_to_repeat():
    llm, _ = client([REPEAT])
    out = llm.judge_teach_back("Sorry, can you say that again?", [{"point": p} for p in POINTS],
                               "")
    assert out["kind"] == "repeat"
    _, schema = PromptBuilder(prompts()).judge_teach_back("x", [{"point": "a"}], "")
    assert "repeat" in schema["properties"]["kind"]["enum"]


def test_the_first_request_repeats_the_last_line_then_in_other_words():
    llm, calls = client(["The counters with a blue ear sign have a loop.", REPEAT, REPEAT,
                         "Look for the blue ear and set your aid to T.", OTHER, REPEAT])
    env, state = explanation(llm)
    opening = get_skill("explanation.open")(env, state, SkillInput()).reply
    assert get_skill("explanation.check")(env, state, SkillInput(
        user_text="Sorry, again?")).label == "repeat"
    first = get_skill("explanation.repeat")(env, state, SkillInput())
    assert first.reply == opening and len(calls) == 2  # literal: no LLM
    get_skill("explanation.check")(env, state, SkillInput(user_text="What did you say?"))
    second = get_skill("explanation.repeat")(env, state, SkillInput())
    assert second.reply.startswith("Look for the blue ear") and second.reply.endswith(
        state["last_check"])
    assert state["attempts"] == 0  # not a failed check
    # Another kind of reply starts the run again: the next request is literal again
    get_skill("explanation.check")(env, state, SkillInput(user_text="Hmm."))
    get_skill("explanation.check")(env, state, SkillInput(user_text="Again?"))
    assert get_skill("explanation.repeat")(env, state, SkillInput()).reply == second.reply


def test_the_tree_counts_a_turn_without_progress_and_no_failed_check():
    tree = ET.parse(_TREE).getroot()
    turn_tree = next(t for t in tree.iter("BehaviorTree") if t.get("ID") == "StrategyTurn")
    branch = turn_tree.find("Sequence/IfThenElse")
    assert branch[0].get("code") == "label == 'repeat'"
    inner = branch[1]
    assert inner[0].get("ID") == "NoProgress"
    assert inner[1].get("skill") == "explanation.repeat"
    assert "ExplanationRepair" not in ET.tostring(inner, encoding="unicode")
