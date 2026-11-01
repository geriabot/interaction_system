# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the spoken cancellation (detect_cancel_intent), checked on every user turn
by cancel_check before the strategy handles it. A case with "strict": true is asked as while
slot filling asks a closed-choice slot (the question tells the LLM to accept only an explicit
request to stop).

detect_cancel_intent first tries a local check of the text (local_cancel_check) and asks the
LLM only when that check cannot decide. The benchmark measures the model, so it asks the LLM
for every case, through the production method with the local check disabled; what the system
as a whole would decide (the local check, or the LLM when the check cannot decide) is reported
apart ("production"). The model chooses among cancel, finished (the user has nothing more to
ask or add: the normal end of the task) and none; only cancel counts as a detection.

Not critical (the author, decision 6a of the methodology review): a detection is always
followed by the confirmation question ("Do you want to stop?"), so a false detection costs a
question, not the task; only a false "yes" to that question (cancel_reply) is critical. Reported:
a turn that is not a request to stop taken as one (false_cancel_detected), a request to stop
missed by the model (cancel_missed), and the errors of the production decision
(production_false_cancel, production_cancel_missed).
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

from interaction_intelligence import llm_client  # noqa: E402
from interaction_intelligence.utils import local_cancel_check  # noqa: E402

DATASET = "datasets/phase_a/cancel_detect.json"


def ask(llm, case: dict) -> dict:
    text = case["user_text"]
    strict = bool(case.get("strict", False))
    local = local_cancel_check(text, llm.language)  # the exact commands of the case's language
    with mock.patch.object(llm_client, "local_cancel_check", return_value=None):
        # The system sends the task description (2026-11-01): the part of the situation that
        # describes the task (before the robot's question)
        task = case.get("task") or str(case.get("situation", "")).split(";")[0].strip()
        model = llm.detect_cancel_intent(text, None, strict=strict, task=task)
    return {"cancel": model, "local": local, "production": model if local is None else local}


def score(case: dict, got: dict) -> dict:
    expected = bool(case["expected_cancel"])
    flags = []
    if got["cancel"] and not expected:
        flags.append("false_cancel_detected")
    if expected and not got["cancel"]:
        flags.append("cancel_missed")
    if got["production"] and not expected:
        flags.append("production_false_cancel")
    if expected and not got["production"]:
        flags.append("production_cancel_missed")
    return {"correct": got["cancel"] == expected, "critical": None, "flags": flags}


def _production(records: list[dict]) -> dict:
    ok = [r for r in records if r.get("got") and not {
        "production_false_cancel", "production_cancel_missed"} & set(r["flags"])]
    return {"production_accuracy": round(len(ok) / len(records), 4) if records else None,
            "decided_locally": sum(1 for r in records
                                   if r.get("got") and r["got"].get("local") is not None)}


# The figures of this benchmark besides the accuracy (the gate summarizes them again when it
# scores the saved answers with the current dataset: core/gate.py)
EXTRA = _production


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("cancel_detect", cfg, dataset_path, ask, score, verbose, infer_fn,
                     extra=_production)


if __name__ == "__main__":
    main("cancel_detect", run, DATASET)
