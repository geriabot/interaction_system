# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The client reads a correct answer of the model as the program needs it (layer 1 of the
debugging before E0a, evaluation/debug/coverage.yaml SF7): the extraction of a slot keeps only
the allowed values of a closed slot, in any case and language, and an answer with nothing
allowed is a turn without values. The scripted LLM of the system tests bypasses this reading, so
it is checked here with the real client and the real prompts."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def client(answer, language="English"):
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    builder = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"],
                            language=language)
    return LLMDialogClient("nlu", "nlg", lambda *a: json.dumps(answer), builder,
                           language=language)


def frame(values):
    return DialogFrame.from_slots_json(json.dumps({"slots": [
        {"name": "drink", "type": "str", "canonical_values": values},
        {"name": "extras", "type": "list_str", "canonical_values": values},
        {"name": "name", "type": "str"}]}))


@pytest.mark.parametrize("language,values,said", [
    ("English", ["coffee", "tea"], "Tea"), ("Spanish", ["café", "té"], "té")])
def test_an_allowed_value_is_kept_in_any_case(language, values, said):
    act, values_ = client({"act": "values", "extractions": [
        {"slot": "drink", "value": said}]}, language).extract_turn(said, frame(values))
    assert act == "values" and [v["value"] for v in values_] == [said]


def test_a_value_not_allowed_is_dropped_and_a_list_keeps_only_the_allowed():
    c = client({"extractions": [{"slot": "drink", "value": "water"},
                                {"slot": "extras", "value": ["tea", "whisky"]},
                                {"slot": "name", "value": "Ana"}]})
    act, values = c.extract_turn("water, tea and whisky, I'm Ana", frame(["coffee", "tea"]))
    assert {v["slot"]: v["value"] for v in values} == {"extras": ["tea"], "name": "Ana"}
    assert act == "values"


def test_an_answer_with_nothing_allowed_is_a_turn_without_values():
    c = client({"extractions": [{"slot": "drink", "value": "water"}]})
    assert c.extract_turn("water", frame(["coffee", "tea"])) == ("other", [])


def test_a_slot_that_does_not_exist_is_ignored():
    c = client({"act": "values", "extractions": [{"slot": "size", "value": "large"}]})
    assert c.extract_turn("large", frame(["coffee", "tea"]))[1] == []


# Found in the debugging with real models (2026-10-31): the judges' answers that a small model
# got wrong, read so that they cannot do harm

SCENE = {"scenario": "A garage", "role": "The mechanic", "criteria": ["Asks the price"]}
HISTORY = [{"role": "assistant", "content": "What's wrong?"},
           {"role": "user", "content": "It makes a noise."}]


def test_a_criterion_no_line_shows_scores_one():
    c = client({"lines": [], "comment": "Asked nothing.", "score": 4})
    assert c.evaluate_roleplay(SCENE, HISTORY)["scores"][0]["score"] == 1


def test_a_criterion_a_line_shows_keeps_its_score():
    c = client({"lines": [1], "comment": "Clear.", "score": 4})
    assert c.evaluate_roleplay(SCENE, HISTORY)["scores"][0]["score"] == 4


def test_the_teach_back_judge_counts_one_entry_per_point():
    points = [{"point": "Green: swim"}, {"point": "Red: no swimming"}]
    c = client({"kind": "restates", "points": [
        {"id": 1, "status": "correct", "said": ""},
        {"id": 1, "status": "wrong", "said": "red means swim"},
        {"id": 2, "status": "missing", "said": ""}]})
    assert c.judge_teach_back("green means swim", points, "")["points"] == [
        (0, "correct", ""), (1, "missing", "")]


def test_the_teach_back_judge_is_asked_for_every_point():
    _, schema = PromptBuilder({"judge_teach_back": "{key_points}{asked}{user_text}"}
                              ).judge_teach_back("x", [{"point": "a"}, {"point": "b"}], "")
    assert schema["properties"]["points"]["minItems"] == 2
    assert schema["properties"]["points"]["maxItems"] == 2
