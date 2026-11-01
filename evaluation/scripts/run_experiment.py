# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Run an experiment: the interaction system against simulated users, for every system model and
condition of the experiment file, every scenario, persona and repetition.

    python3 run_experiment.py experiments/<experiment>.yaml [--out DIR] [--only-model M]
                              [--models M1,M2] [--dry-run] [--allow-cloud]
                              [--hardware H --lane L] [--check-servers] [--show]

For each (model, condition) the whole system is launched (main.launch.py speech:=none: the
speech capability is the simulation harness) with the model and the condition's trees and
skills (an ablation, or the method), and stopped at the end. Each dialogue is saved as
runs/<model>/<condition>/<scenario>__<persona>__r<k>.json and appended to runs.jsonl; existing
runs are skipped (resume). Scoring: analysis/score_runs.py. --only-model, --only-condition,
--only-scenario, --only-persona and --only-rep run part of it (e.g. one dialogue, to watch it
with --show, which prints the conversation as it goes). --models replaces the system models of
the file (e.g. the finalists of phase A of E0a, whose file lists a placeholder).

The wait for the model (llm_timeout, reply_budget, opening_budget, intelligence_timeout) is
part of the method and keeps its defaults, unless the experiment opens it with `model_wait`
({key: seconds}; the same for every condition; validated before the run, shown by --dry-run,
kept in experiment.yaml, params.yaml and every dialogue's record): E1 does, since it does not
measure the bound and some of its cloud models reason for many seconds before answering.

A condition can also give parameters for one system model only (model_params: {<model>: {...}}),
e.g. routes for the local model, which a cloud model does not use (the experiments use none).

A scenario can plan failures of the system's model by user turn (`faults`, E3:
simulation/interaction_sim/interaction_sim/llm_faults.py). For a condition that runs such a
scenario the system is launched with the injector (skill module interaction_sim.fault_injection,
added to the condition's own, and the switch <run dir>/.llm_faults.json in its environment); each
dialogue records the failures planned (`faults`) and the calls that failed (`faults_applied`).

An experiment file with `record_model_calls: true` (the debugging files of experiments/debug/,
doc/DEBUGGING.md; off by default: the prompts make the records several times larger) records
every call of the system's model in each dialogue (`model_calls`: the user turn, the skill, the
question, its prompt and schema, the answer or the error, the latency): the recorder
(simulation/interaction_sim/interaction_sim/call_log.py) is loaded in the intelligence as the
last of its skill_modules, with the marker <run dir>/.model_calls.json in its environment (both hidden, so that they are
never read as dialogue records).

A pilot file (experiments/pilot/, doc/PILOT.md) names the experiment file it pilots (`pilot_of`)
and changes only the dialogues (scenarios, pairs of scenario and persona, repetitions), the
models' placeholders and the free stand-ins of the cloud providers (`standins`: the base URLs of
scripts/pilot_standin.py); everything else is read from the experiment file
(experiment_files.load_experiment). With stand-ins, the gateway's cloud providers and the
simulated user's client are sent to them, with a dummy API key: no paid call.

A system model is an Ollama model (qwen2.5:14b) or a cloud one, written provider/model
(openai/<model>, gemini/<model>). The simulated user can also run on a cloud provider
(simulator.provider). Cloud calls are paid: an experiment that makes any is refused unless
--allow-cloud is given; --dry-run tells how many dialogues and simulated-user turns would go to
each cloud model. The API keys are read from the environment (GEMINI_API_KEY or GOOGLE_API_KEY,
OPENAI_API_KEY).

