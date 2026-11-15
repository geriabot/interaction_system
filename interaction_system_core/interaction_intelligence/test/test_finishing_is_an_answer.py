# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Where saying "I've finished" is a normal reply to the strategy's element
(DialogueMode.finishing_is_an_answer), the cancel detector's "finished" goes to the strategy, not to
the question whether to stop: in a procedure "I'm relaxed enough, I want to skip it" on a step is
the step's reply (in the E1 validation it was asked about, the simulated user left and the dialogue
ended cancelled). In a quiz or a questionnaire "I'm done, that's enough" is no answer: it is still
asked about. Answering "I'm done" to the question itself still agrees to stop."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
LANGUAGES = pytest.mark.parametrize("language,line", [
    ("English", "Okay, I am relaxed enough, I want to skip it."),
    ("Spanish", "Ya estoy, me lo salto."),
])
STEPS = [{"name": "steps", "content": json.dumps(
    [{"step": "Breathe in for four seconds"}, {"step": "Repeat the cycle three times"}])}]
QUIZ = [{"name": "quiz", "content": json.dumps([{"question": "Two plus two?", "answer": "4"}])}]


def client(detect, reply="{\"answer\": \"no\"}", language="English"):
    """A real client whose model reads a request to stop as `detect` and the reply to the
    question whether to stop as `reply`; any other prompt gets a plain line."""
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    pb = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"],
                       language=language)

    def infer(session, prompt, max_tokens, schema):
        if "Decide whether the user wants to STOP or CANCEL" in prompt:
            return detect
        if "The robot asked the user whether they want to end this conversation" in prompt:
            return reply
        return "[line]"

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language)


def begin(llm, mode, resources):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": mode, "task_description": "Task", "resources_json": json.dumps(resources)}))
    return env, state


def test_which_strategies_take_finishing_as_an_answer():
    takes = {"procedure": True, "explanation": True, "roleplay": True, "slot_filling": True,
             "quiz": False, "questionnaire": False}
    for mode, value in takes.items():
        assert get_mode(mode).finishing_is_an_answer is value, mode


@LANGUAGES
def test_in_a_procedure_finishing_goes_to_the_step(language, line):
    llm = client('{"about": "conversation", "request": "finished"}', language=language)
    env, state = begin(llm, "procedure", STEPS)
    result = get_skill("cancel_check")(env, state, SkillInput(user_text=line, option="Step 2"))
    assert result.label == "none" and not state.get("awaiting_cancel")


@LANGUAGES
def test_in_a_quiz_finishing_is_still_asked_about(language, line):
    llm = client('{"about": "conversation", "request": "finished"}', language=language)
    env, state = begin(llm, "quiz", QUIZ)
    result = get_skill("cancel_check")(env, state, SkillInput(user_text="I'm done, enough.",
                                                              option="Two plus two?"))
    assert result.label == "ask" and state["awaiting_cancel"]


@LANGUAGES
def test_in_a_procedure_im_done_to_the_question_agrees_to_stop(language, line):
    llm = client('{"about": "conversation", "request": "cancel"}', '{"answer": "finished"}',
                 language)
    env, state = begin(llm, "procedure", STEPS)
    cancel_check = get_skill("cancel_check")
    assert cancel_check(env, state, SkillInput(user_text="I want to stop.", option="x")).label \
        == "ask"
    result = cancel_check(env, state, SkillInput(user_text="I'm done for today.",
                                                 option=state["cancel_question"]))
    assert result.label == "confirmed" and state["user_cancelled"]
