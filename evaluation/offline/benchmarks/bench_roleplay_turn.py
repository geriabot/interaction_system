# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: a turn of a practice roleplay (classify_roleplay_turn), as roleplay.classify
asks it: continue, repeat, coach, end or scene_end ("unclear" when the model gives no valid
answer: never correct).

Critical (false_end): the practice closed ("end" or "scene_end") when the user did not ask to
stop and the scene was not over: the scene is scored on what was said so far and cannot be
resumed, without asking the user. Reported apart: a request to stop or a closed scene not
taken as such (end_missed: one more exchange, and the user can ask again), and the intent wrong
otherwise.

A case gives the scene ({scenario, role, user_goal}), the transcript before the turn
("history", the character's lines as "assistant") and the user's turn; the prompt shows the
two turns before it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/roleplay_turn.json"
_ENDS = ("end", "scene_end")


def ask(llm, case: dict) -> dict:
    history = list(case.get("history", [])) + [{"role": "user", "content": case["user_text"]}]
    return {"intent": llm.classify_roleplay_turn(case["user_text"], case["scene"], history, None)}


def score(case: dict, got: dict) -> dict:
    expected = case["expected_intent"]
    intent = got["intent"]
    flags = []
    critical = "false_end" if intent in _ENDS and expected not in _ENDS else None
    if expected in _ENDS and intent not in _ENDS:
        flags.append("end_missed")
    return {"correct": intent == expected, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("roleplay_turn", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("roleplay_turn", run, DATASET)
