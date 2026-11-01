# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Settings of the client that must not be ignored silently: the routes that send a question to
another model reach the question that the strategy really asks (a route that names no question
stops the client), and the usual names of a language reach its phrase set."""

import os

import pytest
import yaml

from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder

ROUTED = ("routed_nlu", "routed_nlg")
_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def production_builder() -> PromptBuilder:
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    return PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"])


def client(routes):
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(session)
        return '{"act": "provide", "extractions": []}' if schema else "Fine."

    c = LLMDialogClient("default_nlu", "default_nlg", infer, production_builder(), routes=routes)
    return c, calls


def frame():
    return DialogFrame.from_slots_json('{"slots": [{"name": "drink", "type": "str"}]}')


@pytest.mark.parametrize("name", ["extract_slots", "extract_turn"])
def test_the_slot_extraction_goes_to_its_route(name):
    # The slot filling strategy asks extract_turn; the route may name it or its template
    c, calls = client({name: ROUTED})
    c.extract_turn("a coffee", frame())
    assert calls and set(calls) == {"routed_nlu"}


def test_unrouted_questions_use_the_default_model():
    c, calls = client({"evaluate_quiz_answer": ROUTED})
    c.extract_turn("a coffee", frame())
    assert set(calls) == {"default_nlu"}


def test_a_route_to_an_unknown_question_is_an_error():
    with pytest.raises(ValueError, match="unknown question"):
        client({"extract_slotz": ROUTED})


def test_every_routable_question_is_a_method_that_asks_the_llm():
    questions = LLMDialogClient.questions()
    assert {"extract_turn", "evaluate_quiz_answer", "classify_cancel_reply"} <= questions
    assert "_call_nlu" not in questions and "phrase" not in questions


def test_language_names_reach_the_phrase_sets():
    # language:=es must not leave the fixed phrases in English
    from interaction_intelligence.llm_client import canonical_language, phrase_languages
    assert canonical_language("es") == canonical_language(" Spanish ") == "Spanish"
    assert canonical_language("english") == "English"
    assert canonical_language("Catalan") == "Catalan"
    assert {"English", "Spanish"} <= set(phrase_languages())
