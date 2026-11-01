# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The injector of model failures (llm_faults.py), without a running system: the plan of a
scenario armed turn by turn, the switch, the failing call to the model, the real client wrapped
(install) and the scripted LLM behind its proxy (make_scripted). The dialogues with injected
failures are in test_model_failures.py."""

import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from interaction_sim import llm_faults
from interaction_sim.harness import ScriptedUser
from interaction_sim.llm_faults import (
    FaultPlan, FaultSwitch, FaultyClient, InjectedFault, LlmFault, faulty_infer,
    validate_faults,
)

PROMPTS = (Path(__file__).resolve().parents[4] / "interaction_system_core"
           / "interaction_intelligence" / "config" / "prompts.yaml")


@pytest.fixture
def switch_path(tmp_path):
    return str(tmp_path / "faults.json")


@pytest.fixture
def templates(monkeypatch):
    """The prompt templates of the repository (not of an installed workspace)."""
    with open(PROMPTS, encoding="utf-8") as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]
    monkeypatch.setattr(llm_faults, "_templates", dict(prompts))
    return prompts


# ==== THE PLAN ====


def test_a_fault_covers_its_turns():
    one = LlmFault(turn=2, kind="error")
    assert [t for t in range(6) if one.covers(t)] == [2]
    three = LlmFault(turn=1, kind="invalid", turns=3)
    assert [t for t in range(6) if three.covers(t)] == [1, 2, 3]
    sustained = LlmFault(turn=2, kind="error", turns=0)
    assert [t for t in range(6) if sustained.covers(t)] == [2, 3, 4, 5]
    assert LlmFault(turn=0, kind="hang", questions="get_opening").questions == ["get_opening"]


@pytest.mark.parametrize("bad", [
    {"turn": 1, "kind": "slow"}, {"turn": -1, "kind": "error"},
    {"turn": 1, "kind": "error", "turns": -2}, {"turn": 1, "kind": "hang", "hang_s": 0},
    {"turn": 1, "kind": "error", "questions": [""]},
])
def test_malformed_faults_are_refused(bad):
    with pytest.raises((ValueError, TypeError)):
        validate_faults([bad])


def test_unknown_questions_are_refused_when_the_client_is_importable():
    pytest.importorskip("interaction_intelligence.llm_client")
    assert validate_faults([{"turn": 1, "kind": "error",
                             "questions": ["detect_cancel_intent", "extract_slots"]}])
    with pytest.raises(ValueError, match="detect_cancel"):
        validate_faults([{"turn": 1, "kind": "error", "questions": ["detect_cancel"]}])


def test_the_plan_arms_each_user_turn_before_the_line_is_delivered(switch_path):
    plan = FaultPlan([{"turn": 0, "kind": "error", "questions": ["get_opening"]},
                      {"turn": 2, "kind": "invalid", "turns": 2}], switch_path)
    seen = []

    def policy(robot, transcript):
        seen.append(json.loads(Path(switch_path).read_text()))
        return None

    plan.start()
    first = json.loads(Path(switch_path).read_text())
    assert first["turn"] == 0 and [f["i"] for f in first["faults"]] == [0]
    user = plan.user(policy)
    for _ in range(4):
        user("robot line", [])
    assert [s["turn"] for s in seen] == [1, 2, 3, 4]
    assert [[f["i"] for f in s["faults"]] for s in seen] == [[], [1], [1], []]
    assert all(s["seq"] == plan.seq for s in seen)
    assert plan.finish() == []
    assert json.loads(Path(switch_path).read_text())["faults"] == []


def test_the_plan_wraps_any_user_policy(switch_path):
    plan = FaultPlan([], switch_path)
    plan.start()
    user = plan.user(ScriptedUser(["a", None]))
    assert user("hi", []).text == "a"
    assert user("hi", []).text is None


def test_finish_returns_the_failed_calls_of_this_dialogue_only(switch_path):
    switch = FaultSwitch(switch_path)
    plan = FaultPlan([{"turn": 1, "kind": "error"}], switch_path)
    plan.start()
    plan.user(lambda r, t: None)("x", [])
    fault, state = switch.active(["detect_cancel_intent"], llm_faults.KINDS)
    switch.record(state, fault, "detect_cancel_intent")
    switch.record({"seq": "other dialogue", "turn": 1}, fault, "verify_answer")
    applied = plan.finish()
    assert [(a["fault"], a["turn"], a["question"], a["kind"]) for a in applied] == [
        (0, 1, "detect_cancel_intent", "error")]


# ==== THE SWITCH AND THE FAILING CALL ====


def armed(switch_path, *faults, turn=1):
    plan = FaultPlan(list(faults), switch_path)
    plan.start()
    for _ in range(turn):
        plan.user(lambda r, t: None)("x", [])
    return plan, FaultSwitch(switch_path)


def test_the_switch_matches_the_questions_and_the_kinds(switch_path):
    _, switch = armed(switch_path, {"turn": 1, "kind": "error", "questions": ["verify_answer"]},
                      {"turn": 1, "kind": "crash", "questions": ["classify_step_turn"]})
    assert switch.active(["detect_cancel_intent"], llm_faults.KINDS) is None
    assert switch.active(["verify_answer"], ("error",))[0]["i"] == 0
    assert switch.active(["verify_answer"], ("crash",)) is None
    assert switch.active(["x", "classify_step_turn"], ("crash",))[0]["i"] == 1
    # A missing or broken switch: no fault
    assert FaultSwitch(switch_path + ".missing").active(["x"], llm_faults.KINDS) is None


def model(calls):
    def infer(sid, prompt, max_tokens, schema):
        calls.append(prompt)
        return '{"ok": true}'
    return infer


def test_without_a_fault_the_call_goes_to_the_model(switch_path):
    _, switch = armed(switch_path, {"turn": 2, "kind": "error"})
    calls = []
    assert faulty_infer(model(calls), switch)("nlu", "p", 10, {}) == '{"ok": true}'
    assert calls == ["p"]


def test_error_and_invalid_replace_the_model(switch_path):
    plan, switch = armed(switch_path,
                         {"turn": 1, "kind": "error", "questions": ["detect_cancel_intent"]},
                         {"turn": 1, "kind": "invalid", "questions": ["classify_cancel_reply"]},
                         {"turn": 1, "kind": "invalid", "questions": ["verify_answer"],
                          "answer": "{}"})
    calls = []
    infer = faulty_infer(model(calls), switch)

    def _call_nlu(schema):  # as the client's: the question is the function that called it
        return infer("nlu", "p", 10, schema)

    def detect_cancel_intent():
        return _call_nlu({"type": "object"})

    def classify_cancel_reply(schema):
        return _call_nlu(schema)

    def verify_answer():
        return _call_nlu({"type": "object"})

    with pytest.raises(InjectedFault):
        detect_cancel_intent()
    assert classify_cancel_reply({"type": "object"}) == llm_faults.INVALID_UNDERSTANDING
    assert classify_cancel_reply(None) == llm_faults.INVALID_GENERATION
    assert verify_answer() == "{}"
    assert calls == []
    assert [a["question"] for a in plan.finish()] == [
        "detect_cancel_intent", "classify_cancel_reply", "classify_cancel_reply", "verify_answer"]


def test_a_hang_returns_nothing_after_hang_s_or_when_the_harness_moves_on(switch_path):
    plan, switch = armed(switch_path, {"turn": 1, "kind": "hang", "hang_s": 0.3})
    infer = faulty_infer(model([]), switch)
    t0 = time.monotonic()
    assert infer("nlu", "p", 10, None) is None
    assert 0.25 <= time.monotonic() - t0 < 2.0
    plan, switch = armed(switch_path, {"turn": 1, "kind": "hang", "hang_s": 60})
    threading.Timer(0.3, lambda: plan.user(lambda r, t: None)("x", [])).start()
    t0 = time.monotonic()
    assert infer("nlu", "p", 10, None) is None
    assert time.monotonic() - t0 < 5.0


def test_a_hang_ends_when_the_client_stops_waiting(switch_path):
    # Without hang_s, the model never answers: the client's wait for the call ends it
    plan, switch = armed(switch_path, {"turn": 1, "kind": "hang"})
    limits = type("Limits", (), {"wait": 0.3})()
    infer = faulty_infer(model([]), switch, lambda: limits)
    t0 = time.monotonic()
    assert infer("nlu", "p", 10, None) is None
    assert 0.25 <= time.monotonic() - t0 < 2.0
    # hang_s shorter than the client's wait ends it first
    plan, switch = armed(switch_path, {"turn": 1, "kind": "hang", "hang_s": 0.2})
    limits.wait = 30.0
    t0 = time.monotonic()
    assert faulty_infer(model([]), switch, lambda: limits)("nlu", "p", 10, None) is None
    assert time.monotonic() - t0 < 2.0


# ==== THE REAL CLIENT, WRAPPED ====


@pytest.fixture
def client(switch_path, templates):
    llm_client = pytest.importorskip("interaction_intelligence.llm_client")
    from interaction_intelligence.prompt_builder import PromptBuilder

    assert llm_faults.install(switch_path)
    calls = []

    def infer(sid, prompt, max_tokens, schema):
        calls.append(sid)
        if schema is None:
            return "A generated line."
        return json.dumps({"cancel": True, "answer": "yes", "supported": True,
                           "kind": "answer", "correct": True, "feedback": "Right!",
                           "intent": "done"})

    c = llm_client.LLMDialogClient("nlu", "nlg", infer, PromptBuilder(templates))
    c.calls = calls
    yield c
    llm_faults.uninstall()


def test_install_leaves_the_client_unchanged_without_a_fault(client, switch_path):
    armed(switch_path, {"turn": 2, "kind": "error"})
    assert client.detect_cancel_intent("I'd rather leave it") is True
    assert client.classify_cancel_reply("yes") == "yes"
    assert client.verify_answer("It opens at 10.", [{"name": "m", "content": "Opens at 10."}])
    assert client.calls == ["nlu", "nlu", "nlu"]


def test_a_failed_cancel_detection_is_no_cancel_but_an_exact_command_still_cancels(
        client, switch_path):
    plan, _ = armed(switch_path, {"turn": 1, "kind": "error",
                                  "questions": ["detect_cancel_intent"]})
    assert client.detect_cancel_intent("I'd rather leave it") is False
    assert client.detect_cancel_intent("stop") is True   # local, no call
    assert client.classify_cancel_reply("yes") == "yes"  # another question: not failed
    assert client.calls == ["nlu"]
    assert [a["question"] for a in plan.finish()] == ["detect_cancel_intent"]


def test_an_invalid_answer_to_the_cancel_confirmation_withdraws_it(client, switch_path):
    plan, _ = armed(switch_path, {"turn": 1, "kind": "invalid",
                                  "questions": ["classify_cancel_reply"]})
    assert client.classify_cancel_reply("Yes, cancel it.") == "other"
    assert len(plan.finish()) == client.MAX_RETRIES  # asked again, then the fallback


def test_quiz_grading_falls_back_to_the_exact_answer(client, switch_path):
    armed(switch_path, {"turn": 1, "kind": "error", "questions": ["evaluate_quiz_answer"]})
    right = client.evaluate_quiz_answer("How many lungs?", "two", "Two.")
    wrong = client.evaluate_quiz_answer("Which organ pumps the blood?", "the heart",
                                        "I think it's the heart.")
    assert (right["kind"], right["correct"]) == ("answer", True)
    assert (wrong["kind"], wrong["correct"]) == ("answer", False)
    assert client.calls == []


def test_a_failed_verification_does_not_let_the_answer_through(client, switch_path):
    armed(switch_path, {"turn": 1, "kind": "error", "questions": ["verify_answer"]})
    assert client.verify_answer("It opens at 10.", [{"name": "m", "content": "Opens at 10."}]) \
        is False


def test_a_failed_generation_says_the_fixed_phrase(client, switch_path):
    armed(switch_path, {"turn": 1, "kind": "invalid"})
    assert client.ask_cancel_confirmation("Book a taxi") == client.phrase("cancel_ask")


def test_a_crash_fails_the_question_itself(client, switch_path):
    armed(switch_path, {"turn": 1, "kind": "crash", "questions": ["classify_step_turn"]})
    with pytest.raises(InjectedFault):
        client.classify_step_turn("done", "Sit down", "", [])
    assert client.detect_cancel_intent("I'd rather leave it") is True


def test_a_freeze_blocks_the_question_beyond_the_client_limits_then_fails_it(client,
                                                                              switch_path):
    from interaction_intelligence.llm_client import CallLimits

    plan, _ = armed(switch_path, {"turn": 1, "kind": "freeze", "hang_s": 0.4,
                                  "questions": ["classify_step_turn"]})
    client.limits = CallLimits(0.1)
    t0 = time.monotonic()
    with pytest.raises(InjectedFault):
        client.classify_step_turn("done", "Sit down", "", [])
    assert time.monotonic() - t0 >= 0.35
    assert [a["kind"] for a in plan.finish()] == ["freeze"]


def test_a_hang_in_the_real_client_times_out_once_and_the_skill_falls_back(client,
                                                                          switch_path):
    from interaction_intelligence.llm_client import CallLimits

    plan, _ = armed(switch_path, {"turn": 1, "kind": "hang",
                                  "questions": ["detect_cancel_intent"]})
    client.limits = CallLimits(0.3, 5.0)
    t0 = time.monotonic()
    assert client.detect_cancel_intent("I'd rather leave it") is False
    assert 0.25 <= time.monotonic() - t0 < 2.0
    # The model is unresponsive for the rest of the request: no call, the fixed phrase
    assert client.ask_cancel_confirmation("Book a taxi") == client.phrase("cancel_ask")
    assert client.calls == []
    assert (client.limits.timeouts, client.limits.skipped) == (1, 1)
    assert [a["question"] for a in plan.finish()] == ["detect_cancel_intent"]


def test_install_keeps_the_routes_and_uninstall_restores_the_client(client):
    from interaction_intelligence.llm_client import LLMDialogClient

    # The routable questions are still found through the wrappers (functools.wraps)
    assert "classify_intent" in LLMDialogClient.questions()
    assert LLMDialogClient.resolve_routes({"extract_slots": ("a", "b")})
    llm_faults.uninstall()
    assert not hasattr(LLMDialogClient.detect_cancel_intent, "__wrapped__")


# ==== THE SCRIPTED LLM, BEHIND ITS PROXY ====


@pytest.fixture
def scripted(switch_path, templates):
    pytest.importorskip("interaction_intelligence.llm_client")
    from interaction_sim.scripted_llm import ScriptedLLM

    return FaultyClient(ScriptedLLM(), FaultSwitch(switch_path))


def test_the_scripted_llm_answers_unless_a_fault_is_armed(scripted, switch_path):
    armed(switch_path, {"turn": 2, "kind": "error"})
    assert scripted.detect_cancel_intent("please cancel this") is True
    assert scripted.phrase("close_error") == "[close_error]"
    assert scripted.history_max_turns == 200


def test_the_scripted_llm_returns_the_real_fallback_of_a_failed_question(scripted, switch_path):
    plan, _ = armed(switch_path, {"turn": 1, "kind": "error"})
    assert scripted.detect_cancel_intent("please cancel this") is False
    assert scripted.detect_cancel_intent("cancel") is True
    # The real fallback: an exact yes word is read as yes (decided 2026-10-30), else other
    assert scripted.classify_cancel_reply("yes") == "yes"
    assert scripted.classify_cancel_reply("yes, but tea") == "other"
    assert scripted.model_status is False
    graded = scripted.evaluate_quiz_answer(question="Capital of France?", expected="Paris",
                                           given="It is Paris")
    assert graded["correct"] is False and graded["feedback"] == "No, the answer was Paris."
    # The fixed phrases are not questions to the model: never failed
    assert scripted.phrase("close_error") == "[close_error]"
    assert {a["question"] for a in plan.finish()} == {
        "detect_cancel_intent", "classify_cancel_reply", "evaluate_quiz_answer"}


def test_the_scripted_llm_crashes_when_asked(scripted, switch_path):
    plan, _ = armed(switch_path, {"turn": 1, "kind": "crash",
                                  "questions": ["classify_step_turn"]})
    with pytest.raises(InjectedFault):
        scripted.classify_step_turn("done", "Sit down", "", [])
    plan.user(lambda r, t: None)("x", [])  # the next turn: the scripted answer again
    assert scripted.classify_step_turn("done", "Sit down", "", []) == "done"


def test_the_scripted_llm_bounds_a_hang_with_the_limits_of_the_request(scripted,
                                                                        switch_path):
    from interaction_intelligence.llm_client import CallLimits

    plan, _ = armed(switch_path, {"turn": 1, "kind": "hang",
                                  "questions": ["detect_cancel_intent"]})
    scripted.limits = CallLimits(0.3, 5.0)
    t0 = time.monotonic()
    assert scripted.detect_cancel_intent("please cancel this") is False
    assert 0.25 <= time.monotonic() - t0 < 2.0
    # Another question of the same request: the method makes no call, its fallback
    assert scripted.classify_cancel_reply("yes please") == "other"
    assert [a["question"] for a in plan.finish()] == ["detect_cancel_intent"]


def test_the_scripted_llm_freezes_when_asked(scripted, switch_path):
    armed(switch_path, {"turn": 1, "kind": "freeze", "hang_s": 0.3,
                        "questions": ["classify_step_turn"]})
    t0 = time.monotonic()
    with pytest.raises(InjectedFault):
        scripted.classify_step_turn("done", "Sit down", "", [])
    assert time.monotonic() - t0 >= 0.25


def test_make_scripted_without_a_switch_is_the_scripted_llm(monkeypatch):
    pytest.importorskip("interaction_intelligence.llm_client")
    monkeypatch.delenv(llm_faults.ENV, raising=False)
    llm = llm_faults.make_scripted()
    assert llm.detect_cancel_intent("cancel") is True
    assert llm.get_opening("Take an order", type("F", (), {"next_slot": lambda s: "drink"})()) \
        == "[opening] drink?"
