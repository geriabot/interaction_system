# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The records of the harness, without a running system: the conversational fillers are robot
lines marked "filler", kept in the transcript but not lines of the dialogue."""

from interaction_sim.harness import DialogueRun, dialogue_lines

TRANSCRIPT = [
    {"role": "robot", "text": "[opening] drink?", "t": 0.1},
    {"role": "user", "text": "slow 3; drink=coffee", "t": 0.5},
    {"role": "robot", "text": "Hmm…", "t": 2.5, "filler": True},
    {"role": "robot", "text": "Noted. [ask] size?", "t": 3.6},
]


def test_fillers_are_kept_in_order_but_are_not_lines_of_the_dialogue():
    run = DialogueRun(transcript=list(TRANSCRIPT))
    assert run.robot_lines() == ["[opening] drink?", "Noted. [ask] size?"]
    assert run.user_lines() == ["slow 3; drink=coffee"]
    assert run.fillers() == [TRANSCRIPT[2]]
    assert run.transcript == TRANSCRIPT


def test_the_user_policy_does_not_see_the_fillers():
    assert dialogue_lines(TRANSCRIPT) == [TRANSCRIPT[0], TRANSCRIPT[1], TRANSCRIPT[3]]
