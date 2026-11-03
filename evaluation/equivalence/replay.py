# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Criterion 4: the recorded answers of the model lead to the same decisions through the former
gateway and through the interpretation server.

Every distinct answer recorded for a question (the `model_calls` of the dialogue records
written with `record_model_calls`, evaluation/doc/DEBUGGING.md) is given, as the model's
answer, to the question asked through both paths (transports.py), with the arguments of the
first case of that question in the corpus (corpus.py) and the dialogue's language; for the
questions on which the model may reason, also with reasoning on. The decision is what the
client's method returns (a label, the values understood, a line, a frame), with the client's
model status (model_down) and the number of calls it made; the two are compared answer by
answer.

    python3 replay.py ../results/debug_round1 ... [--backend ollama] [--limit N] [--out FILE]
"""

import argparse
import collections
import glob
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from transports import (Gateway, Interpretation, MODEL, client, gateway_provider,  # noqa: E402
                        interpretation_server, use_sources)

# The names of the questions in the records that differ from the corpus's
RECORDED_NAMES = {"_roleplay_line": "roleplay_reply", "_ask": "monolithic_turn",
                  "_llm_only": "detect_cancel_intent"}


def recorded(paths: list[str]) -> dict:
    """{(question, language, has schema): {answer: count}} of the records under the paths."""
    out: dict = collections.defaultdict(collections.Counter)
    for root in paths:
        for path in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
            try:
                with open(path, encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            language = record.get("language") or "English"
            for call in record.get("model_calls") or []:
                if call.get("answer") is None:
                    continue
                question = RECORDED_NAMES.get(call.get("question", ""), call.get("question", ""))
                out[(question, language, bool(call.get("schema")))][call["answer"]] += 1
    return out


def decision(value):
    """A method's result as plain data (a frame as its state)."""
    from interaction_intelligence.dialog_frame import DialogFrame
    if isinstance(value, DialogFrame):
        return {"frame": value.to_state()}
    if isinstance(value, (list, tuple)):
        return [decision(v) for v in value]
    if isinstance(value, dict):
        return {str(k): decision(v) for k, v in value.items()}
    return value


def decide(path: str, transport_of, language: str, reasoning: str, call, fake) -> dict:
    """What the client decides with the model's answer set in the stand-in."""
    llm = client(path, transport_of, language, reasoning)
    fake.clear()
    try:
        result = decision(call(llm))
    except Exception as exc:  # noqa: B902 (a method that raises is a decision too)
        result = f"raised {type(exc).__name__}: {exc}"
    calls = sum(1 for b in fake.bodies if b["path"].endswith(("/chat", "/completions")))
    return {"result": result, "model_status": llm.model_status, "calls": calls}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", nargs="+")
    parser.add_argument("--backend", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--limit", type=int, default=0, help="answers per question (0: all)")
    parser.add_argument("--out", default="", help="the differences, as JSON lines")
    parser.add_argument("--src", default=None)
    args = parser.parse_args(argv)
    use_sources(args.src)
    from corpus import REASONING, ablation_cases, cases
    from fake_backend import FakeBackend

    logging.getLogger("LLMDialogClient").setLevel(logging.CRITICAL)
    first = {}
    for language in ("English", "Spanish"):
        for name, question, call in cases(language) + ablation_cases(language):
            first.setdefault((question, language), call)
    answers = recorded(args.results)
    fake = FakeBackend().start()
    provider = gateway_provider(args.backend, fake.url)
    server = interpretation_server(args.backend, fake.url)
    paths = {
        "gateway": lambda limits: Gateway(provider, MODEL[args.backend], limits),
        "interpretation": lambda limits: Interpretation(server, "replay", limits),
    }
    totals = collections.Counter()
    per_question = collections.defaultdict(collections.Counter)
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    try:
        for (question, language, _schema), counter in sorted(answers.items()):
            call = first.get((question, language))
            if call is None:
                totals["no case"] += sum(counter.values())
                continue
            modes = ("none", "critical") if question in REASONING else ("none",)
            for answer, n in (counter.most_common(args.limit) if args.limit
                              else counter.items()):
                for reasoning in modes:
                    fake.answers = [answer]
                    got = {p: decide(p, t, language, reasoning, call, fake)
                           for p, t in paths.items()}
                    same = got["gateway"] == got["interpretation"]
                    key = "same" if same else "different"
                    totals[key] += 1
                    totals[key + " (recorded calls)"] += n
                    per_question[question][key] += 1
                    if not same and out:
                        out.write(json.dumps({"question": question, "language": language,
                                              "reasoning": reasoning, "answer": answer,
                                              **got}, ensure_ascii=False, default=str) + "\n")
    finally:
        fake.stop()
        if out:
            out.close()
    for key, n in sorted(totals.items()):
        print(f"{key}: {n}")
    for question, c in sorted(per_question.items()):
        if c["different"]:
            print(f"  {question}: {c['different']} different of {c['same'] + c['different']}")
    print(f"questions: {len(per_question)}")
    return 0 if not totals["different"] else 1


if __name__ == "__main__":
    sys.exit(main())
