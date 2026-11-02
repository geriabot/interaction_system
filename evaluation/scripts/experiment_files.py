# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""What an experiment file selects, shared by run_experiment.py and the control panel (which
does not import ROS): one definition, so that the panel counts the dialogues the runner runs."""


def select_scenarios(scenarios: list[dict], exp: dict) -> list[dict]:
    """The scenarios of the experiment: `scenario_ids` if given, then the first
    `scenarios_per_strategy` of each strategy, in the order of the file."""
    if exp.get("scenario_ids"):
        scenarios = [s for s in scenarios if s["id"] in exp["scenario_ids"]]
    if exp.get("scenarios_per_strategy"):
        per, kept = int(exp["scenarios_per_strategy"]), []
        for s in scenarios:
            if sum(1 for k in kept if k.get("strategy") == s.get("strategy")) < per:
                kept.append(s)
        scenarios = kept
    return scenarios


def condition_scenarios(scenarios: list[dict], condition: dict) -> list[dict]:
    """The scenarios a condition runs: its own `scenario_ids` (an ablation compared on the
    scenarios of its mechanism only, as in E3), else every scenario of the experiment."""
    ids = (condition or {}).get("scenario_ids")
    return [s for s in scenarios if s["id"] in ids] if ids else scenarios


def check_pairs(scenarios: list[dict], exp: dict) -> None:
    """ValueError when a pair names a scenario the experiment does not select."""
    known = {s["id"] for s in scenarios}
    missing = [sid for sid, _ in exp.get("pairs") or [] if sid not in known]
    if missing:
        raise ValueError(f"pairs: scenarios {missing} are not in {exp.get('scenarios')}")


def condition_dialogues(scenarios: list[dict], exp: dict, condition: dict) -> list[tuple]:
    """(scenario, persona) of every dialogue a condition runs (one repetition): each scenario of
    the condition with each persona of the experiment, or, when the experiment lists `pairs`
    ([scenario id, persona] each: a pilot, which pairs every scenario with one persona instead of
    crossing them), those of the pairs whose scenario the condition runs, in the pairs' order."""
    mine = condition_scenarios(scenarios, condition)
    if exp.get("pairs"):
        by_id = {s["id"]: s for s in mine}
        return [(by_id[sid], persona) for sid, persona in exp["pairs"] if sid in by_id]
    return [(s, p) for s in mine for p in exp.get("personas", [])]


# ------------------------------------------------------------------ the pilot (doc/PILOT.md)
# A pilot file names the experiment file it pilots (`pilot_of`) and changes only what it may:
# its name, which dialogues (scenarios, scenario_ids, pairs, personas, repetitions), the
# placeholders of the models (`models` and `simulator_model`: {placeholder: model}) and the free
# stand-ins of the cloud providers (`standins`). Everything else (conditions, limits, model_wait,
# simulator, launch options, timeouts) is read from the experiment file when the pilot is loaded,
# so the pilot cannot drift from it.
PILOT_KEYS = {"pilot_of", "name", "scenarios", "scenario_ids", "pairs", "personas",
              "repetitions", "models", "simulator_model", "standins"}
# The selection keys of an experiment: a pilot that gives any of them replaces them all
SELECTION_KEYS = ("scenario_ids", "scenarios_per_strategy", "pairs")
# The models a pilot may replace, by kind: every local (Ollama) model of the experiment (the
# provisional model, or the placeholder of E0a's finalists), every OpenAI or Gemini system model
# (a placeholder <pin>, or a pinned one: the pilot's stand-in answers it)
MODEL_KINDS = ("<local>", "<openai>", "<gemini>")


def model_kind(model: str) -> str:
    provider, sep, _ = model.partition("/")
    return f"<{provider}>" if sep and provider in ("openai", "gemini") else "<local>"


# Where a stand-in may answer: this computer only (a stand-in never reaches a paid API)
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
STANDIN_KEYS = {"gemini_base_url", "openai_base_url"}


