# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The questionnaire never chooses for the person: an option the LLM chose is not taken when
the reply says the words that only another option has (a dropped modifier: "very satisfied" as
"Satisfied", "not sure" as "Yes"); a synonym or a paraphrase is kept. A change of the answer to
the previous item is recorded on that item."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.interpretation_context import (build_prompt, decode_context,
                                                             encode_context)
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.questionnaire import _contradicted

SATISFACTION = ["Very dissatisfied", "Dissatisfied", "Satisfied", "Very satisfied"]
YES_NO = ["Yes", "No", "Not sure"]
AMOUNT = ["Not at all", "A little", "Quite", "Very much"]
CANTIDAD = ["Nada", "Un poco", "Bastante", "Mucho"]
FEEDBACK = ["No", "Partly", "Yes"]
EN_PARTE = ["No", "En parte", "Sí"]


@pytest.mark.parametrize("labels,chosen,said,contradicted", [
    (SATISFACTION, 2, "very satisfied", True),       # the modifier dropped
    (SATISFACTION, 2, "I'm satisfied", False),
    (SATISFACTION, 3, "very satisfied, yes", False),
    (YES_NO, 0, "not sure", True),
    (YES_NO, 0, "absolutely", False),                # a synonym is kept
    (YES_NO, 2, "I'm not sure", False),
    (AMOUNT, 1, "not very much", True),
    (AMOUNT, 1, "a bit", False),
    (CANTIDAD, 1, "no mucho", True),
    (CANTIDAD, 3, "muchísimo", False),
    # A one-word option said as a discourse word beside the chosen one, said in full (E1
    # validation, qn_event_feedback): the choice is kept
    (FEEDBACK, 1, "Partly comfortable, yes.", False),
    (EN_PARTE, 1, "Sí, en parte, la verdad.", False),
    (FEEDBACK, 2, "Yes, it was useful.", False),
    (FEEDBACK, 1, "Yes.", True),                    # says only another option
])
def test_an_option_the_reply_contradicts_is_not_taken(labels, chosen, said, contradicted):
    assert _contradicted(chosen, labels, said) is contradicted


class Llm:
    language = "English"

    def __init__(self, answers):
        self.answers = list(answers)
        self.previous = []

    def map_scale_answer(self, user_text, item, options, ctx=None, previous=None):
        self.previous.append(previous)
        return self.answers.pop(0)

    def phrase(self, key):
        return f"[{key}]"


def state_of():
    instrument = {"name": "S", "instructions": "", "scoring": "sum", "items": [
        {"text": "I enjoyed it", "options": [{"label": o, "value": i + 1}
                                             for i, o in enumerate(AMOUNT)],
         "reverse": False, "clarification": ""},
        {"text": "It was long", "options": [{"label": o, "value": i + 1}
                                            for i, o in enumerate(AMOUNT)],
         "reverse": False, "clarification": ""},
    ]}
    return {"instrument": instrument, "idx": 0, "answers": []}


def record(llm, state, text):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    return get_skill("questionnaire.record")(env, state, SkillInput(user_text=text))


def test_a_contradicted_choice_reads_the_options_again():
    llm = Llm([{"kind": "option", "option": 1}])
    state = state_of()
    assert record(llm, state, "not very much").label == "ambiguous"
    assert state["answers"] == []


def test_a_change_of_the_previous_answer_is_recorded_there():
    llm = Llm([{"kind": "option", "option": 2},
               {"kind": "corrects_previous", "option": 3}])
    state = state_of()
    assert record(llm, state, "quite").label == "next"
    assert llm.previous[0] is None          # the first item has no previous one
    result = record(llm, state, "for the last one I meant very much")
    assert result.label == "corrected"
    assert llm.previous[1] == {"item": "I enjoyed it", "options": AMOUNT, "answer": "Quite"}
    assert state["idx"] == 1 and len(state["answers"]) == 1
    assert state["answers"][0]["answer"] == "Very much" and state["answers"][0]["value"] == 4
    assert state["answers"][0]["corrected_from"] == "Quite"
    reply = get_skill("questionnaire.corrected")(SkillEnv(llm=llm, ctx=DialogContext()), state,
                                                SkillInput()).reply
    assert reply.startswith("[questionnaire_corrected] [questionnaire_item] It was long.")


def test_a_correction_without_a_previous_item_is_not_understood():
    llm = Llm([{"kind": "corrects_previous", "option": 0}])
    assert record(llm, state_of(), "for the last one, not at all").label == "other"


# ==== A correction of the previous item needs a reference to it (E1 validation) ====

def state_at(idx, items, answers, options=AMOUNT):
    instrument = {"name": "S", "instructions": "", "scoring": "sum", "items": [
        {"text": t, "options": [{"label": o, "value": i + 1} for i, o in enumerate(options)],
         "reverse": False, "clarification": ""} for t in items]}
    return {"instrument": instrument, "idx": idx, "answers": [
        {"item": items[i], "status": "answered", "answer": a, "value": options.index(a) + 1,
         "said": a} for i, a in enumerate(answers)]}


MOOD = ["Little interest or pleasure in doing things", "Feeling down, depressed or hopeless"]


def test_after_an_explanation_the_reply_answers_the_current_item():
    # qn_mood_screen: the robot explained "hopeless"; the LLM read the answer as a change of
    # item 1 (same options); without a reference to item 1 it is the answer to item 2
    llm = Llm([{"kind": "corrects_previous", "option": 0}])
    state = state_at(1, MOOD, ["A little"])
    assert record(llm, state, "Oh, I see. Not at all.").label == "last"
    assert [a["answer"] for a in state["answers"]] == ["A little", "Not at all"]
    assert "corrected_from" not in state["answers"][0]


