# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A key point said only in part is not a wrong point, and the robot speaks to the person
(decided by the author, 2026-10-31, after the second debugging round with real models):

  - the teach-back judge has a fourth status, incomplete (part of the point, nothing false);
  - the explanation handles it as missing, never as wrong: the point stays to verify, the part
    said is kept, the robot confirms it and asks only for the rest, and alone it is no progress;
  - the repair is told which part was right (never "it is not X" for it) and speaks to the
    person, not about them;
  - the verification of a grounded answer accepts an immediate inference (17:00 is in the
    afternoon) and an answer with only the part asked for."""

import json
import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
POINTS = ["Duvets go in the large machines, numbers 4 or 5",
          "Pay at the central machine: choose the machine number and insert 5 euros",
          "Wash coloured clothes at 40 degrees"]


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
    key_points = json.dumps({"key_points": POINTS})
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": "explanation", "task_description": "How to use the laundrette",
        "resources_json": json.dumps([{"name": "key points", "content": key_points}])}))
    return env, state


def judged(*statuses):
    return json.dumps({"kind": "restates", "points": [
        {"id": i + 1, "status": s, "said": said} for i, (s, said) in enumerate(statuses)]})


def test_the_judge_reads_incomplete():
    c, _ = client([judged(("correct", ""), ("incomplete", "I pay 5 euros"),
                          ("missing", ""))])
    out = c.judge_teach_back("Machines 4 or 5, I pay 5 euros", [{"point": p} for p in POINTS],
                             "")
    assert out["points"][1] == (1, "incomplete", "I pay 5 euros")
    _, schema = PromptBuilder(prompts()).judge_teach_back("x", [{"point": "a"}], "")
    assert "incomplete" in schema["properties"]["points"]["items"]["properties"]["status"][
        "enum"]


def test_an_incomplete_point_is_still_to_verify_and_never_wrong():
    llm, _ = client([judged(("correct", ""), ("incomplete", "I pay 5 euros"),
                            ("correct", ""))])
    env, state = explanation(llm)
    result = get_skill("explanation.check")(env, state, SkillInput(
        user_text="Machines 4 or 5, I pay 5 euros, colours at 40"))
    point = state["key_points"][1]
    assert (point["status"], point["said"]) == ("incomplete", "I pay 5 euros")
    assert result.label == "progress"  # two new points right, nothing wrong: ask the rest
    assert not any(p["status"] == "wrong" for p in state["key_points"])


def test_alone_an_incomplete_point_is_no_progress():
    llm, _ = client([judged(("missing", ""), ("incomplete", "I pay 5 euros"), ("missing", ""))])
    env, state = explanation(llm)
    result = get_skill("explanation.check")(env, state, SkillInput(user_text="I pay 5 euros"))
    assert result.label == "gaps"  # weighs as a missing point in the count of checks


def test_a_point_already_right_is_not_downgraded_to_incomplete():
    llm, _ = client([judged(("correct", ""), ("missing", ""), ("missing", "")),
                     judged(("incomplete", "machines 4 or 5"), ("missing", ""), ("missing", ""))])
    env, state = explanation(llm)
    get_skill("explanation.check")(env, state, SkillInput(user_text="large machines 4 or 5"))
    get_skill("explanation.check")(env, state, SkillInput(user_text="machines 4 or 5"))
    assert state["key_points"][0]["status"] == "correct"


def test_the_robot_confirms_the_part_said_and_asks_for_the_rest():
    llm, calls = client([judged(("correct", ""), ("incomplete", "I pay 5 euros"),
                                ("correct", "")), "Good, it is 5 euros. And where do you pay?"])
    env, state = explanation(llm)
    get_skill("explanation.check")(env, state, SkillInput(user_text="x"))
    get_skill("explanation.ask_missing")(env, state, SkillInput())
    assert 'part of "Pay at the central machine' in calls[-1]
    assert "I pay 5 euros" in calls[-1]


def test_the_repair_keeps_the_right_part_and_speaks_to_the_person():
    pb = PromptBuilder(prompts())
    prompt = pb.repair_explanation("Laundrette", [
        {"point": POINTS[1], "status": "incomplete", "said": "I pay 5 euros"},
        {"point": POINTS[2], "status": "wrong", "said": "60 degrees"}], [POINTS[0]])
    assert 'said only this part, right but INCOMPLETE: "I pay 5 euros"' in prompt
    assert 'the person said instead: "60 degrees"' in prompt
    assert "Speak TO the person" in prompt and "never say it was wrong" in prompt



def test_a_point_not_mentioned_is_never_attributed_to_the_person():
    prompt = PromptBuilder(prompts()).repair_explanation("Laundrette", [
        {"point": POINTS[0], "status": "pending", "said": ""}], [])
    assert f"- {POINTS[0]} (the person did not mention it)" in prompt
    assert "never say that they said it or got it right" in " ".join(prompt.split())

def test_the_verification_accepts_an_immediate_inference():
    text = prompts()["verify_answer"]
    assert "immediate inference" in text and "17:00 is in the" in text
    assert "only the part of the MATERIAL that was asked for" in text
