# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
How the recorded answers of the model read through the client's parser and through the
interpretation server's: the ground for criterion 4 (the same decisions from the same answers).

The client reads each answer of an understanding question with utils.parse_loose_json, and a
spoken line as the text itself. The interpretation server reads every answer as a JSON object
(behavior_interpretation.validation.parse_answer, `parse: loose`); an answer it cannot read is
`invalid`. For each recorded call (the `model_calls` of the dialogue records written with
`record_model_calls`, evaluation/doc/DEBUGGING.md) this tool says whether both read the same.

    python3 answers.py <results dir> [<results dir> ...]      # e.g. results/debug_round7

Categories: a spoken line (no schema) that the server rejects or reads as JSON; for the
understanding questions, the same object, both fail, only one of them reads it, or different
objects.
"""

import argparse
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(
    HERE, "..", "..", "interaction_system_core", "interaction_intelligence")))


def classify(answer: str, has_schema: bool) -> str:
    from behavior_interpretation.validation import InvalidAnswer, parse_answer
    from interaction_intelligence.utils import parse_loose_json

    try:
        server = parse_answer(answer)
    except InvalidAnswer:
        server = None
    if not has_schema:
        return "line: the server rejects it" if server is None else "line: the server reads JSON"
    try:
        client = parse_loose_json(answer)
    except Exception:  # noqa: B902 (the client takes any failure as no answer)
        client = None
    if client is None and server is None:
        return "json: both fail"
    if client is None:
        return "json: only the server reads it"
    if server is None:
        return "json: only the client reads it"
    return "json: same object" if client == server else "json: different objects"


def calls(paths: list[str]):
    """The recorded calls with an answer: (question, answer, has schema)."""
    for root in paths:
        for path in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
            try:
                with open(path, encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            for call in record.get("model_calls") or []:
                if call.get("answer") is not None:
                    yield call.get("question", ""), call["answer"], bool(call.get("schema"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    totals = collections.Counter()
    by_question = collections.Counter()
    for question, answer, has_schema in calls(args.results):
        kind = classify(answer, has_schema)
        totals[kind] += 1
        if kind not in ("json: same object", "json: both fail"):
            by_question[(question, kind)] += 1
    for kind, n in sorted(totals.items()):
        print(f"{kind}: {n}")
    for (question, kind), n in by_question.most_common(20):
        print(f"  {question} ({kind}): {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