def is_local_url(url: str) -> bool:
    from urllib.parse import urlparse
    return urlparse(url or "").hostname in LOCAL_HOSTS


def validate_standins(standins: dict) -> dict:
    """The stand-ins of a pilot, checked: known keys, base URLs on this computer only."""
    if not isinstance(standins, dict):
        raise ValueError("standins must be a mapping")
    unknown = sorted(set(standins) - STANDIN_KEYS)
    if unknown:
        raise ValueError(f"standins: unknown keys {unknown}; known: {sorted(STANDIN_KEYS)}")
    for key in ("gemini_base_url", "openai_base_url"):
        if key in standins and not is_local_url(standins[key]):
            raise ValueError(f"standins.{key} must be on this computer ({LOCAL_HOSTS}): a stand-in "
                             f"never reaches a paid API")
    return standins


def derive_pilot(pilot: dict, base: dict) -> dict:
    """The experiment a pilot runs: its experiment file (base) with what the pilot may change.
    ValueError for a key a pilot may not change, a placeholder the experiment does not have, or
    a stand-in that is not on this computer."""
    import copy
    unknown = sorted(set(pilot) - PILOT_KEYS)
    if unknown:
        raise ValueError(f"A pilot changes only {sorted(PILOT_KEYS - {'pilot_of'})}; not "
                         f"{unknown} (the rest is the experiment's)")
    if not pilot.get("name") or pilot["name"] == base.get("name"):
        raise ValueError("A pilot has a name of its own (its results go to their own folder)")
    exp = copy.deepcopy(base)
    exp["name"] = pilot["name"]
    if "scenarios" in pilot:
        exp["scenarios"] = pilot["scenarios"]
    if any(k in pilot for k in SELECTION_KEYS) or "scenarios" in pilot:
        for k in SELECTION_KEYS:
            exp.pop(k, None)
    for k in ("scenario_ids", "pairs", "personas", "repetitions"):
        if k in pilot:
            exp[k] = copy.deepcopy(pilot[k])
    if exp.get("pairs"):
        used = {p for _, p in exp["pairs"]}
        foreign = sorted(used - set(exp.get("personas", [])))
        if foreign:
            raise ValueError(f"pairs: personas {foreign} are not of the experiment "
                             f"{exp.get('personas')}")
        exp["personas"] = [p for p in exp["personas"] if p in used]
        exp["scenario_ids"] = list(dict.fromkeys(sid for sid, _ in exp["pairs"]))
    for kind, model in (pilot.get("models") or {}).items():
        if kind not in MODEL_KINDS:
            raise ValueError(f"models: the keys are {sorted(MODEL_KINDS)} (every local model, "
                             f"every OpenAI or Gemini system model of the experiment), not {kind}")
        if not any(model_kind(m) == kind for m in exp["models"]):
            raise ValueError(f"models: the experiment has no {kind} model ({exp['models']})")
        exp["models"] = [model if model_kind(m) == kind else m for m in exp["models"]]
    if "simulator_model" in pilot and "<" in str(exp["simulator"].get("model", "")):
        # Only a placeholder is filled (a pinned simulator keeps its name: the stand-in answers
        # any name)
        exp["simulator"]["model"] = pilot["simulator_model"]
    if "standins" in pilot:
        exp["standins"] = validate_standins(pilot["standins"])
    exp["pilot_of"] = pilot["pilot_of"]
    return exp


