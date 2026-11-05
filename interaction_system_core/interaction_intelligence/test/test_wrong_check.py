# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A wrong check of the last answer in grounded QA (detect_wrong_check).

A person who repeats back a value of the robot's last answer with another value has
misunderstood it. answer_from_material reads a plain wrong check as a request to repeat, but a
wrong check followed by a comment as a reaction, so the robot only invited more questions and
the misunderstanding passed. A question of its own, asked only for a turn read as a reaction
after an answer from the material, turns such a turn into a request to repeat: the robot says
the answer again. A failed question leaves the turn a reaction, as without it."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.interpretation_context import (QUESTIONS, build_prompt,
                                                             decode_context, encode_context)
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
_QUESTIONS = os.path.join(os.path.dirname(__file__), "..", "config", "interpretation",
                          "questions.yaml")
MATERIAL = [{"name": "information",
             "content": "The guided tour costs 12 euros and leaves at 16:30 from the main hall."}]
ANSWER = "The guided tour leaves at 16:30 from the main hall."

LANGUAGES = pytest.mark.parametrize("language", ["English", "Spanish"])


def templates():
    with open(_PROMPTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers, language="English"):
    """A real client whose model gives these answers in turn (None: no answer)."""
    pb = PromptBuilder(templates(), language=language)
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append({"prompt": prompt, "max_tokens": max_tokens, "schema": schema})
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language), calls


def qa(llm):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": "grounded_qa", "task_description": "Museum desk",
        "resources_json": json.dumps(MATERIAL)}))
    return env, state


def turn(act, kind="other", answer=""):
    return json.dumps({"sense": "yes", "act": act, "kind": kind, "answer": answer,
                       "evidence": [MATERIAL[0]["content"]] if answer else []})


ANSWERED = [turn("asks", "answerable", ANSWER), '{"supported": true, "unsupported": []}']


def say(env, state, text):
    return get_skill("grounded_qa.answer")(env, state, SkillInput(user_text=text))


# ==== THE CLIENT READS THE ANSWER ====


@pytest.mark.parametrize("raw,expected", [
    ('{"wrong_check": true}', True),
    ('{"wrong_check": false}', False),
    ('```json\n{"wrong_check": true}\n```', True),
])
def test_the_client_reads_the_answer(raw, expected):
    c, calls = client([raw])
    assert c.detect_wrong_check("Six thirty? Fine.", ANSWER) is expected
    assert len(calls) == 1 and calls[0]["max_tokens"] == 20


@pytest.mark.parametrize("raw", [None, "", "Yes, it is a wrong check.", "{}",
                                 '{"wrong_check": "maybe"}', "[true]"])
def test_a_failed_question_is_none_after_its_retries(raw):
    c, calls = client([raw])
    assert c.detect_wrong_check("Six thirty? Fine.", ANSWER) is None
    assert len(calls) == LLMDialogClient.MAX_RETRIES


def test_a_failed_first_answer_is_asked_again():
    c, calls = client(["not JSON", '{"wrong_check": true}'])
    assert c.detect_wrong_check("Six thirty? Fine.", ANSWER) is True
    assert len(calls) == 2


# ==== THE PROMPT ====


def test_the_prompt_compares_the_values_of_the_reply_and_the_answer():
    c, calls = client(['{"wrong_check": false}'])
    c.detect_wrong_check("Six thirty? Fine.", ANSWER)
    prompt = " ".join(calls[0]["prompt"].split())
    assert f'ANSWER: "{ANSWER}"' in prompt and 'REPLY: "Six thirty? Fine."' in prompt
    assert "with a DIFFERENT value" in prompt
    # A comment around the value does not change the decision: the case the main question misses
    assert "A comment before or after the repeated value" in prompt
    # The same value in another form is not a wrong check (12-hour and 24-hour times)
    assert "12-hour or a 24-hour time" in prompt
    assert calls[0]["schema"] == {"type": "object",
                                  "properties": {"wrong_check": {"type": "boolean"}},
                                  "required": ["wrong_check"]}
    assert prompt.rstrip().endswith('{"wrong_check": true | false}')


