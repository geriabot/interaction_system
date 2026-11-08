# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The fixes found in the development run of E1: a scale answer read by its label (a scale of
numbers is never read by position), the answer of grounded QA verified with its question, the
role play closed with its evaluation when the user leaves the scene in silence, a list slot that
says what it holds, and a procedure closing that never gets the text of the steps not done."""

import json
import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
SCALE = [str(n) for n in range(11)]


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers):
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts())), calls


def test_a_scale_of_numbers_is_read_by_label_not_position():
    llm, calls = client(['{"kind": "option", "option": "4"}'])
    assert llm.map_scale_answer("I'd say a four", "How much pain?", SCALE) == {
        "kind": "option", "option": 4}
    # The options are given by their label, not numbered
    assert "- 4" in calls[0] and "5. 4" not in calls[0]


def test_a_label_is_matched_without_case_or_final_punctuation():
    llm, _ = client(['{"kind": "option", "option": "quite."}'])
    options = ["Not at all", "A little", "Quite", "Very much"]
    assert llm.map_scale_answer("Quite", "item", options)["option"] == 2


def test_an_option_that_is_not_a_label_is_ambiguous():
    llm, _ = client(['{"kind": "option", "option": "Sometimes"}'])
    assert llm.map_scale_answer("Sometimes", "item", ["Yes", "No"]) == {
        "kind": "ambiguous", "option": -1}


def test_the_answer_is_verified_with_its_question():
    llm, calls = client(['{"supported": true, "unsupported": []}'])
    material = [{"name": "i", "content": "Members may borrow up to five books."}]
    assert llm.verify_answer("Yes.", material, question="Can I borrow three books?")
    assert 'QUESTION: "Can I borrow three books?"' in calls[0]


SCENE = {"scenario": "A café", "role": "The waiter", "criteria": ["Orders", "Is polite"],
         "max_exchanges": 6}


class Model:
    """A test double of the client for the role play."""

    def __init__(self, scores=True):
        self.scores = scores
        self.ended_by = None

    def phrase(self, key):
        return f"[{key}]"

    def classify_roleplay_turn(self, user_text, scene, history, ctx=None):
        return "continue"

    def roleplay_reply(self, scene, history, ctx=None):
        return "[character]"

    def evaluate_roleplay(self, scene, history, ctx=None):
        if not self.scores:
            return {}
        return {"scores": [{"criterion": c, "score": 3, "evidence": "", "comment": ""}
                           for c in SCENE["criteria"]]}

    def roleplay_feedback(self, scene, evaluation, ended_by, ctx=None):
        self.ended_by = ended_by
        return {"text": "[feedback]", "highlight": "", "improve": ""}


def start(model, mode="roleplay"):
    env = SkillEnv(llm=model, ctx=DialogContext())
    state = {}
    goal = {"dialog_mode": mode, "task_description": "Practise ordering",
            "resources_json": json.dumps([{"name": "scene", "content": json.dumps(SCENE)}])}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def test_a_silent_leave_after_a_line_of_the_scene_gets_the_evaluation():
    model = Model()
    env, state = start(model)
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="A coffee."))
    result = get_skill("silent_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("closed", "[feedback]")
    assert model.ended_by == "silence"
    assert get_mode("roleplay").outcome(state) == ("completed", "")


def test_a_silent_leave_before_any_line_is_the_silent_ending():
    env, state = start(Model())
    result = get_skill("silent_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("none", "")
    assert not state.get("completed")


def test_a_silent_leave_without_scores_is_the_silent_ending():
    env, state = start(Model(scores=False))
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="A coffee."))
    result = get_skill("silent_end")(env, state, SkillInput())
    assert result.label == "none" and not state.get("completed")


FORM = {"dialog_mode": "slot_filling", "task_description": "Take an order",
        "frame_schema_json": json.dumps({"slots": [
            {"name": "size", "type": "str", "canonical_values": ["small", "large"]},
            {"name": "extras", "type": "list_str", "canonical_values": ["a", "b", "c"]}]})}


def test_a_list_slot_says_whether_it_is_empty_or_what_it_holds():
    llm, calls = client([
        '{"extractions": [{"slot": "size", "value": "large", "source_span": "large"}],'
        ' "act": "values"}',
        '{"extractions": [{"slot": "extras", "value": ["a"], "source_span": "a"}],'
        ' "act": "values"}',
        '{"extractions": [], "act": "other"}'])
    env, state = SkillEnv(llm=llm, ctx=DialogContext()), {}
    get_skill("begin")(env, state, SkillInput(args=FORM))
    get_skill("slot_filling.prepare")(env, state, SkillInput())
    for text in ("large", "a", "hmm"):
        get_skill("slot_filling.update")(env, state, SkillInput(user_text=text, number=2))
    extract = [p for p in calls if "SLOTS YOU CAN UPDATE" in p]
    assert "extras (type: list_str) [LIST — empty — needs a value;" in extract[1]
    assert "extras (type: list_str) [LIST — currently holds ['a'];" in extract[2]


def test_the_procedure_closing_gets_how_steps_ended_not_their_text():
    llm, calls = client(['{"label": "skip"}', "[closing]"])
    env, state = SkillEnv(llm=llm, ctx=DialogContext()), {}
    goal = {"dialog_mode": "procedure", "task_description": "Make tea",
            "resources_json": json.dumps([{"name": "steps", "content": json.dumps(
                [{"step": "Boil the water"}])}])}
    get_skill("begin")(env, state, SkillInput(args=goal))
    state["status"] = ["skipped"]
    state["idx"] = 0
    get_skill("procedure.advance")(env, state, SkillInput(option="skipped"))
    closing = calls[-1]
    assert "Boil the water" not in closing and ": skipped" in closing
