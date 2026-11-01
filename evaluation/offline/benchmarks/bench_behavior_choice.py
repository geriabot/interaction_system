# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Benchmark: the choice of the dialogue behavior for a request without dialog_mode (E2).

Methods:
  - method:    the interaction system's choice: the catalog filtered by what the request provides
               (interaction_intelligence.selection), then the LLM among the offered behaviors
  - no_filter: the LLM chooses among every behavior of the catalog, whatever the request provides
  - keywords:  a rule baseline on the words of the task
  - task_mode: the two-mode classifier of the starting system (slot filling or explanation);
               kept as a tool, not run by "all" (not a condition of the experiments)

Metrics:
  - accuracy on the feasible cases (the behavior asked for can run with the request)
  - infeasible choices: a behavior whose needs the request does not provide (the dialogue
    would fail at once), over every case
  - on the cases that ask for a behavior that cannot run: the rate at which it is avoided
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.path_setup import setup_path, load_config, load_prompts, resolve_path  # noqa: E402

setup_path()

from core.llm_adapter import make_infer_fn  # noqa: E402
from core.report import print_section, print_summary, save_json  # noqa: E402

from interaction_intelligence.llm_client import LLMDialogClient  # noqa: E402
from interaction_intelligence.prompt_builder import PromptBuilder  # noqa: E402
from interaction_intelligence.selection import choose_behavior, provided_needs  # noqa: E402

from benchmarks.bench_task_mode import load_catalog  # noqa: E402

# The conditions of the experiment ("all"); task_mode only on request
METHODS = ["method", "no_filter", "keywords"]

KEYWORDS = [
    ("roleplay", r"\b(practi[sc]e|rehearse|role ?play|play the|ensaya|practica)\b"),
    ("questionnaire", r"\b(survey|questionnaire|scale|rate|feedback form|evaluation questionnaire|"
                      r"encuesta|escala|valora)\b"),
    ("grounded_qa", r"\b(answer .*(questions|ask)|information point|reception desk|doubts|"
                    r"responde .*preguntas|punto de informaci[oó]n)\b"),
    ("quiz", r"\b(quiz|test|questions|exam|evaluate|check whether .* remembers|memory exercise|"
             r"preguntas|cuestionario)\b"),
    ("procedure", r"\b(guide|step|walk .* through|help .* (prepare|measure|do)|exercises|"
                  r"acompaña|guía|pasos)\b"),
    ("explanation", r"\b(explain|tell|inform|describe|understand|explica)\b"),
]


def build_resources(short: dict) -> list[dict]:
    """The resources of a case, from its short form."""
    out = []
    if short.get("bank"):
        out.append({"name": "questions", "description": "quiz bank", "content": json.dumps(
            [{"question": "What is the capital of France?", "answer": "Paris"},
             {"question": "How many legs does a spider have?", "answer": "8"}])})
    if short.get("steps"):
        out.append({"name": "steps", "description": "steps", "content": json.dumps(
            ["Do the first step", "Do the second step", "Do the last step"])})
    if short.get("scene"):
        out.append({"name": "scene", "description": "roleplay scene", "content": json.dumps(
            {"scenario": "A practice situation", "role": "The other person",
             "criteria": ["Speaks clearly"]})})
    if short.get("text"):
        out.append({"name": "info", "description": "reference", "content": short["text"]})
    if short.get("instrument"):
        out.append({"name": "instrument", "description": "questionnaire", "content": json.dumps(
            {"options": ["Not at all", "A little", "Quite", "Very much"],
             "items": ["The first statement", "The second statement"]})})
    return out


def keywords_choice(task: str) -> str:
    text = task.lower()
    for mode, pattern in KEYWORDS:
        if re.search(pattern, text):
            return mode
    return "slot_filling"


def run(cfg: dict, dataset_path: str, verbose: bool = False, method: str = "method") -> dict:
    templates = load_prompts(resolve_path(cfg["prompts_yaml"]))
    language = cfg.get("dialog", {}).get("language", "English")
    llm = LLMDialogClient("eval_nlu", "eval_nlg", make_infer_fn(cfg),
                          PromptBuilder(templates, language=language), language=language)
    catalog = load_catalog()
    by_id = {b["behavior_id"]: b for b in catalog}
    by_mode = {b["mode"]: b for b in catalog}

    with open(dataset_path, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print_section(f"BENCHMARK: behavior choice ({method})")
    results = []
    for case in cases:
        resources = build_resources(case.get("resources", {}))
        provided = provided_needs(resources)
        t0 = time.monotonic()
        if method == "method":
            chosen, _ = choose_behavior(llm, case["task"], catalog, resources, None)
            got = by_id[chosen]["mode"] if chosen in by_id else "none"
        elif method == "no_filter":
            chosen = llm.choose_dialogue_behavior(case["task"], catalog, None)
            got = by_id[chosen]["mode"] if chosen in by_id else "none"
        elif method == "keywords":
            got = keywords_choice(case["task"])
        else:
            got = llm.classify_task_mode(case["task"], None)
        elapsed = time.monotonic() - t0
        infeasible = got in by_mode and not set(by_mode[got]["needs"]) <= provided
        row = {"id": case["id"], "task": case["task"], "expected": case["expected"],
               "infeasible_target": case.get("infeasible_target"), "got": got,
               "correct": case["expected"] is not None and got == case["expected"],
               "infeasible_choice": infeasible, "latency_s": round(elapsed, 3)}
        results.append(row)
        if verbose:
            print(f"  {case['id']} {got:13s} {'ok' if row['correct'] else '--'} "
                  f"{'INFEASIBLE' if infeasible else ''}")

    feasible = [r for r in results if r["expected"] is not None]
    asked_infeasible = [r for r in results if r["expected"] is None]
    summary = {
        "accuracy": round(sum(r["correct"] for r in feasible) / len(feasible), 4),
        "infeasible_rate": round(sum(r["infeasible_choice"] for r in results) / len(results), 4),
        "avoided_on_infeasible_requests": round(
            sum(not r["infeasible_choice"] for r in asked_infeasible) / len(asked_infeasible), 4)
        if asked_infeasible else None,
        "errors": 0,
    }
    print_summary("Accuracy (feasible cases)", summary["accuracy"])
    print_summary("Infeasible choices", summary["infeasible_rate"])
    print_summary("Avoided when asked for what cannot run", summary["avoided_on_infeasible_requests"])
    return {"benchmark": "behavior_choice", "method": method,
            "provider": cfg["llm"]["provider"], "model": cfg["llm"]["model"],
            "total_cases": len(cases), "global": summary, "cases": results}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--method", choices=METHODS + ["task_mode", "all"], default="all")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    dataset = args.dataset or resolve_path("datasets/behavior_choice.json")
    for m in (METHODS if args.method == "all" else [args.method]):
        res = run(cfg, dataset, verbose=args.verbose, method=m)
        if args.save:
            path = save_json(res, f"behavior_choice_{m}",
                             resolve_path(cfg.get("results_dir", "results")))
            print(f"  Saved to: {path}")
