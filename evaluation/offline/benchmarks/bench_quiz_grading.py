# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the grading of a spoken quiz answer (evaluate_quiz_answer), as quiz.evaluate
asks it: whether the reply is an answer ("answer") or not ("repeat": the user asks to repeat or
clarify, did not hear or understand), and whether the answer is correct.

Critical (wrong_answer_accepted): a wrong answer graded correct, or a reply that is not an answer
graded as a correct one. The score records it and the user is told it is right. Reported apart:
a right answer graded wrong (the user hears the right answer: conservative), an answer taken as
a request to repeat (the question is asked again) and a request to repeat graded as an answer.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/quiz_grading.json"


def ask(llm, case: dict) -> dict:
    res = llm.evaluate_quiz_answer(question=case["question"], expected=case["expected_answer"],
                                   given=case["given_answer"])
    return {"kind": res.get("kind", "answer"), "correct": bool(res.get("correct")),
            "feedback": res.get("feedback", "")}


def score(case: dict, got: dict) -> dict:
    expected_kind = case.get("expected_kind", "answer")
    truth = expected_kind == "answer" and bool(case["ground_truth_correct"])
    flags = []
    critical = None
    if got["kind"] == "answer" and got["correct"] and not truth:
        critical = "wrong_answer_accepted"
    if expected_kind == "answer" and got["kind"] == "repeat":
        flags.append("answer_taken_as_repeat")
    if expected_kind == "repeat" and got["kind"] == "answer":
        flags.append("repeat_graded")
    if truth and got["kind"] == "answer" and not got["correct"]:
        flags.append("right_answer_rejected")
    if expected_kind == "repeat":
        correct = got["kind"] == "repeat"
    else:
        correct = got["kind"] == "answer" and got["correct"] == truth
    return {"correct": correct, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("quiz_grading", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("quiz_grading", run, DATASET)
