# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Criterion 3 of the equivalence of the interpretation path with the former LLM gateway: for
every question, the HTTP body that reaches the model's server is the same, byte for byte.

The calls of the corpus (corpus.py: every question of LLMDialogClient, in English and
Spanish, with and without reasoning) are made through two paths, each against a local stand-in
of the model's server that records the bodies (fake_backend.py):

  gateway         the client's call as the interaction intelligence made it through
                  ros2_llm_manager: the goal of /llm/inference (intelligence_node._infer_sync)
                  and the manager's handling of it (llm_manager_node._on_inference: the session's
                  messages, the schema read back from JSON, streaming), with the gateway's
                  provider (OllamaProvider, OpenAIProvider) and its HTTP client
  interpretation  the client's transport of the interaction intelligence: the question asked to
                  behavior_architecture's InterpretationServer with the interaction system's
                  questions file, prompts.yaml as its templates and its question module, on
                  routes of the llm_manager dialect: the client sends the arguments of the
                  PromptBuilder method (interpretation_context.py), and the server builds the
                  prompt and the schema

(transports.py.) Both paths make the same calls: the stand-in answers the same text to the same
request, and the client receives it in both. The bodies are then compared call by call.

    python3 bodies.py all [--out DIR]                 # every combination, in this tree
    python3 bodies.py run --path gateway --backend ollama --language Spanish --out g.jsonl
                          [--src <a checkout of the repository, e.g. of main>]
    python3 bodies.py compare g.jsonl i.jsonl

`--src` runs the path with the interaction intelligence and ros2_llm_manager of another
checkout (the former code, when the client itself has changed).
"""

import argparse
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from transports import (Gateway, Interpretation, client, gateway_provider,  # noqa: E402
                        interpretation_server, MODEL, use_sources)

LANGUAGES = ("English", "Spanish")
BACKENDS = ("ollama", "openai")


def run(path: str, backend: str, language: str) -> list[dict]:
    """The bodies of every call of the corpus through one path: [{case, question, reasoning,
    n, body (base64)}]."""
    import logging
    from corpus import REASONING, ablation_cases, cases
    from fake_backend import FakeBackend

    logging.getLogger("LLMDialogClient").setLevel(logging.ERROR)
    fake = FakeBackend().start()
    try:
        if path == "gateway":
            provider = gateway_provider(backend, fake.url)

            def transport_of(limits):
                return Gateway(provider, MODEL[backend], limits)
        else:
            server = interpretation_server(backend, fake.url)

            def transport_of(limits):
                return Interpretation(server, "corpus", limits)
        out = []
        for reasoning in ("none", "critical"):
            for name, question, call in cases(language) + ablation_cases(language):
                if reasoning == "critical" and question not in REASONING:
                    continue
                llm = client(path, transport_of, language, reasoning)
                fake.clear()
                fake.label = name
                call(llm)
                for n, entry in enumerate(b for b in fake.bodies
                                          if b["path"].endswith(("/chat", "/completions"))):
                    out.append({"case": name, "question": question, "reasoning": reasoning,
                                "n": n, "body": base64.b64encode(entry["body"]).decode()})
        return out
    finally:
        fake.stop()


def compare(a: list[dict], b: list[dict]) -> dict:
    """Call by call: {question: {"calls", "identical", "same_json", "differ": [case...]}}, and
    the calls that only one path made."""
    key = (lambda r: (r["reasoning"], r["case"], r["n"]))
    left, right = {key(r): r for r in a}, {key(r): r for r in b}
    summary: dict = {}
    for k in sorted(set(left) | set(right)):
        question = (left.get(k) or right.get(k))["question"]
        s = summary.setdefault(question, {"calls": 0, "identical": 0, "same_json": 0,
                                          "differ": [], "missing": []})
        s["calls"] += 1
        if k not in left or k not in right:
            s["missing"].append("/".join(map(str, k)))
            continue
        x = base64.b64decode(left[k]["body"])
        y = base64.b64decode(right[k]["body"])
        if x == y:
            s["identical"] += 1
            s["same_json"] += 1
        else:
            if json.loads(x) == json.loads(y):
                s["same_json"] += 1
            s["differ"].append("/".join(map(str, k)))
    return summary


def report(summary: dict, title: str) -> tuple[int, int, int]:
    calls = sum(s["calls"] for s in summary.values())
    identical = sum(s["identical"] for s in summary.values())
    same_json = sum(s["same_json"] for s in summary.values())
    print(f"{title}: {len(summary)} questions, {calls} calls, {identical} identical bytes, "
          f"{same_json} identical JSON")
    for question, s in sorted(summary.items()):
        if s["identical"] != s["calls"]:
            print(f"  {question}: {s['identical']}/{s['calls']} identical, "
                  f"{s['same_json']} same JSON; differ {s['differ'][:3]} missing "
                  f"{s['missing'][:3]}")
    return calls, identical, same_json


def write(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def read(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("run", help="the bodies of one path")
    one.add_argument("--path", choices=("gateway", "interpretation"), required=True)
    one.add_argument("--backend", choices=BACKENDS, default="ollama")
    one.add_argument("--language", choices=LANGUAGES, default="English")
    one.add_argument("--src", default=None)
    one.add_argument("--out", required=True)
    both = sub.add_parser("compare", help="compare the bodies of two runs")
    both.add_argument("a")
    both.add_argument("b")
    every = sub.add_parser("all", help="every backend and language, both paths, in this tree")
    every.add_argument("--out", default="")
    args = parser.parse_args(argv)

    if args.command == "compare":
        calls, identical, _ = report(compare(read(args.a), read(args.b)),
                                     f"{args.a} vs {args.b}")
        return 0 if calls and identical == calls else 1
    use_sources(getattr(args, "src", None))
    if args.command == "run":
        write(run(args.path, args.backend, args.language), args.out)
        return 0
    status = 0
    for backend in BACKENDS:
        for language in LANGUAGES:
            g = run("gateway", backend, language)
            i = run("interpretation", backend, language)
            if args.out:
                os.makedirs(args.out, exist_ok=True)
                write(g, os.path.join(args.out, f"gateway_{backend}_{language}.jsonl"))
                write(i, os.path.join(args.out, f"interpretation_{backend}_{language}.jsonl"))
            calls, identical, same_json = report(compare(g, i), f"{backend} {language}")
            if not calls or identical != calls:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
