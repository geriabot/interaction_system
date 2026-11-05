# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
A web page, on this computer only, to drive the evaluation without a terminal: the whole simulated
evaluation, step after step in the order of doc/REPRODUCING.md (scripts/whole_evaluation.py: the
stops where a person must act, the permission for every paid step, the method's code hash, the
run health of every step), the campaign of an
experiment on a hardware profile (what it would run, whether the servers are free, start, pause,
its progress), one dialogue of an experiment with its simulated user without storing anything,
the sessions with real participants (the campaigns of real sessions and their configurations,
the pre-session check, the plans, the task cards full screen, a test that stores nothing and the
trial that counts), the configurations of the real sessions (how the robot sounds: drafts edited
and tried, frozen once a campaign uses them; real/campaigns.py), and the Tests tab: any scenario
tried by voice with a person as the user, nothing stored (scripts/test_panel.py, its code).

    python3 evaluation/scripts/control_panel.py [--port 8095] [--env jazzy|lyrical]
    # then open http://localhost:8095

It only calls the scripts of the repository (scripts/whole_evaluation.py, scripts/run_parallel.py,
scripts/run_experiment.py, real/real_sessions.py), with the same options as in a terminal, inside pixi with the workspace
sourced (install/, or install_lyrical/ with --env lyrical). One job at a time (a simulation test
or a trial: they share the GPU and the ROS domains), and none while a campaign started from the
page runs. The output of each job is kept in ~/.ros/interaction_panel/. It listens on 127.0.0.1
only. doc/CONTROL_PANEL.md explains every button.
"""

import argparse
import glob
import html
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml

if __name__ == "__main__":
    # The Tests tab (test_panel.py) imports this module as control_panel: the same module, with
    # the same jobs (one at a time), not a second copy
    sys.modules.setdefault("control_panel", sys.modules[__name__])

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)
SRC = os.path.dirname(EVAL)                          # the repository (src/interaction_system)
WS = os.path.dirname(os.path.dirname(SRC))           # the colcon workspace (install/)
sys.path.insert(0, os.path.join(EVAL, "real"))
import campaigns  # noqa: E402
import real_sessions  # noqa: E402
import sessions_common  # noqa: E402
import whole_evaluation as whole  # noqa: E402
from experiment_files import condition_dialogues, select_scenarios  # noqa: E402

EXPERIMENTS = os.path.join(EVAL, "experiments")
HARDWARE = os.path.join(EXPERIMENTS, "hardware")
RESULTS = os.path.join(EVAL, "results")
JOB_LOGS = os.path.expanduser("~/.ros/interaction_panel")
CAMPAIGN = "panel_campaign.json"     # in results/<experiment>: the campaign started from the page
ACTIVE_S = 900                       # an experiment that saved a dialogue this recently is running
PROMPT = re.compile(r"Enter: (.+?)(?:   q: (.+?))? > ?$")   # a question of a runner to the operator
CHECK = re.compile(r"^CHECK (\w+): (OK|FAILED|SKIPPED) - (.*)$")
CHECKS_FILE = os.path.join(JOB_LOGS, "checks.json")   # the last result of each check

lock = threading.Lock()
job = {}          # the job under way (or the last one): kind, title, command, process, log, started
args = None


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def tail(path, lines=20):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


def in_workspace(command):
    """A command (a list) inside pixi, with the workspace sourced, from the workspace root."""
    install = "install_lyrical" if args.env == "lyrical" else "install"
    environment = ["-e", "lyrical"] if args.env == "lyrical" else []
    # Run from the workspace root (every caller passes cwd=WS), without --manifest-path: pixi
    # would follow the link to the repository's pixi.toml and make a second environment there
    # (docs/PIXI.md)
    return (["pixi", "run"] + environment
            + ["bash", "-c", f"source {shlex.quote(os.path.join(WS, install, 'setup.bash'))} && "
               f"exec {shlex.join(command)}"])


# ------------------------------------------------------------------ catalogues
_cache = {}


def cached(path, read):
    """read(path), again only when the file changed."""
    key = (path, read.__name__)
    stamp = os.path.getmtime(path)
    if key not in _cache or _cache[key][0] != stamp:
        _cache[key] = (stamp, read(path))
    return _cache[key][1]


def experiment_files():
    """{file stem: path} of the experiment files (those with models and conditions)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(EXPERIMENTS, "*.yaml"))):
        data = cached(path, load) or {}
        if "models" in data and "conditions" in data:
            out[os.path.splitext(os.path.basename(path))[0]] = path
    return out


def hardware_files():
    return {os.path.splitext(os.path.basename(p))[0]: p
            for p in sorted(glob.glob(os.path.join(HARDWARE, "*.yaml")))}


def scenarios_of(path):
    exp = cached(path, load)
    scenarios = cached(os.path.join(os.path.dirname(path), exp["scenarios"]), load)["scenarios"]
    return select_scenarios(scenarios, exp)


def shorten(text, size):
    return text if len(text) <= size else text[:size].rsplit(" ", 1)[0] + " …"


def experiment_info(stem, path):
    """What the page shows of an experiment, and the dialogues it runs (as run_experiment.py
    counts them)."""
    exp = cached(path, load)
    conditions = {k: v or {} for k, v in exp["conditions"].items()}
    scenarios = scenarios_of(path)
    personas = exp.get("personas", [])
    reps = int(exp.get("repetitions", 1))
    pairs = [(m, c) for m in exp["models"] for c in conditions
             if not conditions[c].get("models") or m in conditions[c]["models"]]
    sim = exp.get("simulator", {})
    return {
        "stem": stem, "name": exp["name"], "file": os.path.relpath(path, EVAL),
        "models": exp["models"], "language": exp.get("language", "English"),
        "conditions": {c: (conditions[c].get("models") or exp["models"]) for c in conditions},
        "scenarios": [s["id"] for s in scenarios], "personas": personas, "repetitions": reps,
        "simulator": f"{sim.get('provider', 'ollama')}/{sim.get('model', '')}",
        "simulator_cloud": sim.get("provider", "ollama") in sessions_common.CLOUD_PROVIDERS,
        "cloud_models": [m for m in exp["models"] if sessions_common.is_cloud(m)],
        # A placeholder: <pin> (a cloud model's dated version) or <...> (e.g. the finalists of E0a)
        "unpinned": [m for m in exp["models"] + [sim.get("model", "")] if "<" in m],
        "expected": sum(len(condition_dialogues(scenarios, exp, conditions[c], m))
                        for m, c in pairs) * reps,
        "description": shorten(" ".join(l.lstrip("# ") for l in open(path, encoding="utf-8").read()
                                        .splitlines() if l.startswith("#")), 900),
    }


def lane_models(exp, spec):
    """The system models of a lane of a hardware profile (as run_experiment.apply_hardware)."""
    models, select = list(exp["models"]), spec.get("models", "all")
    if select == "local":
        models = [m for m in models if not sessions_common.is_cloud(m)]
    elif select == "cloud":
        models = [m for m in models if sessions_common.is_cloud(m)]
    elif isinstance(select, list):
        models = [m for m in models if m in select]
    if spec.get("shard"):
        i, n = spec["shard"]
        models = models[i::n]
    return models


def hardware_info(stem, path):
    hardware = cached(path, load)
    return {"stem": stem, "name": hardware.get("name", stem), "file": os.path.relpath(path, EVAL),
            "open_simulator": hardware.get("open_simulator", ""),
            "lanes": {name: {"models": spec.get("models", "all"), "system": spec["system"],
                             "simulator": spec.get("simulator", spec["system"]),
                             "ros_domain_id": spec["ros_domain_id"]}
                      for name, spec in hardware.get("lanes", {}).items()}}


def catalogue():
    return {"experiments": [experiment_info(s, p) for s, p in experiment_files().items()],
            "hardware": [hardware_info(s, p) for s, p in hardware_files().items()],
            "sim_domain": args.sim_domain}


# ------------------------------------------------------------------ campaign
def results_dir(stem):
    return os.path.join(RESULTS, cached(experiment_files()[stem], load)["name"])


def campaign_of(stem):
    """The campaign of an experiment started from the page ({} if none), and whether it runs."""
    path = os.path.join(results_dir(stem), CAMPAIGN)
    try:
        info = json.load(open(path))
    except (OSError, ValueError):
        return {}, False
    try:
        os.killpg(int(info["pgid"]), 0)
        return info, True
    except (OSError, ValueError, KeyError):
        return info, False


WHOLE = "the whole evaluation"        # what running_campaign() names while it runs


def running_campaign():
    """The campaign started from the page that runs: an experiment's, or the whole evaluation."""
    if whole_running():
        return WHOLE
    return experiment_campaign()


def experiment_campaign():
    for stem in experiment_files():
        if campaign_of(stem)[1]:
            return stem
    return ""


def campaign_health(stem, info):
    """The run health of the last campaign of an experiment started from the page, once it ended
    (kept in its panel_campaign.json): its end (the lanes' exit, or paused), the server check of
    its profile, and what its dialogues say (whole_evaluation.dialogue_measures)."""
    if info.get("health") or not info.get("started"):
        return info.get("health")
    lines = tail(info.get("log", ""), 60)
    paused = bool(info.get("paused"))
    failed = any("Lanes that failed" in line or line.startswith("Traceback") for line in lines)
    code = 0 if paused or not failed else 1
    measures = [whole.measure("End", "paused" if paused else "lanes failed: see its output"
                              if failed else "every lane finished", "" if paused else
                              "CHECK" if failed else "OK", "")]
    hardware = hardware_files().get(info.get("hardware") or "")
    if hardware:
        rc, output = scripted(["python3", os.path.join(HERE, "run_parallel.py"),
                               experiment_files()[stem], hardware, "--check-servers"], 240)
        measures.append(whole.measure("GPU server free (the server check after the campaign)",
                                      {0: "free", 3: "in use by other work"}.get(rc, "could not check"),
                                      {0: "OK", 3: "CHECK"}.get(rc, ""), ""))
    faults = any(s.get("faults") for s in scenarios_of(experiment_files()[stem]))
    more, details = whole.dialogue_measures([results_dir(stem)], info["started"], faults)
    info["health"] = whole.summarise(measures + more, details, paused, code)
    try:
        with open(os.path.join(results_dir(stem), CAMPAIGN), "w") as f:
            json.dump(info, f, indent=1)
    except OSError:
        pass
    return info["health"]


def campaign_table():
    """One row per experiment: dialogues done of those expected, successes, the end of the lane
    logs; for the campaign under way, the pace and the estimated end."""
    rows, current = [], running_campaign()
    for stem, path in experiment_files().items():
        info = experiment_info(stem, path)
        out = results_dir(stem)
        files = glob.glob(os.path.join(out, "runs", "*", "*", "*.json"))
        success = {}
        for line in tail(os.path.join(out, "runs.jsonl"), 100000):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            success[(r["model"], r["condition"], r["scenario"], r["persona"], r["rep"])] = r["success"]
        campaign, running = campaign_of(stem)
        if campaign and not running:
            try:
                campaign["health"] = campaign_health(stem, campaign)
            except (OSError, ValueError, KeyError) as exc:
                campaign["health"] = {"verdict": "", "sentence": f"Run health: {exc}",
                                      "measures": [], "details": []}
        done = len(files)
        times = sorted(os.path.getmtime(f) for f in files)
        # A dialogue saved in the last minutes, with no campaign of the page: it runs from a terminal
        active = not running and bool(times) and time.time() - times[-1] < ACTIVE_S
        status = ("running" if running else "active" if active else "done"
                  if info["expected"] and done >= info["expected"] else "partial" if done else "pending")
        row = {"stem": stem, "name": info["name"], "status": status, "done": done,
               "expected": info["expected"], "successes": sum(success.values()),
               "lanes": {os.path.basename(p)[5:-4]: tail(p, 3)
                         for p in sorted(glob.glob(os.path.join(out, "lane_*.log")))},
               "campaign": {k: campaign.get(k) for k in ("hardware", "lanes", "started", "allow_cloud",
                                                         "health")}
               if campaign else {}, "pace_s": None, "eta": None,
               "last": time.strftime("%a %H:%M", time.localtime(times[-1])) if times else ""}
        if (stem == current and campaign) or active:
            # The pace of this campaign (or of the last 20 dialogues of a run outside the page)
            times = [t for t in times if t >= campaign.get("started", 0)] if running else times[-20:]
            if len(times) > 2:
                pace = (times[-1] - times[0]) / (len(times) - 1)
                row["pace_s"] = round(pace, 1)
                left = max(0, info["expected"] - done)
                row["eta"] = time.strftime("%a %H:%M", time.localtime(time.time() + pace * left))
        rows.append(row)
    return {"rows": rows, "running": current, "whole": current == WHOLE}


def scripted(command, timeout):
    """A script of the repository run to its end (inside pixi): (return code, its output)."""
    try:
        result = subprocess.run(in_workspace(command), cwd=WS, capture_output=True, text=True,
                                timeout=timeout, stdin=subprocess.DEVNULL,
                                env={**os.environ, "ROS_DOMAIN_ID": str(args.sim_domain)})
    except subprocess.TimeoutExpired:
        return -1, f"No answer in {timeout} s"
    return result.returncode, (result.stdout + result.stderr).strip()


def parallel_command(body, *flags):
    experiments, hardware = experiment_files(), hardware_files()
    stem, profile = body.get("experiment", ""), body.get("hardware", "")
    if stem not in experiments or profile not in hardware:
        raise ValueError("Unknown experiment or hardware profile")
    command = ["python3", os.path.join(HERE, "run_parallel.py"), experiments[stem], hardware[profile]]
    lanes = [lane for lane in body.get("lanes") or [] if lane in load(hardware[profile]).get("lanes", {})]
    if lanes:
        command += ["--lanes", ",".join(lanes)]
    return command + list(flags)


def dry_run(body):
    """run_parallel.py --dry-run: the dialogues of each lane, and the paid cloud calls."""
    code, output = scripted(parallel_command(body, "--dry-run"), 240)
    cloud = {}
    for name, count in re.findall(r"cloud: (.+?): (\d+) dialogues", output):
        cloud[name] = cloud.get(name, 0) + int(count)
    return {"output": output, "returncode": code,
            "cloud": [{"name": k, "dialogues": v} for k, v in cloud.items()],
            "error": "" if code == 0 else "The dry run failed: see its output"}


def check_servers(body):
    """run_parallel.py --check-servers: whether the servers of each lane are free."""
    code, output = scripted(parallel_command(body, "--check-servers"), 240)
    return {"output": output, "returncode": code, "busy": code == 3,
            "error": "" if code in (0, 3) else "The check failed: see its output"}


