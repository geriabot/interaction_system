# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: the understanding of a gathering turn of slot filling (extract_turn), as
slot_filling.update asks it: what the turn does (values, question, skip, repeat, other) and the
values it gives or corrects.

A case is correct when the act and the values are both right. Not in the safety gate: every
value and every slot left blank is read back at the confirmation, where the user can correct it
(the confirmation question is in the gate). Reported apart: a false skip (a slot left blank
although the user did not decline it: "It's Laura" taken as a skip), a missed skip, values
invented and values missed, and the precision, recall and F1 of the values.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import build_frame, context, main, norm_value, run_cases  # noqa: E402

DATASET = "datasets/phase_a/turn_act.json"


def _pairs(extractions: list[dict]) -> list[tuple]:
    """(slot, value) pairs, a list operation kept as (kind, items)."""
    pairs = []
    for e in extractions:
        value = e.get("value")
        for kind in ("remove", "replace", "add"):
            if e.get(f"_{kind}") is True:
                value = {f"_{kind}": True, "items": value if isinstance(value, list) else [value]}
        pairs.append((e.get("slot"), norm_value(value)))
    return sorted(pairs, key=repr)


def ask(llm, case: dict) -> dict:
    frame = build_frame(case)
    act, extractions = llm.extract_turn(case["user_text"], frame, context(case))
    return {"act": act, "extractions": [
        {k: v for k, v in e.items() if k != "source_span"} for e in extractions]}


def score(case: dict, got: dict) -> dict:
    expected_act = case["expected_act"]
    expected = _pairs(case.get("expected_extractions", []))
    found = _pairs(got.get("extractions", []))
    remaining = list(expected)
    tp = 0
    for pair in found:
        if pair in remaining:
            remaining.remove(pair)
            tp += 1
    fp, fn = len(found) - tp, len(remaining)
    flags = []
    if got["act"] != expected_act:
        flags.append("act_wrong")
        if got["act"] == "skip":
            flags.append("false_skip")
        if expected_act == "skip":
            flags.append("missed_skip")
    if fp:
        flags.append("value_invented")
    if fn:
        flags.append("value_missed")
    return {"correct": got["act"] == expected_act and fp == 0 and fn == 0,
            "critical": None, "flags": flags, "tp": tp, "fp": fp, "fn": fn}


def _values(records: list[dict]) -> dict:
    """The accuracy of the act and the precision, recall and F1 of the values."""
    acts = [r for r in records if not {"act_wrong", "case_error"} & set(r["flags"])]
    tp = sum(r.get("tp", 0) for r in records)
    fp = sum(r.get("fp", 0) for r in records)
    fn = sum(r.get("fn", 0) for r in records)
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"act_accuracy": round(len(acts) / len(records), 4) if records else None,
            "value_precision": round(p, 4), "value_recall": round(rc, 4),
            "value_f1": round(2 * p * rc / (p + rc), 4) if p + rc else 0.0}


# The figures of this benchmark besides the accuracy (the gate summarizes them again when it
# scores the saved answers with the current dataset: core/gate.py)
EXTRA = _values


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("turn_act", cfg, dataset_path, ask, score, verbose, infer_fn,
                     extra=_values)


if __name__ == "__main__":
    main("turn_act", run, DATASET)
