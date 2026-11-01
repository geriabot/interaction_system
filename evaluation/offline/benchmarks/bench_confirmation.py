# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the answer to the read-back of slot filling (classify_intent), as
slot_filling.classify_confirmation asks it: confirms, corrects, question or unclear.

Critical (false_confirmation): a correction or a question taken as "confirms". It closes the
form with data the user did not accept, and nothing after it shows them to the user again.
Reported apart: a hedged or ironic reply taken as "confirms" (hedged_as_confirms: the case has
"hedged": true), another unclear reply taken as "confirms" (unclear_as_confirms), a confirmation
missed (confirm_missed: one more turn) and a correction understood with a wrong value
(correction_value_wrong: the frame is read back again).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import build_frame, context, main, norm_value, run_cases  # noqa: E402

DATASET = "datasets/phase_a/confirmation.json"


def ask(llm, case: dict) -> dict:
    frame = build_frame(case)
    intent, corrections = llm.classify_intent(case["user_text"], frame, context(case))
    return {"intent": intent, "corrections": corrections}


def score(case: dict, got: dict) -> dict:
    expected = case["expected_intent"]
    intent = got["intent"]
    flags = []
    critical = None
    if intent == "confirms" and expected in ("corrects", "question"):
        critical = "false_confirmation"
    if intent == "confirms" and expected == "unclear":
        flags.append("hedged_as_confirms" if case.get("hedged") else "unclear_as_confirms")
    if expected == "confirms" and intent != "confirms":
        flags.append("confirm_missed")
    if expected == "corrects" and intent == "corrects":
        want = {k: norm_value(v) for k, v in (case.get("expected_corrections") or {}).items()}
        have = {k: norm_value(v) for k, v in (got.get("corrections") or {}).items()}
        if want and want != have:
            flags.append("correction_value_wrong")
    return {"correct": intent == expected, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("confirmation", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("confirmation", run, DATASET)
