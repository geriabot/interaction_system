# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling at the read-back: "take out B and put C" on a list slot applies both changes
(the model applied only the removal when one operation per slot was possible)."""

import json
import os

import yaml

from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.frame_operations import (
    OpKind, apply_operations, corrections_to_extractions, extractions_to_operations)
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def frame():
    f = DialogFrame.from_slots_json(json.dumps({"slots": [
        {"name": "toppings", "type": "list_str",
         "canonical_values": ["olives", "mushrooms", "ham"]}]}))
    f.try_update({"toppings": ["olives", "ham"]})
    return f


def classify(answer):
    with open(_PROMPTS) as fh:
        prompts = yaml.safe_load(fh)["interaction_intelligence"]["ros__parameters"]["prompts"]
    llm = LLMDialogClient("nlu", "nlg", lambda *a: json.dumps(answer), PromptBuilder(prompts))
    return llm.classify_intent("Take out the olives and put mushrooms.", frame())


def apply(corrections):
    f = frame()
    ops = extractions_to_operations(f, corrections_to_extractions(corrections),
                                    default_list_op=OpKind.ADD)
    apply_operations(f, ops)
    return f.to_dict()["toppings"]


def test_take_out_and_put_in_is_applied_whole():
    intent, corrections = classify({"intent": "corrects", "corrections": {
        "toppings": {"remove_items": ["olives"], "add_items": ["mushrooms"]}}})
    assert intent == "corrects"
    assert corrections == {"toppings": [{"_remove": True, "items": ["olives"]},
                                        {"_add": True, "items": ["mushrooms"]}]}
    assert sorted(apply(corrections)) == ["ham", "mushrooms"]


def test_an_item_out_of_the_list_is_not_added():
    _, corrections = classify({"intent": "corrects", "corrections": {
        "toppings": {"remove_items": ["olives"], "add_items": ["pineapple"]}}})
    assert apply(corrections) == ["ham"]


def test_the_schema_and_the_prompt_offer_the_combined_operation():
    schema = frame().schema_for_intent()
    op = schema["properties"]["corrections"]["properties"]["toppings"]["anyOf"][1]
    assert {"remove_items", "add_items"} <= set(op["properties"])
