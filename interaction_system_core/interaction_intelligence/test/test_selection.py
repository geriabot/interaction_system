# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The first phase of a request (selection.py): the LLM chooses the behavior the task needs over
the whole catalog, or none, only when the request does not name it; then, always, the check of
the material decides whether the request is feasible."""

import json
import os

import pytest
import yaml

from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.selection import (
    NO_BEHAVIOR_FITS, NONE, choice_context, decide_behavior, missing_needs, provided_needs)

_HERE = os.path.dirname(__file__)
_PROMPTS = os.path.join(_HERE, "..", "config", "prompts.yaml")
_CATALOG = os.path.join(_HERE, "..", "..", "interaction_system", "config",
                        "interaction_orchestrator_params.yaml")


def catalog() -> list[dict]:
    """The orchestrator's catalog, as the intelligence receives it."""
    with open(_CATALOG, encoding="utf-8") as f:
        params = yaml.safe_load(f)["/interaction_orchestrator"]["ros__parameters"]
    behaviors = params["dialogue_behaviors"]
    return [{"behavior_id": behaviors[n]["behavior_id"], "name": n,
             "mode": behaviors[n]["mode"], "explanation": behaviors[n]["explanation"],
             "needs": [x for x in behaviors[n]["needs"] if x]}
            for n in behaviors["behaviors"]]


def ids() -> dict[str, int]:
    return {b["mode"]: b["behavior_id"] for b in catalog()}


def res(name, content):
    return {"name": name, "description": name,
            "content": content if isinstance(content, str) else json.dumps(content)}


BANK = res("questions", [{"question": "Capital of France?", "answer": "Paris"}])
STEPS = res("steps", ["Sit down", "Raise your arms"])
SCENE = res("scene", {"scenario": "A job interview", "role": "Interviewer",
                      "criteria": ["Clarity"]})
TEXT = res("leaflet", "The clinic opens at 9 am and closes at 5 pm.")
KEY_POINTS = res("key points", {"key_points": ["No food after midnight"]})
INSTRUMENT = res("instrument", {"options": ["No", "Yes"], "items": ["I slept well"]})


class FakeLLM:
    """Answers the choice with a fixed id (None: no valid answer) and records what it saw."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def choose_dialogue_behavior(self, task, behaviors, ctx=None):
        self.calls.append((task, behaviors, ctx))
        return self.answer


def builder() -> PromptBuilder:
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    return PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"])


def client(answers):
    prompts = []

    def infer(session, prompt, max_tokens, schema):
        prompts.append((prompt, schema))
        return answers[min(len(prompts), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, builder()), prompts


# ==== THE NEEDS ====

def test_explanation_needs_a_reference_text_or_key_points():
    explanation = next(b for b in catalog() if b["mode"] == "explanation")
    assert explanation["needs"] == ["reference"]
    assert missing_needs(explanation, []) == ["reference"]
    assert missing_needs(explanation, [KEY_POINTS]) == []
    assert missing_needs(explanation, [TEXT]) == []
    # Structured content is not a reference text
    assert missing_needs(explanation, [BANK, STEPS]) == ["reference"]


def test_only_slot_filling_needs_nothing():
    assert [b["mode"] for b in catalog() if not b["needs"]] == ["slot_filling"]


def test_what_each_resource_provides():
    assert provided_needs([BANK]) == {"question_bank"}
    assert provided_needs([STEPS]) == {"steps"}
    assert provided_needs([SCENE]) == {"scene"}
    assert provided_needs([INSTRUMENT]) == {"instrument"}
    assert provided_needs([TEXT]) == {"material", "reference"}
    assert provided_needs([KEY_POINTS]) == {"reference"}
    assert provided_needs([]) == set()


# ==== STEP 1: THE CHOICE, ONLY WITHOUT dialog_mode ====

def test_the_llm_sees_the_whole_catalog_and_its_choice_is_checked():
    llm = FakeLLM(ids()["quiz"])
    d = decide_behavior(llm, "Memory workshop: running the recall quiz", catalog(), [BANK], None)
    assert (d.behavior_id, d.feasible, d.reason) == (ids()["quiz"], True, "")
    assert len(llm.calls[0][1]) == len(catalog())


def test_the_choice_without_its_material_is_not_feasible():
    # The task needs a quiz and the request attaches only a text: no other behavior is chosen
    # in its place
    llm = FakeLLM(ids()["quiz"])
    d = decide_behavior(llm, "Memory workshop: running the recall quiz", catalog(), [TEXT], None)
    assert d.behavior_id == ids()["quiz"]
    assert d.feasible is False
    assert d.reason == "The task needs a quiz, but no question bank was provided"


def test_none_fits():
    d = decide_behavior(FakeLLM(NONE), "Docking to recharge", catalog(), [TEXT], None)
    assert (d.behavior_id, d.feasible, d.reason) == (NONE, False, NO_BEHAVIOR_FITS)


@pytest.mark.parametrize("answer", [None, 42])
def test_no_valid_answer_is_a_failure_of_the_system(answer):
    with pytest.raises(RuntimeError):
        decide_behavior(FakeLLM(answer), "Take an order", catalog(), [], None)


# ==== STEP 2: THE CHECK, ALSO WITH dialog_mode ====

@pytest.mark.parametrize("mode, resources, reason", [
    ("slot_filling", [], ""),
    ("explanation", [KEY_POINTS], ""),
    ("explanation", [], "The task needs an explanation, but no reference text or key points "
                        "were provided"),
    ("quiz", [STEPS], "The task needs a quiz, but no question bank was provided"),
    ("procedure", [], "The task needs a procedure, but no steps were provided"),
    ("roleplay", [TEXT], "The task needs a role play, but no scene was provided"),
    ("grounded_qa", [KEY_POINTS], "The task needs grounded question answering, but no material "
                                  "was provided"),
    ("questionnaire", [BANK], "The task needs a questionnaire, but no instrument was provided"),
])
def test_named_behavior_checked_without_the_llm(mode, resources, reason):
    llm = FakeLLM(None)
    d = decide_behavior(llm, "Any task", catalog(), resources, None, mode)
    assert llm.calls == []
    assert d.behavior_id == ids()[mode]
    assert d.feasible is (reason == "")
    assert d.reason == reason


def test_unknown_named_mode_raises():
    with pytest.raises(ValueError):
        decide_behavior(FakeLLM(None), "Any task", catalog(), [], None, "dance")


# ==== THE QUESTION TO THE MODEL ====

def test_the_question_offers_none_and_hides_the_resources():
    llm, prompts = client(['{"behavior_id": -1}'])
    ctx = choice_context("hospital")
    assert llm.choose_dialogue_behavior("Docking to recharge", catalog(), ctx) == NONE
    prompt, schema = prompts[0]
    assert -1 in schema["properties"]["behavior_id"]["enum"]
    for b in catalog():
        assert f"- {b['behavior_id']}: {b['name']}." in prompt
    assert "hospital" in prompt
    assert ctx.resources == []


def test_an_answer_outside_the_catalog_is_retried_then_none():
    llm, prompts = client(['{"behavior_id": 9}', "not json"])
    assert llm.choose_dialogue_behavior("Take an order", catalog(), None) is None
    assert len(prompts) == LLMDialogClient.MAX_RETRIES
    llm, _ = client(['{"behavior_id": 9}', '{"behavior_id": 3}'])
    assert llm.choose_dialogue_behavior("Guide it", catalog(), None) == 3
