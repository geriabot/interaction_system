# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the mapping of a questionnaire answer to an option of the scale
(map_scale_answer), as questionnaire.record asks it: option (with its number), ambiguous,
meaning, refuses, repeat or other.

Critical (wrong_option_recorded): an option recorded that the reply did not clearly choose (a
different option, or any option for a reply between options, a question, a refusal...). The
item is closed with it and the instrument is never read back. Reported apart: a refusal
recorded when the user did not refuse (false_refusal: the item closes without a value, which
is missing data rather than a false one) and a clear answer not mapped (the options are said
again: conservative).

expected_option is 1-based, as the prompt numbers the options.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/scale_answer.json"


def ask(llm, case: dict) -> dict:
    res = llm.map_scale_answer(case["user_text"], case["item"], case["options"], None)
    return {"kind": res["kind"], "option": res["option"] + 1 if res["kind"] == "option" else None}


def score(case: dict, got: dict) -> dict:
    expected_kind = case["expected_kind"]
    expected_option = case.get("expected_option") if expected_kind == "option" else None
    flags = []
    critical = None
    if got["kind"] == "option" and got["option"] != expected_option:
        critical = "wrong_option_recorded"
    if got["kind"] == "refuses" and expected_kind != "refuses":
        flags.append("false_refusal")
    if expected_kind == "option" and got["kind"] != "option":
        flags.append("clear_answer_missed")
    if got["kind"] != expected_kind:
        flags.append("kind_wrong")
    correct = got["kind"] == expected_kind and got["option"] == expected_option
    return {"correct": correct, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("scale_answer", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("scale_answer", run, DATASET)
