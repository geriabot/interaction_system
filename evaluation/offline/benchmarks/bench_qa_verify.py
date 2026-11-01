# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the verification of an answer against the material (verify_answer), the
check grounded_qa.answer makes before an answer is said, asked on its own with answers written
for it: supported paraphrases and answers with one claim the material does not support (a
different number, time or place, a fact added from general knowledge, a generalisation).

Critical (unsupported_answer_accepted): an answer the material does not support verified.
Reported apart: a supported answer rejected (the robot abstains: conservative).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/qa_verify.json"


def ask(llm, case: dict) -> dict:
    return {"supported": llm.verify_answer(case["answer"], case["material"], None)}


def score(case: dict, got: dict) -> dict:
    expected = bool(case["expected_supported"])
    flags = []
    critical = None
    if got["supported"] and not expected:
        critical = "unsupported_answer_accepted"
    if expected and not got["supported"]:
        flags.append("supported_rejected")
    return {"correct": got["supported"] == expected, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("qa_verify", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("qa_verify", run, DATASET)
