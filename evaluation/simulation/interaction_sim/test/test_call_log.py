# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The record of the calls of the system's model (call_log.py), without a running system: the
turn marked from the harness side, the real client's calls recorded with their question, skill,
prompt, answer and latency, alone and together with the injector of model failures."""

import json
from pathlib import Path

import pytest
import yaml

from interaction_sim import call_log, llm_faults
from interaction_sim.call_log import CallLog, Recorder, recorded_infer

PROMPTS = (Path(__file__).resolve().parents[4] / "interaction_system_core"
           / "interaction_intelligence" / "config" / "prompts.yaml")


def templates():
    with open(PROMPTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


@pytest.fixture
def marker(tmp_path):
    return str(tmp_path / "calls.json")


def test_the_harness_marks_each_turn_and_collects_its_dialogue_only(marker):
    log = CallLog(marker)
    log.start()
    recorder = Recorder(marker)
    infer = recorded_infer(lambda *a: "ok", recorder, {"detect_cancel_intent"})
    infer("nlu", "opening prompt", 10, None)
    seen = []
    policy = log.user(lambda robot, transcript: seen.append(robot) or None)
    policy("Hello?", [])
    infer("nlu", "turn 1 prompt", 10, {"type": "object"})
    calls = log.finish()
    assert [(c["turn"], c["prompt"], c["answer"]) for c in calls] == [
        (0, "opening prompt", "ok"), (1, "turn 1 prompt", "ok")]
    assert calls[1]["schema"] == {"type": "object"} and calls[1]["latency_s"] >= 0
    assert seen == ["Hello?"]
    # Another dialogue: its own calls only; outside a dialogue nothing is recorded
    infer("nlu", "between dialogues", 10, None)
    log.start()
    infer("nlu", "second dialogue", 10, None)
    assert [c["prompt"] for c in log.finish()] == ["second dialogue"]


def test_a_failed_call_is_recorded_with_its_error(marker):
    log = CallLog(marker)
    log.start()

    def broken(*args):
        raise RuntimeError("gateway down")

    with pytest.raises(RuntimeError):
        recorded_infer(broken, Recorder(marker), set())("nlu", "p", 10, None)
    (call,) = log.finish()
    assert call["answer"] is None and call["error"] == "RuntimeError: gateway down"


@pytest.fixture
def installed(marker):
    pytest.importorskip("interaction_intelligence.llm_client")
    assert call_log.install(marker)
    yield marker
    call_log.uninstall()


def client(answer):
    from interaction_intelligence.llm_client import LLMDialogClient
    from interaction_intelligence.prompt_builder import PromptBuilder

    return LLMDialogClient("nlu", "nlg", lambda *a: answer, PromptBuilder(templates()))


def test_install_records_the_question_skill_prompt_and_answer(installed):
    from interaction_intelligence.dialog_context import DialogContext
    from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

    log = CallLog(installed)
    log.start()
    log.user(lambda r, t: None)("What would you like?", [])
    llm = client('{"cancel": false}')
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": "slot_filling", "task_description": "Take a drink order",
        "frame_schema_json": json.dumps({"slots": [{"name": "size", "type": "str"}]})}))
    result = get_skill("cancel_check")(env, state, SkillInput(user_text="I'd rather leave it"))
    assert result.label == "none"
    (call,) = log.finish()
    assert (call["turn"], call["skill"], call["question"]) == (
        1, "cancel_check", "detect_cancel_intent")
    assert "I'd rather leave it" in call["prompt"]
    assert call["answer"] == '{"cancel": false}' and call["error"] is None


def test_an_exact_command_makes_no_call_and_nothing_is_recorded(installed):
    log = CallLog(installed)
    log.start()
    assert client('{"cancel": false}').detect_cancel_intent("Stop.") is True
    assert log.finish() == []


def test_with_the_injector_the_injected_answer_is_recorded(marker, tmp_path):
    # As run_experiment.py loads them: the injector first, the recorder after it (outside)
    pytest.importorskip("interaction_intelligence.llm_client")
    switch = str(tmp_path / "faults.json")
    assert llm_faults.install(switch)
    assert call_log.install(marker)
    try:
        plan = llm_faults.FaultPlan([{"turn": 1, "kind": "invalid",
                                      "questions": ["detect_cancel_intent"],
                                      "answer": "not json"}], switch)
        log = CallLog(marker)
        plan.start()
        log.start()
        log.user(plan.user(lambda r, t: None))("Hello?", [])
        assert client('{"cancel": true}').detect_cancel_intent("I'd rather leave it") is False
        calls = log.finish()
        assert [(c["turn"], c["question"], c["answer"]) for c in calls] == [
            (1, "detect_cancel_intent", "not json")]
        assert {a["question"] for a in plan.finish()} == {"detect_cancel_intent"}
    finally:
        call_log.uninstall()
        llm_faults.uninstall()
