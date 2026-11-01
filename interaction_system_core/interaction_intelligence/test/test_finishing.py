# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Finishing is not cancelling, and grounded QA answers only what was asked (decided by the
author, 2026-10-31, after the second debugging round with real models):

  - the cancel detector chooses among cancel, finished and none; only cancel is a cancel
    request ("finished" goes to the strategy like any other turn); the former boolean answer is
    still read;
  - the reply to "do you want to stop?" may be "finished": in a strategy whose end the user
    decides (grounded QA), its own closing and a normal end; in the others, the strategy takes
    the turn;
  - a QA turn first says what the person does: asks, finished or reacts; only "asks" can be
    answered from the material, whatever the rest of the model's output says;
  - after a denied cancellation the robot resumes with its last line, without the "let's
    continue" of an earlier denial (it used to pile up: one more per denial)."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.utils import strip_leading

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
MATERIAL = [{"name": "information", "content": "Tickets cost 18 euros. The play starts at 20:00."}]
DRINKS = {"slots": [{"name": "drink", "type": "string", "required": True,
                     "description": "The drink"}]}


LANGUAGES = pytest.mark.parametrize("language", ["English", "Spanish"])


def client(answers, language="English"):
    """A real client whose model gives these answers in turn."""
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    pb = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"],
                       language=language)
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language), calls


def begin(llm, goal):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def qa(llm):
    return begin(llm, {"dialog_mode": "grounded_qa", "task_description": "Theatre desk",
                       "resources_json": json.dumps(MATERIAL)})


# ==== THE CANCEL DETECTOR: CANCEL, FINISHED OR NONE ====


@pytest.mark.parametrize("answer,cancel", [
    ('{"request": "cancel"}', True), ('{"request": "finished"}', False),
    ('{"request": "none"}', False), ('{"cancel": true}', True), ('{"cancel": false}', False),
])
def test_only_a_cancel_intent_is_a_cancel_request(answer, cancel):
    c, _ = client([answer])
    assert c.detect_cancel_intent("No, I have no more questions.") is cancel


def test_the_detector_offers_finished_as_its_own_answer():
    c, calls = client(['{"request": "none"}'])
    c.detect_cancel_intent("That's all")
    assert '"finished"' in calls[0] and "never \"cancel\"" in calls[0]


@LANGUAGES
def test_finished_does_not_ask_whether_to_stop(language):
    env, state = qa(client(['{"request": "finished"}'], language)[0])
    result = get_skill("cancel_check")(env, state, SkillInput(user_text="That's all", option="q?"))
    assert result.label == "none" and not state["awaiting_cancel"]


# ==== THE REPLY TO "DO YOU WANT TO STOP?": FINISHED ====


def test_the_reply_may_be_finished():
    c, _ = client(['{"answer": "finished"}'])
    assert c.classify_cancel_reply("No, I have no more questions.") == "finished"


def ask_then_reply(env, state):
    ask = get_skill("cancel_check")(env, state, SkillInput(user_text="I think that's all",
                                                            option="Anything else?"))
    assert ask.label == "ask"
    return get_skill("cancel_check")(env, state,
                                     SkillInput(user_text="No, I have no more questions."))


@LANGUAGES
def test_in_grounded_qa_finished_is_the_normal_end(language):
    llm, _ = client(['{"request": "cancel"}', "Do you want to stop?", '{"answer": "finished"}'],
                    language)
    env, state = qa(llm)
    result = ask_then_reply(env, state)
    assert (result.label, result.reply) == ("finished", llm.phrase("qa_bye"))
    assert state["completed"] and not state["user_cancelled"]
    assert get_skill("result")(env, state, SkillInput()).label == "completed"


@LANGUAGES
def test_where_the_user_does_not_decide_the_end_the_robot_resumes(language):
    llm, _ = client(['{"request": "cancel"}', "Do you want to stop?", '{"answer": "finished"}'],
                    language)
    env, state = begin(llm, {"dialog_mode": "slot_filling", "task_description": "Drink order",
                             "frame_schema_json": json.dumps(DRINKS)})
    result = ask_then_reply(env, state)
    assert result.label == "denied" and not state["user_cancelled"]


@LANGUAGES
def test_only_a_yes_stops_any_other_answer_resumes(language):
    # "No, I want the plumber to come" read as "other" reached the strategy and was recorded as
    # a value (round 6): the answer to "do you want to stop?" never reaches the strategy
    llm, _ = client(['{"request": "cancel"}', "Do you want to stop?", '{"answer": "other"}'],
                    language)
    env, state = begin(llm, {"dialog_mode": "slot_filling", "task_description": "Drink order",
                             "frame_schema_json": json.dumps(DRINKS)})
    result = ask_then_reply(env, state)
    assert result.label == "denied" and not state["user_cancelled"]
    assert result.reply.startswith(llm.phrase("cancel_no"))


# ==== A DENIED CANCELLATION RESUMES WITHOUT PILING UP "LET'S CONTINUE" ====


