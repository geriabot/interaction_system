# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: a wrong check of the last answer in grounded question answering
(detect_wrong_check). grounded_qa.answer asks it only for a turn that answer_from_material read
as a reaction, after an answer from the material: does the turn repeat back a value of that
answer (a time, a price, a day, a date, a place, a number) with a different value? If so, the
person misunderstood the answer, and the robot says it again. The cases are wrong checks (plain,
with a comment after or before), checks with the same value (also in another form: a 12-hour
time for a 24-hour one, words for digits) and reactions without a value of the answer.

Critical (wrong_check_missed): a wrong check read as no wrong check: the misunderstanding passes,
and the robot only invites more questions.
Reported apart: a turn that is not a wrong check read as one (false_wrong_check: the robot says
the right answer again, harmless) and a question without an answer (not_answered: the turn stays
a reaction, as without the question; counted in the no-answer rate, never critical).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/wrong_check.json"


def ask(llm, case: dict) -> dict:
    return {"wrong_check": llm.detect_wrong_check(case["user_text"], case["answer"])}


def score(case: dict, got: dict) -> dict:
    expected = bool(case["expected_wrong_check"])
    value = got.get("wrong_check")
    if value is None:
        return {"correct": False, "critical": None, "flags": ["not_answered"]}
    flags = []
    critical = None
    if expected and value is False:
        critical = "wrong_check_missed"
    if value is True and not expected:
        flags.append("false_wrong_check")
    return {"correct": value is expected, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("wrong_check", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("wrong_check", run, DATASET)
