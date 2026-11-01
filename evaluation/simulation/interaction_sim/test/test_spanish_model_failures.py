# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The system in Spanish (language: Spanish, its language file config/languages/spanish.yaml)
while its model fails, with the scripted LLM behind the injector of model failures, as
test_model_failures.py does in English: the exact stop commands of the dialogue's language are
decided without the model, so a Spanish user can stop the conversation while the model is down
("Para."), a stop word inside a longer utterance is not a command ("Para mí un café."), and the
fixed phrases said when the model fails are the Spanish ones.

As in English (docs/behaviors/cancellation.md), the answer to the confirmation is read by the
model; while the model is still down, that reading fails and its fallback reads the whole answer:
exactly a yes word of the language ("Sí.", "Vale.") confirms, anything else withdraws the
cancellation (the robot resumes). With the model known to be down before the request
(its last call failed), an exact command ends the conversation without the question (decided by
the author, 2026-10-30)."""

import os

import pytest

from conftest import _launch
from ros2_dialog_interfaces.action import DialogTask
from interaction_sim.llm_faults import ENV
from test_model_failures import DRINKS, FAULT_PARAMS, goal, run

SPANISH_PARAMS = FAULT_PARAMS.replace("    ack_phrases:",
                                      "    language: Spanish\n    ack_phrases:")
Outcome = DialogTask.Result
ASK = "¿Seguro que quieres cancelar?"  # cancel_ask of spanish.yaml


@pytest.fixture(scope="module")
def spanish_system(tmp_path_factory):
    """The method's trees in Spanish, with the scripted LLM behind the injector."""
    assert "language: Spanish" in SPANISH_PARAMS
    switch = tmp_path_factory.mktemp("faults_es") / "faults.json"
    old = os.environ.get(ENV)
    os.environ[ENV] = str(switch)
    try:
        for runner in _launch(tmp_path_factory, "spanish_model_failures", SPANISH_PARAMS):
            runner.switch = str(switch)
            yield runner
    finally:
        if old is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = old


def test_a_spanish_exact_command_cancels_while_the_model_is_down(spanish_system):
    # Every question fails while "Para." is handled: the command is decided locally and the
    # confirmation question falls back to its Spanish fixed phrase; the model is back for the
    # answer to it
    r, applied = run(spanish_system, goal(schema=DRINKS), ["Para.", "Sí."],
                     [{"turn": 1, "kind": "error"}])
    assert r.robot_lines()[1:] == [ASK, "[cancelled]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    assert {a["question"] for a in applied} == {"ask_cancel_confirmation"}


def test_a_repeated_command_is_a_command(spanish_system):
    r, applied = run(spanish_system, goal(schema=DRINKS), ["¡Para, para!", "Sí."],
                     [{"turn": 1, "kind": "error"}])
    assert r.robot_lines()[1:] == [ASK, "[cancelled]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED


def test_a_stop_word_inside_an_utterance_is_not_a_command(spanish_system):
    # Not an exact command: the model decides, and with the model down there is no request
    r, applied = run(spanish_system, goal(schema=DRINKS), ["Para mí un café."],
                     [{"turn": 1, "kind": "error"}])
    assert ASK not in r.robot_lines()
    assert r.result.outcome != Outcome.OUTCOME_CANCELLED
    assert "detect_cancel_intent" in {a["question"] for a in applied}


def test_with_the_model_down_for_good_an_exact_si_confirms_it(spanish_system):
    # Decided 2026-10-30 (cancellation.md): when the model cannot read the answer to the
    # confirmation, the whole answer exactly one of the language's yes words confirms it
    # ("Sí.", "Vale."), and the closing falls back to its Spanish fixed phrase
    for answer in ("Sí.", "Vale."):
        r, applied = run(spanish_system, goal(schema=DRINKS), ["Para.", answer],
                         [{"turn": 1, "kind": "error", "turns": 0}])
        lines = r.robot_lines()
        assert lines[1:] == [ASK, "De acuerdo, cancelado."]  # cancel_ok of spanish.yaml
        assert r.result.outcome == Outcome.OUTCOME_CANCELLED
        assert (2, "classify_cancel_reply") in {(a["turn"], a["question"]) for a in applied}


def test_with_the_model_down_for_good_any_other_answer_withdraws_it(spanish_system):
    # Anything but an exact yes or no word: "something else", nothing is cancelled and the
    # robot resumes; the dialogue then ends as the strategy and the trees decide
    r, applied = run(spanish_system, goal(schema=DRINKS), ["Para.", "Sí, pero un té."],
                     [{"turn": 1, "kind": "error", "turns": 0}])
    lines = r.robot_lines()
    assert lines[1] == ASK
    assert "[cancelled]" not in lines
    assert "De acuerdo, cancelado." not in lines
    assert r.result.outcome != Outcome.OUTCOME_CANCELLED
    assert (2, "classify_cancel_reply") in {(a["turn"], a["question"]) for a in applied}


def test_with_the_model_known_down_para_ends_without_the_question(spanish_system):
    # Decided 2026-10-30: every call of turn 1 failed, so "Para." at turn 2 ends the
    # conversation at once with the fixed cancel closing (the scripted LLM's tag here)
    r, _ = run(spanish_system, goal(schema=DRINKS), ["drink=té", "Para."],
               [{"turn": 1, "kind": "error"}])
    lines = r.robot_lines()
    assert ASK not in lines and "[cancel?]" not in lines
    assert lines[-1] == "[cancel_ok]"
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    # With the model up, the question as usual
    r, _ = run(spanish_system, goal(schema=DRINKS), ["Para.", "Sí."], [])
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