def start_campaign(body):
    stem = body.get("experiment", "")
    if stem not in experiment_files():
        return {"error": "Unknown experiment"}
    if running_campaign():
        return {"error": f"A campaign is running ({running_campaign()}): pause it first"}
    if job_running():
        return {"error": "A job is running: stop it first"}
    info = experiment_info(stem, experiment_files()[stem])
    if info["unpinned"]:
        return {"error": f"Write the dated version of these models in {info['file']}: {info['unpinned']}"}
    plan = dry_run(body)
    if plan["error"]:
        return plan
    if plan["cloud"] and not body.get("allow_cloud"):
        return {"error": "This campaign makes paid cloud calls: tick 'allow paid cloud calls' and "
                         "confirm them", "output": plan["output"]}
    check = check_servers(body)
    if check["error"] or check["busy"]:
        return {"error": check["error"] or "The servers are not free (see why below): the campaign "
                "is not started", "output": check["output"]}
    command = parallel_command(body, *(["--allow-cloud"] if plan["cloud"] else []))
    out = results_dir(stem)
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, time.strftime("panel_%Y%m%d_%H%M%S.out"))
    with open(log, "w") as f:
        f.write("$ " + " ".join(command) + "\n\n")
    process = subprocess.Popen(in_workspace(command), cwd=WS, stdout=open(log, "a"),
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               start_new_session=True)
    # Reaped when it ends: a zombie would keep its process group, seen as running
    threading.Thread(target=process.wait, daemon=True).start()
    with open(os.path.join(out, CAMPAIGN), "w") as f:
        json.dump({"pgid": process.pid, "started": time.time(), "experiment": stem,
                   "hardware": body.get("hardware"), "lanes": body.get("lanes") or "all",
                   "allow_cloud": bool(plan["cloud"]), "log": log, "command": command}, f, indent=1)
    return {"error": "", "output": f"Started: {' '.join(command)}\nIts output: {log}"}


def pause_campaign(body):
    stem = body.get("experiment") or running_campaign()
    if stem not in experiment_files():
        return {"error": "No campaign running"}
    info, running = campaign_of(stem)
    if not running:
        return {"error": "No campaign of this experiment is running"}
    # As Ctrl-C: run_parallel.py stops every lane (each stops its system); the dialogue under way
    # is not saved, and is run again on resume
    os.killpg(int(info["pgid"]), signal.SIGINT)
    info["paused"] = time.time()   # its run health then reads the end as a pause, not a failure
    with open(os.path.join(results_dir(stem), CAMPAIGN), "w") as f:
        json.dump(info, f, indent=1)
    return {"error": "", "output": "Pausing: each lane stops its dialogue under way and its system "
                                   "(up to a minute); the next Start resumes where it stopped"}


# ------------------------------------------------------------------ the whole evaluation
_hash = {"value": "", "time": 0.0}


def current_hash():
    """The method's code hash (provenance.py), computed again at most every 10 s."""
    if time.time() - _hash["time"] > 10:
        _hash.update(value=whole.code_hash(), time=time.time())
    return _hash["value"]


def whole_capture(argv, timeout):
    """whole_evaluation.capture for the panel: a script of the evaluation (a dry run, a server
    check) inside pixi with the workspace, from evaluation/."""
    return scripted(["bash", "-c", f"cd {shlex.quote(EVAL)} && exec {shlex.join(argv)}"], timeout)


def whole_running():
    return whole.alive(whole.read_state(args.whole_root).get("runner"))


def whole_state():
    """What the Whole evaluation shows: whole_evaluation.overview, the end of its log and of the
    output of the step under way (or the last one), and what keeps it from starting."""
    root = args.whole_root
    out = whole.overview(root, current_hash())
    step = (out["halt"] or {}).get("step") or out["next"] or ""
    if not step:
        ended = [s for s in out["steps"] if s.get("ended")]
        step = max(ended, key=lambda s: s["ended"])["id"] if ended else ""
    out["log"] = whole.tail(os.path.join(root, "campaign.log"), 30)
    out["step_log"] = {"id": step, "label": whole.label_of(step) if step else "",
                       "lines": whole.tail(os.path.join(root, "logs", f"{step}.log"), 200)}
    out["blocked"] = ("A job is running: stop it first" if job_running() else
                      f"The campaign of {experiment_campaign()} is running: pause it first"
                      if experiment_campaign() else "")
    return out


def whole_ctx(body):
    state = whole.read_state(args.whole_root)
    options = dict(state["options"])
    if "openai_judges" in body:
        options["openai_judges"] = bool(body["openai_judges"])
    return whole.Ctx(args.whole_root, options)


def whole_estimate(body):
    """The estimate of a paid step (its dry run, the authors' cost figures): no call made."""
    step = whole.by_id().get(body.get("step", ""))
    if not step or step["kind"] != "run":
        return {"error": "Unknown step"}
    ctx = whole_ctx(body)
    if not step["paid"](ctx):
        return {"error": "This step makes no paid call" if step["paid"](ctx) is False else
                step["requires"](ctx) or "Not known yet"}
    est = whole.estimate(step, ctx)
    return {"error": est.get("error", ""), "label": whole.label_of(step["id"]), **est}


def whole_allow(body):
    """The author's permission for a step's paid calls, for the estimate the page showed (its
    key): refused if what the step would pay for changed since."""
    step = whole.by_id().get(body.get("step", ""))
    if not step or step["kind"] != "run":
        return {"error": "Unknown step"}
    est = whole.estimate(step, whole_ctx(body))
    if est.get("error"):
        return {"error": est["error"]}
    if est["key"] != body.get("key"):
        return {"error": "What the step would pay for changed since the estimate was shown: look "
                         "at it again"}
    return {"error": whole.allow(args.whole_root, step["id"], est)}


def whole_start(body):
    """Start (or resume) the whole evaluation at a step, detached from the page:
    whole_evaluation.py run --from STEP, in its own process group (closing the page or the panel
    does not stop it). Refused while anything else runs, and, when the method changed since the
    evaluation started, until the page confirms it (--accept-method-change)."""
    root = args.whole_root
    if whole_running():
        return {"error": "The whole evaluation is running"}
    if experiment_campaign():
        return {"error": f"The campaign of {experiment_campaign()} is running: pause it first"}
    if job_running():
        return {"error": "A job is running: stop it first"}
    state = whole.read_state(root)
    ids = whole.order()
    start = body.get("from") or state.get("next") or ids[0]
    if start not in ids:
        return {"error": "Unknown step"}
    method = whole.method_check(root, state, current_hash())
    if method["changed"] and not body.get("accept_method"):
        return {"error": "The method changed since the evaluation started: confirm it first",
                "method": method}
    command = ["python3", os.path.join(HERE, "whole_evaluation.py"), "--root", root, "run",
               "--from", start]
    command += ["--accept-method-change"] if method["changed"] else []
    command += ["--openai-judges"] if body.get("openai_judges") else []
    os.makedirs(root, exist_ok=True)
    out = open(os.path.join(root, "runner.out"), "a")
    out.write(f"\n$ {' '.join(command)}   ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
    out.flush()
    process = subprocess.Popen(in_workspace(command), cwd=WS, stdout=out, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True,
                               env={**os.environ, "PYTHONUNBUFFERED": "1"})
    threading.Thread(target=process.wait, daemon=True).start()   # reaped when it ends
    with whole.locked(root) as st:
        if (st.get("runner") or {}).get("pgid") != process.pid:
            # Seen as running at once; the runner writes the same group when it starts
            st["runner"] = {"pid": process.pid, "pgid": process.pid, "from": start,
                            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    return {"error": "", "output": f"Started at {whole.label_of(start)}: {' '.join(command)}"}


def whole_pause(body):
    runner = whole.read_state(args.whole_root).get("runner")
    if not whole.alive(runner):
        return {"error": "The whole evaluation is not running"}
    # As Ctrl-C to its process group: the script under way stops what it launched (the dialogue
    # under way is not saved); the evaluation resumes at that step
    os.killpg(int(runner["pgid"]), signal.SIGINT)
    return {"error": "", "output": "Pausing: the step under way stops what it launched (up to a "
                                   "few minutes); Start resumes it"}


def whole_done(body):
    """A stop of a person: what it awaited is done (the page then continues)."""
    if whole_running():
        return {"error": "The whole evaluation is running"}
    return {"error": whole.acknowledge(args.whole_root, body.get("step", ""))}


# ------------------------------------------------------------------ jobs
def job_running():
    process = job.get("process")
    return process is not None and process.poll() is None


def start_job(kind, title, command, env=None, stdin=False, meta=None, cleanup=""):
    with lock:
        if job_running():
            return f"Another job is running ({job['title']}): stop it first"
        if running_campaign():
            return "A campaign is running: pause it first (they share the GPU and the ROS domains)"
        finish_job()
        os.makedirs(JOB_LOGS, exist_ok=True)
        log = os.path.join(JOB_LOGS, time.strftime("%Y%m%d_%H%M%S_") + kind + ".log")
        out = open(log, "w")
        out.write("$ " + " ".join(command) + "\n\n")
        out.flush()
        process = subprocess.Popen(
            in_workspace(command), cwd=WS, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            env={**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})})
        job.clear()
        job.update(kind=kind, title=title, command=command, process=process, log=log,
                   started=time.time(), meta=meta or {}, cleanup=cleanup)
    return ""


def finish_job():
    """Once the last job ended: delete its temporary folder (a simulation test), and keep the
    verdict of a check (its last line CHECK <name>: OK | FAILED | SKIPPED - <why>)."""
    if not job or job_running():
        return
    if job.get("cleanup"):
        shutil.rmtree(job["cleanup"], ignore_errors=True)
        job["cleanup"] = ""
    name = job.get("meta", {}).get("check")
    if name and not job.get("checked"):
        job["checked"] = True
        found = [CHECK.match(line) for line in tail(job["log"], 50)]
        found = [m for m in found if m]
        status, detail = (found[-1].group(2), found[-1].group(3)) if found else (
            "FAILED", f"it ended without a verdict (exit status {job['process'].poll()}): see its output")
        results = checks()
        results[name] = {"status": status, "detail": detail,
                         "time": time.strftime("%Y-%m-%d %H:%M"), "log": job["log"]}
        os.makedirs(JOB_LOGS, exist_ok=True)
        with open(CHECKS_FILE, "w") as f:
            json.dump(results, f, indent=1)


def checks():
    try:
        return json.load(open(CHECKS_FILE))
    except (OSError, ValueError):
        return {}


def job_state():
    if not job:
        return {}
    finish_job()
    lines = tail(job["log"], 3000)
    process = job["process"]
    transcript = [{"role": line.split(":", 1)[0].lower(), "text": line.split(":", 1)[1].strip()}
                  for line in lines if line.startswith(("ROBOT: ", "USER: "))]
    return {
        "kind": job["kind"], "title": job["title"], "log": job["log"], "meta": job["meta"],
        "running": process.poll() is None, "returncode": process.poll(),
        "elapsed_s": round(time.time() - job["started"]),
        "prompt": prompt(lines) if process.poll() is None else None,
        "output": lines[-300:], "transcript": transcript,
    }


def prompt(lines):
    """The question the runner waits on (its last line, e.g. 'Enter: start the conversation
    q: quit without running it > '): {enter, quit} labels, or None."""
    match = PROMPT.search(lines[-1]) if lines else None
    return {"enter": match.group(1), "quit": match.group(2) or ""} if match else None


def with_rmw(page):
    """The page with the DDS that the jobs inherit (this panel's RMW_IMPLEMENTATION) in the first
    option of the DDS list."""
    rmw = os.environ.get("RMW_IMPLEMENTATION") or "not set: the distribution's default"
    return page.replace(">as in this terminal<", f">inherited ({html.escape(rmw)})<")


def stop_job():
    process = job.get("process")
    if process is None or process.poll() is not None:
        return "No job running"

    def stop():
        try:
            os.killpg(process.pid, signal.SIGINT)   # as Ctrl+C: the runners stop what they launched
            for _ in range(90):
                if process.poll() is not None:
                    return
                time.sleep(0.5)
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
    threading.Thread(target=stop, daemon=True).start()
    return ""


def answer_job(text):
    process = job.get("process")
    if process is None or process.poll() is not None or process.stdin is None:
        return "No job waiting for an answer"
    process.stdin.write((text + "\n").encode())
    process.stdin.flush()
    return ""


# ------------------------------------------------------------------ simulation test
def sim_test(body):
    experiments, hardware = experiment_files(), hardware_files()
    stem, profile = body.get("experiment", ""), body.get("hardware", "")
    if stem not in experiments or profile not in hardware:
        return "Unknown experiment or hardware profile"
    info = experiment_info(stem, experiments[stem])
    model, condition = body.get("model", ""), body.get("condition", "")
    scenario, persona = body.get("scenario", ""), body.get("persona", "")
    if condition not in info["conditions"] or model not in info["conditions"][condition] \
            or scenario not in info["scenarios"] or persona not in info["personas"]:
        return "Unknown condition, model, scenario or persona of this experiment"
    paid = sessions_common.is_cloud(model) or info["simulator_cloud"]
    if paid and not body.get("allow_cloud"):
        return "This dialogue makes paid cloud calls: confirm them first"
    if any("<" in m for m in (model, info["simulator"])):
        return f"Write the dated version of the model in {info['file']}"
    exp, profile_data = load(experiments[stem]), load(hardware[profile])
    lanes = [name for name, spec in profile_data.get("lanes", {}).items()
             if model in lane_models(exp, spec)]
    if not lanes:
        return f"No lane of {profile} runs {model}"
    out = tempfile.mkdtemp(prefix="interaction_simtest_")
    command = ["python3", os.path.join(HERE, "run_experiment.py"), experiments[stem],
               "--hardware", hardware[profile], "--lane", lanes[0], "--only-model", model,
               "--only-condition", condition, "--only-scenario", scenario, "--only-persona", persona,
               "--only-rep", "1", "--out", out, "--show"] + (["--allow-cloud"] if paid else [])
    error = start_job("sim", f"Simulation test: {info['name']} / {condition} / {model} / {scenario} / "
                      f"{persona} (nothing stored)", command,
                      {"ROS_DOMAIN_ID": str(args.sim_domain)}, cleanup=out)
    if error:
        shutil.rmtree(out, ignore_errors=True)
    return error


# ------------------------------------------------------------------ real sessions
def real_campaign(name):
    """(a campaign of real sessions, its sessions file, the folder of its participants);
    ValueError when there is no such campaign."""
    campaign = campaigns.load_campaign(name)
    return (campaign, sessions_common.load_sessions(campaign["sessions_path"]),
            campaigns.campaign_out(campaign["name"], args.real_out))


def campaigns_state():
    """The campaigns (each with its configuration, state and pre-session check), the
    configurations to choose from, the sessions files, and the participants stored before
    campaigns existed."""
    return {"campaigns": campaigns.campaigns(args.real_out),
            "configs": [{k: c[k] for k in ("name", "state", "used_by", "error")}
                        for c in campaigns.configurations()],
            "sessions_files": campaigns.sessions_files(),
            "legacy": campaigns.legacy_participants(args.real_out), "out": args.real_out}


