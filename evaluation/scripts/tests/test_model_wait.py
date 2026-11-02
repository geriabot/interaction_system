# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The wait for the model an experiment may open (`model_wait`, experiment_files.py): validated,
passed to the node that reads each parameter (run_experiment.System writes them into the
params file of the launch), absent = the method's defaults; set by E1 only (decided 2026-10-30:
E0a, E3 and E4 keep the defaults)."""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from experiment_files import (describe_model_wait, model_wait,  # noqa: E402
                              model_wait_params)

EXPERIMENTS = os.path.join(os.path.dirname(__file__), "..", "..", "experiments")
E1 = {"reply_budget": 0.0, "opening_budget": 0.0, "llm_timeout": 90.0,
      "intelligence_timeout": 300.0}


def load(name):
    with open(os.path.join(EXPERIMENTS, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_absent_is_the_methods_defaults():
    assert model_wait({}) == {} and model_wait({"model_wait": None}) == {}
    assert model_wait_params({}) == {}
    assert describe_model_wait({}) == "the method's defaults"


def test_each_parameter_goes_to_the_node_that_reads_it_as_a_float():
    params = model_wait_params({"model_wait": {"reply_budget": 0, "opening_budget": 0,
                                               "llm_timeout": 90, "intelligence_timeout": 300}})
    assert params == {
        "interaction_orchestrator": {"ros__parameters": {
            "reply_budget": 0.0, "opening_budget": 0.0, "intelligence_timeout": 300.0}},
        "interaction_intelligence": {"ros__parameters": {"llm_timeout": 90.0}}}
    assert all(isinstance(v, float) for node in params.values()
               for v in node["ros__parameters"].values())
    assert describe_model_wait({"model_wait": E1}) == (
        "reply_budget none, opening_budget none, intelligence_timeout 300 s, llm_timeout 90 s")


@pytest.mark.parametrize("exp", [
    {"model_wait": {"reply_budgt": 0.0}},                 # unknown key
    {"model_wait": {"reply_budget": -1.0}},               # negative
    {"model_wait": {"llm_timeout": "90"}},                # not a number
    {"model_wait": {"llm_timeout": True}},
    {"model_wait": {"intelligence_timeout": 0.0}},        # the hard limit cannot be off
    {"model_wait": [1, 2]},
    {"limits": {"reply_budget": 0.0}},                    # one place only
])
def test_a_wrong_model_wait_is_refused(exp):
    with pytest.raises(ValueError):
        model_wait(exp)


def test_e1_and_its_variant_open_the_wait_and_the_others_keep_the_defaults():
    for name in ("e1_bounded_vs_llm_led.yaml", "e1_role_swap.yaml"):
        exp = load(name)
        assert model_wait(exp) == E1, name
        # Room for 25 slow turns: the dialogue limit is only a guard against a stuck dialogue
        assert float(exp["dialogue_timeout"]) >= 3600, name
    for name in sorted(os.listdir(EXPERIMENTS)):
        if name.endswith(".yaml") and not name.startswith("e1_"):
            exp = load(name)
            assert "model_wait" not in exp, name
            model_wait(exp)  # nor the parameters in `limits`


def test_the_runner_writes_them_into_the_params_of_the_launch(tmp_path, monkeypatch):
    """run_experiment.System: the params file it launches the system with carries the wait of the
    experiment for every condition (the LLM-led one too), and the defaults without it."""
    re = pytest.importorskip("run_experiment")
    launched = []

    class Proc:
        pid = 0

    monkeypatch.setattr(re.subprocess, "Popen", lambda args, **kw: launched.append(args) or Proc())
    app = {"interaction_intelligence": {"ros__parameters": {"llm_timeout": 15.0}},
           "interaction_orchestrator": {"ros__parameters": {
               "reply_budget": 20.0, "opening_budget": 30.0, "intelligence_timeout": 30.0}}}
    real_load = re.load
    monkeypatch.setattr(re, "load", lambda p: app if p.endswith("app_params.yaml") else real_load(p))
    exp = load("e1_role_swap.yaml")
    for cname, condition in exp["conditions"].items():
        for e, expected in ((exp, E1), ({k: v for k, v in exp.items() if k != "model_wait"},
                                        {"reply_budget": 20.0, "opening_budget": 30.0,
                                         "llm_timeout": 15.0, "intelligence_timeout": 30.0})):
            out = tmp_path / f"{cname}_{bool(e.get('model_wait'))}"
            system = re.System(e, "qwen3:8b", condition or {}, str(out))
            system.log.close()
            with open(system.params_file, encoding="utf-8") as f:
                params = yaml.safe_load(f)
            orch = params["interaction_orchestrator"]["ros__parameters"]
            intel = params["interaction_intelligence"]["ros__parameters"]
            got = {k: orch[k] for k in ("reply_budget", "opening_budget", "intelligence_timeout")}
            got["llm_timeout"] = intel["llm_timeout"]
            assert got == expected, (cname, got)
            assert orch["wait_timeout"] == 60.0  # the experiment's limits are kept
    assert len(launched) == 4


def test_a_reasoning_variant_runs_the_same_model_with_selective_reasoning(tmp_path, monkeypatch):
    """"<model>@critical" (E0a's selective-reasoning candidates): the gateway serves <model>, and
    the intelligence lets it reason before the critical questions only."""
    re = pytest.importorskip("run_experiment")

    class Proc:
        pid = 0

    monkeypatch.setattr(re.subprocess, "Popen", lambda args, **kw: Proc())
    assert re.split_model("qwen3:14b@critical") == ("ollama", "qwen3:14b")
    assert re.reasoning_of("qwen3:14b@critical") == "critical"
    assert re.reasoning_of("qwen3:14b") == "none"
    with pytest.raises(ValueError):
        re.reasoning_of("qwen3:14b@always")
    exp = load("e1_role_swap.yaml")
    condition = next(iter(exp["conditions"].values())) or {}
    for model, model_name, reasoning in (("qwen3:14b@critical", "qwen3:14b", "critical"),
                                         ("qwen3:14b", "qwen3:14b", "none")):
        system = re.System(exp, model, condition, str(tmp_path / reasoning))
        system.log.close()
        with open(system.params_file, encoding="utf-8") as f:
            params = yaml.safe_load(f)
        gateway = params["llm_manager_node"]["ros__parameters"]["ollama"]
        assert gateway["model"] == model_name
        assert params["interaction_intelligence"]["ros__parameters"]["reasoning"] == reasoning
