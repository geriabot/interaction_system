# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A of E0a (the choice of the local model): the narrow questions of the interaction system,
offline, with several Ollama models and repetitions, and the safety gate.

Runs the phase A benchmarks (both languages) for each model and repetition, and the benchmarks
reported in E0a without gating (REPORTED_BENCHMARKS: wording, the form of the spoken lines),
then applies the safety gate to the phase A ones only, and writes under --out:
  <model>/rep<k>/<benchmark>.json   the full results of each run (per case)
  summary.csv                       one row per model, benchmark and repetition (for wording,
                                    the pass rate of each check: rate_<check>)
  gate.json                         per model: passed, critical errors per category, metrics
  finalists.txt                     the models that passed, smaller first
  report.md                         the gate, readable, with the critical cases to review
  model_info.json                   the size of each model (read from the server)
Two passes (the author, decision 13): repetition 1 of every model, then the gate, then
repetitions 2..reps only for the models that still pass in at least one language (gate.json
records the repetitions each model ran and why it stopped).
It resumes where it stopped: a run already saved is not repeated. With --pull, a model missing on
the server is pulled first; with --remove-pulled, the models this run pulled are removed once
their runs are done (never the models that were on the server before this work).

The models come from --models and --models-file; without either, from the registered candidates
(e0a_candidates.txt). Each model runs with the reasoning off (think=false) unless it cannot switch
it off (gpt-oss: think "low"), and with the context of the configuration (num_ctx); what it used
is recorded in model_info.json and in every run, with whether the model is entirely in GPU
memory with that context (gpu: size, size_vram, fits; /api/ps).

  python run_models.py --config config_ollama.yaml --reps 3 --pull --remove-pulled \\
      --out ../results/e0a_phase_a
  python run_models.py --reps 3 --out ../results/e0a_phase_a --gate-only
