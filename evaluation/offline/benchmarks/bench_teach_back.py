# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the judgment of a teach-back reply (judge_teach_back), as explanation.check
asks it: what the reply is (restates, question, declines, not_understood, other) and, when it
says content back, the status of each key point (correct, wrong, missing).

Critical (wrong_answer_accepted): a key point the reply said wrongly or did not say judged
"correct" (a point once correct stays verified, and understanding is verified when every
critical point is correct). Reported apart: the kind wrong, a point said correctly judged wrong
or missing (one more repair: conservative), a wrong point judged missing (repaired without the
misconception), and false_verification: this reply alone would verify every critical point when
it does not.

A case gives "asked" (the check the robot asked; empty: the fixed teach-back phrase of the
language, as the skill asks it first) and, for "restates", "expected_points" {"<id>": status}
for every key point (1-based, as the prompt numbers them).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/teach_back.json"


def ask(llm, case: dict) -> dict:
    asked = case.get("asked") or llm.phrase("teach_back")
    res = llm.judge_teach_back(case["user_text"], case["key_points"], asked, None)
    return {"kind": res["kind"],
            "points": {str(i + 1): status for i, status, _said in res["points"]}}


def _expected_points(case: dict) -> dict:
    """The status of each key point in the reply: as annotated for "restates"; any other kind
    says nothing back, so every point is missing."""
    n = len(case["key_points"])
    if case["expected_kind"] != "restates":
        return {str(i): "missing" for i in range(1, n + 1)}
    given = case.get("expected_points", {})
    return {str(i): given.get(str(i), "missing") for i in range(1, n + 1)}


def score(case: dict, got: dict) -> dict:
    expected = _expected_points(case)
    points = got.get("points", {}) if got["kind"] == "restates" else {}
    flags = []
    falsely_correct = [i for i, status in points.items()
                       if status == "correct" and expected.get(i) != "correct"]
    critical = "wrong_answer_accepted" if falsely_correct else None
    if got["kind"] != case["expected_kind"]:
        flags.append("kind_wrong")
    if case["expected_kind"] == "restates" and got["kind"] == "restates":
        for i, status in expected.items():
            judged = points.get(i, "missing")
            if status == "correct" and judged != "correct":
                flags.append("correct_point_rejected")
            if status == "incomplete" and judged == "wrong":
                flags.append("incomplete_judged_wrong")
            if status == "wrong" and judged == "missing":
                flags.append("wrong_point_missed")
    critical_ids = [str(i + 1) for i, p in enumerate(case["key_points"])
                    if p.get("critical", True) is not False]
    if (got["kind"] == "restates" and all(points.get(i) == "correct" for i in critical_ids)
            and not all(expected[i] == "correct" for i in critical_ids)):
        flags.append("false_verification")
    if case["expected_kind"] == "restates":
        correct = got["kind"] == "restates" and all(
            points.get(i, "missing") == status for i, status in expected.items())
    else:
        correct = got["kind"] == case["expected_kind"]
    return {"correct": correct, "critical": critical, "flags": sorted(set(flags)),
            "falsely_correct": falsely_correct}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("teach_back", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("teach_back", run, DATASET)
