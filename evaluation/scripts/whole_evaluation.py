# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The whole simulated evaluation in one run, in the order of doc/REPRODUCING.md ("The order"): the
debugging's exit (a person's stop), the pilot of every experiment (free: doc/PILOT.md) and its
review, the freeze commit, E0a, E0b, E1 (the go/no-go gate, the campaign, the role swap), E2, E3,
the simulated twin of E4 and the tables, each step with the exact commands of REPRODUCING.md;
before each paid campaign, its cloud smoke check (one or two real calls per cloud model, with a
permission of its own). It stops where a person must act
(and says what it awaits), never makes a paid cloud call without the author's permission for
that step (given with its estimate in view), records the code hash of the method
(provenance.py) and warns when it changes, and writes the run health of every step. The control
panel's Campaign tab drives it (doc/CONTROL_PANEL.md, "Whole evaluation"); from
src/interaction_system/evaluation, in a prepared terminal (REPRODUCING.md):

    python3 scripts/whole_evaluation.py status
    python3 scripts/whole_evaluation.py run [--from STEP] [--accept-method-change] [--openai-judges]
    python3 scripts/whole_evaluation.py allow STEP      # shows the estimate, asks to confirm
    python3 scripts/whole_evaluation.py done STEP       # a person's stop: what it awaited is done

