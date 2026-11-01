# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Whole dialogues through the LLM-led conversation (the monolithic ablation, condition llm_led
of E1), with the scripted LLM (scripted_llm.py, "LLM-led" rules): the LLM's decision to end and
its result reach the action's result; silence and the turn limit are the method's, while a
spoken cancellation and misunderstandings are left to the LLM (no subtree, no breakdown count)."""

import json

from ros2_dialog_interfaces.action import DialogTask
from interaction_sim.harness import ScriptedUser

DRINKS = {"slots": [
    {"name": "drink", "type": "str", "canonical_values": ["coffee", "tea"]},
    {"name": "size", "type": "str"},
]}


def goal(**kw):
    return DialogTask.Goal(task_description="Take a drink order", dialog_mode="slot_filling",
                           frame_schema_json=json.dumps(DRINKS), **kw)


def run(system, g, lines, **kw):
    return system.node.run(g, ScriptedUser(lines), timeout=kw.pop("timeout", 60), **kw)


def test_the_llm_ends_with_success_and_the_data(llm_led_system):
    r = run(llm_led_system, goal(), ["a tea", "end success; drink=tea; size=small"])
    assert r.status == "succeeded"
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED
    assert json.loads(r.result.final_frame_json) == {"drink": "tea", "size": "small"}
    assert r.robot_lines() == ["[mono_open]", "[mono] a tea", "[mono_end] success"]
    assert r.result.total_turns == 2


def test_the_llm_ends_with_failure(llm_led_system):
    r = run(llm_led_system, goal(), ["end failed"])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_FAILED


def test_the_llm_ends_with_cancellation(llm_led_system):
    r = run(llm_led_system, goal(), ["end cancelled"])
    assert r.result.outcome == DialogTask.Result.OUTCOME_CANCELLED


def test_the_llm_handles_a_spoken_stop(llm_led_system):
    # No spoken cancellation subtree: "stop" reaches the LLM, which decides
    r = run(llm_led_system, goal(), ["stop", "end cancelled"])
    assert r.robot_lines()[1] == "[mono] stop"
    assert r.result.outcome == DialogTask.Result.OUTCOME_CANCELLED


def test_no_breakdown_count(llm_led_system):
    # No count of turns without progress: more unclear turns than max_stalled (4) do not end it
    r = run(llm_led_system, goal(), ["maybe"] * 5 + ["end success; drink=tea; size=small"])
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED
    assert r.result.total_turns == 6


def test_unparseable_answer_asks_to_repeat_and_goes_on(llm_led_system):
    r = run(llm_led_system, goal(), ["garble", "end success; drink=coffee; size=large"])
    assert r.robot_lines()[1] == "Sorry, could you repeat that?"
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED


def test_silence_ends_the_dialogue(llm_led_system):
    r = run(llm_led_system, goal(), [None, None])
    assert r.result.outcome == DialogTask.Result.OUTCOME_USER_SILENT
    assert r.robot_lines()[-1] == "[close_user_silent]"


def test_turn_limit(llm_led_system):
    r = run(llm_led_system, goal(max_turns=2), ["hello", "a tea", "end success"])
    assert r.result.outcome == DialogTask.Result.OUTCOME_TURN_LIMIT
    assert r.result.total_turns == 2
    assert r.robot_lines()[-1] == "[close_turn_limit]"


def test_skip_intro(llm_led_system):
    r = run(llm_led_system, goal(skip_intro=True), ["end success; drink=tea; size=small"])
    assert r.robot_lines()[0] == "[mono_open_direct]"