def test_a_self_correction_inside_the_reply_answers_the_current_item():
    items = ["Was the workshop useful to you?", "Was the room comfortable?",
             "Would you come to another workshop?"]
    llm = Llm([{"kind": "corrects_previous", "option": 2}])
    state = state_at(2, items, ["Yes", "Partly"], FEEDBACK)
    assert record(llm, state, "Sorry, I meant yes.").label == "last"
    assert [a["answer"] for a in state["answers"]] == ["Yes", "Partly", "Yes"]


@pytest.mark.parametrize("said", ["For the previous question I meant quite.",
                                  "for the last one I meant quite",
                                  "Actually, I enjoyed it quite a lot."])
def test_a_reply_that_refers_to_the_previous_item_changes_it(said):
    llm = Llm([{"kind": "corrects_previous", "option": 2}])
    state = state_at(1, ["I enjoyed it", "It was long"], ["A little"])
    assert record(llm, state, said).label == "corrected"
    assert state["answers"][0]["answer"] == "Quite"
    assert state["answers"][0]["corrected_from"] == "A little"


def test_spanish_a_reference_to_the_previous_item_changes_it():
    class Es(Llm):
        language = "Spanish"
    items = ["Me gustó", "Fue largo"]
    llm = Es([{"kind": "corrects_previous", "option": 2}])
    state = state_at(1, items, ["Un poco"], CANTIDAD)
    assert record(llm, state, "En la anterior quería decir bastante.").label == "corrected"
    llm = Es([{"kind": "corrects_previous", "option": 0}])
    state = state_at(1, items, ["Un poco"], CANTIDAD)
    assert record(llm, state, "Ah, vale. Nada.").label == "last"
    assert [a["answer"] for a in state["answers"]] == ["Un poco", "Nada"]


def test_without_a_reference_an_option_this_item_lacks_is_not_recorded():
    llm = Llm([{"kind": "corrects_previous", "option": 2}])
    items = ["I enjoyed it", "It was long"]
    instrument_state = state_at(1, items, ["A little"])
    instrument_state["instrument"]["items"][1]["options"] = [
        {"label": "Yes", "value": 1}, {"label": "No", "value": 0}]
    assert record(llm, instrument_state, "Oh, quite.").label == "other"
    assert len(instrument_state["answers"]) == 1


# ==== THE OPTION WRITTEN BY ITS LABEL (the client and the interpretation server) ====

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
_QUESTIONS = os.path.join(os.path.dirname(__file__), "..", "config", "interpretation",
                          "questions.yaml")


def templates():
    with open(_PROMPTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def model(answers):
    """A real client whose model gives these answers in turn."""
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append({"prompt": prompt, "schema": schema})
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(templates())), calls


def test_the_model_writes_the_option_by_its_label():
    llm, calls = model(['{"kind": "option", "option": "Quite"}'])
    state = state_of()
    assert record(llm, state, "Quite, I'd say.").label == "next"
    assert state["answers"][0]["answer"] == "Quite"
    assert calls[0]["schema"]["properties"]["option"] == {"type": "string"}
    assert "- Quite" in calls[0]["prompt"] and "3. Quite" not in calls[0]["prompt"]


def test_a_label_the_reply_contradicts_still_reads_the_options_again():
    # The local checks work on the option the label names: a dropped modifier is not taken
    llm, _ = model(['{"kind": "option", "option": "A little"}'])
    state = state_of()
    assert record(llm, state, "not very much").label == "ambiguous"
    assert state["answers"] == []


def test_a_change_of_the_previous_answer_is_written_by_the_previous_label():
    llm, calls = model(['{"kind": "corrects_previous", "option": "Quite"}'])
    state = state_at(1, ["I enjoyed it", "It was long"], ["A little"])
    assert record(llm, state, "For the previous one I meant quite.").label == "corrected"
    assert state["answers"][0]["answer"] == "Quite"
    assert "- Quite" in calls[0]["prompt"].split("PREVIOUS ITEM:")[1]


def test_a_change_to_a_label_the_previous_item_lacks_is_not_recorded():
    llm, _ = model(['{"kind": "corrects_previous", "option": "Sometimes"}'])
    state = state_at(1, ["I enjoyed it", "It was long"], ["A little"])
    assert record(llm, state, "For the previous one I meant sometimes.").label == "other"
    assert state["answers"][0]["answer"] == "A little"


def test_the_server_writes_the_same_prompt_and_schema():
    args = {"user_text": "Quite.", "item": "I enjoyed it", "options": AMOUNT,
            "previous": {"item": "It was long", "options": AMOUNT, "answer": "A little"}}
    expected = PromptBuilder(templates()).map_scale_answer(**args)
    decoded = decode_context(json.loads(encode_context("map_scale_answer", "English", args)))
    assert build_prompt("map_scale_answer", templates(), decoded) == expected
    assert expected[1]["properties"]["option"] == {"type": "string"}
    # The question's declaration takes its schema from PromptBuilder: the server reads no field
    with open(_QUESTIONS, encoding="utf-8") as f:
        spec = yaml.safe_load(f)["questions"]["map_scale_answer"]
    assert spec["validate"] == "object" and "fields" not in spec