def load_experiment(path: str) -> dict:
    """An experiment file, or a pilot file derived from its experiment file (`pilot_of`, relative
    to the pilot file). Paths inside (scenarios, personas_file) are made relative to the pilot's
    folder when they come from the experiment file."""
    import os
    import yaml
    with open(path, encoding="utf-8") as f:
        exp = yaml.safe_load(f) or {}
    if "standins" in exp and "pilot_of" not in exp:
        raise ValueError("standins are only for a pilot (pilot_of): an experiment calls the real "
                         "providers")
    if "pilot_of" not in exp:
        return exp
    base_path = os.path.normpath(os.path.join(os.path.dirname(path), exp["pilot_of"]))
    with open(base_path, encoding="utf-8") as f:
        base = yaml.safe_load(f)
    rel = os.path.relpath(os.path.dirname(base_path), os.path.dirname(path))
    for key in ("scenarios", "personas_file"):
        if key in base:
            base[key] = os.path.normpath(os.path.join(rel, base[key]))
    if "personas_file" not in base:
        base["personas_file"] = os.path.normpath(os.path.join(rel, "personas.yaml"))
    return derive_pilot(exp, base)


# The wait for the model (docs/behaviors/endings.md, "The wait for the model"): the parameters an
# experiment may open with `model_wait`, and the node that reads each. Absent: the method's
# defaults (app_params.yaml), the same in every run. Experiments that do not measure the bound
# open it for every condition alike (E1: some of its cloud models reason before answering and
# would be cut in normal operation); E0a, E3 and E4 keep the defaults.
MODEL_WAIT_NODES = {
    "reply_budget": "interaction_orchestrator",          # 0: no budget for a reply
    "opening_budget": "interaction_orchestrator",        # 0: no budget for the opening
    "intelligence_timeout": "interaction_orchestrator",  # the hard limit of a request (> 0)
    # the same when the model may reason (> 0)
    "reasoning_intelligence_timeout": "interaction_orchestrator",
    "llm_timeout": "interaction_intelligence",           # 0: no limit of its own per call
    "reasoning_timeout": "interaction_intelligence",     # 0: a reasoning call is never cut
}
# The ones that cannot be 0: the tree waits for each request at most this long
MODEL_WAIT_POSITIVE = {"intelligence_timeout", "reasoning_intelligence_timeout"}


def model_wait(exp: dict) -> dict:
    """The parameters of the wait for the model the experiment sets (`model_wait`), as floats; {}
    when it sets none (the method's defaults). ValueError for an unknown key, a value that is not
    a number, a negative one (0 is "no limit" for the budgets and llm_timeout), a zero
    intelligence_timeout, or one of these parameters set in `limits` instead (one place only)."""
    wait = exp.get("model_wait") or {}
    if not isinstance(wait, dict):
        raise ValueError(f"model_wait must be a mapping of {sorted(MODEL_WAIT_NODES)}")
    unknown = sorted(set(wait) - set(MODEL_WAIT_NODES))
    if unknown:
        raise ValueError(f"model_wait: unknown keys {unknown}; known: {sorted(MODEL_WAIT_NODES)}")
    misplaced = sorted(set(exp.get("limits") or {}) & set(MODEL_WAIT_NODES))
    if misplaced:
        raise ValueError(f"limits: {misplaced} belong to model_wait")
    out = {}
    for key, value in wait.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"model_wait.{key}: a number of seconds, not {value!r}")
        if value < 0 or (key in MODEL_WAIT_POSITIVE and value == 0):
            raise ValueError(f"model_wait.{key}: must be "
                             + ("> 0" if key in MODEL_WAIT_POSITIVE else ">= 0 (0: no limit)"))
        # Floats: the nodes declare them as doubles and refuse an integer
        out[key] = float(value)
    return out


def model_wait_params(exp: dict) -> dict:
    """The ROS parameters of `model_wait`, per node: {node: {"ros__parameters": {...}}}."""
    out = {}
    for key, value in model_wait(exp).items():
        out.setdefault(MODEL_WAIT_NODES[key], {"ros__parameters": {}})["ros__parameters"][key] = value
    return out


def describe_model_wait(exp: dict) -> str:
    """One line for --dry-run: the wait for the model of the run."""
    wait = model_wait(exp)
    if not wait:
        return "the method's defaults"

    def shown(key):
        value = wait[key]
        return "none" if value == 0 else f"{value:g} s"
    return ", ".join(f"{k} {shown(k)}" for k in MODEL_WAIT_NODES if k in wait)
