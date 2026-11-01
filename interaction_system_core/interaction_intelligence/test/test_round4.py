# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Provisional changes after the third debugging round with real models (2026-11-01, made while
the author was away; each to be confirmed by the author, evaluation/results/
debug_triage_patterns.md):

  - the cancel detector gets the task description and takes a hesitant wish to drop the task
    as cancel;
  - grounded QA: unintelligible speech has its own act ("unclear": a turn without progress);
  - teach-back: a "restatement" with no key point at all is not a failed check;
  - slot filling: a closed slot takes only a value the user's words contain; an unreadable
    correction is read again by the extraction; after a correction the frame is read back as
    recorded;
  - procedure: a reply that reports the step done is "done" even if it also asks something."""

import json
import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers):
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts())), calls


def begin(llm, goal):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def test_the_cancel_detector_knows_the_task_and_confirms_a_hesitant_wish():
    llm, calls = client(['{"request": "none"}'])
    llm.detect_cancel_intent("I need to cancel my card", task="Help with lost bank cards")
    assert 'TASK OF THE CONVERSATION: "Help with lost bank cards"' in calls[-1]
    text = " ".join(prompts()["detect_cancel_intent"].split())
    assert "maybe I should just forget about it" in text and "choose \"cancel\"" in text
    assert '"about": "conversation" | "task"' in text and "always cancel" not in text


def test_unintelligible_speech_is_a_turn_without_progress_in_grounded_qa():
    llm, _ = client([json.dumps({"act": "unclear", "kind": "off_task", "answer": "",
                                 "evidence": []})])
    env, state = begin(llm, {"dialog_mode": "grounded_qa", "task_description": "Aquarium desk",
                             "resources_json": json.dumps([{"name": "information",
                                                            "content": "Open at 10."}])})
    result = get_skill("grounded_qa.answer")(env, state, SkillInput(
        user_text="Table the of green across."))
    assert result.label == "other" and not state["questions"]


def explanation(llm):
    points = json.dumps({"key_points": ["Use the blue counter", "Switch to T"]})
    return begin(llm, {"dialog_mode": "explanation", "task_description": "Hearing loop",
                       "resources_json": json.dumps([{"name": "key points",
                                                      "content": points}])})


def test_a_restatement_with_no_key_point_is_not_a_failed_check():
    nothing = json.dumps({"kind": "restates", "points": [
        {"id": 1, "status": "missing", "said": ""}, {"id": 2, "status": "missing", "said": ""}]})
    llm, _ = client([nothing])
    env, state = explanation(llm)
    assert get_skill("explanation.check")(env, state, SkillInput(
        user_text="Yellow window seven the river.")).label == "other"
    # "I don't know" to the direct question of the fallback stays a failed check
    state["check_mode"] = "targeted"
    assert get_skill("explanation.check")(env, state, SkillInput(
        user_text="I don't know.")).label == "gaps"


def slot_filling(llm, extra_slots=()):
    schema = {"slots": [{"name": "company", "type": "str",
                         "canonical_values": ["Vela Legal", "Norte Logistics"]},
                        *extra_slots]}
    env, state = begin(llm, {"dialog_mode": "slot_filling", "task_description": "Visitor desk",
                             "frame_schema_json": json.dumps(schema)})
    get_skill("slot_filling.prepare")(env, state, SkillInput())
    return env, state


def test_a_closed_slot_takes_only_a_value_the_user_said():
    picked = json.dumps({"act": "values", "extractions": [
        {"slot": "company", "value": "Vela Legal", "source_span": "Yellow window"}]})
    llm, _ = client([picked])
    env, state = slot_filling(llm)
    assert get_skill("slot_filling.update")(env, state, SkillInput(
        user_text="Yellow window seven the river.", number=2)).label == "not_understood"
    llm, _ = client([picked])
    env, state = slot_filling(llm)
    assert get_skill("slot_filling.update")(env, state, SkillInput(
        user_text="I'm visiting vela legal.", number=2)).label == "complete"


def test_an_unreadable_correction_is_read_again_and_the_frame_read_back():
    tickets = {"name": "tickets", "type": "int", "minimum": 1, "maximum": 8}
    llm, calls = client([
        json.dumps({"act": "values", "extractions": [
            {"slot": "company", "value": "Vela Legal", "source_span": "vela legal"},
            {"slot": "tickets", "value": 2, "source_span": "two"}]}),
        json.dumps({"intent": "corrects", "corrections": {"tickets": "}}</think>"}}),
        json.dumps({"act": "values", "extractions": [
            {"slot": "tickets", "value": 3, "source_span": "three"}]}),
        "Vela Legal, 3 tickets: is that right?"])
    env, state = slot_filling(llm, [tickets])
    get_skill("slot_filling.update")(env, state, SkillInput(user_text="Vela Legal, two.",
                                                            number=2))
    label = get_skill("slot_filling.classify_confirmation")(env, state, SkillInput(
        user_text="No, sorry, three tickets, not two.")).label
    assert label == "corrects_applied"
    assert json.loads(json.dumps(state["frame"]))  # serialisable
    reply = get_skill("slot_filling.correction_reply")(env, state, SkillInput()).reply
    assert reply.endswith("Vela Legal, 3 tickets: is that right?")


def test_done_comes_first_in_a_procedure_turn():
    text = prompts()["classify_step_turn"]
    assert "\"done\" even if it also asks" in text


def test_an_ending_with_its_own_reason_keeps_a_standard_outcome():
    from interaction_intelligence.skills import get_skill as skill
    llm, _ = client(["{}"])
    env = SkillEnv(llm=llm, ctx=DialogContext())
    closing = skill("closing")(env, {}, SkillInput(option="off_task"))
    assert closing.reply == llm.phrase("close_off_task")
    result = skill("result")(env, {"mode": "grounded_qa"}, SkillInput(option="off_task"))
    assert result.label == "breakdown" and result.reply.startswith("Off-task")


def test_unintelligible_words_are_not_understood_whatever_the_rest_says():
    llm, _ = client([json.dumps({"sense": "no", "act": "asks", "kind": "off_task",
                                 "answer": "", "evidence": []})])
    out = llm.answer_from_material("Table the of green across.", "Desk", [{"name": "i",
                                   "content": "Open at 10."}], [])
    assert out["act"] == "unclear" and out["kind"] == "other"


def test_a_request_turns_the_acknowledgments_on_or_off():
    from interaction_intelligence.skills.slot_filling import _pop_ack
    on = SkillEnv(llm=None, ctx=DialogContext(), ack_phrases=("Noted.",), ack_enabled=True)
    off = SkillEnv(llm=None, ctx=DialogContext(), ack_phrases=("Noted.",), ack_enabled=False)
    assert _pop_ack(on, {}) == "Noted." and _pop_ack(off, {}) == ""
    assert _pop_ack(on, {"acknowledgments": 0}) == ""
    assert _pop_ack(off, {"acknowledgments": 1}) == "Noted."


def test_only_the_points_not_yet_verified_are_judged():
    # Round 6: with the verified point in the list, the model put "the red flag" in its entry
    first = json.dumps({"kind": "restates", "points": [
        {"id": 1, "status": "correct", "said": ""}, {"id": 2, "status": "missing", "said": ""}]})
    second = json.dumps({"kind": "restates", "points": [
        {"id": 1, "status": "correct", "said": ""}]})
    llm, calls = client([first, "Right. And the other one?", second])
    env, state = explanation(llm)
    assert get_skill("explanation.check")(env, state, SkillInput(
        user_text="The blue counter.")).label == "progress"
    get_skill("explanation.ask_missing")(env, state, SkillInput())
    assert get_skill("explanation.check")(env, state, SkillInput(
        user_text="And switch to T.")).label == "verified"
    assert "Use the blue counter" not in calls[-1] and "Switch to T" in calls[-1]
