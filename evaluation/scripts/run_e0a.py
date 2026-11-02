# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
E0a, the choice of the local model (doc/EXPERIMENTS.md), one model per language (English for E1
and E3, Spanish for E4), in stages, each one resumable:

    python3 run_e0a.py preflight [--allow-cloud]
        The GPU server is free, the API key is set and (with --allow-cloud) one tiny call to the
        simulated user's Gemini model answers.
    python3 run_e0a.py phase-a
        The critical questions offline, every candidate of offline/e0a_candidates.txt, both
        languages, in two passes (offline/run_models.py: repetition 1 of every candidate, then
        repetitions 2-3 for those still passing in some language; one model on the server at a
        time, unloaded and removed after its runs). Writes the safety gate and
        finalists_<en|es>.txt (or the fallback rule's: core/gate.py).
    python3 run_e0a.py phase-b --allow-cloud
        Whole dialogues of the finalists of each language (e0a_dialogues.yaml in English,
        e0a_dialogues_es.yaml in Spanish, 3 repetitions; the simulated user is Gemini, paid), one
        model at a time, unloaded (and removed if this stage pulled it) after its dialogues;
        then the local annotator marks the simulated user's lines (annotate_turns.py: read by
        the grounded-QA scoring and the adherence check) and the dialogues are scored. A
        separate stage: it needs the author's go (credit loaded).
    python3 run_e0a.py decide
        The rule (the author), per language, on ALL the dialogues of phase B (3 repetitions; E0a
        excludes none, the simulator's exclusion rate is reported as a check): among the
        finalists of phase A that FIT the GPU (loaded with the 8k context, entirely in GPU
        memory: /api/ps size_vram >= size, recorded when each model was loaded in phase B, else
        in phase A), the one with the highest task success; ties: the smaller model (Ollama's
        total parameters), then the name. One model per language (the same model when it wins
        both). The paired difference of every other finalist with the chosen one (same scenario
        x persona x repetition units) and its 95% cluster-bootstrap interval (scenario x persona
        clusters, BOOTSTRAP draws, fixed seed) are reported, not used to decide. A language whose
        finalists do not all have their planned dialogues is "incomplete": nothing is chosen. No
        model is discarded for its latency (it depends on the hardware): the system's time per
        turn (p50, p95, the share of turns over SLOW_TURN_S) is reported. Writes
        results/e0a/decision.json and decision.md.
    python3 run_e0a.py apply
        Writes the chosen model of each language in the files that use the local model (E0b, E1,
        the twin of E4, the real sessions). Then the validation and the debugging are repeated
        with it (EXPERIMENTS.md), before E0b.
    python3 run_e0a.py all
        preflight and phase-a (with the gate) in a row. Phase B, decide and apply are run
        explicitly: phase B pays for the simulated user.

The stages that use the GPU hold the lock of gpu_lock.py: E0a and E0b never run at once (same
GPU, lane and ROS_DOMAIN_ID). Nothing of this work stays in the GPU's memory after a stage, and
the models a stage pulled (persisted in results/e0a/pulled.json) are removed even after a crash,
on the resume.
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.normpath(os.path.join(HERE, ".."))
OFFLINE = os.path.join(EVAL, "offline")
sys.path.append(os.path.join(OFFLINE, "core"))
sys.path.append(os.path.join(EVAL, "analysis"))
import annotate_turns  # noqa: E402
import gpu_lock  # noqa: E402
import ollama_models  # noqa: E402
import provenance  # noqa: E402
from experiment_files import select_scenarios  # noqa: E402
from percentiles import percentile  # noqa: E402

PHASE_A = os.path.join(EVAL, "results", "e0a_phase_a")
OUT = os.path.join(EVAL, "results", "e0a")
PULLED = os.path.join(OUT, "pulled.json")
HARDWARE = os.path.join(EVAL, "experiments", "hardware", "single_gpu.yaml")
OLLAMA = "http://localhost:11434"
SIMULATOR = ("gemini", "gemini-2.5-flash-lite")
# Phase B per language: its experiment file and its results folder
PHASE_B = {"en": ("e0a_dialogues.yaml", os.path.join(EVAL, "results", "e0a_dialogues")),
           "es": ("e0a_dialogues_es.yaml", os.path.join(EVAL, "results", "e0a_dialogues_es"))}
# The rule, fixed before running (EXPERIMENTS.md, E0a; the author, decision 3). No latency gate
# (the author: it depends on the hardware): the latency is reported, with the share of turns over
# two seconds (Shiwa et al. 2009, International Journal of Social Robotics, p. 153: "the response
# time of a robot should be designed to be less than two seconds")
SLOW_TURN_S = 2.0
BOOTSTRAP = 10000               # the intervals of the differences (reported)
BOOTSTRAP_SEED = 20261029
# Where the chosen model is written: English and Spanish files (E1 and E3 run in English only,
# decided 2026-10-30; Spanish is E4, its twin and the E0b dialogues that validate its judge)
TARGETS = {"en": ["experiments/e0b_judge_dialogues.yaml", "experiments/e1_bounded_vs_llm_led.yaml",
                  "experiments/e1_role_swap.yaml", "experiments/e3_mechanisms.yaml",
                  "experiments/e3_noise.yaml", "experiments/e3_failures.yaml"],
           "es": ["experiments/e0b_judge_dialogues_es.yaml", "experiments/e4_sim_es.yaml",
                  "real/sessions_es.yaml"]}
PROVISIONAL = "qwen3:8b"
# Models that were on the server before this work, or another work's: never removed
KEEP = ollama_models.KEEP


def run(cmd: list[str], cwd: str = EVAL) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def py(path: str, *args: str) -> list[str]:
    return [sys.executable, os.path.join(EVAL, path), *args]


def server_models() -> set:
    return ollama_models.installed(OLLAMA)


def remove(model: str, pulled: list[str] | None = None) -> None:
    """Unload and delete a model this stage pulled (gone already: fine), and forget it."""
    if not ollama_models.protected(model):
        if model in server_models():
            ollama_models.remove(OLLAMA, model)
        else:
            ollama_models.unload(OLLAMA, model)
            print(f"   {model} is not on the server any more", flush=True)
    if pulled is not None and model in pulled:
        pulled.remove(model)
        ollama_models.save_pulled(PULLED, pulled)


def server_name(model: str) -> str:
    """The model's name on the Ollama server: "<model>@critical" (selective reasoning) is the
    same model, pulled, unloaded and removed by its base name."""
    return model.partition("@")[0]


def finalists(lang: str) -> list[str]:
    """The finalists of a language (finalists_<lang>.txt; a "#" line is a note, e.g. the fallback
    rule's)."""
    path = os.path.join(PHASE_A, f"finalists_{lang}.txt")
    if not os.path.exists(path):
        sys.exit(f"{path} is missing: run phase-a first")
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def preflight(args) -> None:
    run(py("scripts/run_parallel.py", "experiments/e0b_judge_dialogues.yaml", HARDWARE,
           "--check-servers"))
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    if args.allow_cloud:
        sys.path.insert(0, os.path.join(EVAL, "simulation", "interaction_sim"))
        from interaction_sim.cloud_llm import ask_json
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        raw, meta = ask_json(*SIMULATOR, 'Answer {"ok": true}.', schema, max_tokens=40)
        if '"ok"' not in raw:
            sys.exit(f"{SIMULATOR[1]} did not answer JSON: {raw!r}")
        print(f"{SIMULATOR[1]} answers (version {meta.get('model_version', '?')})")
    print("Preflight OK")


def phase_a(args) -> None:
    with gpu_lock.held("run_e0a.py phase-a"):
        run([sys.executable, "run_models.py", "--config", "config_ollama.yaml", "--reps", "3",
             "--pull", "--remove-pulled", "--out", PHASE_A]
            + (["--force-provenance"] if getattr(args, "force_provenance", False) else []),
            cwd=OFFLINE)


def phase_a_models() -> list[str]:
    """Every model of phase A (gate.json), to unload what is left of it before phase B."""
    path = os.path.join(PHASE_A, "gate.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [m for m in json.load(f) if not m.startswith("_")]


def phase_b(args) -> None:
    if not args.allow_cloud:
        sys.exit("The simulated user is Gemini (paid): --allow-cloud")
    per_lang = {lang: finalists(lang) for lang in PHASE_B}
    for lang, models in per_lang.items():
        if not models:
            print(f"No finalist in {lang}: a negative result, reported as such")
    with gpu_lock.held("run_e0a.py phase-b"):
        # What phase A left in memory (its last model: deleting does not unload)
        ollama_models.unload_all(OLLAMA, phase_a_models())
        pulled = ollama_models.load_pulled(PULLED)
        # One model on the server at a time: its dialogues in every language it is a finalist of
        order = sorted({m for ms in per_lang.values() for m in ms})
        for i, model in enumerate(order):
            name = server_name(model)
            if name not in server_models() and name not in pulled:
                pulled.append(name)  # recorded before the pull: a crash is cleaned on resume
                ollama_models.save_pulled(PULLED, pulled)
            try:
                for lang, (experiment, out) in PHASE_B.items():
                    if model in per_lang[lang]:
                        run(py("scripts/run_parallel.py", f"experiments/{experiment}", HARDWARE,
                               "--models", model, "--lanes", "local", "--out", out,
                               "--allow-cloud"))
            finally:
                # Out of the GPU before the next model (the provider loads with keep_alive -1)
                ollama_models.unload(OLLAMA, name)
            # Kept for the next variant of the same model (qwen3:14b, then qwen3:14b@critical)
            if name in pulled and name not in {server_name(m) for m in order[i + 1:]}:
                remove(name, pulled)
        for model in list(pulled):  # a model pulled before a crash and no longer a finalist
            remove(model, pulled)
        annotate([out for lang, (_, out) in PHASE_B.items() if per_lang[lang]], pulled)
    for lang, (_, out) in PHASE_B.items():
        if per_lang[lang]:
            run(py("analysis/score_runs.py", out))


def annotate(outs: list[str], pulled: list[str]) -> None:
    """The local annotator's marks of the simulated user's lines in the dialogues of phase B
    (annotate_turns.py, no paid call; what is annotated already is skipped on a resume), with
    the system's models out of the GPU; its model is pulled if missing and removed after."""
    if not outs:
        return
    model = annotate_turns.DEFAULT_MODEL
    if model not in server_models():
        if model not in pulled:
            pulled.append(model)
            ollama_models.save_pulled(PULLED, pulled)
        ollama_models.pull(OLLAMA, model)
    try:
        for out in outs:
            run(py("analysis/annotate_turns.py", out, "--model", model, "--host", OLLAMA))
    finally:
        ollama_models.unload(OLLAMA, model)
    if model in pulled:
        remove(model, pulled)


def unannotated(out: str) -> list[str]:
    """The records of phase B without the annotator's marks (annotate_turns.py)."""
    import score_runs
    return [p for p in score_runs.run_files(out)
            if not os.path.exists(annotate_turns.sidecar(p))]


def phase_b_units(out: str) -> dict:
    """{model: {(scenario, persona, rep): {"success": 0|1, "latencies": [...], "excluded":
    bool}}} from the records of phase B with the annotator's marks merged (the grounded-QA
    scoring and the adherence check read them): every dialogue (E0a excludes none)."""
    import score_runs
    units: dict[str, dict] = {}
    for path in score_runs.run_files(out):
        rec = annotate_turns.load_record(path)
        row = score_runs.add_adherence(score_runs.score(rec), rec)
        units.setdefault(row["model"], {})[(rec["scenario"], rec["persona"], rec.get("rep", 1))] = {
            "success": int(bool(row["task_success"])), "latencies": row["_latencies"],
            "excluded": bool(row.get("sim_excluded")), "fillers": row.get("fillers", 0)}
    return units


def planned_units(experiment: str) -> int:
    """The dialogues each model must have in phase B: scenarios x personas x repetitions x the
    conditions of the experiment file (189 = 21 x 3 x 3 x 1)."""
    import yaml
    path = os.path.join(EVAL, "experiments", experiment)
    with open(path, encoding="utf-8") as f:
        exp = yaml.safe_load(f)
    with open(os.path.join(os.path.dirname(path), exp["scenarios"]), encoding="utf-8") as f:
        scenarios = select_scenarios(yaml.safe_load(f)["scenarios"], exp)
    return (len(scenarios) * len(exp["personas"]) * int(exp.get("repetitions", 1))
            * len(exp.get("conditions") or {"": None}))


def figures(units: dict) -> dict:
    """n, success (all dialogues), the system's time per turn on all dialogues (p50, p95, the
    share of turns over SLOW_TURN_S; reported, not a gate), the fillers said, the simulator's
    exclusion rate (a check only)."""
    n = len(units)
    lat = [x for u in units.values() for x in u["latencies"]]
    return {"n": n,
            "success": round(sum(u["success"] for u in units.values()) / n, 4) if n else None,
            "latency_p50_s": percentile(lat, 0.5),
            "latency_p95_s": percentile(lat, 0.95),
            "turns_over_2s": round(sum(1 for x in lat if x > SLOW_TURN_S) / len(lat), 4)
            if lat else None,
            "fillers": sum(u.get("fillers", 0) for u in units.values()),
            "sim_excluded_rate": round(sum(u["excluded"] for u in units.values()) / n, 4)
            if n else None}


def paired_bootstrap(best: dict, other: dict, draws: int = BOOTSTRAP,
                     seed: int = BOOTSTRAP_SEED) -> dict:
    """The paired difference of success, best minus other, over their common units, and its
    95% interval by a cluster bootstrap that resamples scenario x persona clusters (all the
    repetitions of a cluster together). Reported, not used to decide."""
    common = sorted(set(best) & set(other))
    clusters: dict[tuple, list] = {}
    for unit in common:
        clusters.setdefault(unit[:2], []).append(best[unit]["success"] - other[unit]["success"])
    keys = sorted(clusters)
    sums = [(sum(clusters[k]), len(clusters[k])) for k in keys]
    total_n = sum(n for _, n in sums)
    d = sum(s for s, _ in sums) / total_n if total_n else float("nan")
    rng = random.Random(seed)
    values = []
    for _ in range(draws if sums else 0):
        pick = [sums[rng.randrange(len(sums))] for _ in sums]
        values.append(sum(x for x, _ in pick) / sum(x for _, x in pick))
    lo, hi = ((percentile(values, 0.025), percentile(values, 0.975)) if values
              else (float("nan"), float("nan")))
    return {"d": round(d, 4), "ci95": [round(lo, 4), round(hi, 4)], "units": total_n,
            "clusters": len(keys)}


def _size(sizes: dict, m: str) -> float:
    return sizes.get(m) if sizes.get(m) is not None else float("inf")


def gpu_fits() -> dict:
    """{model: {size, size_vram, fits, source}}: whether each model was entirely in GPU memory
    with the 8k context, as recorded when it was loaded in phase B (model_fit.json of its
    results), else in phase A (model_info.json)."""
    out = {}
    path = os.path.join(PHASE_A, "model_info.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for m, info in json.load(f).items():
                if isinstance(info, dict) and isinstance(info.get("gpu"), dict):
                    out[m] = {**info["gpu"], "source": "phase A"}
    for _, folder in PHASE_B.values():
        path = os.path.join(folder, "model_fit.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for m, info in json.load(f).items():
                    if info.get("fits") is not None or m not in out:
                        out[m] = {**info, "source": "phase B"}
    return out


def decide_language(candidates: list[str], units: dict, planned: int, sizes: dict,
                    fits: dict) -> dict:
    """The decision of one language from the units of each candidate (phase_b_units): the
    highest task success among the finalists that fit the GPU (ties: smaller, then name)."""
    figs = {m: figures(units.get(m) or {}) for m in candidates}
    short = {m: figs[m]["n"] for m in candidates if figs[m]["n"] != planned}
    out = {"candidates": candidates, "planned": planned, "figures": figs,
           "gpu": {m: fits.get(m) for m in candidates},
           "bootstrap": {"draws": BOOTSTRAP, "seed": BOOTSTRAP_SEED,
                         "cluster": "scenario x persona", "interval": "95% percentile",
                         "use": "reported, not used to decide"},
           "latency": "reported, not a gate",
           "sizes_b": {m: sizes.get(m) for m in candidates}}
    if not candidates:
        return {**out, "state": "none", "chosen": None}
    if short:
        return {**out, "state": "incomplete", "short": short, "chosen": None}
    eligible = [m for m in candidates if (fits.get(m) or {}).get("fits") is True]
    out["eligible"] = eligible
    out["not_eligible"] = {m: ("does not fit the GPU (CPU offload)"
                               if (fits.get(m) or {}).get("fits") is False
                               else "GPU fit unknown (not recorded)")
                           for m in candidates if m not in eligible}
    if not eligible:
        return {**out, "state": "none", "chosen": None}
    chosen = sorted(eligible, key=lambda m: (-figs[m]["success"], _size(sizes, m), m))[0]
    out["differences"] = {m: paired_bootstrap(units[chosen], units[m])
                          for m in candidates if m != chosen}
    return {**out, "state": "chosen", "chosen": chosen}


def model_digests(models: list[str]) -> dict:
    """The digest each chosen model ran with in phase B (its provenance.json), else the
    server's."""
    out = {}
    for _, folder in PHASE_B.values():
        path = os.path.join(folder, "provenance.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out.update({m: d for m, d in (json.load(f).get("models") or {}).items()
                            if m in models and d})
    missing = [m for m in models if m not in out]
    if missing:
        try:
            out.update({m: d for m, d in provenance.model_digests(missing, OLLAMA).items() if d})
        except Exception:
            pass
    return out


def decide(args) -> None:
    with open(os.path.join(PHASE_A, "gate.json"), encoding="utf-8") as f:
        gate = json.load(f)
    sizes = {m: g.get("size_b") for m, g in gate.items() if not m.startswith("_")}
    fits = gpu_fits()
    decision = {}
    for lang, (experiment, out) in PHASE_B.items():
        missing = unannotated(out) if os.path.isdir(out) else []
        if missing:
            sys.exit(f"{len(missing)} dialogues of {out} without the annotator's marks: run "
                     f"phase-b again (it resumes) or analysis/annotate_turns.py {out}")
        units = phase_b_units(out) if os.path.isdir(out) else {}
        decision[lang] = decide_language(finalists(lang), units, planned_units(experiment), sizes,
                                         fits)
        decision[lang]["phase_a"] = (gate.get("_finalists") or {}).get(lang) or {}
    chosen = sorted({d["chosen"] for d in decision.values() if d.get("chosen")})
    result = {"languages": decision, "digests": model_digests(chosen),
              "rule": "per language, among the finalists of phase A that fit the GPU (entirely "
                      "in GPU memory with the 8k context), the highest task success in phase B "
                      "(3 repetitions, all dialogues); ties: the smaller model, then the name. "
                      "The paired differences with the chosen model and their 95% "
                      "cluster-bootstrap intervals are reported, not used to decide. No latency "
                      "gate (reported: p50, p95, share of turns over 2 s, Shiwa et al. 2009)"}
    lines = write_decision(result)
    provenance.catalog("decision", OUT, {
        "chosen": {lang: d.get("chosen") for lang, d in decision.items()},
        "state": {lang: d.get("state") for lang, d in decision.items()},
        "decision_json": os.path.join(OUT, "decision.json")})
    print("\n".join(lines))


def write_decision(result: dict) -> list[str]:
    lines = ["# E0a: the decision", "", f"Rule: {result['rule']}.", ""]
    for lang, d in result["languages"].items():
        lines += [f"## {lang}", ""]
        info = d.get("phase_a") or {}
        if info.get("fallback"):
            lines += [f"Phase A: {info.get('note')}", ""]
        lines += [f"Finalists of phase A: {', '.join(d['candidates']) or 'none'}; planned "
                  f"dialogues per model: {d['planned']}", "",
                  "| model | size (B) | fits the GPU | dialogues | success | p50 / p95 (s) | "
                  "turns > 2 s | fillers | sim. excluded (check) | d vs chosen [95% CI] |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for m in d["candidates"]:
            f = d["figures"][m]
            gpu = (d.get("gpu") or {}).get(m) or {}
            fit = {True: "yes", False: "no (CPU offload)"}.get(gpu.get("fits"), "unknown")
            diff = (d.get("differences") or {}).get(m)
            dtext = ("chosen" if m == d.get("chosen") else
                     f"{diff['d']} [{diff['ci95'][0]}, {diff['ci95'][1]}]" if diff else "-")
            p50, p95 = f.get("latency_p50_s"), f.get("latency_p95_s")
            lines.append(f"| {m} | {d['sizes_b'].get(m)} | {fit} | {f['n']} | {f['success']} | "
                         f"{'-' if p50 is None else round(p50, 2)} / "
                         f"{'-' if p95 is None else round(p95, 2)} | {f.get('turns_over_2s')} | "
                         f"{f.get('fillers', 0)} | {f['sim_excluded_rate']} | {dtext} |")
        if d.get("not_eligible"):
            lines += ["", "Not eligible: " + "; ".join(f"{m}: {why}"
                                                       for m, why in d["not_eligible"].items())]
        if d["state"] == "incomplete":
            verdict = f"none: incomplete runs {d.get('short')}"
        else:
            verdict = d["chosen"] or "none (negative result)"
        lines += ["", f"**Chosen ({lang}): {verdict}**", ""]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "decision.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    with open(os.path.join(OUT, "decision.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return lines


def apply(args) -> None:
    with open(os.path.join(OUT, "decision.json"), encoding="utf-8") as f:
        decision = json.load(f)["languages"]
    changed = []
    for lang, files in TARGETS.items():
        chosen = decision[lang]["chosen"]
        if not chosen:
            print(f"No model chosen for {lang} ({decision[lang]['state']}): nothing written")
            continue
        for rel in files:
            path = os.path.join(EVAL, rel)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            new = re.sub(rf'(?<![\w.:-]){re.escape(PROVISIONAL)}(?![\w.:-])', chosen, text)
            if new != text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
                changed.append(rel)
                print(f"   {rel}: {PROVISIONAL} -> {chosen}")
    provenance.catalog("apply", OUT, {"files": changed,
                                      "chosen": {lang: d["chosen"] for lang, d in decision.items()}})
    if changed:
        print("These files are sources of the code hash (provenance.py): a results folder begun "
              "before apply cannot be resumed after it (it would mix two versions); E0b starts "
              "after apply, in new folders.")
    print("Now repeat the validation and the debugging with the chosen model(s), before E0b.")


def all_(args) -> None:
    preflight(args)
    phase_a(args)
    print("Phase A done. Phase B pays for the simulated user: run it explicitly "
          "(run_e0a.py phase-b --allow-cloud), then decide.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="stage", required=True)
    for name in ("preflight", "phase-a", "phase-b", "decide", "apply", "all"):
        p = sub.add_parser(name)
        if name == "phase-a":
            p.add_argument("--force-provenance", action="store_true",
                           help="Resume phase A begun with other code (recorded in its "
                                "provenance.json): only when the change cannot alter the runs "
                                "already saved")
        if name in ("preflight", "phase-b", "all"):
            p.add_argument("--allow-cloud", action="store_true",
                           help="Paid calls (phase-b: the simulated user; preflight, all: one "
                                "tiny test call)")
    args = parser.parse_args()
    {"preflight": preflight, "phase-a": phase_a, "phase-b": phase_b, "decide": decide,
     "apply": apply, "all": all_}[args.stage](args)


if __name__ == "__main__":
    main()