def campaign_detail(name):
    """One campaign as the Real sessions tab shows it, with what its sessions file gives the
    checks (the enabled conditions, the scenarios, the cloud models)."""
    row = next((c for c in campaigns.campaigns(args.real_out) if c["name"] == name), None)
    if row is None:
        raise ValueError(f"No campaign {name}")
    try:
        sessions = real_campaign(name)[1]
        row["sessions"] = {"conditions": sessions["enabled"],
                           "scenarios": [s["id"] for s in sessions["scenarios"]],
                           "barge_in": bool(sessions.get("barge_in", False)),
                           "cloud_models": [c["model"] for c in sessions["enabled"].values()
                                            if sessions_common.is_cloud(c["model"])], "error": ""}
    except (OSError, ValueError, KeyError) as exc:
        row["sessions"] = {"conditions": {}, "scenarios": [], "cloud_models": [], "error": str(exc)}
    row["describe"] = campaigns.describe(row["values"]) if row.get("values") else ""
    return row


def participant_state(text, name):
    """The plan of a participant of a campaign with the current details of each trial, its card
    and what is stored of it."""
    _, sessions, out = real_campaign(name)
    pid, _ = sessions_common.participant(text)
    plan = sessions_common.read_plan(pid, out)
    if plan is None:
        return {"participant": pid, "campaign": name, "plan": None}
    stored = sessions_common.stored(pid, out)
    trials = []
    for t in plan["trials"]:
        try:
            trial = sessions_common.details(sessions, t)
            error = ""
        except ValueError as exc:
            trial, error = dict(t), str(exc)
        record = {}
        if t["k"] in stored:
            try:
                r = json.load(open(stored[t["k"]]))
                record = {k: r.get(k) for k in ("status", "success", "total_turns", "duration_s",
                                                "started", "model", "configuration")}
            except (OSError, ValueError):
                record = {"status": "unreadable"}
        trials.append({**trial, "error": error, "cloud": sessions_common.is_cloud(trial["model"]),
                       "provider": sessions_common.split_model(trial["model"])[0],
                       "unpinned": "<pin>" in trial["model"], "stored": record,
                       "card_footer": sessions.get("card_footer") or [],
                       "detour_label": sessions.get("detour_label", "Also:")})
    same = sessions_common.assignment(plan) == sessions_common.assignment(
        sessions_common.make_plan(sessions, plan["number"]))
    return {"participant": pid, "campaign": name, "plan": {k: plan.get(k) for k in (
        "order", "latin_square_row", "created", "sessions_file")}, "trials": trials,
        "note": "" if same else "The sessions file now gives this participant another plan "
                                "(real_sessions.py plan --replace, only before the first trial)"}


