# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Benchmark: dialog mode classification (task_mode).

Measures whether the system correctly assigns slot_filling vs explanation
from the textual task description.

Two methods:
  - behavior (default): the interaction system's choice of the dialogue behavior
    (interaction_intelligence.selection) from the catalog of the orchestrator
    (interaction_orchestrator_params.yaml). Without resources, the behaviors offered are those
    that need nothing: slot filling and explanation.
  - task_mode: the original classifier of DialoStack (prompt classify_task_mode).

Metrics:
  - Global accuracy
  - Per-class precision / recall / F1
  - Confusion matrix
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.path_setup import setup_path, load_config, load_prompts, resolve_path

setup_path()

from core.llm_adapter import make_infer_fn
from core.metrics import MultiMetrics, print_confusion
from core.report import print_section, print_table, print_summary, save_json

from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.selection import choose_behavior

_LABELS = ["slot_filling", "explanation"]
_CATALOG = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "interaction_system_core", "interaction_system",
    "config", "interaction_orchestrator_params.yaml",
)


def load_catalog() -> list[dict]:
    """The dialogue behaviors of the orchestrator's catalog, as the intelligence receives them."""
    import yaml

    with open(_CATALOG, encoding="utf-8") as f:
        params = yaml.safe_load(f)["/interaction_orchestrator"]["ros__parameters"]
    catalog = params["dialogue_behaviors"]
    return [
        {
            "behavior_id": catalog[name]["behavior_id"],
            "name": name,
            "mode": catalog[name]["mode"],
            "explanation": catalog[name]["explanation"],
            "needs": [n for n in catalog[name]["needs"] if n],
        }
        for name in catalog["behaviors"]
    ]


def run(cfg: dict, dataset_path: str, verbose: bool = False, method: str = "behavior") -> dict:
    prompts_path = resolve_path(cfg["prompts_yaml"])
    templates = load_prompts(prompts_path)
    language = cfg.get("dialog", {}).get("language", "Spanish")
    history_max = cfg.get("dialog", {}).get("history_max_turns", 200)

    infer_fn = make_infer_fn(cfg)
    pb = PromptBuilder(templates, language=language)
    llm = LLMDialogClient(
        nlu_session_id="eval_nlu",
        nlg_session_id="eval_nlg",
        infer_fn=infer_fn,
        prompt_builder=pb,
        language=language,
        history_max_turns=history_max,
    )

    catalog = load_catalog()
    modes = {b["behavior_id"]: b["mode"] for b in catalog}

    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)
    cases = dataset.get("cases", [])

    metrics = MultiMetrics(labels=_LABELS)
    case_results = []

    print_section("BENCHMARK: Task mode classification")
    print(f"  Dataset : {os.path.basename(dataset_path)}")
    print(f"  Cases   : {len(cases)}")
    print(f"  Method  : {method}")
    print(f"  Provider: {cfg['llm']['provider']} / {cfg['llm']['model']}\n")

    total = len(cases)
    for idx, case in enumerate(cases, 1):
        cid = case["id"]
        task = case["task"]
        expected = case["expected_mode"]

        print(f"  [{idx}/{total}] {cid} ...", end="", flush=True)
        if verbose:
            print(f"\n         {case['description']}")
            print(f"         task: '{task[:80]}'")

        t0 = time.monotonic()
        try:
            if method == "task_mode":
                got = llm.classify_task_mode(task, ctx=None)
            else:
                chosen, _ = choose_behavior(llm, task, catalog, [], None)
                got = modes.get(chosen, "none")
        except Exception as exc:
            metrics.errors += 1
            case_results.append({"id": cid, "status": "llm_error", "error": str(exc)})
            continue
        elapsed = time.monotonic() - t0

        metrics.record(got, expected)
        correct = got == expected

        case_results.append(
            {
                "id": cid,
                "description": case["description"],
                "task": task,
                "expected": expected,
                "got": got,
                "correct": correct,
                "latency_s": round(elapsed, 3),
            }
        )

        mark = "✓" if correct else "✗"
        print(f" {got}  {mark}  ({elapsed:.2f}s)", flush=True)
        if verbose:
            print(f"         got: {got}")

    pc = metrics.per_class()
    print_section("GLOBAL RESULTS")
    print_summary("Accuracy", f"{metrics.accuracy:.3f}")
    print_summary("Macro-F1", f"{metrics.macro_f1():.3f}")
    print_summary("LLM errors", metrics.errors)

    class_rows = []
    for label in _LABELS:
        m = pc[label]
        class_rows.append(
            [label, m.support, f"{m.precision:.3f}", f"{m.recall:.3f}", f"{m.f1:.3f}"]
        )
    print_table(
        ["Class", "Support", "Precision", "Recall", "F1"],
        class_rows,
        title="Per-class metrics",
    )

    print("\n  Confusion matrix (rows=true, columns=predicted):")
    print_confusion(metrics.confusion_matrix(), _LABELS)

    case_rows = [
        [
            r["id"],
            r.get("expected", "—"),
            r.get("got", "ERROR"),
            "✓" if r.get("correct") else "✗",
            r.get("latency_s", "—"),
        ]
        for r in case_results
    ]
    print_table(["ID", "Expected", "Got", "OK?", "Latency(s)"], case_rows, title="Per-case results")

    results = {
        "benchmark": "task_mode",
        "method": method,
        "provider": cfg["llm"]["provider"],
        "model": cfg["llm"]["model"],
        "total_cases": len(cases),
        "global": {
            "accuracy": round(metrics.accuracy, 4),
            "macro_f1": round(metrics.macro_f1(), 4),
            "errors": metrics.errors,
        },
        "by_class": {
            label: {
                "precision": round(pc[label].precision, 4),
                "recall": round(pc[label].recall, 4),
                "f1": round(pc[label].f1, 4),
                "support": pc[label].support,
            }
            for label in _LABELS
        },
        "confusion_matrix": metrics.confusion_matrix(),
        "cases": case_results,
    }
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Task mode classification benchmark")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--method", choices=["behavior", "task_mode"], default="behavior")
    args = parser.parse_args()

    cfg = load_config(args.config)
    dataset = args.dataset or resolve_path("datasets/task_mode.json")
    results = run(cfg, dataset, verbose=args.verbose, method=args.method)

    if args.save:
        results_dir = resolve_path(cfg.get("results_dir", "results"))
        path = save_json(results, f"task_mode_{args.method}", results_dir)
        print(f"\n  Saved to: {path}")
