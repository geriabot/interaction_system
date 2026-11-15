# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Grounded QA: a word salad read as a question whose answer is not in the material got the
abstention, which counts as progress, so a person who could not make themselves understood
never reached the breakdown (E2 pre-run verification, qwen3:8b without reasoning). A turn that
would get an abstention is first checked to be intelligible (check_intelligible); a turn not
understood asks to repeat, and from the second in a row the robot says what can be asked
(qa_topics, generated once); a reaction is neutral for the count of turns without progress."""

import xml.etree.ElementTree as ET

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
MATERIAL = [{"name": "information",
             "content": "The aquarium opens from 10:00 to 19:00. The sea lion show is at 12:00."}]
ANSWER = "The aquarium opens from 10:00 to 19:00."

LANGUAGES = pytest.mark.parametrize("language", ["English", "Spanish"])


def templates():
    with open(_PROMPTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers, language="English"):
    """A real client whose model gives these answers in turn (None: no answer)."""
    pb = PromptBuilder(templates(), language=language)
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append({"prompt": prompt, "schema": schema})
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language), calls


def qa(llm):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": "grounded_qa", "task_description": "Aquarium desk",
        "resources_json": json.dumps(MATERIAL)}))
    return env, state


def turn(act, kind="other", answer=""):
    return json.dumps({"sense": "yes", "act": act, "kind": kind, "answer": answer,
                       "evidence": [MATERIAL[0]["content"]] if answer else []})


ANSWERED = [turn("asks", "answerable", ANSWER), '{"supported": true, "unsupported": []}']
SALAD = turn("asks", "not_in_material")
NOT_INTELLIGIBLE = '{"intelligible": false}'
INTELLIGIBLE = '{"intelligible": true}'
TOPICS = "You can ask me about the opening hours or the sea lion show."


def say(env, state, text):
    return get_skill("grounded_qa.answer")(env, state, SkillInput(user_text=text))


_TREE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "interaction_system_behaviors",
                     "behavior_tree_xml", "grounded_qa.xml")


@LANGUAGES
def test_a_word_salad_that_would_be_abstained_is_not_understood(language):
    llm, calls = client([SALAD, NOT_INTELLIGIBLE], language)
    env, state = qa(llm)
    result = say(env, state, "Lamp beneath forty the singing.")
    assert (result.label, result.reply) == ("other", llm.phrase("unclear"))
    assert state["questions"] == [] and len(calls) == 2
    assert "intelligible" in str(calls[1]["schema"])


@LANGUAGES
def test_an_intelligible_question_not_in_the_material_is_abstained(language):
    llm, calls = client([SALAD, INTELLIGIBLE], language)
    env, state = qa(llm)
    result = say(env, state, "Is there a cloakroom?")
    assert result.label == "abstained" and state["questions"][0]["resolution"] == "abstained"


@LANGUAGES
@pytest.mark.parametrize("failure", [None, "I cannot tell.", "{}"])
def test_a_failed_check_keeps_the_abstention(language, failure):
    llm, calls = client([SALAD, failure], language)
    env, state = qa(llm)
    assert say(env, state, "Is there a cloakroom?").label == "abstained"
    assert len(calls) == 1 + LLMDialogClient.MAX_RETRIES


@LANGUAGES
def test_the_second_turn_not_understood_says_what_can_be_asked(language):
    llm, calls = client([turn("unclear"), turn("unclear"), TOPICS, turn("unclear")], language)
    env, state = qa(llm)
    assert say(env, state, "Hmm the across.").reply == llm.phrase("unclear")
    second = say(env, state, "Blue forty lamp.")
    assert second.label == "other"
    assert second.reply == f"{llm.phrase('qa_unclear_again')} {TOPICS}"
    third = say(env, state, "Singing beneath.")
    assert third.reply == second.reply and len(calls) == 4  # the topics are generated once
    assert "two to four topics" in " ".join(calls[2]["prompt"].split())


@LANGUAGES
def test_failed_topics_give_the_fixed_phrase(language):
    llm, calls = client([turn("unclear"), turn("unclear"), None], language)
    env, state = qa(llm)
    say(env, state, "Hmm the across.")
    result = say(env, state, "Blue forty lamp.")
    assert result.reply == f"{llm.phrase('qa_unclear_again')} {llm.phrase('qa_topics')}"


@LANGUAGES
def test_a_turn_understood_starts_the_run_again(language):
    llm, calls = client([turn("unclear")] + ANSWERED + [turn("unclear")], language)
    env, state = qa(llm)
    say(env, state, "Hmm the across.")
    assert say(env, state, "When does it open?").label == "answered"
    assert say(env, state, "Blue forty lamp.").reply == llm.phrase("unclear")


def test_the_phrases_exist_in_every_language():
    for language in ("English", "Spanish"):
        llm, _ = client([None], language)
        assert llm.phrase("qa_unclear_again") and llm.phrase("qa_topics")


def test_a_reaction_is_neutral_for_the_count_of_turns_without_progress():
    tree = ET.parse(_TREE).getroot()
    turn_tree = next(t for t in tree.iter("BehaviorTree") if t.get("ID") == "StrategyTurn")
    switch = turn_tree.find("Sequence/Switch5")
    cases = [switch.get(f"case_{i}") for i in range(1, 6)]
    comment = list(switch)[cases.index("comment")]
    assert comment.tag == "Script" and comment.get("code") == "progress := false"
