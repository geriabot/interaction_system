# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The pilot of every experiment (doc/PILOT.md): each pilot file of experiments/pilot/ through the
whole chain of its experiment (launch, dialogues, store, score, judge, latency and run health,
provenance), with the pilot's system model (qwen2.5:32b) and the free stand-ins of the cloud
providers (pilot_standin.py: no paid call), then the exit criteria (pilot_check.py). From
src/interaction_system/evaluation, in a prepared terminal (REPRODUCING.md):

    python3 scripts/run_pilot.py list                  # the pilots, their dialogues and judges
    python3 scripts/run_pilot.py dry-run               # the dry run of each (no call)
    python3 scripts/run_pilot.py run [--only NAME,...] [--lanes local,cloud] [--user-model M
                                     [--user-ollama URL]]
    python3 scripts/run_pilot.py check [--only NAME,...]

`run` takes the GPU lock (gpu_lock.py), starts the stand-in on 127.0.0.1 for each pilot (its calls
logged in results/pilot/<name>/standin_calls.jsonl) and stops it by its PID, runs the lanes of the
hardware profile one after the other (the local cells, then the cloud cells: only the pilot's
system model is on the GPU), scores, runs the pilot's judge (E0b: its whole chain) and the check.
Each pilot resumes where it stopped (the runners skip what is saved). Results in results/pilot/
(--root), never reported. --lanes local runs only the local cells; --user-model is the fallback
for the simulated user (a small local model instead of the script; pilot_standin.py).
"""

import argparse
import csv
import glob
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(EVAL, "offline", "core"))
from experiment_files import condition_dialogues, load_experiment, select_scenarios  # noqa: E402

PILOTS = os.path.join(EVAL, "experiments", "pilot")
ROOT = os.path.join(EVAL, "results", "pilot")
HARDWARE = os.path.join(EVAL, "experiments", "hardware", "single_gpu.yaml")
OLLAMA = "http://127.0.0.1:11434"
SYSTEM_MODEL = "qwen2.5:32b"        # the pilot's system model (doc/PILOT.md)
PORT = 8766                         # the stand-in's port (the pilot files' base URLs)
# The order of the evaluation (REPRODUCING.md, The order)
ORDER = ["e0a_dialogues_pilot", "e0a_dialogues_es_pilot", "e0b_judge_dialogues_pilot",
         "e0b_judge_dialogues_es_pilot", "e1_bounded_vs_llm_led_pilot", "e1_role_swap_pilot",
         "e3_mechanisms_pilot", "e3_noise_pilot", "e3_failures_pilot", "e4_sim_es_pilot"]
# The pilot's judge of the experiments that have one (provider, model, tag): the first cloud rung
# of E0b's ladder, answered by the stand-in (synthetic labels), so that the cloud judge's path
# runs; in the role swap, whose system is Google's, a local judge (the system model already on
# the GPU), so that the local judge's path runs too. E0b: its own chain (e0b_chain).
CLOUD_JUDGE = ("gemini", "gemini-2.5-flash-lite", "gemini_2_5_flash_lite")
LOCAL_JUDGE = ("ollama", SYSTEM_MODEL, "qwen2_5_32b")
JUDGES = {"e1_bounded_vs_llm_led_pilot": CLOUD_JUDGE, "e1_role_swap_pilot": LOCAL_JUDGE,
          "e3_noise_pilot": CLOUD_JUDGE, "e4_sim_es_pilot": CLOUD_JUDGE}
E0B = ("e0b_judge_dialogues_pilot", "e0b_judge_dialogues_es_pilot")
SHEET_N = 20                        # utterances per annotation sheet in the pilot (150 in E0b)


def pilot_files() -> dict:
    """{name: path} of the pilot files, in the order of the evaluation."""
    found = {}
    for path in glob.glob(os.path.join(PILOTS, "*.yaml")):
        found[load_experiment(path)["name"]] = path
    return {n: found[n] for n in ORDER if n in found} | {
        n: p for n, p in sorted(found.items()) if n not in ORDER}


def planned(path: str) -> list[tuple]:
    """(model, condition, scenario id, persona, rep) of every dialogue of a pilot."""
    import yaml
    exp = load_experiment(path)
    base = os.path.dirname(path)
    with open(os.path.join(base, exp["scenarios"]), encoding="utf-8") as f:
        scenarios = select_scenarios(yaml.safe_load(f)["scenarios"], exp)
    conditions = {k: v or {} for k, v in exp["conditions"].items()}
    return [(m, c, s["id"], p, r) for m in exp["models"] for c in conditions
            if not conditions[c].get("models") or m in conditions[c]["models"]
            for s, p in condition_dialogues(scenarios, exp, conditions[c], m)
            for r in range(1, int(exp.get("repetitions", 1)) + 1)]


def results(name: str, root: str = ROOT) -> str:
    return os.path.join(root, name)


def judge_of(name: str):
    return JUDGES.get(name)


def standin_env(environ=None) -> dict:
    """The environment of the pilot's children: the stand-ins' URLs and a dummy API key (never a
    real one: a call that reached a real API by mistake would be refused, not billed)."""
    from interaction_sim.cloud_llm import PILOT_ENV
    env = dict(environ if environ is not None else os.environ)
    env[PILOT_ENV["gemini"]] = f"http://127.0.0.1:{PORT}"
    env[PILOT_ENV["openai"]] = f"http://127.0.0.1:{PORT}/v1"
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        env[key] = "pilot-standin-no-key"
    env.pop("GOOGLE_API_KEY", None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def py(script: str, *args: str) -> list[str]:
    return [sys.executable, os.path.join(EVAL, script), *args]


def run(cmd: list[str], env: dict, log) -> int:
    log.write(f"\n$ {' '.join(cmd)}   ({time.strftime('%H:%M:%S')})\n")
    log.flush()
    print("+ " + " ".join(os.path.relpath(c, EVAL) if c.startswith(EVAL) else c for c in cmd),
          flush=True)
    return subprocess.run(cmd, cwd=EVAL, env=env, stdout=log, stderr=subprocess.STDOUT,
                          check=False).returncode


class Standin:
    """The stand-in server of one pilot, stopped by its PID."""

    def __init__(self, log_path: str, user_model: str = "", user_ollama: str = ""):
        import requests
        try:
            requests.get(f"http://127.0.0.1:{PORT}/health", timeout=2)
            sys.exit(f"Port {PORT} is in use: another stand-in or another program (stop it by "
                     f"its PID)")
        except requests.RequestException:
            pass
        cmd = py("scripts/pilot_standin.py", "serve", "--port", str(PORT), "--ollama", OLLAMA,
                 "--log", log_path) + (["--user-model", user_model] if user_model else []) + (
                     ["--user-ollama", user_ollama] if user_ollama else [])
        self.proc = subprocess.Popen(cmd, cwd=EVAL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            try:
                if requests.get(f"http://127.0.0.1:{PORT}/health", timeout=1).ok:
                    return
            except requests.RequestException:
                time.sleep(0.2)
        self.stop()
        sys.exit("The stand-in did not start")

    def stop(self) -> None:
        if self.proc.poll() is None:
            os.kill(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.kill(self.proc.pid, signal.SIGKILL)


def fill_sheets(folder: str) -> tuple[str, str]:
    """Synthetic annotations of the pilot's two sheets (the report's chain runs on them; two
    people fill the real ones): 1/0 per label, deterministic, mostly agreeing."""
    out = []
    for name in ("a", "b"):
        src = os.path.join(folder, "e0", f"sheet_{name}.csv")
        dst = os.path.join(folder, "e0", f"sheet_{name}_synthetic.csv")
        with open(src, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            for col in ("in_frame (1/0)", "unsupported (1/0)"):
                h = int(hashlib.sha256(f"{row['id']}|{col}".encode()).hexdigest()[:6], 16)
                flip = int(hashlib.sha256(f"{name}|{row['id']}|{col}".encode()).hexdigest()[:2],
                           16) < 26     # about 10 % disagreement between the two
                value = (h % 5 != 0) if col.startswith("in_frame") else (h % 5 == 0)
                row[col] = str(int(value != flip))
        with open(dst, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["id"])
            writer.writeheader()
            writer.writerows(rows)
        out.append(dst)
    return out[0], out[1]


def e0b_chain(name: str, folder: str, env: dict, log) -> int:
    """E0b's chain on the pilot's dialogues (run_e0b.py label, report and label-cloud, step by
    step, as JUDGE_VALIDATION.md): split; the local judge on the development half, then on every
    dialogue; the sheets; the synthetic annotations; the report with the local judge only; then,
    as when no local judge passes (always in the pilot, which is free, so that the path runs),
    every cloud judge of the ladder on the sheets' utterances through the stand-in, and the report
    again on the same annotations. The combine of both languages: e0b_combine."""
    import run_e0b
    order = ",".join(t for _, _, t in run_e0b.LOCAL_JUDGES + run_e0b.CLOUD_JUDGES
                     + run_e0b.OPENAI_JUDGES + [LOCAL_JUDGE])
    code = 0
    if not os.path.exists(os.path.join(folder, "e0", "split.json")):
        code = run(py("analysis/judge_validation.py", "split", folder), env, log)
    provider, model, tag = LOCAL_JUDGE
    base = py("analysis/judge_runs.py", folder, "--provider", provider, "--model", model,
              "--tag", tag, "--host", OLLAMA)
    code = code or run(base + ["--only-split", "development"], env, log)
    code = code or run(base, env, log)
    key = os.path.join(folder, "e0", "key.json")
    if not code and not os.path.exists(key):
        code = run(py("analysis/judge_validation.py", "sheets", folder, "--n", str(SHEET_N)),
                   env, log)
    if code:
        return code
    a, b = fill_sheets(folder)
    report = py("analysis/judge_validation.py", "report", folder, "--a", a, "--b", b,
                "--order", order)
    code = run(report, env, log)                      # the local judge only
    for provider, model, tag in run_e0b.CLOUD_JUDGES + run_e0b.OPENAI_JUDGES:
        code = code or run(py("analysis/judge_runs.py", folder, "--provider", provider,
                              "--model", model, "--tag", tag, "--only-key", key,
                              "--allow-cloud"), env, log)
    return code or run(report, env, log)              # again, with the cloud judges


def e0b_combine(root: str, env: dict, log) -> int:
    import run_e0b
    reports = [os.path.join(results(n, root), "e0", "report.json") for n in E0B]
    if not all(os.path.exists(r) for r in reports):
        return 0
    order = ",".join(t for _, _, t in run_e0b.LOCAL_JUDGES + run_e0b.CLOUD_JUDGES
                     + run_e0b.OPENAI_JUDGES + [LOCAL_JUDGE])
    return run(py("analysis/judge_validation.py", "combine", *reports, "--order", order,
                  "--out", os.path.join(root, "e0b_report.json")), env, log)


def run_one(name: str, path: str, root: str, lanes: list[str], user_model: str,
            user_ollama: str = "") -> int:
    folder = results(name, root)
    os.makedirs(folder, exist_ok=True)
    env = standin_env()
    standin = Standin(os.path.join(folder, "standin_calls.jsonl"), user_model, user_ollama)
    code = 0
    with open(os.path.join(folder, "pilot.log"), "a", encoding="utf-8") as log:
        try:
            for lane in lanes:   # one after the other: one model on the GPU at a time
                code = run(py("scripts/run_parallel.py", path, HARDWARE, "--lanes", lane,
                              "--out", folder, "--allow-cloud"), env, log)
                if code:
                    print(f"   lane {lane} failed ({code}): see {folder}/lane_{lane}.log",
                          flush=True)
                    return code
            run(py("analysis/score_runs.py", folder), env, log)
            judge = judge_of(name)
            if judge:
                provider, model, tag = judge
                base = py("analysis/judge_runs.py", folder, "--provider", provider, "--model",
                          model, "--tag", tag)
                code = run(base + (["--allow-cloud"] if provider != "ollama" else
                                   ["--host", OLLAMA]), env, log)
                run(py("analysis/judge_runs.py", folder, "--tag", tag, "--summary"), env, log)
            elif name in E0B:
                code = e0b_chain(name, folder, env, log)
        finally:
            standin.stop()
            import ollama_models
            ollama_models.unload_all(OLLAMA, [SYSTEM_MODEL] + ([user_model] if user_model
                                                                and not user_ollama else []))
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=ROOT, help="Where the pilot's results go")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    sub.add_parser("dry-run")
    p = sub.add_parser("run")
    p.add_argument("--only", default="", help="Only these pilots (names, comma separated)")
    p.add_argument("--lanes", default="local,cloud")
    p.add_argument("--user-model", default="",
                   help="The fallback simulated user: a local model instead of the script")
    p.add_argument("--user-ollama", default="",
                   help="The Ollama server of --user-model (default: the GPU server's)")
    p = sub.add_parser("check")
    p.add_argument("--only", default="")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    files = pilot_files()
    only = [n for n in getattr(args, "only", "").split(",") if n]
    unknown = sorted(set(only) - set(files))
    if unknown:
        print(f"Unknown pilots {unknown}: {', '.join(files)}", file=sys.stderr)
        return 1
    chosen = {n: p for n, p in files.items() if not only or n in only}
    if args.action == "list":
        total = 0
        for name, path in chosen.items():
            plan = planned(path)
            total += len(plan)
            judge = judge_of(name)
            print(f"{name:32s} {len(plan):4d} dialogues  judge: "
                  + (f"{judge[0]}/{judge[1]}" if judge else
                     "E0b's chain" if name in E0B else "none (as the experiment)"))
        print(f"{'total':32s} {total:4d}")
        return 0
    if args.action == "dry-run":
        env = standin_env()
        for name, path in chosen.items():
            subprocess.run(py("scripts/run_parallel.py", path, HARDWARE, "--dry-run",
                              "--out", results(name, root)), cwd=EVAL, env=env, check=False)
        return 0
    if args.action == "check":
        return subprocess.run(py("scripts/pilot_check.py", "--root", root,
                                 *(["--only", ",".join(only)] if only else [])),
                              cwd=EVAL, check=False).returncode
    import gpu_lock
    import ollama_models
    if SYSTEM_MODEL not in ollama_models.installed(OLLAMA):
        print(f"{SYSTEM_MODEL} is not on the GPU server: pull it for the pilot (after the "
              f"debugging's models are removed: the disk is shared; doc/PILOT.md)",
              file=sys.stderr)
        return 1
    lanes = [lane for lane in args.lanes.split(",") if lane]
    failed = []
    with gpu_lock.held("run_pilot.py run"):
        for name, path in chosen.items():
            print(f"== {name}", flush=True)
            if run_one(name, path, root, lanes, args.user_model, args.user_ollama):
                failed.append(name)
        if set(E0B) <= set(chosen):
            with open(os.path.join(root, "pilot.log"), "a", encoding="utf-8") as log:
                e0b_combine(root, standin_env(), log)
    code = subprocess.run(py("scripts/pilot_check.py", "--root", root,
                             *(["--only", ",".join(chosen)] if only else [])),
                          cwd=EVAL, check=False).returncode
    if failed:
        print(f"Pilots that stopped: {', '.join(failed)} (see their pilot.log)", file=sys.stderr)
    return 1 if failed else code


if __name__ == "__main__":
    sys.exit(main())
