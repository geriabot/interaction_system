# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the answer to "do you want to stop?" (classify_cancel_reply), as cancel_check
asks it after a cancel request: yes (the dialogue ends, cancelled), no or other (the reply does
not answer whether to stop, or is ambiguous). Only a yes stops: no and other both resume with
the robot's last question (decided by the author, 2026-11-01), so either is right for a case
that expects one of them. The model may also answer finished (nothing more to ask or add: a
normal end where the strategy lets the user end it, otherwise the robot resumes): some cases
expect it (2026-11-01); it never cancels.

Critical (false_cancel): a reply that does not confirm the cancellation taken as "yes": the
dialogue ends cancelled. Reported apart: a confirmation missed (cancel_missed: the user can
ask again).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/cancel_reply.json"


def ask(llm, case: dict) -> dict:
    return {"answer": llm.classify_cancel_reply(case["user_text"], None)}


def score(case: dict, got: dict) -> dict:
    expected = case["expected_answer"]
    answer = got["answer"]
    flags = []
    critical = "false_cancel" if answer == "yes" and expected != "yes" else None
    if expected == "yes" and answer != "yes":
        flags.append("cancel_missed")
    resumes = ("no", "other")
    correct = answer == expected or (expected in resumes and answer in resumes)
    return {"correct": correct, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("cancel_reply", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("cancel_reply", run, DATASET)
