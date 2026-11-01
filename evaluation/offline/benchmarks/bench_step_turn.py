# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: a turn of a guided procedure (classify_step_turn), as procedure.classify asks
it: done, question, problem, repeat, back, skip or unclear.

Critical (false_step_advance): the step closed as "done" when the user did not report it done,
or as "skipped" when the user neither asked to skip it nor finished it. The procedure moves on
(the user may start the next step before finishing this one) and the step is recorded so.
Reported apart: a step reported done not taken as done (done_missed: the robot reminds or
helps: conservative) and a finished step taken as a skip (done_as_skip: recorded as not done).

A case gives the whole procedure ("steps": [{step, check}], which the system has in the context
of every prompt, as a resource), the index of the current step (0-based), the conversation
before the turn ("history") and the user's turn.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import context, main, run_cases  # noqa: E402

DATASET = "datasets/phase_a/step_turn.json"


def ask(llm, case: dict) -> dict:
    steps = case["steps"]
    current = steps[case["step_index"]]
    ctx_case = {"domain": case.get("domain", ""),
                "resources": [{"name": "steps", "description": "the steps of the procedure",
                               "content": json.dumps(steps, ensure_ascii=False)}]}
    history = list(case.get("history", [])) + [{"role": "user", "content": case["user_text"]}]
    intent = llm.classify_step_turn(case["user_text"], current["step"], current.get("check", ""),
                                    history, context(ctx_case))
    return {"intent": intent}


def score(case: dict, got: dict) -> dict:
    expected = case["expected_intent"]
    intent = got["intent"]
    flags = []
    critical = None
    if (intent == "done" and expected != "done") or (
            intent == "skip" and expected not in ("skip", "done")):
        critical = "false_step_advance"
    if expected == "done" and intent != "done":
        flags.append("done_as_skip" if intent == "skip" else "done_missed")
    return {"correct": intent == expected, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("step_turn", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("step_turn", run, DATASET)
