# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Grounded QA when the user stops answering (DialogueMode.on_silent_end): a visitor who walks
away after getting their answers has finished, so the robot says the normal closing and the
outcome is completed; without any question within the task answered or abstained, the ending is
user_silent (E2 pre-run verification: the visitor got 90 s of prompts and a failed outcome)."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill

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


def silent_end(env, state):
    return get_skill("silent_end")(env, state, SkillInput())


@LANGUAGES
def test_after_an_answer_the_silence_closes_normally(language):
    llm, _ = client(ANSWERED, language)
    env, state = qa(llm)
    say(env, state, "When does it open?")
    result = silent_end(env, state)
    assert (result.label, result.reply) == ("closed", llm.phrase("qa_bye"))
    assert get_mode("grounded_qa").succeeded(state)


@LANGUAGES
def test_after_an_abstention_the_silence_closes_normally(language):
    llm, _ = client([SALAD, INTELLIGIBLE], language)
    env, state = qa(llm)
    say(env, state, "Is there a cloakroom?")
    assert silent_end(env, state).label == "closed"


@LANGUAGES
def test_without_a_question_the_silence_ends_as_user_silent(language):
    llm, _ = client([turn("reacts")], language)
    env, state = qa(llm)
    assert silent_end(env, state).label == "none"
    say(env, state, "Okay.")
    assert silent_end(env, state).label == "none"
    assert not get_mode("grounded_qa").succeeded(state)


def test_a_declined_request_alone_is_not_enough():
    llm, _ = client([turn("asks", "off_task")])
    env, state = qa(llm)
    assert say(env, state, "Can you call me a taxi?").label == "declined"
    assert silent_end(env, state).label == "none"
