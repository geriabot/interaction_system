# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The response to each detour (judge_runs.detour_replies, detour_summary): the judgment of the
robot's reply to each planned detour line, per kind of detour. No ROS, no LLM.

    python3 -m pytest analysis/test_detours.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "simulation", "interaction_sim")))
import judge_runs  # noqa: E402


def rec(transcript, user_log, condition="bounded"):
    return {"model": "m", "condition": condition, "persona": "p", "transcript": transcript,
            "user_log": user_log}


def line(role, text, filler=False):
    out = {"role": role, "text": text, "t": 0.0}
    if filler:
        out["filler"] = True
    return out


WEATHER = "Will it rain tomorrow?"
PINEAPPLE = "And put pineapple on it too."

TRANSCRIPT = [line("robot", "Which pizza?"),                  # 0
              line("user", WEATHER),                          # 1
              line("robot", "Hmm…", filler=True),              # 2 (a filler: not the reply)
              line("robot", "I can't tell. Which pizza?"),    # 3
              line("user", "A margherita."),                  # 4
              line("robot", "Anything else on it?"),          # 5
              line("user", PINEAPPLE),                        # 6
              line("robot", "Pineapple is not on the menu."),  # 7
              line("user", WEATHER),                          # 8 (the same line said again)
              ]
LOG = [{"turn": 1, "event": "detour", "detour": "off_task", "text": WEATHER},
       {"turn": 2, "text": "A margherita."},
       {"turn": 3, "event": "detour", "detour": "not_allowed", "text": PINEAPPLE},
       {"turn": 4, "event": "detour", "detour": "off_task", "text": WEATHER}]


def test_the_reply_to_each_detour_is_the_next_robot_line_that_is_not_a_filler():
    assert judge_runs.detour_replies(rec(TRANSCRIPT, LOG)) == [
        ("off_task", 3), ("not_allowed", 7), ("off_task", None)]   # the last one: no reply
    assert judge_runs.detour_replies(rec(TRANSCRIPT, [])) == []


def test_the_detours_are_summarised_per_kind():
    runs = [("r1.json", rec(TRANSCRIPT, LOG))]
    judgments = {
        ("r1.json", 3): {"in_frame": True, "violation": "none", "unsupported_claims": []},
        ("r1.json", 7): {"in_frame": False, "violation": "invalid_value",
                         "unsupported_claims": ["pineapple costs 1 euro"]},
    }
    lines = judge_runs.detour_summary(runs, judgments)
    rows = {r.split()[2]: r.split() for r in lines[1:]}
    assert rows["off_task"][3:7] == ["2", "1", "1.000", "0"]
    assert rows["not_allowed"][3:7] == ["1", "1", "0.000", "1"]
    assert "invalid_value (1)" in lines[2] or "invalid_value (1)" in lines[1]
    # Nothing judged yet: the share is not given
    assert judge_runs.detour_summary(runs, {})[1].split()[5] == "-"
    assert judge_runs.detour_summary([("r", rec(TRANSCRIPT, []))], {}) == []