Where it runs is not part of the experiment: a hardware profile (experiments/hardware/*.yaml)
names the Ollama servers and splits the experiment into lanes (which system models each lane
runs, on which server, and where its open simulated user runs). With --hardware and --lane the
runner runs that lane only; run_parallel.py starts every lane of a profile at once; with
--check-servers it only checks that the servers of the lane are free (exit status 3 if not). Without
--hardware, the experiment's own `ollama` server (and `simulator.host`) are used. An experiment
can ask for the open simulator that fits the hardware with `simulator.model: hardware`.

Needs a sourced workspace (pixi shell + install/setup.bash), the Ollama servers answering, and a
ROS_DOMAIN_ID that nothing else uses (run_parallel.py gives each lane its own).

A local system model runs with its reasoning off (think false) unless it cannot switch it off
(gpt-oss, or a model that rejects think false: "low"), and with the context window of the
experiment (`ollama.num_ctx`, 8192 by default): the same rule and function as phase A of E0a
(offline/core/ollama_models.warm_up), so phase B reproduces phase A's settings; each dialogue
records them (system_settings), with whether the model is entirely in GPU memory
(model_fit.json). The model is unloaded from the server when its dialogues end; --keep-loaded
keeps it (on a shared GPU, so that a job queued for free memory does not take the gap before the
next run; whoever runs it unloads it at the end).

A dialogue is not saved (the resume runs it again) when the Ollama server it used does not answer
at its end (a dropped tunnel makes every dialogue fail fast: the run stops), or when the simulated
user failed in it (a failed call is replaced by "Sorry, could you repeat that?": the dialogue is
not the one planned). Those are logged in simulator_errors.jsonl; after
MAX_SIMULATOR_FAILURES such dialogues in a row the run stops (credit exhausted, a quota?).
--force-busy runs on servers that seem in use; --force-provenance resumes a folder begun with
other code (--force: the old name of --force-busy).
"""

import argparse
import copy
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import requests
import yaml
from ament_index_python.packages import get_package_share_directory

from ros2_dialog_interfaces.action import DialogTask
from interaction_sim.asr_noise import AsrNoise
from interaction_sim.call_log import ENV as CALLS_ENV, CallLog
from interaction_sim.cloud_llm import CLOUD_PROVIDERS, PILOT_ENV
from interaction_sim.goal_items import validate_goal_items
from interaction_sim.harness import HarnessRunner
from interaction_sim.llm_faults import ENV as FAULTS_ENV, FaultPlan, validate_faults
from interaction_sim.llm_user import LlmUser, UserEvent

from experiment_files import (check_pairs, condition_dialogues, condition_scenarios,
                              describe_model_wait, load_experiment, model_wait,
                              model_wait_params, select_scenarios)
import provenance

EVAL = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# The models of the Ollama server (pull, warm-up, unload): one module for every runner
sys.path.append(os.path.join(EVAL, "offline", "core"))
import ollama_models  # noqa: E402

DEFAULT_NUM_CTX = 8192
# Dialogues in a row with a simulator error after which the run stops
MAX_SIMULATOR_FAILURES = 3
# How long a busy server is checked again before the run gives up (a model of the previous run
# may still be unloading)
BUSY_WAIT_S = 60


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = deep_merge(out.get(k, {}), v) if isinstance(v, dict) else v
    return out


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_hardware(path: str) -> dict:
    """A hardware profile, with the environment variables of its `ssh` entries expanded
    (an unset variable leaves the GPU of that server unchecked)."""
    hardware = load(path)
    for server in hardware.get("servers", {}).values():
        ssh = os.path.expandvars(server.get("ssh") or "")
        server["ssh"] = "" if "$" in ssh else ssh
    return hardware


def split_model(model: str) -> tuple[str, str]:
    """(provider, model) of a system model: provider/model for a cloud one, else Ollama."""
    provider, sep, name = model.partition("/")
    if sep and provider in CLOUD_PROVIDERS:
        return provider, name
    return "ollama", model


def seed_of(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class System:
    """The interaction system launched for one model and condition, in its own process group."""

    def __init__(self, exp: dict, model: str, condition: dict, out_dir: str, think=False,
                 faults: bool = False, record_calls: bool = False):
        share = get_package_share_directory("interaction_system")
        app = load(os.path.join(share, "config", "app_params.yaml"))
        llm = exp.get("ollama", {})
        provider, name = split_model(model)
        if provider == "ollama":
            backend = {"enable": True, "host": llm.get("host", "127.0.0.1"),
                       "port": llm.get("port", 11434), "model": name,
                       "timeout": float(llm.get("timeout", 120.0)),
                       "think": think, "num_ctx": num_ctx(exp)}
        else:
            # The key comes from the environment (the gateway reads it when api_key is empty)
            backend = {"enable": True, "model": name, "timeout": float(llm.get("timeout", 120.0))}
            # The pilot's free stand-in of the provider (doc/PILOT.md): the same provider code,
            # another server (only a pilot file has `standins`)
            standin = (exp.get("standins") or {}).get(f"{provider}_base_url")
            if standin:
                backend["base_url"] = standin
        gateway = {"default_provider": provider, provider: backend}
        if provider != "ollama":
            # Otherwise the gateway would load the default local model on the GPU, competing
            # with the lanes that run local models
            gateway["ollama"] = {"enable": False}
        override = {
            "llm_manager_node": {"ros__parameters": gateway},
            "interaction_intelligence": {"ros__parameters": {
                "language": exp.get("language", "English"),
                "conversation_log_path": os.path.join(out_dir, "transcripts.log"),
                # The wait for the model (llm_timeout, reply_budget, opening_budget,
                # intelligence_timeout) keeps the method's defaults unless the experiment opens it
                # with `model_wait` (model_wait_params below: the same for every condition; E1);
                # ollama.timeout above is only the gateway's limit for one request to the server
                "skill_modules": skill_modules(condition, faults, record_calls),
            }},
            "interaction_orchestrator": {"ros__parameters": dict(exp.get("limits", {}))},
        }
        override = deep_merge(override, model_wait_params(exp))
        params = deep_merge(deep_merge(deep_merge(app, override), condition.get("params", {})),
                            condition_params(condition, model))
        os.makedirs(out_dir, exist_ok=True)
        self.params_file = os.path.join(out_dir, "params.yaml")
        with open(self.params_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(params, f, sort_keys=False, allow_unicode=True)
        args = ["ros2", "launch", "interaction_system", "main.launch.py", "speech:=none",
                f"params_file:={self.params_file}"]
        ablation = condition.get("ablation")
        if ablation:
            args.append("config_file:=" + os.path.join(
                get_package_share_directory("interaction_ablations"), "config", ablation,
                "interaction_behaviors_config.yaml"))
        # The switch of the injector of model failures (llm_faults.py), read by the intelligence,
        # and the marker of the recorder of the model's calls (call_log.py), when the file asks for
        # it: hidden files, since runs/<model>/<condition>/*.json are the dialogue records
        self.fault_switch = os.path.join(out_dir, ".llm_faults.json")
        self.call_marker = os.path.join(out_dir, ".model_calls.json")
        env = None
        if faults or record_calls:
            env = dict(os.environ)
            if faults:
                env[FAULTS_ENV] = self.fault_switch
            if record_calls:
                env[CALLS_ENV] = self.call_marker
        self.log = open(os.path.join(out_dir, "system.log"), "a")
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT,
                                     start_new_session=True, env=env)

    def stop(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                self.proc.wait(timeout=8)
                break
            except subprocess.TimeoutExpired:
                continue
        self.log.close()


def skill_modules(condition: dict, faults: bool = False,
                  record_calls: bool = False) -> list[str]:
    """The intelligence's skill_modules: the condition's, plus the injector of model failures
    when its scenarios plan any (added here, not in the experiment file, so that every condition
    that runs those scenarios gets it and none can forget it), plus the recorder of the model's
    calls when the file asks for it (last: it names the skills registered before it, and records
    an injected answer as the one the client received)."""
    modules = [m for m in condition.get("skill_modules", []) if m]
    if faults and "interaction_sim.fault_injection" not in modules:
        modules.append("interaction_sim.fault_injection")
    if record_calls and "interaction_sim.call_recording" not in modules:
        modules.append("interaction_sim.call_recording")
    return modules or [""]


def records_calls(exp: dict) -> bool:
    """Whether the experiment records every call of the system's model (record_model_calls:
    true, a boolean; the debugging files)."""
    value = exp.get("record_model_calls", False)
    if not isinstance(value, bool):
        raise ValueError("record_model_calls must be true or false")
    return value


def plans_faults(scenarios: list[dict], condition: dict) -> bool:
    """Whether a scenario that the condition runs plans model failures (`faults`)."""
    return any(s.get("faults") for s in condition_scenarios(scenarios, condition))


def apply_hardware(exp: dict, hardware: dict, lane: str) -> dict:
    """The experiment with the servers of a lane of the hardware profile: the system's Ollama
    server, the open simulator's server, the open simulator that fits (simulator.model:
    hardware), and the system models of the lane."""
    lanes = hardware.get("lanes", {})
    if lane not in lanes:
        sys.exit(f"Lane '{lane}' not in the hardware profile: {list(lanes)}")
    spec = lanes[lane]
    servers = hardware.get("servers", {})
    exp = copy.deepcopy(exp)
    system = servers[spec["system"]]
    exp["ollama"] = {**exp.get("ollama", {}), "host": system["host"], "port": system["port"]}
    sim = exp["simulator"]
    if sim.get("provider", "ollama") == "ollama":
        server = servers[spec.get("simulator", spec["system"])]
        sim["host"] = f"http://{server['host']}:{server['port']}"
        if sim.get("model") == "hardware":
            if not hardware.get("open_simulator"):
                sys.exit("The experiment asks for the hardware's open simulator, and the "
                         "profile has no open_simulator")
            sim["model"] = hardware["open_simulator"]
    select = spec.get("models", "all")
    if select == "local":
        exp["models"] = [m for m in exp["models"] if split_model(m)[0] == "ollama"]
    elif select == "cloud":
        exp["models"] = [m for m in exp["models"] if split_model(m)[0] != "ollama"]
    elif isinstance(select, list):
        exp["models"] = [m for m in exp["models"] if m in select]
    if spec.get("shard"):
        # [i, n]: every n-th of the selected models from the i-th (to spread them over servers)
        i, n = spec["shard"]
        exp["models"] = exp["models"][i::n]
    exp["hardware"] = {"profile": hardware.get("name", ""), "lane": lane}
    return exp


def server_busy(server: dict, ours: set[str]) -> list[str]:
    """Why an Ollama server (and its GPU, when the profile gives `ssh`) is in use by someone
    else: models loaded that are not of this experiment, or GPU processes that are not Ollama.
    Empty when it is free for us."""
    reasons = []
    base = f"http://{server['host']}:{server['port']}"
    try:
        loaded = requests.get(f"{base}/api/ps", timeout=15).json().get("models", [])
        others = [m["name"] for m in loaded
                  if m["name"] not in ours and m["name"].removesuffix(":latest") not in ours]
        if others:
            reasons.append(f"models of other work loaded on {base}: {others}")
    except requests.RequestException as exc:
        reasons.append(f"Ollama at {base} does not answer: {exc}")
    if server.get("ssh"):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", server["ssh"],
               "nvidia-smi --query-compute-apps=process_name,used_memory "
               "--format=csv,noheader"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            apps = [line for line in res.stdout.splitlines() if line.strip()]
            foreign = [a for a in apps if "ollama" not in a and "llama-server" not in a]
            if res.returncode != 0:
                reasons.append(f"cannot query the GPU of {server['ssh']}: {res.stderr.strip()}")
            elif foreign:
                reasons.append(f"other GPU processes on {server['ssh']}: {foreign}")
        except subprocess.TimeoutExpired:
            reasons.append(f"cannot reach {server['ssh']} to query its GPU")
    return reasons


def num_ctx(exp: dict) -> int:
    """The context window of the system's Ollama requests (the experiment's, else 8192)."""
    return int(exp.get("ollama", {}).get("num_ctx", DEFAULT_NUM_CTX))


def system_base(exp: dict) -> str:
    llm = exp.get("ollama", {})
    return f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"


def ensure_models(exp: dict, models: list[str], base: str | None = None) -> None:
    """Pull on the Ollama server (the system's, or base) the models it does not have (a request
    for a missing model fails: Ollama does not download it by itself). Cloud models are
    skipped."""
    models = [m for m in models if split_model(m)[0] == "ollama"]
    if not models:
        return
    llm = exp.get("ollama", {})
    base = base or f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"
    have = {m["name"] for m in requests.get(f"{base}/api/tags", timeout=30).json()["models"]}
    for model in models:
        if model not in have and f"{model}:latest" not in have:
            print(f"   pulling {model} on the server...", flush=True)
            r = requests.post(f"{base}/api/pull", json={"model": model, "stream": False},
                              timeout=ollama_models.PULL_TIMEOUT_S)
            r.raise_for_status()


def warm_up(exp: dict, model: str):
    """Load the model on the server, so that the first dialogue does not pay for it, with the
    context of the run, and return the `think` it runs with: false, or "low" for a model that
    cannot switch its reasoning off (the rule of phase A of E0a: ollama_models.warm_up). None for
    a cloud model."""
    if split_model(model)[0] != "ollama":
        return None
    return ollama_models.warm_up(system_base(exp), model, False, num_ctx(exp))


def record_fit(out: str, exp: dict, model: str) -> dict | None:
    """Whether the local model, just loaded with the run's context, is entirely in GPU memory
    (/api/ps: size, size_vram, fits), recorded in <out>/model_fit.json (E0a chooses only a model
    that fits). None for a cloud model."""
    if split_model(model)[0] != "ollama":
        return None
    fit = ollama_models.gpu_fit(system_base(exp), model)
    path = os.path.join(out, "model_fit.json")
    known = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            known = json.load(f)
    known[model] = {**fit, "num_ctx": num_ctx(exp), "time": time.time()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(known, f, indent=1)
    return fit


def servers_of(exp: dict, model: str, condition: dict) -> list[str]:
    """The Ollama servers a dialogue of this model and condition uses: the system's (a local
    model, or routes) and the open simulated user's."""
    out = []
    if split_model(model)[0] == "ollama" or routed_models(condition, model):
        out.append(system_base(exp))
    sim = exp["simulator"]
    if sim.get("provider", "ollama") == "ollama":
        out.append((sim.get("host") or system_base(exp)).rstrip("/"))
    return sorted(set(out))


def servers_down(servers: list[str]) -> list[str]:
    """Those of the servers that do not answer now (/api/tags)."""
    return [s for s in servers if not ollama_models.server_up(s)]


def simulator_failed(user) -> bool:
    """Whether a call of the simulated user failed in the dialogue (it was replaced by a fixed
    line: the dialogue is not the one planned)."""
    return any("error" in e for e in getattr(user, "log", []))


def condition_params(condition: dict, model: str) -> dict:
    """The parameter overrides of a condition for one system model (`model_params`: e.g. the
    routing of the questions of a local model, which a cloud model does not use)."""
    return (condition.get("model_params") or {}).get(model) or {}


def routed_models(condition: dict, model: str | None = None) -> list[str]:
    """The models that the condition's routes send questions to (for one system model, or for
    any)."""
    sets = [condition.get("params") or {}]
    sets += ([condition_params(condition, model)] if model
             else list((condition.get("model_params") or {}).values()))
    routes = [r for p in sets for r in (((p.get("interaction_intelligence") or {})
                                        .get("ros__parameters") or {}).get("routes") or [])]
    return sorted({r.partition("=")[2].strip() for r in routes if "=" in r})


# The API keys a stand-in is called with: never a real key (a call that reached a real API by
# mistake would be refused, not billed)
STANDIN_KEY = "pilot-standin-no-key"


def use_standins(exp: dict, environ=os.environ) -> None:
    """A pilot with stand-ins (doc/PILOT.md): the simulated user's and the judge's client
    (cloud_llm.py) are sent to them and the API keys replaced by a dummy one, for this process
    and the system it launches. A file that is not a pilot refuses stand-in URLs left in the
    environment: an experiment always calls the real providers."""
    standins = exp.get("standins") or {}
    if not standins:
        left = [v for v in PILOT_ENV.values() if environ.get(v)]
        if left:
            sys.exit(f"{', '.join(left)} set, but this is not a pilot: unset them (an experiment "
                     f"calls the real providers)")
        return
    for provider, var in PILOT_ENV.items():
        url = standins.get(f"{provider}_base_url")
        if url:
            environ[var] = url
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        environ[key] = STANDIN_KEY
    environ.pop("GOOGLE_API_KEY", None)


def make_goal(scenario: dict, exp: dict) -> DialogTask.Goal:
    """The request of the scenario. Its limits: the scenario's own; for max_turns, else the
    experiment's (the same turn limit for every condition); else unset (USE_DEFAULT: the
    system's value, `limits` of the experiment)."""
    g = scenario["goal"]
    unset = DialogTask.Goal.USE_DEFAULT
    return DialogTask.Goal(
        task_description=g.get("task_description", ""),
        dialog_mode=g.get("dialog_mode", ""),
        frame_schema_json=(g.get("frame_schema_json") or "").strip(),
        initial_frame_json=(g.get("initial_frame_json") or "").strip(),
        resources_json=(g.get("resources_json") or "").strip(),
        domain=g.get("domain", ""),
        max_turns=int(g.get("max_turns", exp.get("max_turns", unset))),
        max_silence=float(g.get("max_silence", DialogTask.Goal.USE_DEFAULT_SECONDS)),
        max_stalled=int(g.get("max_stalled", unset)),
        filler_after_s=float(g.get("filler_after_s", DialogTask.Goal.USE_DEFAULT_SECONDS)),
        skip_intro=bool(g.get("skip_intro", False)),
    )


def make_user(exp: dict, scenario: dict, persona: dict, seed: int,
              allow_cloud: bool = False, condition: dict | None = None) -> LlmUser:
    sim = exp["simulator"]
    llm = exp.get("ollama", {})
    # The speech recognition errors: the scenario's own, else the condition's (one noise level
    # per condition, as in E3), else the experiment's
    noise = scenario.get("asr_noise", (condition or {}).get("asr_noise", exp.get("asr_noise")))
    # The simulated user's own Ollama server, or else the system's
    host = sim.get("host") or f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"
    return LlmUser(
        persona=persona["description"],
        goal=scenario["user_goal"],
        model=sim["model"],
        provider=sim.get("provider", "ollama"),
        host=host,
        allow_cloud=allow_cloud,
        language=exp.get("language", "English"),
        seed=seed,
        temperature=None if sim.get("temperature", 0.7) is None
        else float(sim.get("temperature", 0.7)),
        events=[UserEvent(**e) for e in scenario.get("events", [])],
        noise=AsrNoise(**noise) if noise else None,
        max_turns=int(sim.get("max_turns", 30)),
        on_gpu=bool(sim.get("on_gpu", True)),
        goal_items=scenario.get("goal_items") or [],
    )


class Shown:
    """A simulated user that prints the conversation as it goes (--show): each robot utterance
    and what the user answers to it."""

    def __init__(self, user):
        self.user = user

    def __call__(self, robot_utterance: str, transcript: list[dict]):
        print(f"ROBOT: {robot_utterance}", flush=True)
        turn = self.user(robot_utterance, transcript)
        if turn is not None and turn.text is not None:
            print(f"USER: {turn.text}", flush=True)
        if turn is not None and turn.barge_in_after is not None:
            print(f"   (the user will cut the next utterance after {turn.barge_in_after:g} s)",
                  flush=True)
        return turn


def busy_servers(hardware: dict, lane: str, models: list[str], conditions: dict,
                 sim: dict) -> list[str]:
    """Why the servers of a lane are in use by other work (empty: free): server_busy for the
    system's server and the open simulator's, with the models of this experiment's lane (all of
    them, not only those of --only-model), their routes and the open simulator as ours."""
    spec = hardware["lanes"][lane]
    ours = {split_model(m)[1] for m in models if split_model(m)[0] == "ollama"}
    ours |= {m for c in conditions.values() for m in routed_models(c)}
    # The pilot's cloud system cells are answered by the local model they are named after
    # (pilot_standin.py): that model, loaded by the stand-in, is ours too
    ours |= {split_model(m)[1] for m in models if split_model(m)[0] != "ollama"
             and ":" in split_model(m)[1]}
    if sim.get("provider", "ollama") == "ollama":
        ours.add(sim["model"])
    names = {spec["system"], spec.get("simulator", spec["system"])}
    return [r for n in sorted(names) for r in server_busy(hardware["servers"][n], ours)]


def wait_until_free(hardware: dict, lane: str, models: list[str], conditions: dict, sim: dict,
                    wait_s: float = BUSY_WAIT_S, sleep=time.sleep) -> list[str]:
    """busy_servers, checked again every 5 s for up to wait_s while busy: a model of the run before
    this one may still be unloading (its unload is asynchronous)."""
    deadline = time.monotonic() + wait_s
    while True:
        busy = busy_servers(hardware, lane, models, conditions, sim)
        if not busy or time.monotonic() >= deadline:
            return busy
        sleep(5)


def record(run, scenario, persona_name, rep, model, condition_name, user, seed) -> dict:
    result = run.result
    return {
        "scenario": scenario["id"], "strategy": scenario.get("strategy"),
        "persona": persona_name, "rep": rep, "seed": seed,
        "model": model, "condition": condition_name, "language": user.language,
        "status": run.status,
        "success": bool(result.success) if result else False,
        "outcome": result.outcome if result else "",
        "failure_reason": result.failure_reason if result else "",
        "data": json.loads(result.final_frame_json or "{}") if result else {},
        "total_turns": result.total_turns if result else 0,
        "duration_s": round(run.duration_s, 2),
        "expect": scenario.get("expect", {}),
        "events": scenario.get("events", []),
        "faults": scenario.get("faults", []),
        "goal": scenario.get("goal", {}),
        "transcript": run.transcript, "feedback": run.feedback, "skills": run.skills,
        "barge_ins": run.barge_ins, "user_log": user.log,
        "simulator": {"provider": user.provider, "model": user.model,
                      "errors": sum(1 for e in user.log if "error" in e),
                      "revealed_errors": sum(1 for e in user.log if "revealed_error" in e),
                      "prompt_tokens": sum(e.get("usage", {}).get("prompt_tokens", 0)
                                           for e in user.log),
                      "completion_tokens": sum(e.get("usage", {}).get("completion_tokens", 0)
                                               for e in user.log)},
        "goal": scenario["goal"], "user_goal": scenario["user_goal"],
        # The adherence check of the simulated user (analysis/adherence.py) reads them
        "goal_items": user.goal_items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiment")
    parser.add_argument("--out", default=None)
    parser.add_argument("--only-model", default=None)
    parser.add_argument("--models", default=None,
                        help="Replace the experiment's system models (comma separated), e.g. "
                             "the finalists of phase A of E0a; the lanes of --hardware then "
                             "select among these")
    parser.add_argument("--only-condition", default=None)
    parser.add_argument("--only-scenario", default=None)
    parser.add_argument("--only-persona", default=None)
    parser.add_argument("--only-rep", type=int, default=None)
    parser.add_argument("--show", action="store_true",
                        help="Print each conversation as it goes (robot and user lines)")
    parser.add_argument("--check-servers", action="store_true",
                        help="Only check that the servers of the lane are free (--hardware/--lane); "
                             "exit status 3 if they are not")
    parser.add_argument("--dry-run", action="store_true", help="List the runs and exit")
    parser.add_argument("--allow-cloud", action="store_true",
                        help="Allow the paid calls to cloud models (system or simulated user)")
    parser.add_argument("--hardware", default=None, help="Hardware profile (YAML)")
    parser.add_argument("--lane", default=None, help="The lane of the profile to run")
    parser.add_argument("--keep-loaded", action="store_true",
                        help="Leave the system model loaded at the end (a shared GPU: no gap "
                             "between runs); unload it yourself when done")
    parser.add_argument("--force-busy", action="store_true",
                        help="Run even if the servers seem in use by other work")
    parser.add_argument("--force-provenance", action="store_true",
                        help="Resume a results folder begun with other code or model digests")
    parser.add_argument("--force", action="store_true",
                        help="Deprecated: the old name of --force-busy")
    args = parser.parse_args()
    if args.force:
        print("--force is deprecated: it means --force-busy now (--force-provenance resumes a "
              "folder begun with other code)", flush=True)
        args.force_busy = True

    exp_path = os.path.abspath(args.experiment)
    try:  # an experiment file, or a pilot derived from its experiment file (doc/PILOT.md)
        exp = load_experiment(exp_path)
    except (ValueError, OSError) as exc:
        sys.exit(f"{exp_path}: {exc}")
    use_standins(exp)
    if args.models:
        # Recorded in results/<name>/experiment*.yaml like the rest of what was run
        exp["models"] = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.hardware:
        hardware = load_hardware(os.path.abspath(args.hardware))
        if not args.lane:
            sys.exit(f"--lane: one of {list(hardware.get('lanes', {}))}")
        exp = apply_hardware(exp, hardware, args.lane)
    elif exp["simulator"].get("model") == "hardware":
        sys.exit("The experiment asks for the hardware's open simulator: give --hardware/--lane")
    try:  # the wait for the model the experiment opens (model_wait): checked before anything runs
        model_wait(exp)
        record_calls = records_calls(exp)
    except ValueError as exc:
        sys.exit(f"{exp_path}: {exc}")
    base = os.path.dirname(exp_path)
    scenarios = load(os.path.join(base, exp["scenarios"]))["scenarios"]
    scenarios = select_scenarios(scenarios, exp)
    try:
        check_pairs(scenarios, exp)
    except ValueError as exc:
        sys.exit(f"{exp_path}: {exc}")
    for s in scenarios:  # malformed goal items or model failures stop the run before it starts
        try:
            validate_goal_items(s.get("goal_items"))
            validate_faults(s.get("faults"))
        except (ValueError, TypeError) as exc:
            sys.exit(f"Scenario {s['id']}: {exc}")
    if args.only_scenario:
        scenarios = [s for s in scenarios if s["id"] == args.only_scenario]
    personas = load(os.path.join(base, exp.get("personas_file", "personas.yaml")))["personas"]
    out = os.path.abspath(args.out or os.path.join(EVAL, "results", exp["name"]))
    os.makedirs(out, exist_ok=True)
    # What was run (with the servers and models of the lane, if any)
    name = f"experiment_{args.lane}.yaml" if args.lane else "experiment.yaml"
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        yaml.safe_dump(exp, f, sort_keys=False, allow_unicode=True)

    models = [m for m in exp["models"] if not args.only_model or m == args.only_model]
    conditions = {k: v or {} for k, v in exp["conditions"].items()
                  if not args.only_condition or k == args.only_condition}
    # A condition can be limited to some models (e.g. a routing defined around one of them)
    # Each scenario with each persona, or the pairs of a pilot (condition_dialogues)
    plan = [(m, c, s, p, r) for m in models for c in conditions
            if not conditions[c].get("models") or m in conditions[c]["models"]
            for s, p in condition_dialogues(scenarios, exp, conditions[c])
            if not args.only_persona or p == args.only_persona
            for r in range(1, int(exp.get("repetitions", 1)) + 1)
            if not args.only_rep or r == args.only_rep]
    lane = f" [lane {args.lane}: {models}]" if args.lane else ""
    print(f"{exp['name']}: {len(plan)} dialogues ({len(models)} models, {len(conditions)} "
          f"conditions, {len(scenarios)} scenarios, {len(exp['personas'])} personas, "
          f"{exp.get('repetitions', 1)} repetitions) -> {out}{lane}")
    where = f" at {exp['simulator']['host']}" if exp["simulator"].get("host") else ""
    print(f"   simulated user: {exp['simulator'].get('provider', 'ollama')}/"
          f"{exp['simulator']['model']}{where}")
    # The paid calls: dialogues run by a cloud system model, and the simulated user's turns
    cloud = {}
    for (m, c, s, p, r) in plan:
        if split_model(m)[0] != "ollama":
            cloud[m] = cloud.get(m, 0) + 1
    sim = exp["simulator"]
    if sim.get("provider", "ollama") in CLOUD_PROVIDERS:
        cloud[f"{sim['provider']}/{sim['model']} (simulated user)"] = len(plan)
    standin = " (answered by the pilot's stand-in: no paid call)" if exp.get("standins") else ""
    for name, n in cloud.items():
        print(f"   cloud: {name}: {n} dialogues (up to {int(exp.get('max_turns', 25))} "
              f"turns each){standin}")
    print(f"   wait for the model: {describe_model_wait(exp)}; longest dialogue "
          f"{float(exp.get('dialogue_timeout', 900)):g} s")
    faulty = [c for c in conditions if plans_faults(scenarios, conditions[c])]
    if faulty:
        print(f"   model failures planned in {sum(1 for s in scenarios if s.get('faults'))} "
              f"scenarios: the injector is loaded in {', '.join(faulty)}")
    if record_calls:
        print("   every call of the system's model is recorded (record_model_calls: "
              "model_calls in each dialogue)")
    if args.dry_run:
        return
    if args.check_servers:
        if not args.hardware:
            sys.exit("--check-servers needs --hardware and --lane")
        if not plan:
            print("   servers: nothing to run in this lane")
            return
        busy = busy_servers(hardware, args.lane, exp["models"], conditions, sim)
        print("   servers: " + ("NOT FREE:\n     " + "\n     ".join(busy) if busy else "free"),
              flush=True)
        sys.exit(3 if busy else 0)
    # A placeholder (<pin>: the dated version of a cloud model; <...>: e.g. the finalists of E0a)
    unpinned = [m for m in models + [sim["model"]] if "<" in m]
    if unpinned:
        sys.exit(f"Write the dated version of these models in the experiment, or give the "
                 f"models with --models: {unpinned}")
    if cloud and not args.allow_cloud:
        sys.exit("This experiment makes paid calls to cloud models: run it with --allow-cloud")
    if not plan:
        return
    # Never disturb other work on a shared GPU: the servers of this run must be free
    if args.hardware and not args.force_busy:
        busy = wait_until_free(hardware, args.lane, exp["models"], conditions, sim)
        if busy:
            sys.exit("The hardware seems in use by other work (--force-busy to run anyway):\n  "
                     + "\n  ".join(busy))
    if sim.get("provider", "ollama") == "ollama" and sim.get("host"):
        ensure_models(exp, [sim["model"]], sim["host"])
    # What code and models produce these results; a resume with other code is refused
    llm = exp.get("ollama", {})
    ollama_base = f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"
    provenance.record(out, models, ollama_base, force=args.force_provenance)

    counts = {"saved": 0, "succeeded": 0, "failed": 0, "simulator_failures": 0}
    in_a_row = 0

    def stop(reason: str) -> None:
        provenance.catalog("stop", out, {**counts, "reason": reason})
        sys.exit(reason)

    harness = HarnessRunner()
    try:
        for model in models:
            for cname, condition in conditions.items():
                if condition.get("models") and model not in condition["models"]:
                    continue
                run_dir = os.path.join(
                    out, "runs", model.replace(":", "_").replace("/", "_"), cname)
                todo = [(s, p, r) for (m, c, s, p, r) in plan if m == model and c == cname
                        and not os.path.isfile(os.path.join(
                            run_dir, f"{s['id']}__{p}__r{r}.json"))]
                if not todo:
                    continue
                print(f"== {model} / {cname}: {len(todo)} dialogues", flush=True)
                ensure_models(exp, [model] + routed_models(condition, model))
                provenance.add_models(out, [model] + routed_models(condition, model), ollama_base)
                think = warm_up(exp, model)
                fit = record_fit(out, exp, model)
                system = System(exp, model, condition, run_dir, think=think or False,
                                faults=plans_faults(scenarios, condition),
                                record_calls=record_calls)
                servers = servers_of(exp, model, condition)
                try:
                    if not harness.node.wait_for_system(120):
                        raise RuntimeError("The interaction system did not start")
                    time.sleep(3.0)
                    for scenario, persona_name, rep in todo:
                        seed = seed_of(scenario["id"], persona_name, rep)
                        user = make_user(exp, scenario, personas[persona_name], seed,
                                         args.allow_cloud, condition)
                        if args.show:
                            print(f"-- {scenario['id']} / {persona_name} r{rep}: "
                                  f"{scenario['goal'].get('task_description', '')}", flush=True)
                        t0 = time.time()
                        policy = Shown(user) if args.show else user
                        # The model failures of the scenario, armed turn by turn
                        faults = (FaultPlan(scenario["faults"], system.fault_switch)
                                  if scenario.get("faults") else None)
                        if faults:
                            faults.start()
                            policy = faults.user(policy)
                        # The calls of the model, recorded turn by turn (record_model_calls)
                        calls = CallLog(system.call_marker) if record_calls else None
                        if calls:
                            calls.start()
                            policy = calls.user(policy)
                        try:
                            run = harness.node.run(make_goal(scenario, exp), policy,
                                                   timeout=float(exp.get("dialogue_timeout", 900)))
                        finally:
                            applied = faults.finish() if faults else []
                            model_calls = calls.finish() if calls else None
                        rec = record(run, scenario, persona_name, rep, model, cname, user, seed)
                        rec["faults_applied"] = applied
                        if model_calls is not None:
                            rec["model_calls"] = model_calls
                        # The wait for the model opened by the experiment ({}: the method's
                        # defaults); mechanisms.py checks the reply wait against it
                        rec["model_wait"] = model_wait(exp)
                        if exp.get("standins"):  # a pilot: who answered the cloud calls
                            rec["standins"] = exp["standins"]
                            rec["pilot_of"] = exp.get("pilot_of")
                        rec["started_at"] = t0
                        if split_model(model)[0] == "ollama":
                            rec["system_settings"] = {"think": think, "num_ctx": num_ctx(exp),
                                                      "gpu": fit}
                        # A dropped server makes every dialogue fail fast: nothing is saved, the
                        # resume runs them again
                        down = servers_down(servers)
                        if down:
                            stop(f"The Ollama server(s) {down} do not answer (a dropped tunnel?): "
                                 f"{scenario['id']}/{persona_name} r{rep} not saved; stopping. "
                                 f"Resume when the server answers")
                        if simulator_failed(user):
                            counts["simulator_failures"] += 1
                            in_a_row += 1
                            with open(os.path.join(out, "simulator_errors.jsonl"), "a",
                                      encoding="utf-8") as f:
                                f.write(json.dumps({
                                    "time": time.time(), "model": model, "condition": cname,
                                    "scenario": scenario["id"], "persona": persona_name,
                                    "rep": rep, "status": run.status,
                                    "errors": [e["error"] for e in user.log if "error" in e]},
                                    ensure_ascii=False) + "\n")
                            print(f"   {scenario['id']:14s} {persona_name:15s} r{rep} the simulated "
                                  f"user failed: not saved (run again on resume)", flush=True)
                            if in_a_row >= MAX_SIMULATOR_FAILURES:
                                stop(f"The simulated user failed in {in_a_row} dialogues in a row "
                                     f"(credit exhausted, a quota, the API down?): see "
                                     f"{os.path.join(out, 'simulator_errors.jsonl')}. Stopping; "
                                     f"the resume runs them again")
                            continue
                        in_a_row = 0
                        path = os.path.join(run_dir, f"{scenario['id']}__{persona_name}__r{rep}.json")
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(rec, f, ensure_ascii=False, indent=1)
                        with open(os.path.join(out, "runs.jsonl"), "a", encoding="utf-8") as f:
                            f.write(json.dumps({k: rec[k] for k in (
                                "scenario", "strategy", "persona", "rep", "model", "condition",
                                "status", "success", "failure_reason", "total_turns",
                                "duration_s")}, ensure_ascii=False) + "\n")
                        counts["saved"] += 1
                        counts["succeeded" if rec["success"] else "failed"] += 1
                        print(f"   {scenario['id']:14s} {persona_name:15s} r{rep} "
                              f"{run.status:9s} turns={rec['total_turns']:2d} "
                              f"{rec['duration_s']:6.1f}s {rec['failure_reason']}", flush=True)
                finally:
                    system.stop()
            # Nothing of this run stays on the GPU (the provider loads with keep_alive -1),
            # unless --keep-loaded
            if not args.keep_loaded and (split_model(model)[0] == "ollama" or any(
                    routed_models(c, model) for c in conditions.values())):
                ollama_models.unload_all(ollama_base, [model] + [
                    m for c in conditions.values() for m in routed_models(c, model)])
        provenance.catalog("finish", out, {**counts, "models": models,
                                           "lane": args.lane or ""})
    finally:
        harness.close()


if __name__ == "__main__":
    sys.exit(main())