"""

import argparse
import csv
import importlib
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.path_setup import load_config, resolve_path  # noqa: E402
from core.gate import (PHASE_A_BENCHMARKS, compute_gate, dataset_rescorer,  # noqa: E402
                       finalists, run_path, write_outputs)
from core import ollama_models  # noqa: E402
from run_eval import BENCHMARKS  # noqa: E402
from benchmarks.bench_wording import CHECKS as WORDING_CHECKS  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import provenance  # noqa: E402

# Run in phase A by default and reported, never gating (the safety gate takes only
# PHASE_A_BENCHMARKS): the form of the spoken lines
REPORTED_BENCHMARKS = ["wording"]

# The figures of each benchmark kept in summary.csv (from its "global" block)
SUMMARY_KEYS = {
    "extraction": ["precision", "recall", "f1"],
    "intent": ["accuracy", "macro_f1", "confirms_precision"],
    "task_mode": ["accuracy", "macro_f1"],
    "quiz": ["accuracy", "fp", "fn"],
    **{name: ["accuracy", "trap_accuracy", "critical_total", "no_answer", "calls"]
       for name in PHASE_A_BENCHMARKS},
    "wording": ["accuracy", "no_answer", "calls"] + [f"rate_{c}" for c in WORDING_CHECKS],
}

CANDIDATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e0a_candidates.txt")

# A run with a transport error (the request or its answer was lost: connection, timeout, HTTP
# 5xx; core/llm_adapter.TransportError) is not saved: it would count the lost calls as the
# model's. It is run again, after waiting for the server, up to this many times; then the run
# stops, to be resumed
TRANSPORT_RETRIES = 2


def flatten(name: str, results: dict) -> dict:
    """The figures of one run kept in summary.csv, plus the latencies of its LLM calls."""
    g = results.get("global", {})
    row = {k: g.get(k) for k in SUMMARY_KEYS[name]}
    if name == "intent" and row.get("confirms_precision") is None:
        pc = results.get("by_class", {}).get("confirms", {})
        row["confirms_precision"] = pc.get("precision")
    if name == "quiz":
        cases = results.get("cases", [])
        row["fp"] = row.get("fp", g.get("false_positives"))
        row["fn"] = row.get("fn", g.get("false_negatives"))
        if row["fp"] is None and cases:
            row["fp"] = sum(1 for c in cases if c.get("got") is True and c.get("expected") is False)
    latencies = [c.get("latency_s") for c in results.get("cases", []) if c.get("latency_s")]
    if latencies:
        latencies.sort()
        row["latency_p50_s"] = latencies[len(latencies) // 2]
        row["latency_p95_s"] = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
    row["errors"] = g.get("errors")
    return row


def write_summary(out: str, summary_path: str) -> None:
    """summary.csv from every run saved under out (all the models run so far)."""
    rows = []
    for model_dir in sorted(os.listdir(out)):
        model_path = os.path.join(out, model_dir)
        if not os.path.isdir(model_path):
            continue
        for rep_dir in sorted(os.listdir(model_path)):
            if not rep_dir.startswith("rep"):
                continue
            for file in sorted(os.listdir(os.path.join(model_path, rep_dir))):
                name = file[:-len(".json")]
                if name not in SUMMARY_KEYS:
                    continue
                with open(os.path.join(model_path, rep_dir, file), encoding="utf-8") as f:
                    results = json.load(f)
                row = {"model": results.get("model", model_dir), "rep": int(rep_dir[3:]),
                       "benchmark": name, "wall_time_s": results.get("wall_time_s")}
                row.update(flatten(name, results))
                rows.append(row)
    if not rows:
        return
    fields = sorted({k for r in rows for k in r},
                    key=lambda k: (k not in ("model", "rep", "benchmark"), k))
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_models(models: str | None, models_file: str | None) -> list[str]:
    """The models from --models (comma-separated) and --models-file (one per line; # comments),
    in order, without repetitions."""
    names = [m.strip() for m in (models or "").split(",")]
    if models_file:
        with open(models_file, encoding="utf-8") as f:
            names += [line.split("#", 1)[0].strip() for line in f]
    out = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def _json(path: str, default):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run_model(cfg: dict, model: str, args, out: str, benchmarks: list[str],
              ollama: bool, base: str, info: dict, pulled: list, wanted_think=False,
              reps: list[int] | None = None) -> None:
    """Every missing run of one model in the given repetitions (all by default), pulling it
    first and removing it after, if asked."""
    reps = reps or list(range(1, args.reps + 1))
    todo = [(rep, bench) for rep in reps for bench in benchmarks
            if not os.path.isfile(run_path(out, model, rep, bench))]
    if not todo:
        print(f"\n== {model}: every run is done", flush=True)
    else:
        print(f"\n== {model}: {len(todo)} run(s) to do", flush=True)
    settings = info.get(model) if isinstance(info.get(model), dict) else {}
    if todo and ollama:
        if not ollama_models.wait_for_server(base):
            sys.exit(f"The Ollama server at {base} does not answer: stopping (resume later)")
        try:
            if model not in ollama_models.installed(base):
                if not args.pull:
                    print(f"   {model} is not on the server (use --pull): skipped", flush=True)
                    return
                if model not in pulled:  # recorded first: a pull cut halfway is cleaned too
                    pulled.append(model)
                    _save_json(os.path.join(out, "pulled.json"), pulled)
                ollama_models.pull(base, model)
            think = ollama_models.warm_up(base, model, wanted_think, cfg["llm"].get("num_ctx"))
            # Whether it is entirely in GPU memory with the run's context (E0a chooses only a
            # model that fits)
            fit = ollama_models.gpu_fit(base, model)
            provenance.add_models(out, [model], base)
        except requests.RequestException as exc:
            print(f"   {model} could not be pulled or loaded ({exc}): skipped", flush=True)
            _remove_pulled(args, base, model, pulled, out)
            return
        settings = {"size_b": settings.get("size_b") or ollama_models.show_size(base, model)
                    or ollama_models.parse_size(model),  # read while the model is on the server
                    "think": think, "num_ctx": cfg["llm"].get("num_ctx"), "gpu": fit}
        info[model] = settings
        _save_json(os.path.join(out, "model_info.json"), info)

    cfg["llm"]["model"] = model
    cfg["llm"]["think"] = settings.get("think", wanted_think)
    for rep, name in todo:
        mod = importlib.import_module(BENCHMARKS[name][0])
        for attempt in range(TRANSPORT_RETRIES + 1):
            t0 = time.monotonic()
            results = mod.run(cfg, resolve_path(BENCHMARKS[name][1]), verbose=False)
            lost = results.get("global", {}).get("transport_errors", 0)
            if not lost:
                break
            print(f"   {model} rep{rep}/{name}: {lost} call(s) lost in transport: not saved",
                  flush=True)
            if attempt == TRANSPORT_RETRIES or (ollama and not ollama_models.wait_for_server(base)):
                if ollama:
                    ollama_models.unload(base, model)
                sys.exit(f"Transport errors in {model} rep{rep}/{name} (the server or the tunnel "
                         "failed): not saved; stopping (resume later: it is run again)")
        results["wall_time_s"] = round(time.monotonic() - t0, 1)
        results["think"] = cfg["llm"]["think"]
        results["num_ctx"] = cfg["llm"].get("num_ctx")
        path = run_path(out, model, rep, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _save_json(path, results)
        write_summary(out, os.path.join(out, "summary.csv"))

    if ollama:
        # Out of the GPU whether pulled or not (the next model, or the next phase, finds the
        # server free), then removed if this run pulled it
        if todo:
            ollama_models.unload(base, model)
        _remove_pulled(args, base, model, pulled, out)


def _remove_pulled(args, base: str, model: str, pulled: list, out: str) -> None:
    """With --remove-pulled, remove the model if this run pulled it (disk is limited)."""
    if not args.remove_pulled or model not in pulled:
        return
    try:
        ollama_models.remove(base, model)
    except requests.RequestException as exc:
        if getattr(exc.response, "status_code", None) != 404:  # 404: not there (a failed pull)
            print(f"   {model} could not be removed ({exc})", flush=True)
            return
    pulled.remove(model)
    _save_json(os.path.join(out, "pulled.json"), pulled)


def survivors_after_rep1(out: str, models: list[str], benchmarks: list[str],
                         languages: list[str], rescorer=None) -> list[str]:
    """The models whose repetition 1 passes the gate in at least one language (only these run
    the other repetitions: with zero tolerance, a model that failed in every language is out)."""
    if not benchmarks:
        return list(models)
    gate = compute_gate(out, models, 1, benchmarks, languages, rescorer=rescorer)
    return [m for m in models if any(gate[m]["passed_by_language"].values())]


def rescore_specs(benchmarks: list[str]) -> dict:
    """{benchmark: (score, current dataset, extra figures or None)} for the gate's rescoring."""
    specs = {}
    for name in benchmarks:
        mod = importlib.import_module(BENCHMARKS[name][0])
        specs[name] = (mod.score, resolve_path(BENCHMARKS[name][1]), getattr(mod, "EXTRA", None))
    return specs


def _settings(entry, model: str) -> dict:
    """What a model ran with (model_info.json), its size from its tag if unknown."""
    entry = entry if isinstance(entry, dict) else {"size_b": entry}
    return {**entry, "size_b": entry.get("size_b") or ollama_models.parse_size(model)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config_ollama.yaml")
    parser.add_argument("--models", default="", help="Comma-separated models")
    parser.add_argument("--models-file", default=None,
                        help="A file with one model per line (# starts a comment); without "
                             "--models nor --models-file: e0a_candidates.txt")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--benchmarks",
                        default=",".join(PHASE_A_BENCHMARKS + REPORTED_BENCHMARKS),
                        help="Comma-separated (default: the phase A benchmarks and wording, "
                             "which is reported, not gated)")
    parser.add_argument("--languages", default="en,es")
    parser.add_argument("--out", default="../results/e0a_phase_a")
    parser.add_argument("--pull", action="store_true", help="Pull the models missing on the server")
    parser.add_argument("--remove-pulled", action="store_true",
                        help="Remove the models this run pulled once their runs are done")
    parser.add_argument("--gate-only", action="store_true",
                        help="Only compute the gate from the runs already saved")
    args = parser.parse_args()

    models_file = args.models_file or (None if args.models else CANDIDATES)
    models = read_models(args.models, models_file)
    if not models:
        parser.error("no models: give --models or --models-file")
    benchmarks = [b.strip() for b in args.benchmarks.split(",") if b.strip()]
    unknown = [b for b in benchmarks if b not in BENCHMARKS]
    if unknown:
        parser.error(f"unknown benchmarks {unknown}; available: {sorted(BENCHMARKS)}")
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]

    cfg = load_config(args.config)
    cfg["languages"] = languages
    cfg.setdefault("llm", {})
    wanted_think = cfg["llm"].get("think", False)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    ollama = cfg.get("llm", {}).get("provider") == "ollama"
    base = ollama_models.base_url(cfg)
    info = _json(os.path.join(out, "model_info.json"), {})
    pulled = _json(os.path.join(out, "pulled.json"), [])

    phase_a = [b for b in benchmarks if b in PHASE_A_BENCHMARKS]
    # The saved answers scored again with the current datasets (a pre-registered correction of a
    # label needs no new run); the hash of each dataset goes to gate.json
    rescorer, hashes = dataset_rescorer(rescore_specs(phase_a))
    if not args.gate_only:
        # What code and models produce these results; a resume with other code is refused
        provenance.record(out, models, base if ollama else None)
        # Pass 1: repetition 1 of every model
        for model in models:
            run_model(cfg, model, args, out, benchmarks, ollama, base, info, pulled,
                      wanted_think, reps=[1])
        write_summary(out, os.path.join(out, "summary.csv"))
    # Pass 2: the other repetitions, only for the models that pass in some language after rep 1
    survivors = survivors_after_rep1(out, models, phase_a, languages, rescorer)
    if not args.gate_only and args.reps > 1:
        print(f"\nAfter repetition 1, still passing in some language: "
              f"{', '.join(survivors) or 'none'}", flush=True)
        for model in survivors:
            run_model(cfg, model, args, out, benchmarks, ollama, base, info, pulled,
                      wanted_think, reps=list(range(2, args.reps + 1)))
        write_summary(out, os.path.join(out, "summary.csv"))

    settings = {m: _settings(info.get(m), m) for m in models}
    reps_by_model = {m: args.reps if m in survivors else 1 for m in models}
    gate = compute_gate(out, models, args.reps, phase_a, languages, settings, rescorer, hashes,
                        reps_by_model)
    chosen = write_outputs(out, gate, phase_a, args.reps, languages)
    print(f"\nSummary: {os.path.join(out, 'summary.csv')}")
    print(f"Gate: {os.path.join(out, 'gate.json')} and report.md")
    print(f"Finalists ({len(chosen)}): {', '.join(chosen) or 'none'}")
    if not args.gate_only:
        provenance.catalog("finish", out, {
            "models": models, "reps": args.reps, "reps_2_on": survivors,
            "finalists": chosen,
            "finalists_by_language": {lang: finalists(gate, lang) for lang in languages},
            "fallback_by_language": {lang: gate["_finalists"][lang]["fallback"]
                                     for lang in languages},
            "datasets": hashes})


if __name__ == "__main__":
    main()