@LANGUAGES
def test_the_same_words_are_not_asked_about_again_once_denied(language):
    # A safety net for a false alarm the detector did not catch ("Done, I've pressed cancel"
    # looped: author, 2026-11-01)
    llm, calls = client(['{"request": "cancel"}', "Do you want to stop?", '{"answer": "no"}'],
                        language)
    env, state = qa(llm)
    cancel_check = get_skill("cancel_check")
    said = "Done, I've pressed cancel."
    assert cancel_check(env, state, SkillInput(user_text=said, option="x")).label == "ask"
    denied = cancel_check(env, state, SkillInput(user_text="No, go on."))
    assert denied.label == "denied" and denied.reply.startswith(llm.phrase("cancel_no"))
    n = len(calls)
    assert cancel_check(env, state, SkillInput(user_text=said, option="x")).label == "none"
    assert len(calls) == n  # no model call for it


@LANGUAGES
def test_words_about_the_task_are_never_a_cancel_request(language):
    # The detector first says what the words are about; about the task -> no question
    llm, _ = client(['{"about": "task", "request": "cancel"}'], language)
    env, state = qa(llm)
    assert get_skill("cancel_check")(env, state, SkillInput(
        user_text="I need to cancel my card.", option="x")).label == "none"


def test_the_resume_line_does_not_pile_up():
    assert strip_leading("OK, let's continue. OK, let's continue. Anything else?",
                         "OK, let's continue.") == "Anything else?"


def test_strip_leading():
    assert strip_leading("OK, let's continue. OK, let's continue. Anything else?",
                         "OK, let's continue.") == "Anything else?"
    assert strip_leading("Anything else?", "OK, let's continue.") == "Anything else?"
    assert strip_leading("", "OK") == "" and strip_leading("Anything?", "") == "Anything?"


# ==== GROUNDED QA: WHAT THE PERSON DOES COMES FIRST ====


def answer(model_answer):
    c, _ = client([json.dumps(model_answer)])
    return c.answer_from_material("x", "Theatre desk", MATERIAL, [])


def test_finished_is_done_and_reacts_is_comment_whatever_the_kind():
    said = {"kind": "answerable", "answer": "Tickets cost 18 euros.", "evidence": ["x"]}
    assert answer({"act": "finished", **said}) == {
        "act": "finished", "kind": "done", "answer": "", "evidence": []}
    assert answer({"act": "reacts", **said}) == {
        "act": "reacts", "kind": "comment", "answer": "", "evidence": []}


def test_only_asks_can_be_answered():
    got = answer({"act": "asks", "kind": "answerable", "answer": "Tickets cost 18 euros.",
                  "evidence": ["Tickets cost 18 euros."]})
    assert (got["act"], got["kind"], got["answer"]) == ("asks", "answerable",
                                                         "Tickets cost 18 euros.")


def test_without_a_valid_act_the_kind_decides_as_before():
    assert answer({"kind": "done", "answer": "", "evidence": []})["kind"] == "done"
    assert answer({"act": "maybe", "kind": "comment", "answer": "", "evidence": []})[
        "kind"] == "comment"


@LANGUAGES
def test_a_reaction_is_not_answered_by_the_skill(language):
    llm, calls = client([json.dumps({"act": "reacts", "kind": "answerable",
                                     "answer": "Tickets cost 18 euros.", "evidence": []})],
                        language)
    env, state = qa(llm)
    result = get_skill("grounded_qa.answer")(env, state, SkillInput(user_text="Thanks, great."))
    assert (result.label, result.reply) == ("comment", llm.phrase("qa_more"))
    assert state["questions"] == [] and len(calls) == 1  # no verification asked either


@LANGUAGES
def test_finished_closes_with_success(language):
    # The closing is verified before the dialogue ends (provisional, 2026-11-01)
    llm, _ = client([json.dumps({"act": "finished", "kind": "other", "answer": "",
                                 "evidence": []}), '{"finished": true}'], language)
    env, state = qa(llm)
    result = get_skill("grounded_qa.answer")(env, state, SkillInput(user_text="No, thanks."))
    assert (result.label, result.reply) == ("done", llm.phrase("qa_bye"))
    assert get_skill("result")(env, state, SkillInput()).label == "completed"


@LANGUAGES
def test_an_unverified_closing_does_not_end_the_dialogue(language):
    # Round 3: Llama took the first question for "finished"; the verification says no
    llm, calls = client([json.dumps({"act": "finished", "kind": "other", "answer": "",
                                     "evidence": []}), '{"finished": false}'], language)
    env, state = qa(llm)
    result = get_skill("grounded_qa.answer")(env, state, SkillInput(
        user_text="Should I invest my savings in shares?"))
    assert (result.label, result.reply) == ("other", llm.phrase("unclear"))
    assert not state.get("completed") and "Should I invest" in calls[-1]


def test_the_qa_prompt_asks_what_the_person_does_first():
    c, calls = client([json.dumps({"act": "asks", "kind": "other", "answer": "",
                                   "evidence": []})])
    c.answer_from_material("x", "Theatre desk", MATERIAL, [])
    prompt = " ".join(calls[0].split())
    assert prompt.index('"act"') < prompt.index('decide "kind"')
    for example in ("I wanted to know the price", "No, but what time does it close?",
                    "I have no more questions", "Thanks, that was helpful", "30 euros, perfect"):
        assert example in prompt
    assert 'a "no" with thanks' not in prompt