def test_the_prompt_is_an_understanding_question_without_the_case_it_was_made_for():
    tpl = templates()["detect_wrong_check"]
    # Not a spoken line: no rule on how a line addresses the person
    assert "{address}" not in tpl and "{language}" not in tpl
    # Its examples are not the debugging case it answers (a check of 17:00 as seven o'clock)
    for word in ("Seven o'clock", "17:00", "that's late", "Oh dear"):
        assert word not in tpl


@LANGUAGES
def test_the_server_writes_the_same_prompt(language):
    args = {"user_text": "Six thirty? Fine.", "answer": ANSWER}
    expected = PromptBuilder(templates(), language=language).detect_wrong_check(**args)
    decoded = decode_context(json.loads(encode_context("detect_wrong_check", language, args)))
    assert build_prompt("detect_wrong_check", templates(), decoded) == expected
    assert QUESTIONS["detect_wrong_check"] == ("detect_wrong_check", "detect_wrong_check",
                                               "json")
    with open(_QUESTIONS, encoding="utf-8") as f:
        spec = yaml.safe_load(f)["questions"]["detect_wrong_check"]
    assert spec == {"class": "DialogueQuestion", "template": "detect_wrong_check",
                    "max_tokens": 20, "validate": "object"}


# ==== THE SKILL ====


@LANGUAGES
def test_a_wrong_check_after_an_answer_is_answered_again(language):
    llm, calls = client(ANSWERED + [turn("reacts"), '{"wrong_check": true}'], language)
    env, state = qa(llm)
    say(env, state, "When does the tour leave?")
    result = say(env, state, "Half past six? Lovely, thanks.")
    assert result.label == "repeat"
    assert result.reply == f"{ANSWER} {llm.phrase('qa_more')}"
    assert len(calls) == 4
    # The question asked compares the turn with the answer given
    assert f'ANSWER: "{ANSWER}"' in calls[3]["prompt"]
    assert 'REPLY: "Half past six? Lovely, thanks."' in calls[3]["prompt"]
    # Not recorded as a question, as a request to repeat is not
    assert [q["resolution"] for q in state["questions"]] == ["answered"]


@LANGUAGES
def test_a_check_with_the_same_value_stays_a_comment(language):
    llm, calls = client(ANSWERED + [turn("reacts"), '{"wrong_check": false}'], language)
    env, state = qa(llm)
    say(env, state, "When does the tour leave?")
    result = say(env, state, "Half past four, lovely.")
    assert (result.label, result.reply) == ("comment", llm.phrase("qa_more"))
    assert len(calls) == 4


@LANGUAGES
@pytest.mark.parametrize("failure", [None, "I cannot tell.", "{}"])
def test_a_failed_question_leaves_the_turn_a_comment(language, failure):
    llm, calls = client(ANSWERED + [turn("reacts"), failure], language)
    env, state = qa(llm)
    say(env, state, "When does the tour leave?")
    result = say(env, state, "Half past six? Lovely, thanks.")
    assert (result.label, result.reply) == ("comment", llm.phrase("qa_more"))
    assert len(calls) == 3 + LLMDialogClient.MAX_RETRIES


@LANGUAGES
def test_without_a_last_answer_the_question_is_not_asked(language):
    llm, calls = client([turn("reacts")], language)
    env, state = qa(llm)
    result = say(env, state, "Half past six? Lovely, thanks.")
    assert (result.label, result.reply) == ("comment", llm.phrase("qa_more"))
    assert len(calls) == 1


@LANGUAGES
def test_after_an_abstention_the_question_is_not_asked(language):
    # An abstention gives no value to check
    llm, calls = client([turn("asks", "not_in_material"), turn("reacts")], language)
    env, state = qa(llm)
    assert say(env, state, "Is there a cloakroom?").label == "abstained"
    result = say(env, state, "Oh, what a shame.")
    assert result.label == "comment" and len(calls) == 2


@LANGUAGES
def test_only_a_reaction_is_checked(language):
    # A request, a closing or a turn not understood never asks the question
    llm, calls = client(ANSWERED + [turn("asks", "repeat"), turn("unclear"),
                                    turn("finished"), '{"finished": true}'], language)
    env, state = qa(llm)
    say(env, state, "When does the tour leave?")
    assert say(env, state, "Sorry, say that again?").label == "repeat"
    assert say(env, state, "Blue the of across").label == "other"
    assert say(env, state, "That's all, goodbye.").label == "done"
    assert not any("wrong_check" in str(c["schema"]) for c in calls)
