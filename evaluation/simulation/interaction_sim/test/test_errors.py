# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The error endings of a request that the dialogues of test_dialogues.py cannot reach (O12 of
the coverage audit, 2026-10-30), with the scripted LLM and a degraded system (conftest.py,
`degraded_system`: only the quiz and the questionnaire in the catalog, and a broken questionnaire
tree): no behavior fits the request, the runner of the behavior cannot be activated, and the
orchestrator is deactivated during a dialogue (then activated again, and it serves again). The
choice of the behavior failing is in test_dialogues.py. Each ends with outcome error and its
reason, and the system takes the next request.

    ROS_DOMAIN_ID=<free id> python3 -m pytest simulation/interaction_sim/test/test_errors.py
"""

import json
import threading
import time

from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState

from ros2_dialog_interfaces.action import DialogTask
from interaction_sim.harness import ScriptedUser

QUESTIONS = [{"question": "Capital of France?", "answer": "Paris"},
             {"question": "2+2?", "answer": "4"}]
SURVEY = {"name": "Satisfaction", "options": ["No", "Somewhat", "Yes"],
          "items": ["The robot understood me"]}


def goal(task, mode="", resources=None, **kw):
    return DialogTask.Goal(task_description=task, dialog_mode=mode,
                           resources_json=json.dumps(resources) if resources is not None else "",
                           **kw)


def resource(name, content):
    return [{"name": name, "description": name, "content": json.dumps(content)}]


def run(system, g, lines, **kw):
    return system.node.run(g, ScriptedUser(lines), timeout=kw.pop("timeout", 60), **kw)


def quiz(**kw):
    return goal("A quiz", "quiz", resource("questions", QUESTIONS), **kw)


def change_state(node, transition: int) -> bool:
    """Ask the orchestrator's lifecycle for a transition (its change_state service)."""
    client = node.create_client(ChangeState, "/interaction_orchestrator/change_state")
    try:
        assert client.wait_for_service(timeout_sec=10.0), "No lifecycle service"
        request = ChangeState.Request()
        request.transition.id = transition
        future = client.call_async(request)
        deadline = time.time() + 10
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)
        return future.done() and future.result().success
    finally:
        node.destroy_client(client)


def test_no_behavior_fits_the_request(degraded_system):
    # No question bank and no instrument: neither offered behavior can run it
    r = run(degraded_system, goal("Take a drink order"), [])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_ERROR
    assert r.result.failure_reason == "No dialogue behavior fits the request"
    assert r.robot_lines() == []


def test_a_behavior_whose_runner_cannot_be_activated(degraded_system):
    r = run(degraded_system, goal("Satisfaction survey", "questionnaire",
                                  resource("instrument", SURVEY)), [])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_ERROR
    assert r.result.failure_reason == (
        "The behavior runner interaction_questionnaire could not be activated")
    assert r.robot_lines() == []
    # The other behaviors still run
    r = run(degraded_system, quiz(), ["Paris", "4"])
    assert r.status == "succeeded"
    assert r.robot_lines()[0] == "[quiz 2] Capital of France?"


def test_the_orchestrator_deactivated_during_a_dialogue(degraded_system):
    # max_silence 0: the dialogue waits for the user until something else ends it
    node = degraded_system.node
    result = {}

    def dialogue():
        result["run"] = run(degraded_system, quiz(max_silence=0.0), [None] * 20, timeout=40)

    thread = threading.Thread(target=dialogue)
    thread.start()
    time.sleep(2.0)
    assert change_state(node, Transition.TRANSITION_DEACTIVATE)
    thread.join()
    r = result["run"]
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_ERROR
    assert r.result.failure_reason == "The interaction system was deactivated"
    assert r.robot_lines()[0] == "[quiz 2] Capital of France?"
    # Activated again, it serves the next request
    assert change_state(node, Transition.TRANSITION_ACTIVATE)
    assert node.wait_for_system(10)
    r = run(degraded_system, quiz(), ["Paris", "4"])
    assert r.status == "succeeded"
