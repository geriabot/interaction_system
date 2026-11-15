# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling follows the ladder of the other behaviors: a slot is left unanswered at its
max_attempts-th failed attempt, its last asking is different, and an answer about another slot
is not a failed attempt on the slot asked. The default limits keep max_attempts < max_stalled."""

import json
import os

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.slot_filling import _slots_over_attempts

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "..", "interaction_system", "config")


class Llm:
    language = "English"

    def __init__(self, turns):
        self.turns = list(turns)

    def extract_turn(self, user_text, frame, ctx=None):
        return self.turns.pop(0)

    def phrase(self, key):
        return f"[{key}]"


def state_of():
    frame = DialogFrame.from_slots_json(json.dumps({"slots": [
        {"name": "name", "type": "str"}, {"name": "city", "type": "str"}]}))
    return {"task": "Register", "frame": frame.to_state(), "ack_idx": 0, "acknowledgments": 0}


def turn(llm, state, text):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    return get_skill("slot_filling.update")(env, state, SkillInput(user_text=text, number=3))


def test_a_slot_is_left_unanswered_at_the_max_attempts_th_failure():
    llm = Llm([("other", [])] * 3)
    state = state_of()
    assert [turn(llm, state, "hmm what").label for _ in range(2)] == ["not_understood"] * 2
    assert _slots_over_attempts(DialogFrame.from_state(state["frame"]), 3) == ["name"]
    # Giving up the slot is a turn without progress, as in the other behaviors
    assert turn(llm, state, "the weather").label == "unanswered"
    assert state["last"]["skipped"] == "name"


def test_skipping_a_slot_on_purpose_is_an_answer():
    llm = Llm([("skip", [])])
    state = state_of()
    assert turn(llm, state, "I'd rather not say").label == "incomplete"
    assert state["last"]["skipped"] == "name"


def test_giving_up_a_slot_with_a_value_for_another_is_progress():
    llm = Llm([("other", [])] * 2
              + [("values", [{"slot": "city", "value": "Madrid", "source_span": "Madrid"}])])
    state = state_of()
    turn(llm, state, "hmm")
    turn(llm, state, "what")
    # A value for another slot: not a failed attempt, so the slot asked is not given up yet
    assert turn(llm, state, "Madrid").label == "incomplete"
    assert state["last"]["skipped"] == ""


def test_an_answer_about_another_slot_is_not_a_failed_attempt():
    llm = Llm([("values", [{"slot": "city", "value": "Madrid", "source_span": "Madrid"}])] * 3)
    state = state_of()
    for _ in range(3):
        turn(llm, state, "I live in Madrid")
    frame = DialogFrame.from_state(state["frame"])
    assert frame.attempts("name") == 0 and not frame.is_unanswered("name")


def test_the_default_limits_let_the_ladder_finish_before_a_breakdown():
    for name in ("interaction_orchestrator_params.yaml", "app_params.yaml"):
        with open(os.path.join(_CONFIG, name)) as f:
            text = f.read()
        params = {}
        for line in text.splitlines():
            for key in ("max_attempts", "max_stalled"):
                stripped = line.strip()
                if stripped.startswith(key + ":"):
                    params[key] = int(stripped.split(":")[1].split("#")[0])
        assert params["max_attempts"] < params["max_stalled"], name