def participants_state(name):
    rows = []
    _, _, out = real_campaign(name)
    for pid in sessions_common.participants(out):
        plan = sessions_common.read_plan(pid, out)
        stored = sessions_common.stored(pid, out)
        last = max((os.path.getmtime(p) for p in stored.values()), default=None)
        rows.append({"participant": pid, "trials": len(plan["trials"]), "stored": len(stored),
                     "order": plan["order"],
                     "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else ""})
    return rows


def robot_env(body):
    """The robot's network of the page: ROS_DOMAIN_ID and, if chosen, RMW_IMPLEMENTATION."""
    env = {"ROS_DOMAIN_ID": str(int(body.get("domain") or 0))}
    rmw = body.get("rmw") or ""
    if rmw:
        if rmw not in ("rmw_fastrtps_cpp", "rmw_cyclonedds_cpp"):
            raise ValueError("Unknown RMW_IMPLEMENTATION")
        env["RMW_IMPLEMENTATION"] = rmw
    return env


def runnable(name):
    """The campaign, or ValueError with why it refuses its trials and checks (integrity)."""
    campaign = campaigns.load_campaign(name)
    why = campaigns.integrity(campaign, args.real_out)
    if why:
        raise ValueError(f"Campaign {campaign['name']} refuses its trials and checks: {why}")
    return campaign


def real_command(campaign, *rest):
    return ["python3", os.path.join(EVAL, "real", "real_sessions.py"), "--out", args.real_out,
            "--campaign", campaign] + list(rest)


def real_trial(body):
    name = body.get("campaign", "")
    try:
        campaign = runnable(name)
        state = participant_state(body.get("participant", ""), name)
        env = robot_env(body)
    except ValueError as exc:
        return str(exc)
    if not state["plan"]:
        return "This participant has no plan: create it first"
    k = int(body.get("k") or 0)
    if not 1 <= k <= len(state["trials"]):
        return "Unknown trial"
    trial = state["trials"][k - 1]
    if trial["error"]:
        return trial["error"]
    if trial["unpinned"]:
        return (f"Write the dated version of the cloud model in real/{campaign['sessions_file']} "
                "(it says <pin>)")
    if trial["cloud"] and not body.get("allow_cloud"):
        return f"This trial sends the participant's transcribed text to {trial['provider']}: confirm it first"
    mode = body.get("mode", "try")
    if mode not in ("try", "record"):
        return "Unknown mode"
    if mode == "record" and not body.get("unchecked_ok") and \
            not campaigns.presession_state(campaign, args.real_out)["passed_today"]:
        return (f"The pre-session check of {name} has not passed today with its configuration: "
                "run it first, or confirm that the trial runs without it")
    command = real_command(name, "trial", state["participant"], str(k))
    command += ["--try"] if mode == "try" else []
    command += ["--allow-cloud"] if trial["cloud"] else []
    command += ["--barge-in", "on" if body.get("barge_in") else "off"]
    label = "a test, nothing stored" if mode == "try" else "COUNTS: stored with the sessions"
    return start_job("real", f"Real session ({name}): {state['participant']} trial {k}, "
                     f"{trial['scenario']}, {trial['condition']} ({label})", command, env,
                     stdin=True, meta={"participant": state["participant"], "k": k,
                                       "campaign": name})


def new_campaign(body):
    try:
        campaign = campaigns.create_campaign(body.get("name", ""), body.get("configuration", ""),
                                             body.get("sessions_file") or "sessions_es.yaml",
                                             args.real_out)
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    return {"error": "", "campaign": campaign["name"],
            "output": f"Campaign {campaign['name']} created; configuration "
                      f"{campaign['configuration']} is frozen"}


def switch_configuration(body):
    try:
        campaign = campaigns.switch(body.get("campaign", ""), body.get("configuration", ""),
                                    args.real_out)
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    return {"error": "", "output": f"Campaign {campaign['name']} now uses "
                                   f"{campaign['configuration']}"}


def presession(body):
    """real_sessions.py --campaign C check presession: speak, listen and the model in sequence,
    with the campaign's configuration (its pass is kept in <out>/<C>/presession.json)."""
    name = body.get("campaign", "")
    try:
        runnable(name)
        env = robot_env(body)
    except ValueError as exc:
        return str(exc)
    command = real_command(name, "check", "presession")
    command += ["--allow-cloud"] if body.get("allow_cloud") else []
    return start_job("check", f"Pre-session check of {name}: speak, listen, model (nothing stored)",
                     command, env, stdin=True, meta={"check": f"real:{name}:presession",
                                                     "campaign": name})


# ------------------------------------------------------------------ configurations
def save_configuration(body):
    values = {k: body.get(k) for k in campaigns.CONFIG_KEYS}
    try:
        config = campaigns.save_config(body.get("name", ""), values, body.get("previous", ""),
                                       body.get("note", ""))
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    return {"error": "", "name": config["name"], "output": f"Saved {config['name']}: "
                                                           f"{campaigns.describe(config['values'])}"}


def duplicate_configuration(body):
    try:
        config = campaigns.duplicate(body.get("name", ""), body.get("new_name", ""))
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    return {"error": "", "name": config["name"],
            "output": f"Draft {config['name']} created from {body.get('name')}: edit it and save"}


def configuration_speak(body):
    """Check: speak with the values of the editor (saved or not): real_sessions.py check speak
    --voice ... (only the voice starts; nothing stored)."""
    try:
        values = campaigns.validate_config({k: body.get(k) for k in campaigns.CONFIG_KEYS})
        env = robot_env(body)
    except ValueError as exc:
        return str(exc)
    command = ["python3", os.path.join(EVAL, "real", "real_sessions.py"), "check", "speak",
               "--voice", values["voice"]]
    command += [a for k in sessions_common.PIPER_SETTINGS if values[k] is not None
                for a in (f"--{k.replace('_', '-')}", f"{values[k]:g}")]
    if (body.get("phrase") or "").strip():
        command += ["--phrase", body["phrase"].strip()]
    piper = {"voice": values["voice"], **{k: values[k] for k in sessions_common.PIPER_SETTINGS
                                          if values[k] is not None}}
    return start_job("check", f"Check: speak with {real_sessions.piper_text(piper)} (configuration "
                     f"{body.get('name') or 'being edited'}; nothing stored)", command, env,
                     stdin=True, meta={"check": "config:speak"})


def configurations_state():
    """The configurations (each with its state: draft, or frozen and used by which campaigns)
    and what the editor offers (voices, settings, presets, models of speech recognition)."""
    return {"configs": campaigns.configurations(),
            "voices": [{"value": v, "language": lang, "gender": gender, "label": label}
                       for v, lang, gender, label in campaigns.VOICES],
            "settings": [{"key": k, "label": label, "hint": hint,
                          "low": sessions_common.PIPER_SETTINGS[k][0],
                          "high": sessions_common.PIPER_SETTINGS[k][1]}
                         for k, (label, hint) in campaigns.SETTINGS.items()],
            "presets": [{"label": label, "values": values} for label, values in campaigns.PRESETS],
            "whisper_models": list(campaigns.WHISPER_MODELS),
            "questions": tests().QUESTIONS,
            "check_phrase": (load(sessions_common.SESSIONS) or {}).get("check_phrase", "")}


def tests():
    """The Tests tab: scripts/test_panel.py (imported here: it imports this module)."""
    import test_panel
    return test_panel


# ------------------------------------------------------------------ checks
REAL_CHECKS = real_sessions.CHECKS
SIM_CHECKS = ("start", "scripted", "model")


def real_check(body):
    """real_sessions.py --campaign C check <what> (nothing stored), with the campaign's
    configuration and the robot's network of the page."""
    what = body.get("what", "")
    if what not in REAL_CHECKS:
        return "Unknown check"
    name = body.get("campaign", "")
    try:
        runnable(name)
        _, sessions, _ = real_campaign(name)
        env = robot_env(body)
    except ValueError as exc:
        return str(exc)
    command = real_command(name, "check", what)
    cloud = [c["model"] for c in sessions["enabled"].values() if sessions_common.is_cloud(c["model"])]
    if what == "rehearsal":
        scenario, condition = body.get("scenario", ""), body.get("condition", "")
        if condition not in sessions["enabled"] or scenario not in [s["id"] for s in sessions["scenarios"]]:
            return "Unknown scenario or condition"
        command += ["--scenario", scenario, "--condition", condition]
        cloud = [m for m in cloud if m == sessions["enabled"][condition]["model"]]
    if what in ("llm", "system", "rehearsal", "presession") and cloud and body.get("allow_cloud"):
        command.append("--allow-cloud")
    if what in ("system", "perception", "rehearsal"):
        command += ["--barge-in", "on" if body.get("barge_in") else "off"]
    return start_job("check", f"Check before the session ({name}): {what} (nothing stored)",
                     command, env, stdin=True, meta={"check": f"real:{name}:{what}",
                                                     "campaign": name})


def sim_check(body):
    """sim_checks.py start | scripted | model (nothing stored), on the domain of the tests."""
    what = body.get("what", "")
    if what not in SIM_CHECKS:
        return "Unknown check"
    command = ["python3", os.path.join(HERE, "sim_checks.py"), what]
    if what == "model":
        experiments, hardware = experiment_files(), hardware_files()
        stem, profile, model = body.get("experiment", ""), body.get("hardware", ""), body.get("model", "")
        if stem not in experiments or profile not in hardware:
            return "Unknown experiment or hardware profile"
        info = experiment_info(stem, experiments[stem])
        if model not in info["models"]:
            return "Unknown model of this experiment"
        if sessions_common.is_cloud(model) and not body.get("allow_cloud"):
            return "A cloud model: confirm the paid calls first"
        lanes = [n for n, spec in load(hardware[profile]).get("lanes", {}).items()
                 if model in lane_models(load(experiments[stem]), spec)]
        if not lanes:
            return f"No lane of {profile} runs {model}"
        condition = body.get("condition", "")
        command += [experiments[stem], "--model", model, "--hardware", hardware[profile],
                    "--lane", lanes[0], "--language", "es" if info["language"] == "Spanish" else "en"]
        command += ["--condition", condition] if condition in info["conditions"] else []
        command += ["--allow-cloud"] if sessions_common.is_cloud(model) else []
    return start_job("check", f"Check without hardware: {what} (nothing stored)", command,
                     {"ROS_DOMAIN_ID": str(args.sim_domain)}, meta={"check": "sim:" + what})


# ------------------------------------------------------------------ actions
def act(path, body):
    """A POST of the page: {'error': ...} and, for some, more."""
    if path == "/api/campaign/dryrun":
        return dry_run(body)
    if path == "/api/campaign/check":
        return check_servers(body)
    if path == "/api/campaign/start":
        return start_campaign(body)
    if path == "/api/campaign/pause":
        return pause_campaign(body)
    if path == "/api/whole/start":
        return whole_start(body)
    if path == "/api/whole/pause":
        return whole_pause(body)
    if path == "/api/whole/estimate":
        return whole_estimate(body)
    if path == "/api/whole/allow":
        return whole_allow(body)
    if path == "/api/whole/revoke":
        return {"error": whole.revoke(args.whole_root, body.get("step", ""))}
    if path == "/api/whole/done":
        return whole_done(body)
    if path == "/api/sim/run":
        return {"error": sim_test(body)}
    if path == "/api/real/plan":
        try:
            _, sessions, out = real_campaign(body.get("campaign", ""))
            plan, what = sessions_common.write_plan(sessions, body.get("participant", ""), out)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"error": "", "output": f"{plan['participant']}: {what}", "participant": plan["participant"]}
    if path == "/api/real/run":
        return {"error": real_trial(body)}
    if path == "/api/real/campaign/new":
        return new_campaign(body)
    if path == "/api/real/campaign/switch":
        return switch_configuration(body)
    if path == "/api/real/presession":
        return {"error": presession(body)}
    if path == "/api/config/save":
        return save_configuration(body)
    if path == "/api/config/duplicate":
        return duplicate_configuration(body)
    if path == "/api/config/speak":
        return {"error": configuration_speak(body)}
    if path == "/api/test/run":
        return {"error": tests().run(body)}
    if path == "/api/test/check":
        return {"error": tests().check(body)}
    if path == "/api/check/real":
        return {"error": real_check(body)}
    if path == "/api/check/sim":
        return {"error": sim_check(body)}
    if path == "/api/job/stop":
        return {"error": stop_job()}
    if path == "/api/job/enter":
        return {"error": answer_job("")}
    if path == "/api/job/quit":
        return {"error": answer_job("q")}
    return {"error": "Unknown action"}


# ------------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self.send(200, with_rmw(page()), "text/html")
            if url.path == "/api/state":
                return self.send(200, json.dumps({"campaign": running_campaign(), "job": job_state()}))
            if url.path == "/api/catalogue":
                return self.send(200, json.dumps(catalogue()))
            if url.path == "/api/campaign":
                return self.send(200, json.dumps(campaign_table()))
            if url.path == "/api/whole":
                return self.send(200, json.dumps(whole_state()))
            if url.path == "/api/checks":
                finish_job()
                return self.send(200, json.dumps(checks()))
            if url.path == "/api/real/campaigns":
                return self.send(200, json.dumps(campaigns_state()))
            if url.path == "/api/real/campaign":
                return self.send(200, json.dumps(campaign_detail(query.get("name", ""))))
            if url.path == "/api/real/participants":
                return self.send(200, json.dumps(participants_state(query.get("campaign", ""))))
            if url.path == "/api/real/participant":
                return self.send(200, json.dumps(participant_state(query.get("id", ""),
                                                                   query.get("campaign", ""))))
            if url.path == "/api/configs":
                return self.send(200, json.dumps(configurations_state()))
            if url.path == "/api/test/catalogue":
                return self.send(200, json.dumps(tests().catalogue()))
            if url.path == "/api/test/models":
                return self.send(200, json.dumps(tests().models()))
        except (ValueError, OSError, KeyError) as exc:
            return self.send(200, json.dumps({"error": html.escape(f"{type(exc).__name__}: {exc}")}))
        self.send(404, "{}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            result = act(urlparse(self.path).path, json.loads(self.rfile.read(length) or b"{}"))
        except Exception as exc:  # noqa: B902 - shown on the page
            result = {"error": f"{type(exc).__name__}: {exc}"}
        result["error"] = html.escape(result.get("error") or "")
        self.send(200, json.dumps(result))


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Interaction evaluation</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d232b;--muted:#5d6875;--line:#d9dee5;--accent:#2563eb;--ok:#15803d;--bad:#b91c1c;--warn:#b45309}
@media (prefers-color-scheme: dark){:root{--bg:#14181d;--card:#1d232b;--ink:#e6eaef;--muted:#9aa5b1;--line:#333c47;--accent:#60a5fa;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 system-ui,sans-serif}
header{padding:16px 24px;border-bottom:1px solid var(--line)}h1{font-size:22px;margin:0}
main{max-width:1100px;margin:0 auto;padding:20px 24px 40px;display:flex;flex-direction:column;gap:20px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 24px}
h2{font-size:21px;margin:0 0 14px}h3{font-size:18px;margin:16px 0 8px}label{display:block;margin:10px 0 4px;color:var(--muted);font-size:15px}
select,input[type=text],input[type=number]{width:100%;padding:9px 10px;font-size:16px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
label.inline{display:inline-flex;gap:6px;align-items:center;color:var(--ink);margin-right:14px}
button{margin:12px 8px 0 0;padding:10px 16px;font-size:16px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink);cursor:pointer}
button.small{margin:2px 4px 2px 0;padding:5px 10px;font-size:14px}
nav.tabs{display:flex;gap:6px;max-width:1100px;margin:16px auto 0;padding:0 24px;flex-wrap:wrap}
nav.tabs button{margin:0;font-size:18px;padding:12px 22px;border-radius:10px 10px 0 0;border-bottom:none;background:var(--card);color:var(--muted)}
nav.tabs button.active{color:var(--accent);border-color:var(--accent);border-width:2px;font-weight:600}nav.tabs button:disabled{opacity:.35}
.page{display:none}.page.active{display:block}
button.test{background:var(--bg);border-color:var(--muted)}button.run{background:var(--accent);border-color:var(--accent);color:#fff}
button.counts{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:600}button.danger{background:var(--card);color:var(--bad);border-color:var(--bad)}
button:disabled{opacity:.4;cursor:default}.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:5px}.legend i{width:12px;height:12px;border-radius:3px;display:inline-block;border:1px solid var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.on{color:var(--ok);border-color:var(--ok)}.fail{color:var(--bad);border-color:var(--bad)}.off{color:var(--muted)}.warn{color:var(--warn);border-color:var(--warn)}.cloud{color:var(--warn);border-color:var(--warn)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;max-height:420px;overflow:auto;font-size:13px;white-space:pre-wrap}
.small{color:var(--muted);font-size:15px}.why{color:var(--warn);margin-top:4px}#msg{color:var(--bad);min-height:1em;white-space:pre-wrap}
.box{border-radius:8px;padding:10px 14px;margin:10px 0;white-space:pre-wrap;font-size:15px;background:#2563eb14;border-left:4px solid var(--accent)}
.box b{display:block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin-bottom:3px}
table{width:100%;border-collapse:collapse;font-size:15px}td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:500}.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;min-width:80px}
.bar>i{display:block;height:100%;background:var(--accent)}tr.running{background:#2563eb10;font-weight:600}tr.pending{color:var(--muted)}
.st{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line)}.st.done{color:var(--ok);border-color:var(--ok)}
.st.running,.st.active{color:var(--accent);border-color:var(--accent)}.st.partial{color:var(--warn);border-color:var(--warn)}
.row{display:flex;gap:8px;flex-wrap:wrap}.row>*{flex:1 1 180px}.lanelog{font-family:monospace;font-size:12px;color:var(--muted);white-space:pre-wrap}
#chat{display:flex;flex-direction:column;gap:6px;max-height:460px;overflow:auto;padding:4px}
.said{max-width:80%;padding:8px 12px;border-radius:12px;font-size:16px}.said.robot{background:#2563eb18;align-self:flex-start}
.said.user{background:#15803d18;align-self:flex-end}.said b{display:block;font-size:11px;color:var(--muted);text-transform:uppercase}
#banner{font-size:22px;font-weight:600;padding:10px;border-radius:8px;margin:8px 0;display:none}
#banner.ready{display:block;background:#2563eb22;color:var(--accent)}#banner.talk{display:block;background:#15803d22;color:var(--ok)}#banner.end{display:block;background:#b4530922;color:var(--warn)}
#card{position:fixed;inset:0;z-index:50;display:none;background:#fffdf7;color:#111;cursor:pointer;overflow:auto;padding:5vh 7vw}
#card.on{display:block}#card h1{font-size:min(6vw,8vh);margin:0 0 3vh}#card p{font-size:min(3.8vw,4.8vh);line-height:1.35;margin:0 0 1.6vh}
#card .detour{margin-top:4vh;padding:2vh 2vw;border:4px solid #b45309;border-radius:16px;background:#fff4e0}
#card .detour b{display:block;font-size:min(3.4vw,4vh);color:#b45309}#card .foot p{font-size:min(3vw,3.6vh);color:#444}
#card .esc{position:fixed;right:2vw;bottom:2vh;font-size:14px;color:#888}
.scen{display:flex;gap:8px;align-items:flex-start;padding:6px 8px;border-radius:8px;cursor:pointer}
.scen:hover{background:#2563eb10}.scen.sel{background:#2563eb22}.scen code{font-size:14px}
#t_list{max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:6px}
.card .sec{margin-top:8px}.card i{display:block;font-style:normal;font-weight:600;font-size:13px;color:var(--muted)}
.modes{display:flex;gap:6px;margin:0 0 12px}.modes button{margin:0}.modes button.active{border-color:var(--accent);color:var(--accent);border-width:2px;font-weight:600}
.steps{display:flex;align-items:stretch;gap:6px;flex-wrap:wrap;margin:6px 0 4px}
.step{flex:1 1 140px;border:2px solid var(--line);border-radius:10px;padding:8px 10px;cursor:pointer;background:var(--card)}
.step .n{font-size:13px;color:var(--muted)}.step .t{font-weight:700;font-size:17px}.step .d{font-size:13px;color:var(--muted);line-height:1.3;margin-bottom:4px}
.step.skip{opacity:.4}.step.run{border-color:var(--accent)}.step.start{border-color:var(--accent);background:#2563eb14}
.sub{font-size:13px;padding:2px 4px;border-radius:5px;margin-top:2px;line-height:1.35}.sub:hover{background:#2563eb18}
.sub.skip{opacity:.5}.sub.start{background:#2563eb2a;font-weight:600}.sub .ic{display:inline-block;width:1.3em}
.mh{border-left:4px solid var(--warn);background:#b4530914;padding:8px 12px;border-radius:8px;margin:8px 0;white-space:pre-wrap;font-size:15px}
.mh b{display:block}table.health td{font-size:14px;padding:4px 6px}
#note{color:var(--ok);white-space:pre-wrap}.bad{color:var(--bad);font-weight:600}input:disabled,select:disabled{opacity:.6}
</style></head><body>
<header><h1>Interaction evaluation</h1></header>
<nav class="tabs"><button data-tab="campaign" onclick="tab('campaign')">Campaign</button><button data-tab="sim" onclick="tab('sim')">Simulation test</button><button data-tab="real" onclick="tab('real')">Real sessions</button><button data-tab="configs" onclick="tab('configs')">Configurations</button><button data-tab="tests" onclick="tab('tests')">Tests</button></nav>
<main>
<div class="legend">Buttons:
<span data-tabs="campaign sim real configs tests"><i style="background:var(--bg)"></i>test: nothing is stored</span>
<span data-tabs="campaign"><i style="background:var(--accent);border-color:var(--accent)"></i>starts or resumes the campaign (or the whole evaluation)</span>
<span data-tabs="sim real tests"><i style="background:var(--accent);border-color:var(--accent)"></i>acts on the job under way (Tests: starts the test)</span>
<span data-tabs="configs"><i style="background:var(--accent);border-color:var(--accent)"></i>saves a draft</span>
<span data-tabs="real"><i style="background:var(--ok);border-color:var(--ok)"></i>counts: stored with the sessions</span>
<span data-tabs="campaign"><i style="border-color:var(--bad)"></i>pauses the campaign (or the whole evaluation)</span>
<span data-tabs="sim real configs tests"><i style="border-color:var(--bad)"></i>stops the job</span></div>
<div id="msg"></div><div id="note"></div>

<section class="page" id="page-campaign"><h2>Campaign</h2>
<div class="modes"><button data-mode="whole" onclick="cmode('whole')">Whole evaluation</button><button data-mode="one" onclick="cmode('one')">One experiment</button></div>
<div class="cmode" id="mode-whole">
<div class="small">The whole simulated evaluation, step after step in the order of REPRODUCING.md (scripts/whole_evaluation.py), detached from the page: it stops where a person must act and says what it awaits, never makes a paid cloud call without your permission for that step, warns when the method changed, and gives the run health of every step. Its state and log: <span id="wh_root"></span>.</div>
<div id="wh_status"></div>
<div id="wh_method"></div>
<label>The steps (click one, or one of its parts, to start there)</label><div class="steps" id="wh_steps"></div>
<div id="wh_help"></div>
<label class="inline" title="run_e0b.py --openai: the OpenAI judges join the ladder of E0b (preflight and label). Only with OpenAI credits."><input type="checkbox" id="wh_openai"> E0b: also the OpenAI judges (only with OpenAI credits)</label>
<div><button class="run" id="b_wstart" onclick="wStart(wPickId,false)">&#9654; Start / resume</button>
<button class="danger" id="b_wpause" onclick="if(confirm('Pause the whole evaluation? The step under way stops what it launched (the dialogue under way is not saved and runs again on resume).'))post('/api/whole/pause').then(wLoad)">&#10074;&#10074; Pause</button></div>
<div class="small why" id="w_whole"></div>
<pre id="wh_est" style="display:none"></pre>
<details open><summary class="small" id="wh_steplabel">Output of the step under way</summary><pre id="wh_steplog"></pre></details>
<details><summary class="small">Log of the whole evaluation</summary><pre id="wh_log"></pre></details></div>
<div class="cmode" id="mode-one">
<div class="row"><div><label>Experiment</label><select id="c_exp" onchange="describeExp()"></select></div>
<div><label>Hardware profile</label><select id="c_hw" onchange="fillLanes()"></select></div></div>
<label>Lanes (each runs at the same time, with its own ROS domain)</label><div id="c_lanes"></div>
<div id="c_desc"></div>
<button class="test" id="b_dry" onclick="dryRun()">&#9776; What it would run (dry run)</button>
<button class="test" id="b_check" onclick="checkServers()">&#10003; Check the servers are free</button>
<div><label class="inline" title="The campaign makes paid calls to cloud models (system or simulated user): the dry run counts them. Start passes --allow-cloud only when this is ticked and the counts are confirmed."><input type="checkbox" id="c_cloud"> allow paid cloud calls</label></div>
<button class="run" id="b_start" onclick="startCampaign()">&#9654; Start / resume</button>
<button class="danger" id="b_pause" onclick="if(confirm('Pause the campaign? Each lane stops its dialogue under way (it is run again on resume) and its system.'))post('/api/campaign/pause',{experiment:running})">&#10074;&#10074; Pause</button>
<div class="small why" id="w_camp"></div>
<pre id="c_out" style="display:none"></pre></div>
<h3>Experiments</h3><div class="small">Dialogues done of those expected (results/&lt;experiment&gt;/runs), how many ended in success, and the end of the log of each lane. The campaign under way is highlighted, with its pace and an estimate of its end.</div>
<div id="c_table"></div></section>

<section class="page" id="page-sim"><h2>Simulation test</h2>
<div class="small">One dialogue through the whole system with the simulated user of the experiment: nothing is stored (a temporary folder, deleted at the end). It runs on the lane of the hardware profile that runs the model, with ROS_DOMAIN_ID <span id="s_domain"></span>, and checks first that the servers are free.</div>
<div class="row"><div><label>Experiment</label><select id="s_exp" onchange="fillSim()"></select></div>
<div><label>Hardware profile</label><select id="s_hw"></select></div></div>
<div class="row"><div><label>Condition</label><select id="s_cond" onchange="fillModels()"></select></div>
<div><label>System model</label><select id="s_model"></select></div></div>
<div class="row"><div><label>Scenario</label><select id="s_scen"></select></div>
<div><label>Persona of the simulated user</label><select id="s_pers"></select></div></div>
<div id="s_desc" class="small"></div>
<button class="test" id="b_sim" onclick="simTest()">&#9654; Run one dialogue (nothing stored)</button><div class="small why" id="w_sim"></div>
<h3>Quick checks without hardware</h3><div class="small">Before running experiments: the system with the simulated speech (speech:=none) on ROS_DOMAIN_ID <span class="s_dom"></span>. Nothing is stored.</div>
<table id="s_checks"></table></section>

<section class="page" id="page-real"><h2>Real sessions</h2>
<div class="small">The sessions with real participants, in campaigns: a campaign (a pilot is one) has a sessions file (the study design: conditions, scenarios, cards) and one configuration (how the robot sounds: Configurations), and keeps its participants apart. Each participant has the conversations of their plan (in E4, four, all with the method), each in a different scenario, in the order of their plan. See doc/REAL_SESSIONS.md for the procedure, and have the consent signed before anything.</div>
<h3>Campaign</h3>
<div class="row"><div><label>Campaign of real sessions</label><select id="r_camp" onchange="selectCampaign()"></select></div>
<div style="flex:0 0 auto;align-self:flex-end"><button class="test" onclick="$('r_new').style.display=$('r_new').style.display=='none'?'block':'none'">&#43; New campaign</button></div></div>
<div id="r_new" style="display:none" class="box"><b>New campaign</b>Its configuration is frozen from now on (it can still be switched until the first stored trial). Its participants: the folder of the sessions / &lt;name&gt;.
<div class="row"><div><label>Name (lowercase, digits, - and _; e.g. pilot)</label><input type="text" id="rn_name" placeholder="pilot"></div>
<div><label>Sessions file (real/)</label><select id="rn_sess"></select></div><div><label>Configuration</label><select id="rn_conf"></select></div></div>
<button class="run" onclick="newCampaign()">&#10003; Create the campaign</button></div>
<div id="r_campinfo"></div><div class="small" id="r_legacy"></div>
<div id="r_body">
<h3>The robot's network</h3><div class="small">Set it once: the checks and the trials use it.</div><div class="row"><div><label>ROS_DOMAIN_ID</label><input type="number" id="r_domain" value="0"></div>
<div><label>DDS (RMW_IMPLEMENTATION)</label><select id="r_rmw"><option value="">as in this terminal</option><option>rmw_fastrtps_cpp</option><option>rmw_cyclonedds_cpp</option></select></div></div>
<div><label class="inline" title="Future work: the environment has no camera driver nor the perception's dependencies (MediaPipe, OpenCV, cv_bridge), so barge-in cannot run yet (doc/REAL_SESSIONS.md#barge-in)"><input type="checkbox" id="r_barge" disabled> barge-in by lip activity (camera): future work, off</label></div>
<h3>Pre-session check</h3><div class="small">On every day of sessions, before the first participant: with the campaign's configuration, in sequence, the loudspeaker (the voice says the check phrase; you confirm you heard it), the microphone (one transcription; you confirm the words) and the model of each condition. Nothing is stored but the date of its last pass; the trial that counts asks for confirmation when it has not passed that day.</div>
<button class="test ck" onclick="runPresession()">&#9654; Run the pre-session check</button><div id="r_pre"></div>
<h3>Before the session: the other checks</h3><div class="box" style="background:#15803d10;border-color:var(--ok)"><b>In this order</b>With the robot's network above and the campaign's configuration. Nothing is stored. The result of each stays here, with its time.</div>
<table id="r_checks"></table>
<h3>Participant</h3>
<div class="row"><div><label>Participant (P01, P02...)</label><input type="text" id="r_pid" placeholder="P01"></div>
<div style="flex:0 0 auto;align-self:flex-end"><button class="test" onclick="createPlan()">&#9776; Create / open the plan</button></div></div>
<div id="r_plan"></div>
<div class="small why" id="w_real"></div>
<h3>Participants of the campaign</h3><div id="r_list"></div></div></section>

<section class="page" id="page-configs"><h2>Configurations</h2>
<div class="small">How the robot sounds and the hardware of its speech in the real sessions (real/configs/&lt;name&gt;.yaml): the Piper voice, its speed, expressiveness and rhythm, the filler delay, the microphone and the speech-recognition model. What the participants hear is a condition of the study, the same for all: a configuration that no campaign uses is a <b>draft</b>, edited and tried freely; once a campaign uses it, it is <b>frozen</b> and never edited (Duplicate and edit makes a new draft with everything copied). The simulated experiments are not configured here: their files are frozen.</div>
<div id="g_list"></div>
<div id="g_editor" style="display:none"><h3 id="g_title"></h3><div class="why" id="g_frozen"></div>
<div class="row"><div><label>Name (a draft can be renamed)</label><input type="text" id="g_name"></div><div><label>Note (what it is for)</label><input type="text" id="g_note"></div></div>
<div class="row"><div><label>Voice of (filters the list below)</label><select id="g_gender" onchange="fillVoice('g_',false);gVoiceChanged()"><option value="">a man or a woman</option><option value="woman">a woman</option><option value="man">a man</option></select></div>
<div><label>Voice (Piper)</label><select id="g_voice" onchange="gVoiceChanged()"></select></div></div>
<div class="row"><div><label>Voice configuration (fills the three fields below)</label><select id="g_preset" onchange="usePreset('g_')"></select></div></div>
<div class="row" id="g_settings"></div>
<div class="row"><div><label>Filler after (s): "Mmm…" when the answer takes longer (empty: the scenario's, else the system's; 0: none)</label><input type="number" id="g_filler_after_s" min="0" step="0.1" placeholder="default"></div>
<div><label>Microphone (input_device: a sounddevice name; default: the system's sound server)</label><input type="text" id="g_input_device"></div>
<div><label>Speech recognition (whisper_model)</label><input type="text" id="g_whisper_model" list="g_whispers"><datalist id="g_whispers"></datalist></div></div>
<div class="row"><div><label>Phrase of Check: speak (filled by the choice beside it; you can edit it)</label><input type="text" id="g_phrase" oninput="$('g_phrasepick').value=''"></div>
<div><label>What the robot says</label><select id="g_phrasepick" onchange="pickPhrase('g_')"><option value="q">a question with options, to hear its intonation (in the voice's language)</option><option value="check">the loudspeaker test greeting of the real sessions (check_phrase)</option><option value="">my own text: write it in the box</option></select></div></div>
<div class="small">Check: speak says the phrase with the values above, saved or not (only the voice starts; the robot's network of Real sessions). Check: listen with a configuration is in Tests.</div>
<button class="test ck" onclick="configSpeak()">&#9654; Check: speak</button><button class="run" id="g_save" onclick="saveConfig()">&#10003; Save the draft</button>
<button class="test" onclick="dupConfig(gEditing.name)">&#10697; Duplicate and edit</button><button class="test" onclick="$('g_editor').style.display='none';gEditing=null">Close</button>
<div class="small" id="g_result"></div></div></section>
<!--TESTS-->
<section id="running"><h2>Running now <span id="jobpill" class="pill off">none</span></h2>
<div class="small">What you launched from this page (a simulation test, a check, a trial with a participant or a test): the conversation, its output, and the buttons to answer the runner (the blue one when it waits for you) or stop it. The campaign of an experiment is not shown here: see Campaign.</div>
<div id="jobtitle" class="small"></div><div id="banner"></div>
<button class="run" id="enter" onclick="post('/api/job/enter')" disabled>&#9654; Enter</button>
<button class="danger" id="quit" onclick="post('/api/job/quit')" disabled>q</button>
<button class="danger" id="b_stop" onclick="if(confirm('Stop the job? A conversation under way is cancelled and nothing of it is stored.'))post('/api/job/stop')">&#9632; Stop</button>
<button class="run" id="b_jobcard" style="display:none" onclick="showJobCard()" title="The task card of the trial under way, full screen for the participant. Click or press Esc to leave.">&#9635; Full-screen card</button>
<h3>Conversation</h3><div id="chat"></div>
<details><summary class="small">Output of the job</summary><pre id="out"></pre></details></section>
</main>
<div id="card" onclick="card(null)"></div>
<script>
let cat={experiments:[],hardware:[]};let running='';let jobBusy=false;let current=null;const cloudOk={};
const $=id=>document.getElementById(id), val=id=>$(id).value, chk=id=>$(id).checked;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function post(path,body){$('msg').textContent='';$('note').textContent='';const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
 const j=await r.json();const d=document.createElement('div');d.innerHTML=j.error||'';$('msg').textContent=d.textContent;if(!j.error&&j.output&&path.match(/campaign\/(new|switch)|config\/(save|duplicate)/))$('note').textContent=j.output;refresh();return j;}
function tab(name){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id=='page-'+name));
 document.querySelectorAll('nav.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.tab==name));
 $('running').style.display=name=='campaign'?'none':'block';
 document.querySelectorAll('.legend span[data-tabs]').forEach(s=>s.style.display=s.dataset.tabs.split(' ').includes(name)?'':'none');
 try{localStorage.setItem('interaction_panel_tab',name);}catch(e){}}
const opt=(v,t)=>`<option value="${esc(v)}">${esc(t??v)}</option>`;
const exp=id=>cat.experiments.find(e=>e.stem==val(id))||{conditions:{},scenarios:[],personas:[],models:[]};
const hw=id=>cat.hardware.find(h=>h.stem==val(id))||{lanes:{}};
// ---- campaign
function fillLanes(){const h=hw('c_hw');$('c_lanes').innerHTML=Object.entries(h.lanes).map(([n,l])=>
 `<label class="inline"><input type="checkbox" class="lane" value="${esc(n)}" checked> ${esc(n)} <span class="small">(models ${esc(JSON.stringify(l.models))}, system on ${esc(l.system)}, simulator on ${esc(l.simulator)}, ROS_DOMAIN_ID ${l.ros_domain_id})</span></label>`).join('<br>');}
function lanes(){return [...document.querySelectorAll('.lane:checked')].map(x=>x.value);}
function describeExp(){const e=exp('c_exp');if(!e.name)return;
 const cloud=[...e.cloud_models.map(m=>m+' (system)'),...(e.simulator_cloud?[e.simulator+' (simulated user)']:[])];
 $('c_desc').innerHTML=`<div class="box"><b>${esc(e.name)} · ${esc(e.file)}</b>${esc(e.description)}\n\nModels: ${esc(e.models.join(', '))}\nConditions: ${esc(Object.keys(e.conditions).join(', '))}\n`+
 `${e.scenarios.length} scenarios × ${e.personas.length} personas × ${e.repetitions} repetitions: ${e.expected} dialogues in all · simulated user ${esc(e.simulator)} · ${esc(e.language)}`+
 (cloud.length?`\n<span class="pill cloud">paid cloud calls: ${esc(cloud.join(', '))}</span>`:'')+(e.unpinned.length?`\n<span class="pill warn">write the dated version of ${esc(e.unpinned.join(', '))}</span>`:'')+`</div>`;}
function campaignBody(extra){return Object.assign({experiment:val('c_exp'),hardware:val('c_hw'),lanes:lanes()},extra||{});}
function showOut(t){const o=$('c_out');o.style.display='block';o.textContent=t;}
async function dryRun(){showOut('Running the dry run...');const j=await post('/api/campaign/dryrun',campaignBody());
 showOut((j.output||'')+(j.cloud&&j.cloud.length?'\n\nPAID CLOUD CALLS:\n'+j.cloud.map(c=>`  ${c.name}: ${c.dialogues} dialogues`).join('\n'):'\n\nNo paid cloud calls.'));return j;}
async function checkServers(){showOut('Checking the servers of each lane...');const j=await post('/api/campaign/check',campaignBody());
 showOut((j.output||'')+(j.error?'':j.busy?'\n\nIN USE by other work: the campaign would not start.':'\n\nThe servers are free.'));}
async function startCampaign(){const d=await dryRun();if(d.error)return;let allow=false;
 if(d.cloud.length){if(!chk('c_cloud')){$('msg').textContent='This campaign makes paid cloud calls: tick "allow paid cloud calls" first.';return;}
  if(!confirm('PAID CLOUD CALLS, charged to the account of the API keys:\n\n'+d.cloud.map(c=>`${c.name}: ${c.dialogues} dialogues (up to the turn limit each)`).join('\n')+'\n\nStart the campaign with them?'))return;allow=true;}
 else if(!confirm(`Start (or resume) ${val('c_exp')} on ${val('c_hw')}, lanes ${lanes().join(', ')||'all'}? The servers are checked first.`))return;
 showOut('Checking the servers and starting...');const j=await post('/api/campaign/start',campaignBody({allow_cloud:allow}));showOut(j.output||'');table();}
let activeOutside=[];
async function table(){const c=await (await fetch('/api/campaign')).json();if(c.error)return;
 activeOutside=c.whole?[]:c.rows.filter(r=>r.status=='active').map(r=>r.name);controls();
 $('c_table').innerHTML='<table><tr><th>Experiment</th><th>Status</th><th>Progress</th><th>Dialogues</th><th>Successes</th><th>Lanes (end of their logs)</th></tr>'+c.rows.map(r=>{
  const pct=r.expected?Math.min(100,100*r.done/r.expected):0;
  const lanes=Object.entries(r.lanes).map(([n,l])=>`<b>${esc(n)}</b>\n${esc(l.join('\n'))}`).join('\n');
  return `<tr class="${r.status}"><td>${esc(r.name)}${r.campaign.hardware?`<div class="small">${esc(r.campaign.hardware)}, lanes ${esc(r.campaign.lanes)}</div>`:''}</td>`+
  `<td><span class="st ${r.status}" title="${r.status=='active'?(c.whole?'A dialogue was saved in the last 15 min: the whole evaluation runs it':'A dialogue was saved in the last 15 min, and no campaign of this page runs: it runs from a terminal'):''}">${r.status=='active'?(c.whole?'running (whole evaluation)':'running (terminal)'):r.status}</span>${r.last?`<div class="small">last ${esc(r.last)}</div>`:''}${healthPill(r.campaign.health)}</td>`+
  `<td><div class="bar"><i style="width:${pct}%"></i></div>${r.pace_s?`<div class="small">${r.pace_s} s per dialogue · ends about <b>${esc(r.eta)}</b></div>`:''}</td><td>${r.done}/${r.expected}</td><td>${r.done?r.successes:''}</td><td><div class="lanelog">${lanes}</div></td></tr>`;}).join('')+'</table>';}
// ---- the whole evaluation (scripts/whole_evaluation.py): steps, stops, permissions, run health
let W=null,wPickId='';
function cmode(m){document.querySelectorAll('.cmode').forEach(d=>d.style.display=d.id=='mode-'+m?'':'none');
 document.querySelectorAll('.modes button').forEach(b=>b.classList.toggle('active',b.dataset.mode==m));
 try{localStorage.setItem('interaction_panel_cmode',m);}catch(e){}if(m=='whole')wLoad();}
const cmodeIs=m=>$('mode-'+m).style.display!='none';
function healthPill(h){if(!h)return '';return ` <span class="pill ${h.verdict=='OK'?'on':h.verdict=='CHECK'?'fail':'off'}" title="${esc(h.sentence)}">health ${h.verdict||'-'}</span>`;}
function healthTable(h){if(!h)return '';return `<h3>Run health</h3><table class="health">`+h.measures.map(m=>`<tr><td>${m.verdict?`<span class="pill ${m.verdict=='OK'?'on':'fail'}">${m.verdict}</span>`:'<span class="small">no verdict</span>'}</td><td>${esc(m.what)}</td><td>${esc(m.value)}${m.note?`<div class="small">${esc(m.note)}</div>`:''}</td></tr>`).join('')+
 `</table>${h.details&&h.details.length?`<pre>${esc(h.details.join('\n'))}</pre>`:''}<div><b>${esc(h.sentence)}</b></div>`;}
const wLabel=s=>`${s.n}.${s.k} ${W.titles[s.n][0]} ${s.label}`;
const wStep=id=>W.steps.find(s=>s.id==id);
async function wLoad(){const r=await fetch('/api/whole');const j=await r.json();if(j.error){$('wh_status').innerHTML=`<div class="mh">${esc(j.error)}</div>`;return;}
 W=j;if(!wPickId||!wStep(wPickId))wPickId=W.next||(W.steps.find(s=>s.status!='done')||W.steps[0]).id;wDraw();}
function wPick(id){wPickId=id;wDraw();}
function wIcon(s){if(W.running&&s.id==W.next)return '&#9679;';if(W.halt&&W.halt.step==s.id)return W.halt.kind=='paused'?'&#10074;&#10074;':'&#9632;';
 return {done:'&#10003;',failed:'&#10007;',paused:'&#10074;&#10074;',running:'&#9675;'}[s.status]||'&#183;';}
function wPills(s){let p='';if(s.kind=='person')p+=' <span class="pill warn" title="A person must act here">person</span>';
 if(s.paid===true)p+=s.permission?' <span class="pill on" title="Its paid calls are allowed">$ allowed</span>':' <span class="pill cloud" title="Paid cloud calls: it asks for your permission">$</span>';
 if(s.paid===null&&s.kind=='run')p+=` <span class="pill off" title="${s.id.endsWith('judge')?'Paid only if the judge chosen in E0b is a cloud model':'Paid or not: known once its experiment file exists'}">$?</span>`;
 return p+healthPill(s.health);}
function wDraw(){const ids=W.steps.map(s=>s.id),k=ids.indexOf(wPickId);$('wh_root').textContent=W.root;
 const groups={};W.steps.forEach((s,i)=>{(groups[s.n]=groups[s.n]||[]).push([s,i]);});
 $('wh_steps').innerHTML=Object.entries(groups).map(([n,list])=>{const idx=list.map(x=>x[1]),cls=idx[idx.length-1]<k?'skip':idx.includes(k)?'start':'run';
  return `<div class="step ${cls}" onclick="wPick('${list[0][0].id}')"><div class="n">${n}</div><div class="t">${esc(W.titles[n][0])}</div><div class="d">${esc(W.titles[n][1])}</div>`+
  list.map(([s,i])=>`<div class="sub ${i<k?'skip':i==k?'start':''}" onclick="event.stopPropagation();wPick('${s.id}')" title="${esc(s.what)}"><span class="ic">${wIcon(s)}</span>${n}.${s.k} ${esc(s.label)}${wPills(s)}</div>`).join('')+'</div>';}).join('');
 wStatus();wMethod();wHelp();wButtons();
 const o=$('wh_steplog'),end=o.scrollTop+o.clientHeight>=o.scrollHeight-20;o.textContent=W.step_log.lines.join('\n');if(end)o.scrollTop=o.scrollHeight;
 $('wh_steplabel').textContent='Output of '+(W.step_log.label?W.step_log.label+' (logs/'+W.step_log.id+'.log)':'the step under way');$('wh_log').textContent=W.log.join('\n');
 if(W.options&&W.options.openai_judges!==undefined&&!wDraw.seen){$('wh_openai').checked=!!W.options.openai_judges;wDraw.seen=true;}}
function wStatus(){const h=W.halt,s=h?wStep(h.step):(W.next?wStep(W.next):null);let t='',b='';
 if(W.running)t=`<b>Running</b>${s?wLabel(s):''} (started ${esc((W.runner||{}).started||'')}). Closing the page or the panel does not stop it.`;
 else if(W.status=='running')t=`<b>Stopped unexpectedly</b>The runner is gone (a crash or a reboot?) at ${s?esc(wLabel(s)):'?'}: resume it.`,b=`<button class="run" onclick="wStart(W.next,true)">&#9654; Resume</button>`;
 else if(!h&&W.status=='done')t='<b>Finished</b>Every step ran. Choose a step to run it again.';
 else if(!h)t='<b>Not started</b>Choose the step to start at (a new evaluation starts at 1.1), then Start.';
 else{const head={person:'Waiting for a person',permission:'Waiting for your permission of its paid calls',awaits:'Waiting',busy:'Waiting for free servers',failed:'Failed',paused:'Paused',method:'Stopped: the method changed',continue:'Ready to continue'}[h.kind]||h.kind;
  t=`<b>${esc(head)} · ${esc(wLabel(s))} · ${esc(h.time)}</b>${esc(h.text)}`;
  b=h.kind=='person'?`<button class="run" onclick="wDone('${s.id}')">&#10003; Done: continue</button>`:
   h.kind=='permission'?`<button class="run" onclick="wAllow('${s.id}',true)">$ Show the estimate and allow&hellip;</button>`:
   `<button class="run" onclick="wStart('${s.id}',true)">&#9654; ${h.kind=='paused'?'Resume':h.kind=='failed'?'Run it again':h.kind=='continue'?'Continue':'Continue (check again)'}</button>`;}
 $('wh_status').innerHTML=`<div class="box">${t}${b?'<div>'+b+'</div>':''}</div>`;}
function wMethod(){const m=W.method,k=W.steps.findIndex(s=>s.id==wPickId);let t='';
 if(m.changed)t+=`<div class="mh"><b>The method changed since the evaluation started</b>Code hash of provenance.py: recorded ${esc((m.recorded||'').slice(0,12))}, now ${esc(m.current.slice(0,12))}. `+
  (m.affected.length?`Steps run with the recorded method, whose results come from another method than the steps to come:\n${m.affected.map(a=>'  '+a.label).join('\n')}\n`:'No step has run with the recorded method yet. ')+
  `If the change is the one a stop expected (the corrections of phase A, apply, the judge's instructions, the pilot), accept it when you start; otherwise restore the method, or run the affected steps again (in new folders).</div>`;
 const f=m.folders.filter(x=>W.steps.findIndex(s=>s.id==x.id)>=k);
 if(f.length)t+=`<div class="mh"><b>Results folders begun with other code</b>${f.map(x=>esc(x.label+': '+x.why)).join('\n')}</div>`;
 $('wh_method').innerHTML=t;}
function wHelp(){const s=wStep(wPickId);if(!s){$('wh_help').innerHTML='';return;}const ids=W.steps.map(x=>x.id),k=ids.indexOf(s.id);
 let t=`<div class="box"><b>${esc(wLabel(s))} · ${s.kind=='person'?'a person must act':'runs'}${s.status?' · '+esc(s.status)+(s.ended?' '+esc(s.ended):''):''}</b>${esc(s.what)}`;
 if(s.kind=='person')t+=`\n\nWhat it awaits: ${esc(s.awaits)}`;
 else t+='\n\nCommands (from src/interaction_system/evaluation, as REPRODUCING.md):\n'+(s.commands.length&&s.commands.some(c=>c)?s.commands.map(c=>'  '+esc(c)).join('\n'):'  (none yet: the folders with dialogues, when it runs)');
 if(s.reminder)t+='\n\n'+esc(s.reminder);
 if(s.paid===true)t+='\n\n'+(s.permission?`<span class="pill on">paid calls allowed ${esc(s.permission.time)}</span> ${esc(s.permission.shown.join('; '))}`:'<span class="pill cloud">paid cloud calls</span> The evaluation stops here until you allow them.')+
  `\n<button class="test small" onclick="wAllow('${s.id}',false)">$ Show the estimate${s.permission?'':' and allow'}&hellip;</button>`+(s.permission?`<button class="danger small" onclick="post('/api/whole/revoke',{step:'${s.id}'}).then(wLoad)">Revoke</button>`:'');
 if(s.paid===null&&s.kind=='run')t+=s.id.endsWith('judge')?'\n\nPaid only if the judge chosen in E0b is a cloud model: known once E0b has reported.':'\n\nWhether it pays is known once its experiment file exists.';
 t+=`\n\n<i>Start here: the evaluation runs ${esc(wLabel(s))} and every step after it, stopping where a person must act or a paid step is not allowed.${k?' The steps before it are skipped.':''}</i></div>`;
 $('wh_help').innerHTML=t+healthTable(s.health);}
function wButtons(){const other=running&&running!=='the whole evaluation';enable(['b_wstart'],!W.running&&!jobBusy&&!other);enable(['b_wpause'],W.running);
 $('w_whole').textContent=W.running?'':W.blocked||'';}
async function wAllow(id,thenStart){const o=$('wh_est');o.style.display='block';o.textContent='Running its dry run (no call is made)...';
 const e=await post('/api/whole/estimate',{step:id,openai_judges:chk('wh_openai')});if(e.error){o.textContent=(e.output||'')+'\n'+(e.error||'');return;}
 const c=e.costs||{},costs=[...(c.script||[]),...(c.table||[])];
 o.textContent=e.lines.join('\n')+(costs.length?'\n\nThe authors\' estimate (costs_estimate.py, COSTES.md §1):\n'+costs.join('\n'):'\n\n(No cost estimate of the authors in this clone: the counts only.)')+'\n\nIts dry run:\n'+(e.output||'');
 if(!confirm(`PAID CLOUD CALLS of ${e.label}, charged to the account of the API keys:\n\n${e.lines.join('\n')}`+(costs.length?`\n\nEstimate:\n${costs.join('\n')}`:'')+`\n\nAllow them? (Only this step; asked again if what it would pay for changes.)`))return;
 const j=await post('/api/whole/allow',{step:id,key:e.key,openai_judges:chk('wh_openai')});await wLoad();if(!j.error&&thenStart)wStart(id,true);}
async function wDone(id){const s=wStep(id);if(!confirm(`${wLabel(s)}\n\n${s.awaits}\n\nIs it done? The evaluation continues after it.`))return;
 const j=await post('/api/whole/done',{step:id});if(!j.error)await wStart(id,true);}
async function wStart(from,isContinue){await wLoad();const s=wStep(from)||wStep(W.next)||W.steps[0],ids=W.steps.map(x=>x.id);let accept=false;
 if(W.method.changed){if(!confirm(`THE METHOD CHANGED since the evaluation started (code hash ${(W.method.recorded||'').slice(0,12)} -> ${W.method.current.slice(0,12)}).\n\n`+
  (W.method.affected.length?`Steps run with the recorded method:\n${W.method.affected.map(a=>'  '+a.label).join('\n')}\n\n`:'')+`Accept the current code as the method of the evaluation (recorded) and go on?`))return;accept=true;}
 else if(!isContinue){const redo=s.id!==W.next&&W.steps.slice(ids.indexOf(s.id)).some(x=>x.status);
  if(!confirm(redo?`Start at ${wLabel(s)}? It and every later step run again (their scripts skip what is saved), and the stops of a person from it are asked again.`:
   `Start (or resume) the whole evaluation at ${wLabel(s)}? It runs detached from the page, step after step, and stops where a person must act or a paid step is not allowed.`))return;}
 const j=await post('/api/whole/start',{from:s.id,accept_method:accept,openai_judges:chk('wh_openai')});if(j.output)$('note').textContent=j.output;setTimeout(wLoad,1500);}
// ---- simulation test
function fillSim(){const e=exp('s_exp');$('s_cond').innerHTML=Object.keys(e.conditions).map(c=>opt(c)).join('');
 $('s_scen').innerHTML=e.scenarios.map(s=>opt(s)).join('');$('s_pers').innerHTML=e.personas.map(p=>opt(p)).join('');fillModels();}
function fillModels(){const e=exp('s_exp');$('s_model').innerHTML=(e.conditions[val('s_cond')]||[]).map(m=>opt(m)).join('');
 $('s_desc').textContent=`Simulated user: ${e.simulator}`+(e.simulator_cloud?' (cloud: paid)':'');}
function simTest(){const e=exp('s_exp'),m=val('s_model');const paid=e.cloud_models.includes(m)||e.simulator_cloud;
 if(paid&&!confirm('This dialogue makes PAID cloud calls ('+[e.cloud_models.includes(m)?m+' as the system':'',e.simulator_cloud?e.simulator+' as the simulated user':''].filter(x=>x).join(', ')+'), charged to the account of the API keys. Go on?'))return;
 post('/api/sim/run',{experiment:val('s_exp'),hardware:val('s_hw'),condition:val('s_cond'),model:m,scenario:val('s_scen'),persona:val('s_pers'),allow_cloud:paid});}
// ---- a voice and its settings (Configurations and Tests): voiceCat from /api/configs or /api/test/catalogue
const voiceCat={voices:[],settings:[],presets:[],questions:{},check_phrase:''};
function setVoiceCat(j){['voices','settings','presets','questions','check_phrase'].forEach(k=>{if(j[k]!==undefined)voiceCat[k]=j[k];});}
function fillVoice(p,withDefault){const keep=val(p+'voice'),gen=val(p+'gender'),g=(lang,label)=>`<optgroup label="${label}">`+voiceCat.voices.filter(v=>v.language==lang&&(!gen||v.gender==gen)).map(v=>opt(v.value,`${v.label} (${v.gender})`)).join('')+'</optgroup>';
 $(p+'voice').innerHTML=(withDefault&&!gen?opt('','default: simple_hri\'s voice of the language (es_ES-sharvard-medium speaker 0, a man; en_US-lessac-medium, a woman)'):'')+g('es','Spanish')+g('en','English');
 if(keep&&![...$(p+'voice').options].some(o=>o.value==keep))$(p+'voice').insertAdjacentHTML('beforeend',opt(keep,keep+' (not in the list)'));if(keep)$(p+'voice').value=keep;}
function settingsHtml(p){return voiceCat.settings.map(s=>`<div><label>${esc(s.label)}</label><input type="number" id="${p}${esc(s.key)}" min="${s.low}" max="${s.high}" step="0.05" placeholder="the voice's" oninput="$('${p}preset').value='custom'"><div class="small">${esc(s.hint)}; from ${s.low} to ${s.high}</div></div>`).join('');}
function presetOptions(){return voiceCat.presets.map((x,i)=>opt(i,x.label+(Object.keys(x.values).length?' ('+Object.entries(x.values).map(([k,v])=>k+' '+v).join(', ')+')':''))).join('')+opt('custom','(custom: as written)');}
function usePreset(p){const x=voiceCat.presets[val(p+'preset')];if(!x)return;voiceCat.settings.forEach(s=>$(p+s.key).value=x.values[s.key]??'');}
function presetOf(v){const i=voiceCat.presets.findIndex(x=>voiceCat.settings.every(s=>(x.values[s.key]??null)==(v[s.key]??null)));return i<0?'custom':String(i);}
const settingsOf=p=>Object.fromEntries(voiceCat.settings.map(s=>[s.key,val(p+s.key)]));
const voiceLangOf=(p,fallback)=>(voiceCat.voices.find(v=>v.value==val(p+'voice'))||{}).language||(val(p+'voice')?val(p+'voice').slice(0,2):'')||fallback||'es';
function pickPhrase(p,fallback){const x=val(p+'phrasepick');if(x=='q')$(p+'phrase').value=voiceCat.questions[voiceLangOf(p,fallback)]||'';else if(x=='check')$(p+'phrase').value=voiceCat.check_phrase||'';}
// ---- real sessions: the campaign (real/campaigns/), its configuration, its participants
let camps={campaigns:[],configs:[],sessions_files:[],legacy:[]},camp=null;
const campName=()=>val('r_camp');
async function loadCampaigns(keep){camps=await (await fetch('/api/real/campaigns')).json();let saved='';try{saved=localStorage.getItem('interaction_panel_campaign')||'';}catch(e){}
 const want=keep||campName()||saved;
 $('r_camp').innerHTML=camps.campaigns.length?camps.campaigns.map(c=>opt(c.name,`${c.name} (${c.error?'unreadable':c.configuration+', '+c.stored+' trials stored'})`)).join(''):opt('','(no campaign yet: create one with + New campaign)');
 if(want&&camps.campaigns.some(c=>c.name==want))$('r_camp').value=want;
 $('rn_conf').innerHTML=camps.configs.filter(c=>!c.error).map(c=>opt(c.name,`${c.name} (${c.state}${c.used_by.length?': '+c.used_by.join(', '):''})`)).join('');
 $('rn_sess').innerHTML=camps.sessions_files.map(f=>opt(f)).join('');
 $('r_legacy').textContent=camps.legacy.length?`Participants stored before campaigns existed (in ${camps.out}, in no campaign): ${camps.legacy.join(', ')}. They are kept as they are, never moved nor deleted; their trials do not run again.`:'';
 await selectCampaign(true);}
async function selectCampaign(keepPlan){const n=campName();try{localStorage.setItem('interaction_panel_campaign',n);}catch(e){}
 camp=n?await (await fetch('/api/real/campaign?name='+encodeURIComponent(n))).json():null;if(camp&&camp.error&&!camp.name)camp=null;
 drawCampaign();drawChecks();participants();if(!keepPlan||(current&&current.campaign!==n)){current=null;$('r_plan').innerHTML='';}}
function drawCampaign(){$('r_body').style.display=camp?'block':'none';if(!camp){$('r_campinfo').innerHTML='';return;}
 const c=camp,frozen=`<span class="pill on" title="Used by a campaign: never edited (Configurations: Duplicate and edit)">frozen</span>`;
 const others=camps.configs.filter(x=>!x.error&&x.name!=c.configuration);
 $('r_campinfo').innerHTML=`<div class="box"><b>Campaign ${esc(c.name)} · sessions file real/${esc(c.sessions_file)} · created ${esc(c.created)}</b>`+
  `Configuration <b style="display:inline;font-size:inherit;text-transform:none;letter-spacing:0">${esc(c.configuration)}</b> ${frozen} <span class="small">(hash ${esc(c.configuration_hash)}, associated ${esc(c.associated)})</span>\n${esc(c.describe)}\n`+
  `${c.participants} participants, ${c.stored} trials stored · participants in ${esc(camps.out)}/${esc(c.name)}</div>`+
  (c.integrity?`<div class="bad">REFUSES ITS TRIALS AND CHECKS: ${esc(c.integrity)}</div>`:'')+(c.sessions&&c.sessions.error?`<div class="why">${esc(c.sessions.error)}</div>`:'')+
  (c.can_switch?`<div class="row" style="align-items:flex-end"><div><label>Switch to another configuration (only while no trial is stored; the hash is taken again)</label><select id="r_switch">${others.map(x=>opt(x.name,`${x.name} (${x.state})`)).join('')}</select></div>`+
   `<div style="flex:0 0 auto"><button class="test" onclick="switchConfig()"${others.length?'':' disabled'}>&#8644; Switch configuration</button></div></div>`:
   `<div class="small">Its configuration can no longer change: ${c.stored} trials are stored with it.</div>`);
 const pre=c.presession||{},last=pre.last,pass=pre.last_pass;
 $('r_pre').innerHTML=(last?['speak','listen','llm'].map(k=>{const r=(last.results||{})[k];return `<div><span class="pill ${!r?'off':r.status=='OK'?'on':'fail'}">${{speak:'loudspeaker',listen:'microphone',llm:'model'}[k]}: ${esc(r?r.status:'not run')}</span> <span class="small">${esc(r?r.detail:'')}</span></div>`;}).join('')+`<div class="small">Last run ${esc(last.time)} (configuration ${esc(last.configuration)})</div>`:'<div class="small">Never run for this campaign.</div>')+
  `<div><span class="pill ${pre.passed_today?'on':'warn'}">${pre.passed_today?'passed today':'not passed today'}</span> <span class="small">${pass?'last pass '+esc(pass.time)+(pass.configuration_hash!=c.configuration_hash?' (with another configuration)':''):'never passed'}</span></div>`;}
async function newCampaign(){const name=val('rn_name').trim(),conf=val('rn_conf');if(!name||!conf){$('msg').textContent='Write the name and choose the configuration';return;}
 if(!confirm(`Create campaign ${name} with ${val('rn_sess')} and configuration ${conf}? ${conf} is frozen from now on (it can be switched until the first stored trial).`))return;
 const j=await post('/api/real/campaign/new',{name,sessions_file:val('rn_sess'),configuration:conf});if(j.campaign){$('r_new').style.display='none';$('rn_name').value='';await loadCampaigns(j.campaign);loadConfigs();}}
async function switchConfig(){const to=val('r_switch');if(!to||!confirm(`Switch campaign ${campName()} to ${to}? ${to} is frozen from now on; ${camp.configuration} becomes a draft again if no other campaign uses it.`))return;
 await post('/api/real/campaign/switch',{campaign:campName(),configuration:to});await loadCampaigns(campName());loadConfigs();}
function runPresession(){if(!camp)return;let allow=false;const cloud=(camp.sessions||{}).cloud_models||[];
 if(cloud.length)allow=confirm(`Include the cloud model (${cloud.join(', ')})? It makes one paid call of one token. Cancel to check the local models only.`);
 post('/api/real/presession',{campaign:campName(),domain:val('r_domain'),rmw:val('r_rmw'),allow_cloud:allow});}
async function createPlan(){const j=await post('/api/real/plan',{campaign:campName(),participant:val('r_pid')});if(j.participant){$('r_pid').value=j.participant;openPlan(j.participant);}}
async function openPlan(pid){$('r_pid').value=pid;current=await (await fetch(`/api/real/participant?campaign=${encodeURIComponent(campName())}&id=${encodeURIComponent(pid)}`)).json();drawPlan();participants();}
function drawPlan(){const p=current;if(!p||p.error){$('r_plan').innerHTML=p&&p.error?`<div class="why">${p.error}</div>`:'';return;}
 if(!p.plan){$('r_plan').innerHTML=`<div class="small">${esc(p.participant)} has no plan yet in ${esc(p.campaign)}: Create.</div>`;return;}
 $('r_plan').innerHTML=`<div class="box"><b>${esc(p.campaign)} / ${esc(p.participant)} · order ${esc(p.plan.order.join(' → '))} (row ${p.plan.latin_square_row} of the Latin square) · created ${esc(p.plan.created)}</b>`+
 `Run the trials in this order. Before each: Try (a test) if needed, show the card, then Run the trial.</div>`+(p.note?`<div class="why">${esc(p.note)}</div>`:'')+
 '<table><tr><th>#</th><th>Scenario</th><th>Condition</th><th>Stored</th><th></th></tr>'+p.trials.map(t=>
 `<tr><td>${t.k}</td><td>${esc(t.title)}<div class="small">${esc(t.scenario)} (${esc(t.strategy)})</div></td>`+
 `<td>${esc(t.condition)}<div class="small">${esc(t.control)} · ${esc(t.model)} ${t.cloud?'<span class="pill cloud">cloud</span>':''}</div>${t.error?`<div class="why">${esc(t.error)}</div>`:''}${t.unpinned?'<div class="why">write the dated version of this model in the sessions file</div>':''}</td>`+
 `<td>${t.stored.status?`<span class="pill on">${esc(t.stored.status)}${t.stored.success?', success':''}</span><div class="small">${t.stored.started?esc(t.stored.started)+', ':''}${t.stored.total_turns} turns${t.stored.configuration?', '+esc(t.stored.configuration):''}</div>`:'<span class="small">no</span>'}</td>`+
 `<td><button class="small run" onclick="card(${t.k})" title="The task card, full screen for the participant (Esc or click to leave)">&#9635; Card</button>`+
 `<button class="small test ${t.unpinned||t.error?'" disabled':'rt"'} onclick="realRun(${t.k},'try')" title="real_sessions.py trial --try: the whole trial, with the campaign's configuration, nothing stored">&#9654; Try</button>`+
 `<button class="small counts ${t.unpinned||t.error?'" disabled':'rt"'} onclick="realRun(${t.k},'record')" title="The trial counts: stored with the campaign">&#9679; Run the trial</button></td></tr>`).join('')+'</table>';controls();}
function realRun(k,mode){const t=current.trials[k-1];let unchecked=false;
 if(t.cloud&&!cloudOk[current.participant]){if(!confirm(`This condition uses ${t.model}: what the participant says, transcribed to text on this computer (never the audio), goes to ${t.provider}, as the consent form states. Confirm for the session of ${current.participant}?`))return;cloudOk[current.participant]=true;}
 if(mode=='record'&&camp&&!(camp.presession||{}).passed_today){if(!confirm(`The pre-session check of ${camp.name} has NOT passed today with its configuration (loudspeaker, microphone, model). Run the trial that counts anyway?`))return;unchecked=true;}
 if(mode=='record'&&!confirm(`Trial ${k} of ${current.participant} COUNTS: it is stored with campaign ${current.campaign}${t.stored.status?' (it replaces the one stored)':''}. Go on?`))return;
 post('/api/real/run',{campaign:current.campaign,participant:current.participant,k,mode,allow_cloud:!!cloudOk[current.participant],unchecked_ok:unchecked,domain:val('r_domain'),rmw:val('r_rmw'),barge_in:false});}
async function participants(){if(!campName()){$('r_list').innerHTML='';return;}const l=await (await fetch('/api/real/participants?campaign='+encodeURIComponent(campName()))).json();
 $('r_list').innerHTML=l.length?'<table><tr><th>Participant</th><th>Order</th><th>Trials stored</th><th>Last</th></tr>'+l.map(p=>
 `<tr><td><a href="#" onclick="openPlan('${esc(p.participant)}');return false">${esc(p.participant)}</a></td><td>${esc(p.order.join(' → '))}</td><td>${p.stored}/${p.trials}</td><td>${esc(p.last)}</td></tr>`).join('')+'</table>':'<div class="small">None yet.</div>';}
// The task card, full screen: large Spanish text for the participant
function card(k){const c=$('card');if(k===null){c.classList.remove('on');try{if(document.fullscreenElement)document.exitFullscreen();}catch(e){}return;}
 const t=current&&current.trials[k-1];if(!t)return;
 c.innerHTML=`<h1>${esc(t.title)}</h1>`+t.card.map(l=>`<p>${esc(l)}</p>`).join('')+`<div class="detour"><b>${esc(t.detour_label)}</b><p>${esc(t.detour.text)}</p></div>`+
 `<div class="foot" style="margin-top:4vh">${(t.card_footer||[]).map(l=>`<p>${esc(l)}</p>`).join('')}</div><div class="esc">Press Esc or click to leave</div>`;
 c.classList.add('on');try{document.documentElement.requestFullscreen();}catch(e){}}
async function showJobCard(){const m=(lastJob||{}).meta||{};if(!m.participant)return;
 if(m.campaign&&m.campaign!==campName()){$('r_camp').value=m.campaign;await selectCampaign();}
 if(!current||current.participant!==m.participant)await openPlan(m.participant);card(m.k);}
document.addEventListener('keydown',e=>{if(e.key==='Escape')card(null);});
// ---- checks (real_sessions.py --campaign C check <what>; sim_checks.py <what>): nothing stored
const RCHECKS=[['speak','1. Speak','The voice of the campaign\'s configuration says a fixed Spanish phrase through the speaker of the robot or computer; you confirm that you heard it.'],
 ['listen','2. Listen','One transcription with the configuration\'s microphone and speech-recognition model: someone says a sentence in front of the microphone; the result shows what was heard (and what the intelligence took, if it runs). Checks the microphone, the voice detection, Whisper and the language.'],
 ['llm','3. LLM','The model of each condition answers a call of one token: the server is reachable and has the model; a cloud model only after confirming. Shows the latency.'],
 ['system','4. System','The whole stack starts in each condition (bounded, LLM-led) with its model and the campaign\'s configuration, and serves the conversation and the speech actions; then it is stopped.'],
 ['perception','5. Lip activity (only with barge-in)','With barge-in ticked: starts the lip activity detector on the camera and /user_speaking turns true when someone talks in front of it (the barge-in signal). Skipped otherwise.'],
 ['rehearsal','6. Rehearsal dialogue','A whole conversation with a member of the team, in the scenario and condition chosen, with the campaign\'s configuration: as a trial, not stored.']];
const SCHECKS=[['start','System starts','The system starts with the method (bounded) and with the LLM-led ablation, with the scripted LLM (no model needed).'],
 ['scripted','Dialogue, scripted LLM','One drink order with the scripted LLM and fixed user lines: it must succeed, as in the system tests.'],
 ['model','Dialogue, the model','One coffee order (sf_coffee) with the system model and condition chosen above and fixed user lines, on the lane of the hardware profile that runs the model: the model must answer.']];
let checkResults={};
const verdictCell=r=>r?`<span class="pill ${r.status=='OK'?'on':r.status=='FAILED'?'fail':'off'}">${esc(r.status)}</span> <span class="small">${esc(r.time)}</span><div class="small">${esc(r.detail)}</div>`:'<span class="small">not run yet</span>';
function drawChecks(){const s=(camp&&camp.sessions)||{},conds=Object.keys(s.conditions||{}),scens=s.scenarios||[];
 $('r_checks').innerHTML=RCHECKS.map(([k,n,d])=>`<tr><td style="width:46%"><b>${n}</b><div class="small">${d}</div>`+
  (k=='rehearsal'?`<div class="row" style="margin-top:6px"><select id="rh_scen">${scens.map(x=>opt(x)).join('')}</select><select id="rh_cond">${conds.map(x=>opt(x)).join('')}</select></div>`:'')+
  `</td><td style="width:10%"><button class="small test ck" onclick="realCheck('${k}')">&#9654; Run</button></td><td>${verdictCell(checkResults['real:'+campName()+':'+k])}</td></tr>`).join('');
 $('s_checks').innerHTML=SCHECKS.map(([k,n,d])=>`<tr><td style="width:46%"><b>${n}</b><div class="small">${d}</div></td>`+
  `<td style="width:10%"><button class="small test ck" onclick="simCheck('${k}')">&#9654; Run</button></td><td>${verdictCell(checkResults['sim:'+k])}</td></tr>`).join('');
 $('g_result').innerHTML=checkResults['config:speak']?'Last Check: speak '+verdictCell(checkResults['config:speak']):'';controls();}
async function loadChecks(){const keep={s:$('rh_scen')?val('rh_scen'):'',c:$('rh_cond')?val('rh_cond'):''};
 checkResults=await (await fetch('/api/checks')).json();drawChecks();if(keep.s&&$('rh_scen')){$('rh_scen').value=keep.s;$('rh_cond').value=keep.c;}}
function realCheck(what){const s=(camp&&camp.sessions)||{},conds=s.conditions||{};let cloud=Object.values(conds).filter(c=>(s.cloud_models||[]).includes(c.model)).map(c=>c.model);
 const body={campaign:campName(),what,domain:val('r_domain'),rmw:val('r_rmw'),barge_in:false};
 if(what=='rehearsal'){body.scenario=val('rh_scen');body.condition=val('rh_cond');cloud=cloud.filter(m=>m==conds[body.condition].model);}
 if(['llm','system','rehearsal'].includes(what)&&cloud.length){
  const text=what=='rehearsal'?`This rehearsal uses ${cloud[0]}: what the person says, transcribed on this computer (never the audio), goes to the provider. Go on?`:
   `Include the cloud model (${cloud.join(', ')})? `+(what=='llm'?'It makes one paid call of one token.':'Its gateway checks the key and the model (a free call).')+' Cancel to check the local models only.';
  body.allow_cloud=confirm(text);if(what=='rehearsal'&&!body.allow_cloud)return;}
 post('/api/check/real',body);}
function simCheck(what){const body={what};
 if(what=='model'){const e=exp('s_exp'),m=val('s_model');Object.assign(body,{experiment:val('s_exp'),hardware:val('s_hw'),model:m,condition:val('s_cond')});
  if(e.cloud_models.includes(m)){if(!confirm(`${m} is a cloud model: the dialogue makes PAID calls. Go on?`))return;body.allow_cloud=true;}}
 post('/api/check/sim',body);}
// ---- configurations (real/configs/): drafts edited and tried, frozen ones duplicated
let gcat={configs:[],whisper_models:[]},gEditing=null;
async function loadConfigs(){gcat=await (await fetch('/api/configs')).json();setVoiceCat(gcat);
 $('g_list').innerHTML='<table><tr><th>Configuration</th><th>State</th><th>How the robot sounds</th><th></th></tr>'+gcat.configs.map(c=>
  `<tr><td><b>${esc(c.name)}</b>${c.note?`<div class="small">${esc(c.note)}</div>`:''}${c.duplicated_from?`<div class="small">from ${esc(c.duplicated_from)}</div>`:''}</td>`+
  `<td><span class="pill ${c.state=='frozen'?'on':'off'}">${c.state}</span>${c.used_by.length?`<div class="small">used by ${esc(c.used_by.join(', '))}</div>`:''}</td>`+
  `<td>${c.error?`<div class="why">${esc(c.error)}</div>`:`<div class="small">${esc(describeValues(c.values))}</div>`}</td>`+
  `<td>${c.error?'':`<button class="small test" onclick="editConfig('${esc(c.name)}')">${c.state=='frozen'?'&#128065; View':'&#9998; Edit'}</button>`}<button class="small test" onclick="dupConfig('${esc(c.name)}')">&#10697; Duplicate and edit</button></td></tr>`).join('')+'</table>';
 $('g_settings').innerHTML=settingsHtml('g_');$('g_preset').innerHTML=presetOptions();$('g_whispers').innerHTML=gcat.whisper_models.map(m=>opt(m)).join('');
 if(gEditing)editConfig(gEditing.name);}
function describeValues(v){const st=voiceCat.settings.filter(s=>v[s.key]!=null).map(s=>`${s.key} ${v[s.key]}`);
 return [v.voice,...st].join(', ')+`; filler after ${v.filler_after_s==null?'the default':v.filler_after_s+' s'}; microphone ${v.input_device}; whisper ${v.whisper_model}`;}
function editConfig(name){const c=gcat.configs.find(x=>x.name==name);if(!c)return;const frozen=c.state=='frozen',v=c.values;gEditing={name,frozen};
 $('g_editor').style.display='block';$('g_title').textContent=(frozen?'Configuration ':'Draft ')+name;
 $('g_frozen').textContent=frozen?`Frozen: used by ${c.used_by.join(', ')}. It is never edited; Duplicate and edit makes a draft with everything copied. Check: speak still tries it.`:'';
 $('g_name').value=name;$('g_note').value=c.note||'';$('g_gender').value='';fillVoice('g_',false);$('g_voice').value=v.voice;if(val('g_voice')!==v.voice){$('g_voice').insertAdjacentHTML('beforeend',opt(v.voice,v.voice+' (not in the list)'));$('g_voice').value=v.voice;}
 voiceCat.settings.forEach(s=>$('g_'+s.key).value=v[s.key]??'');$('g_preset').value=presetOf(v);
 ['filler_after_s','input_device','whisper_model'].forEach(k=>$('g_'+k).value=v[k]??'');
 document.querySelectorAll('#g_editor input,#g_editor select').forEach(e=>{if(!['g_phrase','g_phrasepick'].includes(e.id))e.disabled=frozen;});$('g_save').disabled=frozen;gVoiceChanged();}
const gValues=()=>({voice:val('g_voice'),...settingsOf('g_'),filler_after_s:val('g_filler_after_s'),input_device:val('g_input_device'),whisper_model:val('g_whisper_model')});
function gVoiceChanged(){if(val('g_phrasepick')=='q')pickPhrase('g_');}
async function saveConfig(){if(!gEditing||gEditing.frozen)return;const j=await post('/api/config/save',{previous:gEditing.name,name:val('g_name').trim(),note:val('g_note'),...gValues()});
 if(j.name){gEditing={name:j.name,frozen:false};await loadConfigs();loadCampaigns();if(window.tLoad)tLoad();}}
async function dupConfig(name){if(!name)return;const n=prompt(`Duplicate ${name} as (a new draft; empty: ${name}-2 or the next free):`,'');if(n===null)return;
 const j=await post('/api/config/duplicate',{name,new_name:n.trim()});if(j.name){gEditing={name:j.name,frozen:false};await loadConfigs();loadCampaigns();if(window.tLoad)tLoad();}}
function configSpeak(){post('/api/config/speak',{name:gEditing&&gEditing.name,...gValues(),phrase:val('g_phrase'),domain:val('r_domain'),rmw:val('r_rmw')});}
/*TESTS_JS*/
// ---- state
let lastJob=null;
function enable(ids,on){ids.forEach(id=>{const b=$(id);if(b)b.disabled=!on;});}
function controls(){const busy=jobBusy,running_=!!running,refused=!!(camp&&camp.integrity);
 document.querySelectorAll('nav.tabs button').forEach(b=>{if(b.dataset.tab=='campaign'||b.dataset.tab=='configs')return;b.disabled=running_;b.title=running_?'A campaign is running: pause it first':'';});
 if(running_&&!['campaign','configs'].some(t=>$('page-'+t).classList.contains('active')))tab('campaign');
 enable(['b_start'],!running_&&!busy);if(W)wButtons();enable(['b_pause'],running_&&running!=='the whole evaluation');enable(['b_sim'],!running_&&!busy);enable(['b_stop'],busy);
 document.querySelectorAll('button.rt,button.ck').forEach(b=>b.disabled=running_||busy);
 // A campaign that refuses its trials and checks (its configuration file changed): its buttons too
 if(refused)document.querySelectorAll('#page-real button.rt,#page-real button.ck').forEach(b=>b.disabled=true);
 const why=running_?`Disabled: the campaign of ${running} is running (pause it first)`:busy?'Disabled: a job is running (see Running now)':'';
 $('w_camp').textContent=running_?`Running: ${running}`:busy?'Start disabled: a job is running':'';const outside=activeOutside.length?`Note: ${activeOutside.join(', ')} runs from a terminal (a dialogue saved in the last 15 min): it shares the GPU and the LLM server.`:'';
 $('w_sim').textContent=why||outside;$('w_real').textContent=refused?'Disabled: this campaign refuses its trials and checks (see above)':why||outside;if($('w_tests'))$('w_tests').textContent=why||outside;}
async function refresh(){const s=await (await fetch('/api/state')).json();running=s.campaign;const j=s.job;lastJob=j;jobBusy=!!(j&&j.running);controls();
 const pill=$('jobpill');if(!j.kind){pill.textContent='none';pill.className='pill off';return;}
 pill.textContent=j.running?`running ${j.elapsed_s} s`:`finished (${j.returncode})`;pill.className='pill '+(j.running?'on':'off');
 $('jobtitle').textContent=j.title+' · log: '+j.log;const q=j.prompt;$('enter').disabled=!q;$('quit').disabled=!q||!q.quit;
 $('enter').innerHTML='&#9654; '+esc(q?q.enter.replace(/^./,c=>c.toUpperCase()):'Enter');$('quit').textContent=q&&q.quit?q.quit.replace(/^./,c=>c.toUpperCase()):'q';
 $('b_jobcard').style.display=j.kind=='real'&&(j.meta||{}).participant?'':'none';
 const out=$('out');const atEnd=out.scrollTop+out.clientHeight>=out.scrollHeight-20;out.textContent=j.output.join('\n');if(atEnd)out.scrollTop=out.scrollHeight;
 const chat=$('chat');const end=chat.scrollTop+chat.clientHeight>=chat.scrollHeight-20;
 chat.innerHTML=j.transcript.map(t=>`<div class="said ${t.role}"><b>${t.role=='robot'?'Robot':'User'}</b>${esc(t.text)}</div>`).join('');if(end)chat.scrollTop=chat.scrollHeight;
 const said=[...j.output].reverse().find(l=>/^>>> (READY|CONVERSATION|END|CANCELLED|NOT RUN|TIMEOUT)/.test(l))||'';const b=$('banner');
 b.className=!j.running&&!/END/.test(said)?'':/READY/.test(said)?'ready':/CONVERSATION/.test(said)?'talk':said?'end':'';b.textContent=said.replace(/^>>> /,'');
 if(j.kind=='real'&&!j.running&&current&&current.participant==(j.meta||{}).participant&&!refresh.reloaded){refresh.reloaded=true;openPlan(current.participant);}
 if(j.kind=='check'&&!j.running&&!refresh.checked){refresh.checked=true;loadChecks();if((j.meta||{}).campaign)loadCampaigns(campName());}
 if(j.running){refresh.reloaded=false;refresh.checked=false;}}

async function loadCatalogue(){cat=await (await fetch('/api/catalogue')).json();
 const exps=cat.experiments.map(e=>opt(e.stem,e.name+(e.cloud_models.length||e.simulator_cloud?' (cloud)':''))).join('');
 const hws=cat.hardware.map(h=>opt(h.stem)).join('');$('c_exp').innerHTML=exps;$('s_exp').innerHTML=exps;$('c_hw').innerHTML=hws;$('s_hw').innerHTML=hws;
 $('s_domain').textContent=cat.sim_domain;document.querySelectorAll('.s_dom').forEach(x=>x.textContent=cat.sim_domain);
 fillLanes();describeExp();fillSim();loadChecks();}
let first='campaign';try{first=localStorage.getItem('interaction_panel_tab')||first;}catch(e){}
// A link can open a tab (#real, #configs, #tests), a campaign (#real:pilot), a participant's plan
// (#real:pilot:P05) or a task card (#card:pilot:P05:1, e.g. on a second screen in front of the
// participant); without the campaign (#card:P05:1), the one chosen last on this browser
const hash=location.hash.slice(1).split(':');if(hash[0])first=hash[0]=='card'?'real':hash[0];
if(hash[1]&&/^[Pp]\d+$/.test(hash[1]))hash.splice(1,0,'');
tab(['campaign','sim','real','configs','tests'].includes(first)?first:'campaign');
let firstMode='whole';try{firstMode=localStorage.getItem('interaction_panel_cmode')||firstMode;}catch(e){}cmode(['whole','one'].includes(firstMode)?firstMode:'whole');
loadCatalogue();refresh();table();loadConfigs();tLoad();
loadCampaigns(hash[1]||'').then(()=>{if(hash[2]&&campName())openPlan(hash[2]).then(()=>{if(hash[0]=='card')card(parseInt(hash[3]||'1'));});});
setInterval(refresh,1500);setInterval(table,15000);setInterval(()=>{if(cmodeIs('whole')&&$('page-campaign').classList.contains('active'))wLoad();},4000);
</script></body></html>'''


def page():
    """The page, with the Tests tab of scripts/test_panel.py (its markup and its script)."""
    t = tests()
    return PAGE.replace("<!--TESTS-->", t.TAB_HTML).replace("/*TESTS_JS*/", t.TAB_JS)


def main():
    global args
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--env", choices=("jazzy", "lyrical"), default="jazzy",
                        help="The pixi environment and its install folder (install, install_lyrical)")
    parser.add_argument("--real-out", default=sessions_common.OUT,
                        help="The folder of the sessions with real participants")
    parser.add_argument("--sim-domain", type=int, default=91,
                        help="ROS_DOMAIN_ID of the simulation tests (not one of the lanes')")
    parser.add_argument("--whole-root", default=whole.ROOT,
                        help="The folder of the whole evaluation (its state, log and run health)")
    args = parser.parse_args()
    args.whole_root = os.path.abspath(os.path.expanduser(args.whole_root))
    whole.capture = whole_capture
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Open http://localhost:{args.port}  (Ctrl+C to stop the page; it does not stop a campaign)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