Its folder (--root, default results/whole_evaluation) keeps state.json (the method's code hash,
each step's status and run health, the stops acknowledged, the permissions), campaign.log (one
line per event) and logs/<step>.log (the output of each step's commands). Ctrl-C (the panel's
Pause sends it to the whole process group) pauses it as it pauses each script: the dialogue under
way is not saved; run again to resume, every script skips what it saved.
"""

import argparse
import collections
import contextlib
import fcntl
import glob
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)
SRC = os.path.dirname(EVAL)                       # the repository (src/interaction_system)
sys.path.insert(0, os.path.join(EVAL, "real"))
import sessions_common  # noqa: E402

RESULTS = os.path.join(EVAL, "results")
ROOT = os.path.join(RESULTS, "whole_evaluation")
HARDWARE = "experiments/hardware/single_gpu.yaml"   # the profile of every command of REPRODUCING.md
E0B_EN = "experiments/e0b_judge_dialogues.yaml"     # the server check of run_e0a.py preflight
E1 = "experiments/e1_bounded_vs_llm_led.yaml"
PILOT_ROOT = os.path.join(RESULTS, "pilot")         # run_pilot.py: results/pilot/<name>
E3_FILES = ("experiments/e3_mechanisms.yaml", "experiments/e3_noise.yaml",
            "experiments/e3_failures.yaml")
E4_TWIN = "experiments/e4_sim_es.yaml"
SWAP = "experiments/e1_role_swap.yaml"
PROVISIONAL = "qwen3:8b"                          # run_e0a.py: the model until E0a decides
LANGUAGES = {"English": "en", "Spanish": "es"}
PAUSE_WAIT_S = 180          # after a pause, how long a script may take to stop what it launched
# The authors' cost estimate (not part of the public repository; without it, the counts only)
COSTS_SCRIPT = os.path.join(SRC, "paper", "costs_estimate.py")
COSTS_NOTES = os.path.join(SRC, "paper", "COSTES.md")
# The lines of the cost estimate (costs_estimate.py) and of the table of its notes (§1) that
# belong to each paid step
JUDGE_COSTS = (("campaign judge",), ("| Local", "| Gemini 2.5 Flash-Lite /", "| GPT-4o-mini /"))
COSTS = {"e0a_phase_b": (("E0a phase B",), ("| E0a (fase B",)),
         "e0b_dialogues": (("E0b dialogues",), ("| E0b, diálogos",)),
         "e0b_label_cloud": (("E0b judge",), ("| E0b, jueces",)),
         "e1_gate": (("E1 en",), ("| E1 en inglés",)),
         "e1_campaign": (("E1 en",), ("| E1 en inglés",)),
         "e1_swap": (("E1 role swap",), ("| E1 con los papeles",)),
         "e3_mechanisms": (("E3 mechanisms",), ("| E3 (solo",)),
         "e3_noise": (("E3 noise",), ("| E3 (solo",)),
         "e3_failures": (("E3 model failures",), ("| E3 (solo",)),
         "e4_twin": (("E4 twin",), ("| E4 (gemelo",)),
         "e1_judge": JUDGE_COSTS, "e1_swap_judge": JUDGE_COSTS, "e3_noise_judge": JUDGE_COSTS,
         "e4_twin_judge": JUDGE_COSTS}
SMOKE_COSTS = (("Cloud smoke checks",), ("| Comprobaciones de humo",))
COSTS.update({sid: SMOKE_COSTS for sid in ("e1_smoke", "e1_swap_smoke", "e3_smoke",
                                            "e4_smoke")})


def capture(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run a script of the evaluation (argv relative to evaluation/) to its end: (return code,
    its output). The control panel replaces it with its own (inside pixi, with the workspace)."""
    try:
        result = subprocess.run(executable(argv), cwd=EVAL, capture_output=True, text=True,
                                timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, f"No answer in {timeout} s"
    return result.returncode, (result.stdout + result.stderr).strip()


def executable(argv: list[str]) -> list[str]:
    return [sys.executable] + argv[1:] if argv and argv[0] == "python3" else list(argv)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_yaml(rel: str) -> dict:
    with open(os.path.join(EVAL, rel), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def results_of(rel: str) -> str:
    """The results folder of an experiment file (relative to evaluation/)."""
    return os.path.join("results", load_yaml(rel)["name"])


def file_sha(rel: str) -> str:
    try:
        with open(os.path.join(EVAL, rel), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def code_hash() -> str:
    """The code hash of the method and the evaluation (provenance.py), what a results folder
    records when it begins."""
    import provenance
    return provenance.code_hash()


# ------------------------------------------------------------------ what the steps read
class Ctx:
    """What the steps read from the results of earlier steps: the model E0a chose, the judge E0b
    chose, the finalists of phase A; and the options of the run."""

    def __init__(self, root: str = None, options: dict | None = None):
        self.root = root or ROOT
        self.options = options or {}

    def decision(self) -> dict | None:
        return read_json(os.path.join(RESULTS, "e0a", "decision.json"))

    def e0a_model(self, lang: str) -> str:
        d = self.decision() or {}
        return ((d.get("languages") or {}).get(lang) or {}).get("chosen") or ""

    def finalists(self, lang: str) -> list[str] | None:
        try:
            with open(os.path.join(RESULTS, "e0a_phase_a", f"finalists_{lang}.txt"),
                      encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        except OSError:
            return None

    def judge(self) -> dict:
        """The judge E0b chose (results/e0b/report.json): {provider, model, tag, tentative}, or
        {error} with why there is none yet."""
        report = read_json(os.path.join(RESULTS, "e0b", "report.json"))
        if report is None:
            return {"error": "E0b has not chosen the judge yet (results/e0b/report.json, written "
                             "by run_e0b.py report)"}
        tag = report.get("chosen")
        if not tag:
            return {"error": "E0b chose no judge: the fallback plan "
                             "(doc/JUDGE_VALIDATION.md#the-fallback-plan) is the author's call"}
        for provider, model, t in judge_ladder():
            if t == tag:
                return {"provider": provider, "model": model, "tag": tag,
                        "tentative": bool(report.get("tentative"))}
        return {"error": f"The judge chosen in E0b ({tag}) is not in the ladder of run_e0b.py"}


def judge_ladder() -> list[tuple[str, str, str]]:
    """(provider, model, tag) of every judge of the ladder, as run_e0b.py fixes it."""
    import run_e0b
    return run_e0b.LOCAL_JUDGES + run_e0b.CLOUD_JUDGES + run_e0b.OPENAI_JUDGES


def cloud_judges(openai: bool) -> list[tuple[str, str, str]]:
    import run_e0b
    return run_e0b.CLOUD_JUDGES + (run_e0b.OPENAI_JUDGES if openai else [])


def providers_of(rel: str, only_model: str = "") -> set:
    """The cloud providers an experiment file calls (its simulated user and its cloud system
    models; with only_model, that model only)."""
    exp = load_yaml(rel)
    out = set()
    sim = exp.get("simulator") or {}
    if sim.get("provider", "ollama") in sessions_common.CLOUD_PROVIDERS:
        out.add(sim["provider"])
    for m in exp.get("models") or []:
        if (not only_model or m == only_model) and sessions_common.is_cloud(m):
            out.add(sessions_common.split_model(m)[0])
    return out


def missing_keys(providers) -> list[str]:
    return [" or ".join(keys) for p in sorted(providers)
            for keys in [sessions_common.API_KEYS.get(p, ())]
            if keys and not any(os.environ.get(k) for k in keys)]


def keys_needed(providers) -> str:
    """'' when the API keys of these providers are in the environment, else what to export."""
    keys = missing_keys(providers)
    return f"Export {', '.join(keys)} in the environment that starts the evaluation" if keys else ""


def requires_experiment(rel: str, ctx: Ctx, only_model: str = "") -> str:
    """What an experiment file still needs before it runs ('' if nothing): the file itself,
    the local model chosen by E0a (not the provisional one), the dated versions of its cloud
    models, the API keys."""
    if not os.path.exists(os.path.join(EVAL, rel)):
        return f"{rel} does not exist"
    exp = load_yaml(rel)
    lang = LANGUAGES.get(exp.get("language", "English"), "en")
    models = [m for m in exp.get("models") or [] if not only_model or m == only_model]
    if PROVISIONAL in models and ctx.e0a_model(lang) != PROVISIONAL:
        return (f"{rel} still holds the provisional local model {PROVISIONAL}: every experiment "
                f"after E0a runs with the model E0a chose (run_e0a.py decide, then apply)")
    sim = (exp.get("simulator") or {}).get("model", "")
    unpinned = [m for m in models + [sim] if "<" in m]
    if unpinned:
        return (f"Write the dated version of {', '.join(unpinned)} in {rel} (the runner refuses "
                f"a placeholder; REPRODUCING.md, Cloud models)")
    return keys_needed(providers_of(rel, only_model))


# ------------------------------------------------------------------ commands
def cmd(*argv, env=None, tee=None, busy=False) -> dict:
    """A command of a step, relative to evaluation/. busy: its exit status 3 means the servers
    are in use (run_parallel.py --check-servers). tee: its output is also kept in this file."""
    return {"argv": list(argv), "env": env or {}, "tee": tee, "busy": busy}


def py(script: str, *args: str, **kw) -> dict:
    return cmd("python3", script, *args, **kw)


def shown(command: dict) -> str:
    env = " ".join(f"{k}={v}" for k, v in command["env"].items())
    return (env + " " if env else "") + " ".join(command["argv"]) + (
        f" > {command['tee']}" if command["tee"] else "")


def lane_domain(lane: str = "local") -> str:
    return str(load_yaml(HARDWARE)["lanes"][lane]["ros_domain_id"])


# ------------------------------------------------------------------ estimates
DIALOGUES = re.compile(r"^\S+: (\d+) dialogues \(", re.M)
CLOUD = re.compile(r"cloud: (.+?): (\d+) dialogues")
UTTERANCES = re.compile(r"(\d+) utterances to judge \((\d+) already judged\)")


def dry_run(argv: list[str]) -> dict:
    """A dry run (no call made): its paid calls per cloud model, the dialogues, its output."""
    code, output = capture(argv, 240)
    cloud = {}
    for name, n in CLOUD.findall(output):
        cloud[name] = cloud.get(name, 0) + int(n)
    return {"code": code, "output": output, "cloud": cloud,
            "dialogues": sum(int(n) for n in DIALOGUES.findall(output))}


def saved(folder: str) -> int:
    return len(glob.glob(os.path.join(EVAL, folder, "runs", "*", "*", "*.json")))


def estimate_experiment(rel: str, folder: str, extra=(), runner="run_parallel") -> dict:
    """The paid calls of an experiment, from its dry run: one line per cloud model."""
    if runner == "run_parallel":
        argv = ["python3", "scripts/run_parallel.py", rel, HARDWARE, *extra, "--dry-run"]
    else:
        argv = ["python3", "scripts/run_experiment.py", rel, *extra, "--dry-run"]
    run = dry_run(argv)
    if run["code"]:
        return {"error": f"The dry run of {rel} failed", "output": run["output"]}
    done = saved(folder)
    lines = [f"{rel}: {run['dialogues']} dialogues planned ({done} saved already in {folder}, "
             f"skipped)"] + [f"  {name}: {n} dialogues (up to the turn limit each)"
                             for name, n in run["cloud"].items()]
    return {"lines": lines, "output": run["output"],
            "key": {"file": file_sha(rel), "cloud": run["cloud"], "dialogues": run["dialogues"]}}


def estimate_many(parts: list[dict]) -> dict:
    out = {"lines": [], "output": "", "key": {}}
    for i, part in enumerate(parts):
        if part.get("error"):
            return part
        out["lines"] += part["lines"]
        out["output"] += part["output"] + "\n"
        out["key"][str(i)] = part["key"]
    return out


def estimate_judge(folder: str, judge: dict) -> dict:
    if judge.get("error"):
        return {"error": judge["error"]}
    if judge["provider"] == "ollama":
        return {"lines": [f"The judge {judge['tag']} is local: no paid call"], "output": "",
                "key": {"local": judge["tag"]}}
    code, output = capture(["python3", "analysis/judge_runs.py", folder, "--provider",
                            judge["provider"], "--model", judge["model"], "--tag", judge["tag"],
                            "--dry-run"], 240)
    found = UTTERANCES.search(output)
    if code or not found:
        return {"error": f"The dry run of the judge on {folder} failed", "output": output}
    left, done = int(found.group(1)), int(found.group(2))
    return {"lines": [f"{judge['provider']}/{judge['model']} ({judge['tag']}): {left} robot "
                      f"utterances of {folder} to label ({done} labelled already)"],
            "output": output, "key": {"judge": judge["tag"], "utterances": left + done}}


def estimate_label(ctx: Ctx) -> dict:
    """run_e0b.py label-cloud --allow-cloud (only when no local judge passed): every cloud judge
    of the ladder on the utterances of the annotation sheets (--n 150 per language)."""
    judges = cloud_judges(bool(ctx.options.get("openai_judges")))
    sizes = {}
    for lang in ("en", "es"):
        key = read_json(os.path.join(RESULTS, "e0b", lang, "e0", "key.json"))
        sizes[lang] = len(key["items"]) if key and "items" in key else None
    per = " + ".join(f"{n if n is not None else 'about 150 (sheets not made yet)'} ({lang})"
                     for lang, n in sizes.items())
    lines = [f"Each of {len(judges)} cloud judges labels the utterances of the sheets: {per}"]
    lines += [f"  {p}/{m} ({t})" for p, m, t in judges]
    lines.append("  (no local judge passed; a judge of the family of E0b's system model is "
                 "skipped; a tiny call to each first; then the report again)")
    return {"lines": lines, "output": "", "key": {"judges": [t for _, _, t in judges],
                                                  "sheets": sizes}}


def tiny_calls(names: list[str]) -> dict:
    return {"lines": [f"One tiny test call to {n}" for n in names], "output": "",
            "key": {"tiny": names}}


def cost_notes(step_id: str) -> dict:
    """The lines of the authors' cost estimate for a step: the output of costs_estimate.py and
    the rows of the table of COSTES.md §1 ({} when the clone does not have them)."""
    if step_id not in COSTS:
        return {}
    script_prefixes, table_prefixes = COSTS[step_id]
    out = {"script": [], "table": []}
    if os.path.exists(COSTS_SCRIPT):
        try:
            text = subprocess.run([sys.executable, COSTS_SCRIPT], capture_output=True, text=True,
                                  timeout=30).stdout
            out["script"] = [line.rstrip() for line in text.splitlines()
                             if line.startswith(script_prefixes)]
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        with open(COSTS_NOTES, encoding="utf-8") as f:
            text = f.read()
        section = text.split("\n## 1.", 1)[1].split("\n## 2.", 1)[0]
        out["table"] = [line for line in section.splitlines() if line.startswith(table_prefixes)]
    except (OSError, IndexError):
        pass
    return out if out["script"] or out["table"] else {}


def estimate(step: dict, ctx: Ctx) -> dict:
    """What a paid step would pay for: {lines, key, output, costs} or {error}. The key is what
    a permission is given for: the runner computes it again before the step and asks again when
    it changed (another file, other counts)."""
    try:
        est = step["estimate"](ctx) if step.get("estimate") else {"error": "No paid call"}
    except (OSError, ValueError, KeyError) as exc:
        est = {"error": f"{type(exc).__name__}: {exc}"}
    est.setdefault("output", "")
    est["costs"] = cost_notes(step["id"])
    return est


# ------------------------------------------------------------------ the steps
TITLES = {1: ("Pilot", "The debugging's exit, the pilot of every experiment, the freeze"),
          2: ("E0a", "The local model of each language"), 3: ("E0b", "The judge"),
          4: ("E1", "Bounded against LLM-led conversation, and the role swap"),
          5: ("E2", "The choice of the dialogue behavior"),
          6: ("E3", "The shared mechanisms"), 7: ("E4", "The simulated twin of E4"),
          8: ("Tables", "Scores and summaries of every experiment")}


def person(sid, n, label, awaits, what="") -> dict:
    return {"id": sid, "n": n, "label": label, "kind": "person", "awaits": awaits,
            "what": what or "A stop: a person must act before the evaluation goes on."}


def run_step(sid, n, label, what, commands, paid=lambda c: False, estimate=None,
             requires=lambda c: "", results=lambda c: [], server=lambda c: None, faults=False,
             reminder="", prepare=None) -> dict:
    return {"id": sid, "n": n, "label": label, "kind": "run", "what": what,
            "commands": commands, "paid": paid, "estimate": estimate, "requires": requires,
            "results": results, "server": server, "faults": faults, "reminder": reminder,
            "prepare": prepare}


def experiment_step(sid, n, label, rel, what, faults=False, reminder="") -> dict:
    """run_parallel.py on the profile of REPRODUCING.md: the server check, the campaign (with
    --allow-cloud when it makes paid calls) and its scores."""
    def paid(c):
        return bool(providers_of(rel)) if os.path.exists(os.path.join(EVAL, rel)) else None

    def commands(c):
        if not os.path.exists(os.path.join(EVAL, rel)):
            return [py("scripts/run_parallel.py", rel, HARDWARE, "...")]
        return [py("scripts/run_parallel.py", rel, HARDWARE, "--check-servers", busy=True),
                py("scripts/run_parallel.py", rel, HARDWARE,
                   *(["--allow-cloud"] if paid(c) else [])),
                py("analysis/score_runs.py", results_of(rel))]
    return run_step(sid, n, label, what, commands, paid=paid,
                    estimate=lambda c: estimate_experiment(rel, results_of(rel)),
                    requires=lambda c: requires_experiment(rel, c),
                    results=lambda c: [results_of(rel)], server=lambda c: rel, faults=faults,
                    reminder=reminder)


def judge_step(sid, n, label, rel) -> dict:
    """judge_runs.py with the judge E0b chose, on the results of an experiment: the dry run, the
    labels (paid with a cloud judge) and the summary (REPRODUCING.md)."""
    def judge(c):
        j = c.judge()
        return j if not j.get("error") else {"provider": "<p>", "model": "<judge>", "tag": "<tag>"}

    def commands(c):
        j, folder = judge(c), results_of(rel)
        base = ["analysis/judge_runs.py", folder, "--provider", j["provider"], "--model",
                j["model"], "--tag", j["tag"]]
        return [py(*base, "--dry-run"),
                py(*base, *(["--allow-cloud"] if j["provider"] != "ollama" else [])),
                py("analysis/judge_runs.py", folder, "--tag", j["tag"], "--summary")]

    def paid(c):
        j = c.judge()
        return None if j.get("error") else j["provider"] != "ollama"

    def requires(c):
        j = c.judge()
        if j.get("error"):
            return j["error"]
        if not glob.glob(os.path.join(EVAL, results_of(rel), "runs", "*", "*", "*.json")):
            return f"No dialogue in {results_of(rel)} to judge: run its experiment first"
        import judge_validation
        mine = judge_validation.family(j["tag"])
        clash = [m for m in load_yaml(rel).get("models") or []
                 if sessions_common.is_cloud(m) and judge_validation.family(m) == mine]
        if clash:
            return (f"The judge chosen in E0b ({j['tag']}) is of the family of the system model "
                    f"{clash[0]}: a judge never counts for a system of its own family. Run the "
                    f"next judge of the ladder that passed, with its own --tag, by hand, and read "
                    f"its summary for the cloud conditions (REPRODUCING.md, E1; "
                    f"JUDGE_VALIDATION.md, the ladder of judges); then continue")
        return keys_needed({j["provider"]} - {"ollama"})

    def server(c):
        j = c.judge()
        return E0B_EN if not j.get("error") and j["provider"] == "ollama" else None
    what = (f"The judge chosen in E0b labels every robot utterance of {rel}'s results (a local "
            f"judge: free; a cloud one: paid), then its summary.")
    return run_step(sid, n, label, what, commands, paid=paid,
                    estimate=lambda c: estimate_judge(results_of(rel), c.judge()),
                    requires=requires, results=lambda c: [], server=server)


def e2_config(ctx: Ctx) -> str:
    return os.path.join(ctx.root, "e2_config_en.yaml")


def write_e2_config(ctx: Ctx) -> None:
    """REPRODUCING.md, E2: a copy of offline/config_ollama.yaml with llm.model: <the E0a model>
    and dialog.language: English."""
    config = load_yaml("offline/config_ollama.yaml")
    config["llm"]["model"] = ctx.e0a_model("en")
    config.setdefault("dialog", {})["language"] = "English"
    os.makedirs(ctx.root, exist_ok=True)
    with open(e2_config(ctx), "w", encoding="utf-8") as f:
        f.write("# Written by whole_evaluation.py (REPRODUCING.md, E2): offline/config_ollama.yaml "
                "with the model of E0a in English\n")
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)


def sheets_left() -> list[str]:
    """The annotation sheets of E0b not finished yet (the review panel's .progress.json)."""
    left = []
    for lang in ("en", "es"):
        for sheet in ("a", "b"):
            path = os.path.join(RESULTS, "e0b", lang, "e0", f"sheet_{sheet}.progress.json")
            progress = read_json(path) or {}
            if not progress.get("finished"):
                left.append(f"sheet {sheet.upper()} ({lang}): "
                            + (f"{progress.get('done', 0)} of {progress.get('total', '?')}"
                               if progress else "not started"))
    return left


def tables_commands(ctx: Ctx) -> list[dict]:
    """score_runs.py on every results folder of the evaluation that has dialogues, and the
    judge's summary on those judged; each output kept in <root>/tables/."""
    out = os.path.join(ctx.root, "tables")
    j = ctx.judge()
    commands = []
    # The pilot's results are never reported: not in the tables
    for rel in ("experiments/e0a_dialogues.yaml", "experiments/e0a_dialogues_es.yaml", E1, SWAP, "experiments/e3_mechanisms.yaml", "experiments/e3_noise.yaml",
                "experiments/e3_failures.yaml", "experiments/e4_sim_es.yaml"):
        if not os.path.exists(os.path.join(EVAL, rel)):
            continue
        folder = results_of(rel)
        if not glob.glob(os.path.join(EVAL, folder, "runs", "*", "*", "*.json")):
            continue
        name = os.path.basename(folder)
        commands.append(py("analysis/score_runs.py", folder,
                           tee=os.path.join(out, f"{name}_scores.txt")))
        if not j.get("error") and os.path.exists(
                os.path.join(EVAL, folder, f"judgments_{j['tag']}.jsonl")):
            commands.append(py("analysis/judge_runs.py", folder, "--tag", j["tag"], "--summary",
                               tee=os.path.join(out, f"{name}_judge_{j['tag']}.txt")))
    return commands


def pilot_files() -> dict:
    """{name: file relative to evaluation/} of the pilot files (run_pilot.py)."""
    import run_pilot
    return {n: os.path.relpath(p, EVAL) for n, p in run_pilot.pilot_files().items()}


def pilot_requires(c) -> str:
    try:
        files = pilot_files()
    except (OSError, ValueError, KeyError) as exc:
        return f"The pilot files of experiments/pilot/ do not load: {exc}"
    if not files:
        return "No pilot file in experiments/pilot/ (doc/PILOT.md)"
    return ""


def pilot_results(c) -> list[str]:
    try:
        return [os.path.relpath(os.path.join(PILOT_ROOT, n), EVAL) for n in pilot_files()]
    except (OSError, ValueError, KeyError):
        return []


def smoke_judge(ctx: Ctx, rels: list[str]) -> list[str]:
    """--judge provider/model when the judge chosen in E0b is a cloud one and judges these
    experiments (E1, the role swap, E3's noise, the twin of E4)."""
    j = ctx.judge()
    judged = {E1, SWAP, "experiments/e3_noise.yaml", E4_TWIN}
    if j.get("error") or j["provider"] == "ollama" or not judged & set(rels):
        return []
    return ["--judge", f"{j['provider']}/{j['model']}"]


def smoke_step(sid, n, label, rels: list[str]) -> dict:
    """cloud_smoke.py before a paid campaign: one or two real calls to every cloud model of its
    files (and of the chosen judge, when it is a cloud one), with a permission of its own."""
    def calls(c):
        import cloud_smoke
        from experiment_files import load_experiment
        seen = []
        for rel in rels:
            judge = smoke_judge(c, rels)
            for role, provider, model in cloud_smoke.planned_calls(
                    load_experiment(os.path.join(EVAL, rel)), judge[1] if judge else ""):
                line = f"{provider}/{model} ({role})"
                if line not in seen:
                    seen.append(line)
        return seen

    def requires(c):
        for rel in rels:
            why = requires_experiment(rel, c)
            if why:
                return why
        return ""
    return run_step(sid, n, label,
                    "One or two real calls to every cloud model the next campaign calls, through "
                    "the code the campaign uses (cents): what the free pilot cannot check (keys, "
                    "pinned names, a setting a provider rejects, quotas, real latency).",
                    lambda c: [py("scripts/cloud_smoke.py", *rels, *smoke_judge(c, rels),
                                  "--allow-cloud")],
                    paid=lambda c: True, estimate=lambda c: tiny_calls(calls(c)),
                    requires=requires)


def steps() -> list[dict]:
    """The sub-steps of the whole evaluation, in order: (n, label), what each runs (its exact
    commands, as REPRODUCING.md) or what a person must do."""
    def model_en(c):
        return c.e0a_model("en") or "<the E0a model>"

    def openai(c):
        return ["--openai"] if c.options.get("openai_judges") else []

    def phase_b_estimate(c):
        parts = []
        for lang, (rel, folder) in {"en": ("experiments/e0a_dialogues.yaml", "results/e0a_dialogues"),
                                    "es": ("experiments/e0a_dialogues_es.yaml",
                                           "results/e0a_dialogues_es")}.items():
            finalists = c.finalists(lang)
            if finalists is None:
                return {"error": "The finalists of phase A are not known yet (run phase A)"}
            if finalists:
                parts.append(estimate_experiment(rel, folder, ["--models", ",".join(finalists),
                                                               "--lanes", "local", "--out", folder]))
        return estimate_many(parts) if parts else {"lines": ["No finalist in either language"],
                                                   "output": "", "key": {"none": True}}

    def phase_b_requires(c):
        missing = [lang for lang in ("en", "es") if c.finalists(lang) is None]
        if missing:
            return ("The finalists of phase A are missing (results/e0a_phase_a/finalists_"
                    f"{'/'.join(missing)}.txt): run phase A")
        return keys_needed({"gemini"})

    def decision_text(c):
        d = c.decision()
        if not d:
            return "results/e0a/decision.json is missing: run decide first."
        chosen = "; ".join(f"{lang}: {v.get('chosen') or 'none (' + str(v.get('state')) + ')'}"
                           for lang, v in (d.get("languages") or {}).items())
        return (f"Read results/e0a/decision.md (the rule per language, fixed before running). "
                f"Chosen: {chosen}. apply writes the chosen model of each language into the files "
                f"that use the local model (E0b, E1, the role swap, E3, the twin of E4, the real "
                f"sessions); a language with no model chosen is not written. Continue to apply.")

    def e0b_dialogues_requires(c):
        import run_e0b
        problems = run_e0b.decision_problems()
        if problems:
            return "E0b runs with the model E0a decided for each language: " + "; ".join(problems)
        return keys_needed({"gemini"})

    def e0b_dialogues_estimate(c):
        return estimate_many([estimate_experiment(f"experiments/{f}", f"results/e0b/{lang}",
                                                  ["--out", f"results/e0b/{lang}"])
                              for lang, f in (("en", "e0b_judge_dialogues.yaml"),
                                              ("es", "e0b_judge_dialogues_es.yaml"))])

    def report_requires(c):
        left = sheets_left()
        if left:
            return ("The two annotators fill the four sheets (HUMAN_REVIEW.md, task 4; "
                    "doc/human_review/e0b_annotation.md); still to finish: " + "; ".join(left))
        return ""

    def gate_requires(c):
        if not c.e0a_model("en"):
            return "E0a has not chosen the English model (results/e0a/decision.json)"
        exp = load_yaml(E1)
        if c.e0a_model("en") not in (exp.get("models") or []):
            return f"{E1} does not hold the model E0a chose ({c.e0a_model('en')}): run apply"
        return requires_experiment(E1, c, only_model=c.e0a_model("en"))

    def pilot_review(c):
        check = read_json(os.path.join(PILOT_ROOT, "check.json")) or {}
        seen = ("The check has not run yet." if not check else
                "The check PASSED." if check.get("passed") else
                "The check did NOT pass: fix every FAIL line and run the affected pilots again.")
        return ("Review the pilot's check (python3 scripts/run_pilot.py check; doc/PILOT.md, exit "
                "criteria). " + seen + " Every FAIL is a program or pipeline error: fixed, with a "
                "regression test, and the affected pilots run again. Every LOOK is read and "
                "attributed as in the debugging (C program, P prompt or method, S simulator, M "
                "model); a pilot finding never tunes the method on test scenarios: anything that "
                "would change the method, a prompt or a scenario is recorded for the author. The "
                "tokens per role the check prints go to the authors' cost notes. Fixes change the "
                "code hash: accept it when you continue.")

    def judge_label_cloud_needed(c):
        import run_e0b
        state = run_e0b.report_state()
        return bool(state) and not state["chosen"] and not state["cloud_labelled"]

    def label_cloud_commands(c):
        if not judge_label_cloud_needed(c):
            return []          # a local judge passed (or it is labelled): no cloud call
        return [py("scripts/run_e0b.py", "label-cloud", "--allow-cloud", *openai(c)),
                py("scripts/run_e0b.py", "report", "--a-en", "results/e0b/en/e0/sheet_a.csv",
                   "--b-en", "results/e0b/en/e0/sheet_b.csv",
                   "--a-es", "results/e0b/es/e0/sheet_a.csv",
                   "--b-es", "results/e0b/es/e0/sheet_b.csv")]

    def label_cloud_requires(c):
        import run_e0b
        if not run_e0b.report_state():
            return "The report of E0b (the step before) has not run"
        if not judge_label_cloud_needed(c):
            return ""
        return keys_needed({"gemini"} | ({"openai"} if c.options.get("openai_judges") else set()))

    adherence = ("After it: the audit of the adherence checker on phase B (HUMAN_REVIEW.md, task "
                 "3), which does not stop the evaluation.")
    confirm = ("After the campaign: the confirmation of the adherence checker and of the judge "
               "on a sample of it (HUMAN_REVIEW.md, tasks 5a and 5b), which do not stop the "
               "evaluation.")
    return [
        # ---- 1. The debugging's exit, the pilot of every experiment, the freeze
        person("debugging", 1, "debugging exit",
               lambda c: "The debugging's exit criterion met (doc/DEBUGGING.md): a full pass with "
                         "no program (C) and no prompt or method (P) failure, first with "
                         "qwen3:8b, then with llama3.1:8b; every C or P fix with its regression "
                         "test. Then the GPU server's disk is made ready for the pilot: the "
                         "debugging's models removed, the pilot's system model pulled "
                         "(doc/PILOT.md)."),
        run_step("pilot", 1, "pilot of every experiment",
                 "Every pilot file of experiments/pilot/ through the whole chain of its "
                 "experiment (dialogues, store, score, judge, run health, provenance), with the "
                 "pilot's system model and the free stand-ins of the cloud providers (no paid "
                 "call), then its exit criteria (run_pilot.py, pilot_check.py; doc/PILOT.md).",
                 lambda c: [py("scripts/run_pilot.py", "run")],
                 requires=pilot_requires, results=pilot_results, server=lambda c: E0B_EN,
                 faults=True,
                 reminder="The pilot's results are never reported; its check is reviewed at the "
                          "next stop."),
        person("pilot_review", 1, "pilot review", pilot_review),
        person("freeze", 1, "freeze commit",
               lambda c: "The freeze commit: the method, the experiment files and the evaluation "
                         "as the pilot left them, committed before E0a (EXPERIMENTS.md, rules for "
                         "every campaign); nothing of the method changes after it. Accept the "
                         "code hash when you continue: it is the method of the whole "
                         "evaluation from here."),
        # ---- 2. E0a
        person("e0a_traps", 2, "trap review",
               lambda c: "The trap cases of phase A reviewed by an author and the corrections "
                         "applied (HUMAN_REVIEW.md, task 1; doc/human_review/e0a_trap_review.md), "
                         "the corrected datasets committed before E0a is frozen. The datasets "
                         "are sources of the code hash: if the review changed them, accept the "
                         "new hash when you continue."),
        run_step("e0a_preflight", 2, "preflight",
                 "The GPU server is free, the API key is set, and one tiny call to the simulated "
                 "user's Gemini model answers.",
                 lambda c: [py("scripts/run_e0a.py", "preflight", "--allow-cloud")],
                 paid=lambda c: True,
                 estimate=lambda c: tiny_calls(["gemini/gemini-2.5-flash-lite (the simulated "
                                                "user)"]),
                 requires=lambda c: keys_needed({"gemini"}),
                 server=lambda c: E0B_EN),
        run_step("e0a_phase_a", 2, "phase A",
                 "Every candidate of offline/e0a_candidates.txt on the critical questions, "
                 "offline, both languages (one model on the server at a time); the safety gate "
                 "and the finalists of each language in results/e0a_phase_a. No paid call.",
                 lambda c: [py("scripts/run_e0a.py", "phase-a")], server=lambda c: E0B_EN),
        person("e0a_adjudication", 2, "adjudication",
               lambda c: "The blind adjudication of the cases that half or more of the models "
                         "failed (HUMAN_REVIEW.md, task 2; doc/human_review/e0a_adjudication.md), "
                         "its corrections applied (review_panel.py dataset apply --adjudicate) "
                         "and the gate recomputed (from offline: python3 run_models.py --reps 3 "
                         "--out ../results/e0a_phase_a --gate-only), which rewrites the finalists "
                         "that phase B takes. The datasets are sources of the code hash: accept "
                         "the new hash when you continue."),
        run_step("e0a_phase_b", 2, "phase B",
                 "Whole dialogues of the finalists of each language (e0a_dialogues*.yaml, 3 "
                 "repetitions, the simulated user is Gemini: paid), one model on the server at "
                 "a time, then scored.",
                 lambda c: [py("scripts/run_e0a.py", "phase-b", "--allow-cloud")],
                 paid=lambda c: True, estimate=phase_b_estimate, requires=phase_b_requires,
                 results=lambda c: ["results/e0a_dialogues", "results/e0a_dialogues_es"],
                 server=lambda c: E0B_EN, reminder=adherence),
        run_step("e0a_decide", 2, "decide",
                 "The rule per language on every dialogue of phase B: results/e0a/decision.md.",
                 lambda c: [py("scripts/run_e0a.py", "decide")]),
        person("e0a_decision", 2, "the decision", decision_text),
        run_step("e0a_apply", 2, "apply",
                 "Writes the chosen model of each language in the files that use the local "
                 "model (they are sources of the code hash).",
                 lambda c: [py("scripts/run_e0a.py", "apply")]),
        person("e0a_after_apply", 2, "validation again",
               lambda c: "Repeat the validation and the debugging with the chosen model(s) "
                         "before E0b (REPRODUCING.md, E0a; the checks of Before anything). apply "
                         "changed the experiment files, which are sources of the code hash: "
                         "accept the new hash when you continue."),
        # ---- 3. E0b
        run_step("e0b_preflight", 3, "preflight",
                 "The GPU server is free, the API key is set, and one tiny call to the simulated "
                 "user's model answers (no judge is called here).",
                 lambda c: [py("scripts/run_e0b.py", "preflight", "--allow-cloud")],
                 paid=lambda c: True,
                 estimate=lambda c: tiny_calls(["gemini/gemini-2.5-flash-lite (the simulated "
                                                "user)"]),
                 requires=lambda c: keys_needed({"gemini"}),
                 server=lambda c: E0B_EN),
        run_step("e0b_dialogues", 3, "dialogues",
                 "Phase 1: the dialogues in English and Spanish, with the model E0a decided for "
                 "each language (the simulated user is Gemini: paid), split into development "
                 "and validation halves; not scored.",
                 lambda c: [py("scripts/run_e0b.py", "dialogues", "--allow-cloud")],
                 paid=lambda c: True, estimate=e0b_dialogues_estimate,
                 requires=e0b_dialogues_requires,
                 results=lambda c: ["results/e0b/en", "results/e0b/es"],
                 server=lambda c: E0B_EN),
        run_step("e0b_label_dev", 3, "label (development half)",
                 "Every local judge labels the development half only (free), to tune the "
                 "judge's instructions.",
                 lambda c: [py("scripts/run_e0b.py", "label", "--development-only")],
                 server=lambda c: E0B_EN),
        person("e0b_tune", 3, "tune the instructions",
               lambda c: "Adjust the judge's instructions on the development half only, then "
                         "freeze them (JUDGE_VALIDATION.md, Running E0b): after changing them, "
                         "delete a judge's labels file (or use a new tag) before labelling "
                         "again. The instructions are sources of the code hash: accept the new "
                         "hash when you continue."),
        run_step("e0b_label", 3, "label",
                 "Every local judge labels every dialogue (free); the two annotation sheets of "
                 "each language are drawn. No cloud call.",
                 lambda c: [py("scripts/run_e0b.py", "label")],
                 server=lambda c: E0B_EN),
        run_step("e0b_report", 3, "report",
                 "Once the two annotators have filled the four sheets: the report of each "
                 "language and one judge for both (results/e0b/report.json). A local judge that "
                 "passes is the judge: E0b ends without any cloud call.",
                 lambda c: [py("scripts/run_e0b.py", "report",
                               "--a-en", "results/e0b/en/e0/sheet_a.csv",
                               "--b-en", "results/e0b/en/e0/sheet_b.csv",
                               "--a-es", "results/e0b/es/e0/sheet_a.csv",
                               "--b-es", "results/e0b/es/e0/sheet_b.csv")],
                 requires=report_requires,
                 reminder="Before a judge is blamed, check the agreement between the two "
                          "annotators: below 0.67 on a label, the guide is revised "
                          "(JUDGE_VALIDATION.md, the decision rule)."),
        run_step("e0b_label_cloud", 3, "cloud judges (only if no local one passed)",
                 "Only when the report chose no judge among the local ones: the cloud judges of "
                 "the ladder label the sheets' utterances (paid, after a tiny call to each), then "
                 "the report again with the same sheets. Nothing runs when a local judge passed.",
                 label_cloud_commands, paid=judge_label_cloud_needed, estimate=estimate_label,
                 requires=label_cloud_requires),
        # ---- 4. E1
        run_step("e1_gate", 4, "go/no-go gate",
                 "Only the local cells (A and B) with the model E0a chose (the simulated user is "
                 "a cloud model: paid), then scored. After E0b: its safety measures need the "
                 "validated judge.",
                 lambda c: [py("scripts/run_experiment.py", E1, "--only-model", model_en(c),
                               "--allow-cloud", env={"ROS_DOMAIN_ID": lane_domain()}),
                            py("analysis/score_runs.py", results_of(E1))],
                 paid=lambda c: True,
                 estimate=lambda c: estimate_experiment(E1, results_of(E1),
                                                        ["--only-model", model_en(c)],
                                                        runner="run_experiment"),
                 requires=gate_requires, results=lambda c: [results_of(E1)],
                 server=lambda c: E1),
        person("e1_gate_decision", 4, "gate decision",
               lambda c: "Apply the criterion of \"the same\", written down before running these "
                         "cells (EXPERIMENTS.md, E1, the go/no-go gate), to the scores of the "
                         f"gate (python3 analysis/score_runs.py {results_of(E1)}) and to the "
                         "judge's labels of its utterances. If the method performs the same as "
                         "the LLM-led conversation, nothing else runs: do not continue."),
        smoke_step("e1_smoke", 4, "cloud smoke check", [E1]),
        experiment_step("e1_campaign", 4, "campaign", E1,
                        "The whole campaign: the local conditions in one lane on the GPU, the "
                        "cloud conditions in a second lane at the same time (paid: the large "
                        "cloud model and the simulated user).", reminder=confirm),
        judge_step("e1_judge", 4, "judge", E1),
        smoke_step("e1_swap_smoke", 4, "role swap smoke check", [SWAP]),
        experiment_step("e1_swap", 4, "role swap", SWAP,
                        "The same with the role swap: the large system model is Google's, the "
                        "simulated user OpenAI's (paid).",
                        reminder="A Gemini system model needs the google-genai package in the "
                                 "environment of the LLM gateway (REPRODUCING.md, Cloud models)."),
        judge_step("e1_swap_judge", 4, "role swap judge", SWAP),
        # ---- 5. E2
        run_step("e2", 5, "behavior choice",
                 "Offline, with the local model of E0a and a configuration in English: the "
                 "method, no_filter and keywords (no paid call; results in offline/results).",
                 lambda c: [py("offline/benchmarks/bench_behavior_choice.py", "--config",
                               e2_config(c), "--method", "all", "--save")],
                 requires=lambda c: "" if c.e0a_model("en") else
                 "E0a has not chosen the English model (results/e0a/decision.json)",
                 server=lambda c: E0B_EN, prepare=write_e2_config),
        # ---- 6. E3
        smoke_step("e3_smoke", 6, "cloud smoke check", list(E3_FILES)),
        experiment_step("e3_mechanisms", 6, "mechanisms", "experiments/e3_mechanisms.yaml",
                        "The mechanisms without noise (the simulated user is a cloud model: "
                        "paid)."),
        experiment_step("e3_noise", 6, "noise", "experiments/e3_noise.yaml",
                        "The noise levels with the method and the LLM-led condition (paid: the "
                        "simulated user)."),
        experiment_step("e3_failures", 6, "model failures", "experiments/e3_failures.yaml",
                        "The failures of the system's model, injected by the runner (paid: the "
                        "simulated user).", faults=True),
        judge_step("e3_noise_judge", 6, "noise judge", "experiments/e3_noise.yaml"),
        # ---- 7. E4 twin
        smoke_step("e4_smoke", 7, "cloud smoke check", [E4_TWIN]),
        experiment_step("e4_twin", 7, "twin", E4_TWIN,
                        "The simulated twin of E4, before the sessions (paid: the simulated "
                        "user).", reminder=confirm.replace("the campaign", "the twin")),
        judge_step("e4_twin_judge", 7, "twin judge", E4_TWIN),
        # ---- 8. Tables
        run_step("tables", 8, "tables",
                 "score_runs.py on every results folder of the evaluation with dialogues, and "
                 "the judge's summary where it labelled; each output kept in <the whole-"
                 "evaluation folder>/tables. E0b is not scored (its rule), nor the pilot (never "
                 "reported). The technical check has no script yet.", tables_commands),
    ]


def by_id() -> dict:
    return {s["id"]: s for s in steps()}


def order() -> list[str]:
    return [s["id"] for s in steps()]


# ------------------------------------------------------------------ state
def state_path(root: str) -> str:
    return os.path.join(root, "state.json")


def empty_state() -> dict:
    return {"created": now(), "method": None, "method_history": [], "status": "idle",
            "next": None, "halt": None, "runner": None, "steps": {}, "acks": {},
            "permissions": {}, "options": {}}


def read_state(root: str) -> dict:
    state = read_json(state_path(root))
    return {**empty_state(), **state} if isinstance(state, dict) else empty_state()


@contextlib.contextmanager
def locked(root: str):
    """The state, read and written under a lock (the runner and the panel both write it)."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, ".state.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state(root)
        yield state
        tmp = state_path(root) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, ensure_ascii=False)
        os.replace(tmp, state_path(root))


def log(root: str, text: str) -> None:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "campaign.log"), "a", encoding="utf-8") as f:
        f.write(f"### {time.strftime('%a %d %H:%M:%S')} {text}\n")


def alive(runner: dict | None) -> bool:
    """Whether the process group of a runner still has a live process (a zombie left by its
    launcher does not count)."""
    if not runner or not runner.get("pgid"):
        return False
    try:
        pgid = int(runner["pgid"])
        os.killpg(pgid, 0)
    except (OSError, ValueError):
        return False
    for stat in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(stat) as f:
                fields = f.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[2]) == pgid and fields[0] != "Z":     # state, ppid, pgrp
            return True
    return False


def label_of(sid: str) -> str:
    steps_ = steps()
    for i, s in enumerate(steps_):
        if s["id"] == sid:
            k = sum(1 for t in steps_[:i + 1] if t["n"] == s["n"])
            return f"{s['n']}.{k} {TITLES[s['n']][0]} {s['label']}"
    return sid


def reset_from(state: dict, start: str) -> None:
    """A start at another step than the one to resume: that step and every later one run
    again (their scripts skip what is saved) and the stops of a person among them are asked
    again. The permissions stay (each is checked against its estimate)."""
    ids = order()
    for sid in ids[ids.index(start):]:
        state["steps"].pop(sid, None)
        state["acks"].pop(sid, None)


def acknowledge(root: str, sid: str) -> str:
    step = by_id().get(sid)
    if not step or step["kind"] != "person":
        return "Not a stop of a person"
    with locked(root) as state:
        state["acks"][sid] = {"time": now()}
        state["steps"][sid] = {"status": "done", "ended": now()}
        if (state.get("halt") or {}).get("step") == sid:
            state["halt"] = {"step": sid, "kind": "continue", "time": now(),
                             "text": "Done (said by a person): start to continue after it."}
    log(root, f"{label_of(sid)}: done (said by a person)")
    return ""


def allow(root: str, sid: str, est: dict) -> str:
    """Record the author's permission for the paid calls of a step, for the estimate shown."""
    step = by_id().get(sid)
    if not step or step["kind"] != "run":
        return "Unknown step"
    if est.get("error"):
        return est["error"]
    with locked(root) as state:
        state["permissions"][sid] = {"time": now(), "key": est["key"], "shown": est["lines"]}
    log(root, f"{label_of(sid)}: paid calls allowed ({'; '.join(est['lines'])})")
    return ""


def revoke(root: str, sid: str) -> str:
    with locked(root) as state:
        if not state["permissions"].pop(sid, None):
            return "No permission to revoke"
    log(root, f"{label_of(sid)}: permission revoked")
    return ""


def method_check(root: str, state: dict | None = None, current: str | None = None) -> dict:
    """The method's code hash recorded when the evaluation started (or accepted since) against
    the current one; which steps a change affects: those that ran with another hash (their
    results come from another method than the steps to come) and the results folders begun
    with other code (their scripts refuse to resume them: provenance.py)."""
    state = state or read_state(root)
    current = current or code_hash()
    recorded = (state.get("method") or {}).get("code_hash")
    changed = bool(recorded and recorded != current)
    affected, folders = [], []
    for step in steps():
        s = state["steps"].get(step["id"]) or {}
        # The steps run with the recorded method (those run before a change accepted earlier
        # were accepted then)
        if changed and step["kind"] == "run" and s.get("code_hash") == recorded:
            affected.append({"id": step["id"], "label": label_of(step["id"]),
                             "why": f"ran ({s.get('status')}) with another method: its results "
                                    f"come from another method than the steps to come"})
        try:
            mine = step["results"](Ctx(root)) if step["kind"] == "run" else []
        except (OSError, ValueError, KeyError):
            mine = []
        for folder in mine:
            prov = read_json(os.path.join(EVAL, folder, "provenance.json")) or {}
            if prov.get("code_hash") and prov["code_hash"] != current:
                folders.append({"id": step["id"], "label": label_of(step["id"]),
                                "folder": folder,
                                "why": f"{folder} was begun with other code: its script refuses "
                                       f"to resume it (provenance.json); move it aside (e.g. to "
                                       f"results_archive/) to run this step again"})
    return {"recorded": recorded, "current": current, "changed": changed, "affected": affected,
            "folders": folders, "history": state.get("method_history") or []}


def halt(root: str, sid: str, kind: str, text: str) -> int:
    with locked(root) as state:
        state["status"] = {"paused": "paused", "failed": "failed"}.get(kind, "halted")
        state["halt"] = {"step": sid, "kind": kind, "text": text, "time": now()}
        state["next"] = sid
    log(root, f"{label_of(sid)}: {kind.upper()}: {text}")
    return {"failed": 1, "paused": 0}.get(kind, 2)


# ------------------------------------------------------------------ run health
def server_check(rel: str) -> dict:
    """The server check of run_parallel.py (as before a run): free, or in use by other work."""
    code, output = capture(["python3", "scripts/run_parallel.py", rel, HARDWARE,
                            "--check-servers"], 240)
    if code == 0:
        return measure("GPU server free (the server check after the step)", "free", "OK", "")
    if code == 3:
        return measure("GPU server free (the server check after the step)",
                       "in use by other work", "CHECK",
                       "if it was used during the step, its latencies are not comparable "
                       "(REPRODUCING.md, Rules for every run)")
    return measure("GPU server free (the server check after the step)", "could not check", "",
                   output.splitlines()[-1] if output else f"exit status {code}")


def measure(what, value, verdict, note) -> dict:
    return {"what": what, "value": value, "verdict": verdict, "note": note}


def dialogue_measures(folders: list[str], since: float, faults: bool = False) -> tuple:
    """What the dialogues saved since `since` in these results folders say of the run: the
    model's latency (descriptive), the calls to the model cut (0 in normal operation), the
    dialogues the simulator's exclusion rule leaves out (reported), the simulated user's failures
    (not saved), the statuses. (measures, the latency lines of score_runs.py)."""
    sys.path.insert(0, os.path.join(EVAL, "analysis"))
    import score_runs
    rows, unreadable, sim_failures, statuses = [], 0, 0, collections.Counter()
    for folder in folders:
        path = folder if os.path.isabs(folder) else os.path.join(EVAL, folder)
        for p in score_runs.run_files(path):
            if os.path.getmtime(p) < since:
                continue
            try:
                rec = score_runs.load_record(p)
                rows.append(score_runs.add_adherence(score_runs.score(rec), rec))
                statuses[rec.get("status") or "?"] += 1
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
        try:
            with open(os.path.join(path, "simulator_errors.jsonl"), encoding="utf-8") as f:
                sim_failures += sum(1 for line in f if line.strip()
                                    and json.loads(line).get("time", 0) >= since)
        except (OSError, ValueError):
            pass
    out = [measure("Dialogues saved by this step", f"{len(rows)}"
                   + (f" ({', '.join(f'{k} {v}' for k, v in sorted(statuses.items()))})"
                      if statuses else "") + (f"; {unreadable} unreadable" if unreadable else ""),
                   "CHECK" if unreadable else "", "unreadable records" if unreadable else "")]
    if sim_failures:
        out.append(measure("Simulated user's failures (not saved; run again on resume)",
                           str(sim_failures), "", "no threshold in the docs; the runner stops "
                           "after several in a row"))
    if not rows:
        return out, []
    lat = [x for r in rows for x in r.get("_latencies") or []]
    if lat:
        over = sum(x > score_runs.SLOW_TURN_S for x in lat) / len(lat)
        out.append(measure("Model latency per turn",
                           f"median {score_runs.pct(lat, .5):.2f} s, p95 "
                           f"{score_runs.pct(lat, .95):.2f} s, worst {max(lat):.2f} s; "
                           f"{over:.0%} of turns over {score_runs.SLOW_TURN_S:g} s", "",
                           "descriptive: the evaluation sets no threshold (METRICS.md)"))
    timeouts = sum(r.get("llm_timeouts") or 0 for r in rows)
    skipped = sum(r.get("llm_skipped") or 0 for r in rows)
    value = f"llm_timeouts {timeouts}, llm_skipped {skipped}"
    if faults:
        out.append(measure("LLM calls cut", value, "", "model failures are injected in this "
                           "experiment: calls cut are expected"))
    else:
        out.append(measure("LLM calls cut", value, "OK" if not (timeouts or skipped) else "CHECK",
                           "0 in normal operation (score_runs.py, latency_summary)"))
    excluded = sum(bool(r.get("sim_excluded")) for r in rows)
    out.append(measure("Dialogues excluded by the simulator's rule", f"{excluded} of {len(rows)} "
                       f"({excluded / len(rows):.0%})", "", "reported as a check; no threshold "
                       "in the docs"))
    return out, score_runs.latency_summary(rows)


def health(step: dict, ctx: Ctx, since: float, code: int | None, paused: bool) -> dict:
    """The run health of a step that ended: its exit status, the server check, what its
    dialogues say; each with OK or CHECK where the docs give the rule, else the value only;
    then a sentence."""
    measures, details = [], []
    if paused:
        measures.append(measure("Exit status", "paused", "", "it resumes where it stopped"))
    else:
        measures.append(measure("Exit status", str(code), "OK" if code == 0 else "CHECK",
                                "" if code == 0 else "see its log"))
    try:
        rel = step["server"](ctx)
    except (OSError, ValueError, KeyError):
        rel = None
    if rel:
        measures.append(server_check(rel))
    try:
        folders = step["results"](ctx)
    except (OSError, ValueError, KeyError):
        folders = []
    if folders:
        more, details = dialogue_measures(folders, since, step.get("faults", False))
        measures += more
    return summarise(measures, details, paused, code)


def summarise(measures: list[dict], details: list[str], paused: bool, code) -> dict:
    checks = [m["what"] for m in measures if m["verdict"] == "CHECK" and m["what"] != "Exit status"]
    if paused:
        sentence = "Paused: it resumes where it stopped."
    elif code:
        sentence = (f"The step failed (exit status {code})"
                    + (f"; it may have failed because of: {', '.join(checks)}" if checks else
                       ": see its log") + ".")
    elif checks:
        sentence = (f"Check before going on: {', '.join(checks)}: the results of this step may "
                    f"be affected.")
    else:
        sentence = "All as usual."
    verdict = "CHECK" if (checks or (code and not paused)) else "OK"
    return {"verdict": "" if paused else verdict, "sentence": sentence, "measures": measures,
            "details": details}


def health_lines(h: dict) -> list[str]:
    lines = ["Run health:"]
    for m in h["measures"]:
        lines.append(f"  {m['verdict'] or '-':5s} {m['what']}: {m['value']}"
                     + (f" ({m['note']})" if m["note"] else ""))
    lines += ["  " + line for line in h.get("details") or []]
    return lines + ["  " + h["sentence"]]


# ------------------------------------------------------------------ the runner
STOP = {"flag": False}


def on_stop(*_):
    STOP["flag"] = True


def execute(root: str, step: dict, commands: list[dict]) -> tuple[int, bool]:
    """Run the commands of a step one after another, output to logs/<step>.log: (exit status of
    the last one run, paused). A pause (SIGINT to the process group: the script under way got it
    too) waits for the script to stop what it launched."""
    os.makedirs(os.path.join(root, "logs"), exist_ok=True)
    path = os.path.join(root, "logs", f"{step['id']}.log")
    code = 0
    for command in commands:
        if STOP["flag"]:
            return code, True
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"\n$ {shown(command)}   ({now()})\n")
        if command["tee"]:
            os.makedirs(os.path.dirname(command["tee"]), exist_ok=True)
        target = command["tee"] or path
        with open(target, "w" if command["tee"] else "a", encoding="utf-8") as out:
            process = subprocess.Popen(executable(command["argv"]), cwd=EVAL, stdout=out,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       env={**os.environ, "PYTHONUNBUFFERED": "1",
                                            **command["env"]})
            stopped_at = None
            while process.poll() is None:
                time.sleep(0.5)
                if STOP["flag"] and stopped_at is None:
                    stopped_at = time.monotonic()
                if stopped_at is not None and time.monotonic() - stopped_at > PAUSE_WAIT_S:
                    process.terminate()
                    stopped_at = float("inf")
        code = process.returncode
        if command["tee"]:
            with open(command["tee"], encoding="utf-8", errors="replace") as f, \
                    open(path, "a", encoding="utf-8") as out:
                out.write(f.read())
        if STOP["flag"]:
            return code, True
        if code == 3 and command["busy"]:
            return 3, False
        if code:
            return code, False
    return code, False


def permission_ok(root: str, step: dict, ctx: Ctx) -> tuple[bool, str, dict]:
    """Whether the author allowed this step's paid calls for what it would pay for now."""
    est = estimate(step, ctx)
    if est.get("error"):
        return False, est["error"], est
    given = read_state(root)["permissions"].get(step["id"])
    if not given:
        return False, "", est
    if given["key"] != est["key"]:
        return False, "changed", est
    return True, "", est


def run(root: str, start: str | None = None, accept_method: bool = False,
        options: dict | None = None) -> int:
    """Run the evaluation from `start` (default: where it stopped) to the end, or to the first
    stop. Exit status: 0 done or paused, 1 a step failed, 2 a stop (a person, a permission, a
    precondition, the method changed)."""
    ids = order()
    signal.signal(signal.SIGINT, on_stop)
    signal.signal(signal.SIGTERM, on_stop)
    current = code_hash()
    with locked(root) as state:
        if alive(state.get("runner")) and int(state["runner"]["pgid"]) != os.getpgrp():
            print("The whole evaluation is already running", file=sys.stderr)
            return 1
        start = start or state.get("next") or ids[0]
        if start not in ids:
            print(f"Unknown step {start}: one of {', '.join(ids)}", file=sys.stderr)
            return 1
        if state.get("next") and start != state["next"] or not state.get("next") and \
                state["steps"].get(start):
            reset_from(state, start)
        state["options"].update(options or {})
        method = state.get("method")
        changed = bool(method and method["code_hash"] != current)
        if not method or (changed and accept_method):
            if changed:
                state["method_history"].append({**method, "replaced": now(), "at": start})
            state["method"] = {"code_hash": current, "time": now(), "at": start}
            changed = False
        state["runner"] = {"pid": os.getpid(), "pgid": os.getpgrp(), "started": now(),
                           "from": start}
        state["status"], state["halt"], state["next"] = "running", None, start
    log(root, f"start at {label_of(start)} (method {current[:12]})")
    if changed:
        return halt(root, start, "method", "The method changed since the evaluation started (code "
                    "hash of provenance.py): see which steps it affects; start again accepting "
                    "the current code (--accept-method-change), or restore the method")
    steps_ = by_id()
    for sid in ids[ids.index(start):]:
        step = steps_[sid]
        with locked(root) as state:
            state["next"] = sid
            ctx = Ctx(root, {**state["options"], "_health": {
                k: v.get("health") for k, v in state["steps"].items()}})
            acked = sid in state["acks"]
        if STOP["flag"]:
            return halt(root, sid, "paused", "Paused before it started")
        if step["kind"] == "person":
            if acked:
                continue
            return halt(root, sid, "person", step["awaits"](ctx))
        if code_hash() != current:
            return halt(root, sid, "method", "The method changed while the evaluation ran: the "
                        "steps after this one would run with another method")
        why = step["requires"](ctx)
        if why:
            return halt(root, sid, "awaits", why)
        paid = step["paid"](ctx)
        if paid:
            ok, why, est = permission_ok(root, step, ctx)
            if not ok:
                text = ("Paid cloud calls: " + "; ".join(est.get("lines") or [why]) + ". "
                        + ("The paid calls changed since they were allowed (" + "; ".join(
                            read_state(root)["permissions"][sid]["shown"]) + "): allow them "
                           "again." if why == "changed" else "Allow them (the page shows the "
                           "estimate), then continue."))
                return halt(root, sid, "permission", text if not est.get("error") else why)
        commands = step["commands"](ctx)
        if not paid and any("--allow-cloud" in c["argv"] for c in commands):
            return halt(root, sid, "awaits", "A command would pass --allow-cloud without the "
                        "step's permission: not run")
        if step.get("prepare"):
            step["prepare"](ctx)
        since = time.time()
        with locked(root) as state:
            state["steps"][sid] = {"status": "running", "started": now(), "code_hash": current,
                                   "since": since, "commands": [shown(c) for c in commands]}
        log(root, f"{label_of(sid)}: running")
        code, paused = execute(root, step, commands)
        h = health(step, ctx, since, code, paused)
        with open(os.path.join(root, "logs", f"{sid}.log"), "a", encoding="utf-8") as f:
            f.write("\n".join(health_lines(h)) + "\n")
        status = "paused" if paused else "done" if code == 0 else "failed"
        with locked(root) as state:
            state["steps"][sid].update(status=status, ended=now(), returncode=code, health=h)
        log(root, f"{label_of(sid)}: {status} (exit {code}); health {h['verdict'] or '-'}: "
                  f"{h['sentence']}")
        if paused:
            return halt(root, sid, "paused", "Paused: start again to resume it (what is saved is "
                        "kept)")
        if code == 3 and any(c["busy"] for c in commands):
            return halt(root, sid, "busy", "The servers are not free (see the step's output): the "
                        "step was not started; continue when they are")
        if code:
            return halt(root, sid, "failed", f"{h['sentence']} See logs/{sid}.log")
    with locked(root) as state:
        state["status"], state["next"], state["halt"] = "done", None, None
    log(root, "the whole evaluation is done")
    return 0


# ------------------------------------------------------------------ what the panel shows
def overview(root: str, current_hash: str | None = None) -> dict:
    """Everything the page shows: the steps (with their commands, what they await, whether they
    pay and the permission, the status and run health), the state, the method check."""
    state = read_state(root)
    ctx = Ctx(root, {**state["options"], "_health": {
        k: v.get("health") for k, v in state["steps"].items()}})
    rows, counts = [], collections.Counter()
    for step in steps():
        counts[step["n"]] += 1
        row = {"id": step["id"], "n": step["n"], "k": counts[step["n"]], "label": step["label"],
               "kind": step["kind"], "what": step["what"],
               "title": TITLES[step["n"]][0], "status": (state["steps"].get(step["id"]) or {})
               .get("status", "done" if step["id"] in state["acks"] else "")}
        s = state["steps"].get(step["id"]) or {}
        row["health"] = s.get("health")
        row["ended"] = s.get("ended", "")
        if step["kind"] == "person":
            try:
                row["awaits"] = step["awaits"](ctx)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row["awaits"] = f"({exc})"
        else:
            try:
                row["commands"] = [shown(c) for c in step["commands"](ctx)]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row["commands"] = [f"(known when it runs: {exc})"]
            try:
                row["paid"] = step["paid"](ctx)
            except (OSError, ValueError, KeyError):
                row["paid"] = None
            row["permission"] = state["permissions"].get(step["id"])
            row["reminder"] = step.get("reminder", "")
        rows.append(row)
    return {"root": root, "titles": {n: list(t) for n, t in TITLES.items()}, "steps": rows,
            "status": state["status"], "next": state["next"], "halt": state["halt"],
            "running": alive(state.get("runner")), "runner": state.get("runner"),
            "options": {k: v for k, v in state["options"].items() if not k.startswith("_")},
            "method": method_check(root, state, current_hash),
            "costs_available": os.path.exists(COSTS_SCRIPT) or os.path.exists(COSTS_NOTES)}


def tail(path: str, lines: int = 40) -> list[str]:
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


# ------------------------------------------------------------------ command line
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=ROOT, help="The folder of the whole evaluation")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("run")
    p.add_argument("--from", dest="start", default=None, choices=order())
    p.add_argument("--accept-method-change", action="store_true",
                   help="The current code becomes the method of the evaluation (recorded)")
    p.add_argument("--openai-judges", action="store_true",
                   help="E0b: also the OpenAI judges (run_e0b.py --openai)")
    sub.add_parser("status")
    for name in ("allow", "done", "revoke"):
        sub.add_parser(name).add_argument("step", choices=order())
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    if args.action == "run":
        options = {"openai_judges": True} if args.openai_judges else None
        return run(root, args.start, args.accept_method_change, options)
    if args.action == "status":
        o = overview(root)
        for row in o["steps"]:
            mark = "*" if row["id"] == o["next"] else " "
            print(f"{mark} {label_of(row['id']):40s} {row['status'] or '-':8s} "
                  f"{(row.get('health') or {}).get('verdict', '')}")
        print(f"\n{o['status']}" + (f": {o['halt']['kind']}: {o['halt']['text']}"
                                    if o["halt"] else ""))
        if o["method"]["changed"]:
            print("The method changed since the evaluation started")
        return 0
    if args.action == "done":
        error = acknowledge(root, args.step)
    elif args.action == "revoke":
        error = revoke(root, args.step)
    else:
        step = by_id()[args.step]
        est = estimate(step, Ctx(root, read_state(root)["options"]))
        print("\n".join(est.get("lines") or [est.get("error", "")]))
        for line in (est.get("costs") or {}).get("script", []) + (est.get("costs") or {}).get(
                "table", []):
            print("  " + line)
        if est.get("error") or input("Allow these paid calls? [y/N] ").strip().lower() != "y":
            return 1
        error = allow(root, args.step, est)
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
