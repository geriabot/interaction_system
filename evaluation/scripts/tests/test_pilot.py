# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The pilot of every experiment (doc/PILOT.md), without ROS, model or paid call: the pilot files
load, are their experiments but for what a pilot may change, and cover what their header says;
the stand-in answers the evaluation's cloud clients (and the gateway's SDKs) in the providers'
shapes; the exit criteria; the cloud smoke check refuses what it must.

    python3 -m pytest scripts/tests/test_pilot.py
"""

import copy
import glob
import json
import os
import sys
import threading

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
EVAL = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
import cloud_smoke  # noqa: E402
import pilot_standin as ps  # noqa: E402
import run_pilot  # noqa: E402
from experiment_files import (condition_dialogues, derive_pilot, load_experiment,  # noqa: E402
                              select_scenarios)

EXPERIMENTS = os.path.join(EVAL, "experiments")
PILOTS = sorted(glob.glob(os.path.join(EXPERIMENTS, "pilot", "*.yaml")))
SYSTEM = "qwen2.5:32b"
# What a pilot may change; everything else must be the experiment's
CHANGED = {"name", "scenarios", "scenario_ids", "scenarios_per_strategy", "pairs", "personas",
           "repetitions", "models", "standins", "pilot_of", "simulator", "personas_file"}
DEV = ("e0a_dev.yaml", "e0a_dev_es.yaml")


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def experiment_of(path):
    pilot = load_yaml(path)
    return os.path.normpath(os.path.join(os.path.dirname(path), pilot["pilot_of"]))


def scenarios_of(path, exp):
    return select_scenarios(load_yaml(os.path.join(os.path.dirname(path), exp["scenarios"]))
                            ["scenarios"], exp)


def all_scenarios(path, exp):
    return load_yaml(os.path.join(os.path.dirname(path), exp["scenarios"]))["scenarios"]


# ---------------------------------------------------------------- the files
def test_one_pilot_per_experiment_file_in_the_order_of_the_evaluation():
    piloted = sorted(os.path.basename(experiment_of(p)) for p in PILOTS)
    experiments = sorted(os.path.basename(p) for p in glob.glob(os.path.join(EXPERIMENTS,
                                                                             "*.yaml"))
                         if "conditions" in (load_yaml(p) or {}))
    assert piloted == experiments
    assert list(run_pilot.pilot_files()) == run_pilot.ORDER


@pytest.mark.parametrize("path", PILOTS, ids=os.path.basename)
def test_a_pilot_is_its_experiment_but_for_what_a_pilot_may_change(path):
    exp = load_experiment(path)
    base = load_yaml(experiment_of(path))
    for key in set(base) | set(exp):
        if key not in CHANGED:
            assert exp.get(key) == base.get(key), key
    sim = {k: v for k, v in exp["simulator"].items() if k != "model"}
    assert sim == {k: v for k, v in base["simulator"].items() if k != "model"}
    if "<" not in base["simulator"]["model"]:
        assert exp["simulator"]["model"] == base["simulator"]["model"]
    # Every model is the pilot's system model (local, or a cloud cell answered by it)
    assert len(exp["models"]) == len(base["models"])
    assert all(m.split("/")[-1] == SYSTEM for m in exp["models"])
    assert [m.split("/")[0] for m in exp["models"] if "/" in m] == \
        [m.split("/")[0] for m in base["models"] if "/" in m]
    assert not any("<" in m for m in exp["models"] + [exp["simulator"]["model"]])
    assert exp["repetitions"] == 1 and exp["name"].endswith("_pilot")
    assert all(ps_url.startswith("http://127.0.0.1:") for ps_url in exp["standins"].values())
    # The personas of the pilot are the experiment's
    assert set(exp["personas"]) <= set(base["personas"])


@pytest.mark.parametrize("path", PILOTS, ids=os.path.basename)
def test_a_pilot_covers_every_cell_strategy_and_persona_of_its_experiment(path):
    exp, base = load_experiment(path), load_yaml(experiment_of(path))
    plan = run_pilot.planned(path)
    cells = {(m, c) for m in exp["models"] for c in exp["conditions"]}
    assert {(m, c) for m, c, *_ in plan} == cells           # every condition x model cell
    base_dir = os.path.dirname(experiment_of(path))
    base_scen = select_scenarios(load_yaml(os.path.join(base_dir, base["scenarios"]))
                                 ["scenarios"], base)
    pilot_scen = {s["id"]: s for s in all_scenarios(path, exp)}
    strategies = {pilot_scen[s]["strategy"] for _, _, s, _, _ in plan}
    assert strategies == {s["strategy"] for s in base_scen}  # every strategy
    assert {p for *_, p, _ in plan} == set(base["personas"])  # every persona type
    assert len(plan) == len(set(plan))


def test_the_machinery_that_does_not_depend_on_the_scenario_runs_on_development_ones():
    for path in PILOTS:
        exp = load_experiment(path)
        dev = os.path.basename(exp["scenarios"]) in DEV
        test_set = any(k in os.path.basename(path) for k in ("e3_mechanisms", "e3_failures",
                                                             "e4_sim"))
        assert dev != test_set, path


def _plan_scenarios(name):
    path = run_pilot.pilot_files()[name]
    exp = load_experiment(path)
    by = {s["id"]: s for s in all_scenarios(path, exp)}
    return path, exp, [(by[s], c) for _, c, s, _, _ in run_pilot.planned(path)]


def test_e3_and_e4_pilots_cover_every_event_fault_detour_and_noise_level():
    for name, base_file in (("e3_mechanisms_pilot", "scenarios/e3.yaml"),
                            ("e4_sim_es_pilot", "scenarios/e4_es.yaml")):
        _, _, runs = _plan_scenarios(name)
        every = load_yaml(os.path.join(EXPERIMENTS, base_file))["scenarios"]
        kinds = {e["kind"] for s in every for e in s.get("events") or []}
        assert {e["kind"] for s, _ in runs for e in s.get("events") or []} == kinds, name
        detours = {e["detour"] for s in every for e in s.get("events") or [] if e.get("detour")}
        assert {e["detour"] for s, _ in runs for e in s.get("events") or []
                if e.get("detour")} == detours, name
    _, _, runs = _plan_scenarios("e3_mechanisms_pilot")
    planned = {(s["id"], c) for s, c in runs}
    # Every strategy-check of the m_ scenarios but the second teach-back (the same checks)
    m_all = {s["id"] for s in load_yaml(os.path.join(EXPERIMENTS, "scenarios", "e3.yaml"))
             ["scenarios"] if s["id"].startswith("m_")}
    assert m_all - {s for s, _ in planned} == {"m_ex_teach_back_two_wrong"}
    _, _, runs = _plan_scenarios("e3_failures_pilot")
    every = load_yaml(os.path.join(EXPERIMENTS, "scenarios", "e3_failures.yaml"))["scenarios"]
    assert {f["kind"] for s, _ in runs for f in s["faults"]} == \
        {f["kind"] for s in every for f in s["faults"]}
    assert {c for _, c in runs} == {"bounded", "llm_led"}
    _, exp, _ = _plan_scenarios("e3_noise_pilot")
    assert sorted((c.get("asr_noise") or {}).get("wer", 0.0) for c in exp["conditions"].values()
                  if c.get("ablation")) == [0.0, 0.1, 0.2, 0.3]


def test_both_languages_and_the_dialogue_counts():
    names = run_pilot.pilot_files()
    assert load_experiment(names["e0a_dialogues_pilot"])["language"] == "English"
    assert load_experiment(names["e0a_dialogues_es_pilot"])["language"] == "Spanish"
    assert load_experiment(names["e0b_judge_dialogues_es_pilot"])["language"] == "Spanish"
    counts = {n: len(run_pilot.planned(p)) for n, p in names.items()}
    assert counts == {"e0a_dialogues_pilot": 7, "e0a_dialogues_es_pilot": 7,
                      "e0b_judge_dialogues_pilot": 14, "e0b_judge_dialogues_es_pilot": 14,
                      "e1_bounded_vs_llm_led_pilot": 71, "e1_role_swap_pilot": 28,
                      "e3_mechanisms_pilot": 24, "e3_noise_pilot": 56, "e3_failures_pilot": 12,
                      "e4_sim_es_pilot": 7}
    # E1: 21 per cell of the 2x2, every development scenario once; the bounded cloud cell keeps
    # only the pairs of its three personas, as in E1
    plan = run_pilot.planned(names["e1_bounded_vs_llm_led_pilot"])
    per_cell = {}
    for m, c, *_ in plan:
        per_cell[(m, c)] = per_cell.get((m, c), 0) + 1
    assert sorted(per_cell.values()) == [8, 21, 21, 21] and len(per_cell) == 4
    assert per_cell[("openai/qwen2.5:32b", "bounded")] == 8


# ---------------------------------------------------------------- the derivation
BASE = {"name": "e", "scenarios": "s.yaml", "personas": ["a", "b"], "repetitions": 3,
        "models": ["qwen3:8b", "openai/<pin>"], "conditions": {"bounded": {}},
        "simulator": {"provider": "openai", "model": "<pin>"}, "scenarios_per_strategy": 2,
        "limits": {"wait_timeout": 60.0}}


def test_derive_changes_only_what_a_pilot_may_change():
    exp = derive_pilot({"pilot_of": "../e.yaml", "name": "e_pilot", "pairs": [["x", "b"]],
                        "repetitions": 1, "models": {"<local>": SYSTEM, "<openai>": "openai/q"},
                        "simulator_model": "user", "standins": {
                            "openai_base_url": "http://127.0.0.1:1/v1"}}, BASE)
    assert exp["models"] == [SYSTEM, "openai/q"] and exp["simulator"]["model"] == "user"
    assert exp["personas"] == ["b"] and exp["scenario_ids"] == ["x"]
    assert "scenarios_per_strategy" not in exp and exp["limits"] == BASE["limits"]
    for bad, why in (({"conditions": {}}, "changes only"), ({"name": "e"}, "name of its own"),
                     ({"pairs": [["x", "z"]]}, "not of the experiment"),
                     ({"models": {"<gemini>": "g"}}, "no <gemini>"),
                     ({"models": {"qwen3:8b": "q"}}, "the keys are"),
                     ({"standins": {"openai_base_url": "https://api.openai.com/v1"}},
                      "this computer")):
        pilot = {"pilot_of": "../e.yaml", "name": "e_pilot", **bad}
        with pytest.raises(ValueError, match=why):
            derive_pilot(pilot, copy.deepcopy(BASE))


def test_an_experiment_file_cannot_have_standins(tmp_path):
    path = tmp_path / "e.yaml"
    path.write_text(yaml.safe_dump({**BASE, "standins": {"gemini_base_url": "http://127.0.0.1"}}))
    with pytest.raises(ValueError, match="only for a pilot"):
        load_experiment(str(path))
    for exp_path in glob.glob(os.path.join(EXPERIMENTS, "*.yaml")):
        assert "standins" not in (load_yaml(exp_path) or {}), exp_path


def test_pairs_replace_the_cross_product_per_condition():
    scenarios = [{"id": "x"}, {"id": "y"}]
    exp = {"personas": ["a", "b"], "pairs": [["y", "a"], ["x", "b"]]}
    assert [(s["id"], p) for s, p in condition_dialogues(scenarios, exp, {})] == \
        [("y", "a"), ("x", "b")]
    assert [(s["id"], p) for s, p in condition_dialogues(scenarios, exp,
                                                         {"scenario_ids": ["x"]})] == [("x", "b")]
    assert len(condition_dialogues(scenarios, {"personas": ["a", "b"]}, {})) == 4


def test_a_cell_can_run_a_subset_of_the_personas():
    scenarios = [{"id": "x"}, {"id": "y"}]
    exp = {"personas": ["a", "b", "c"]}
    cond = {"personas_by_kind": {"<openai>": ["c", "a"]}}
    # The cloud model's cell: the subset, in the experiment's order; the local model's: all
    assert [(s["id"], p) for s, p in condition_dialogues(scenarios, exp, cond, "openai/gpt-x")] \
        == [("x", "a"), ("x", "c"), ("y", "a"), ("y", "c")]
    assert len(condition_dialogues(scenarios, exp, cond, "qwen3:8b")) == 6
    assert len(condition_dialogues(scenarios, exp, cond)) == 6
    # A pilot's pairs keep only the personas of the cell
    pilot = dict(exp, pairs=[["x", "a"], ["y", "b"]])
    assert [(s["id"], p) for s, p in condition_dialogues(scenarios, pilot, cond,
                                                         "openai/qwen2.5:32b")] == [("x", "a")]
    with pytest.raises(ValueError, match="not of the experiment"):
        condition_dialogues(scenarios, exp, {"personas_by_kind": {"<openai>": ["z"]}}, "m")
    with pytest.raises(ValueError, match="the keys are"):
        condition_dialogues(scenarios, exp, {"personas_by_kind": {"cloud": ["a"]}}, "m")


def test_e1_runs_the_bounded_cloud_cell_on_three_personas():
    exp = load_experiment(os.path.join(EXPERIMENTS, "e1_bounded_vs_llm_led.yaml"))
    with open(os.path.join(EXPERIMENTS, exp["scenarios"]), encoding="utf-8") as f:
        scenarios = select_scenarios(yaml.safe_load(f)["scenarios"], exp)
    count = {(m, c): len(condition_dialogues(scenarios, exp, cond, m))
             for m in exp["models"] for c, cond in exp["conditions"].items()}
    local, cloud = exp["models"]
    assert count == {(local, "bounded"): 448, (local, "llm_led"): 448,
                     (cloud, "bounded"): 168, (cloud, "llm_led"): 448}


# ---------------------------------------------------------------- the stand-in
def test_the_synthetic_user_follows_the_goal_and_the_robot():
    from interaction_sim.goal_items import items_block
    from interaction_sim.llm_user import PROMPT
    items = [{"id": "drink", "value": "latte"}, {"id": "size", "value": "large", "then": "small"},
             {"id": "name", "value": "Ana"}]

    def prompt(robot, said=(), language="English"):
        lines = [f"You: {s}" for s in said] or ["(the robot speaks first)"]
        return PROMPT.format(persona="An adult.", goal="Order a coffee.", language=language,
                             transcript="\n".join(lines), robot=robot,
                             items=items_block(items), answer="")
    assert ps.user_line(prompt("What size would you like?"))["text"] == "large."
    assert ps.user_line(prompt("Hello, what can I get you?"))["revealed"] == [
        {"id": "drink", "value": "latte"}]
    said = ["latte.", "large.", "Ana."]
    assert ps.user_line(prompt("Thanks.", said))["text"] == "Actually, make it small."
    assert ps.user_line(prompt("A large latte for Ana, is that right?", said + ["small"]))[
        "text"] == "Yes, that's right."
    assert ps.user_line(prompt("Do you want to stop the order?"))["text"] == "No, let's go on."
    assert ps.user_line(prompt("Thank you, goodbye!", said))["action"] == "end"
    assert ps.user_line(prompt("Small, medium or large?"))["text"] == "large."
    assert ps.user_line(prompt("Next question.", ["latte."]))["text"] == "large."   # in order
    assert ps.user_line(prompt("¿Es correcto?", said + ["small"], "Spanish"))["text"] == \
        "Sí, correcto."


def test_the_synthetic_user_finds_the_goal_items_of_a_scenario_by_its_goal():
    from interaction_sim.llm_user import PROMPT
    import yaml
    with open(os.path.join(ps.SCENARIO_DIR, "e0a_dev.yaml"), encoding="utf-8") as f:
        sc = next(s for s in yaml.safe_load(f)["scenarios"] if s["id"] == "sf_dev_cake")
    prompt = PROMPT.format(persona="An adult.", goal=sc["user_goal"].strip(), language="English",
                           transcript="(the robot speaks first)", robot="Which flavour?",
                           items="", answer="")
    assert ps.parse_user_prompt(prompt)["items"][0] == {"id": "flavour", "value": "lemon",
                                                       "then": "almond"}


def test_synthetic_answers_follow_the_schema():
    schema = {"type": "OBJECT", "properties": {"in_frame": {"type": "BOOLEAN"},
                                               "violation": {"type": "STRING",
                                                             "enum": ["none", "x"]},
                                               "unsupported_claims": {"type": "ARRAY",
                                                                      "items": {"type": "STRING"}}}}
    out = ps.synthetic(schema, "seed")
    assert isinstance(out["in_frame"], bool) and out["violation"] in ("none", "x")
    assert isinstance(out["unsupported_claims"], list)
    assert out == ps.synthetic(schema, "seed")
    assert {ps.synthetic({"type": "boolean"}, str(i)) for i in range(40)} == {True, False}


@pytest.fixture
def standin(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    server = ps.make_server(0, log_path=str(log))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from interaction_sim.cloud_llm import PILOT_ENV
    monkeypatch.setenv(PILOT_ENV["gemini"], f"http://127.0.0.1:{port}")
    monkeypatch.setenv(PILOT_ENV["openai"], f"http://127.0.0.1:{port}/v1")
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "pilot-standin-no-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    yield port, log
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_the_simulated_user_and_the_judge_reach_the_standin_by_their_own_client(standin,
                                                                               provider,
                                                                               monkeypatch):
    from interaction_sim.cloud_llm import ask_json
    from interaction_sim.llm_user import LlmUser
    port, log = standin
    # The simulated user only speaks: the synthetic user finds the goal items by the goal
    monkeypatch.setattr(ps, "_GOALS", {ps._norm("Order a large latte."): [
        {"id": "size", "value": "large"}]})
    user = LlmUser(persona="An adult.", goal="Order a large latte.", model="m",
                   provider=provider, allow_cloud=True,
                   goal_items=[{"id": "size", "value": "large"}])
    turn = user("What size?", [{"role": "robot", "text": "What size?"}])
    assert turn.text == "large." and "revealed" not in user.log[-1]
    assert user.log[-1]["model_version"] == "m-pilot-standin"
    raw, meta = ask_json(provider, "judge", "Judge it", {
        "type": "object", "properties": {"in_frame": {"type": "boolean"}},
        "required": ["in_frame"]})
    assert isinstance(json.loads(raw)["in_frame"], bool) and meta["usage"]["prompt_tokens"] > 0
    calls = [json.loads(line) for line in open(log)]
    assert [c["role"] for c in calls] == ["user", "synthetic"]


def test_a_standin_off_this_computer_is_refused(monkeypatch):
    from interaction_sim.cloud_llm import PILOT_ENV, provider_url
    monkeypatch.setenv(PILOT_ENV["gemini"], "https://example.org")
    with pytest.raises(ValueError, match="this computer"):
        provider_url("gemini", "m")
    monkeypatch.delenv(PILOT_ENV["gemini"])
    assert provider_url("gemini", "m").startswith("https://generativelanguage.googleapis.com/")


def test_the_gateways_sdks_reach_the_standin(standin):
    """The gateway's providers (google-genai and openai SDKs) with their base URL on the stand-in:
    a schema-constrained call and a streamed one each, synthetic answers (no model)."""
    pytest.importorskip("google.genai")
    pytest.importorskip("openai")
    providers = pytest.importorskip("ros2_llm_manager.providers.gemini_provider")
    from ros2_llm_manager.providers.openai_provider import OpenAIProvider
    port, _ = standin
    schema = {"type": "object", "properties": {"value": {"type": "string"}},
              "required": ["value"]}
    for backend in (providers.GeminiProvider("k", 30, "gemini-x",
                                             base_url=f"http://127.0.0.1:{port}"),
                    OpenAIProvider("k", 30, "gpt-x", base_url=f"http://127.0.0.1:{port}/v1")):
        ok, msg = backend.connect()
        assert ok, msg
        result = backend.generate(backend.default_model, [{"role": "system", "content": "s"},
                                                          {"role": "user", "content": "q"}],
                                  max_tokens=40, format_schema=schema)
        assert result.success and json.loads(result.text)["value"] == "pilot"
        result = backend.generate(backend.default_model, [{"role": "user", "content": "q"}],
                                  max_tokens=40, stream_callback=lambda token: True)
        assert result.success and result.text == ps.SYNTHETIC_TEXT
        assert result.prompt_tokens > 0


# ---------------------------------------------------------------- the exit criteria
def _record(scenario, condition="bounded", **extra):
    rec = {"scenario": scenario, "strategy": "quiz", "persona": "cooperative", "rep": 1,
           "seed": 1, "model": SYSTEM, "condition": condition, "language": "English",
           "status": "succeeded", "success": True, "outcome": "completed",
           "failure_reason": "", "data": {}, "total_turns": 2, "duration_s": 3.0,
           "expect": {}, "events": [], "faults": [], "goal": {}, "user_goal": "",
           "transcript": [{"role": "robot", "text": "Hi", "t": 0.0},
                          {"role": "user", "text": "Hello", "t": 1.0},
                          {"role": "robot", "text": "Bye", "t": 2.0}],
           "feedback": [], "skills": [], "barge_ins": [],
           "user_log": [{"turn": 1, "text": "Hello", "model_version": "g-pilot-standin"}],
           "simulator": {"provider": "gemini", "model": "g", "errors": 0},
           "goal_items": [], "model_wait": {}, "faults_applied": []}
    rec.update(extra)
    return rec


def _fake_pilot(tmp_path, monkeypatch, recs, plan):
    import pilot_check
    os.makedirs(tmp_path, exist_ok=True)
    path = tmp_path / "p.yaml"
    path.write_text("")
    monkeypatch.setattr(run_pilot, "pilot_files", lambda: {"x_pilot": str(path)})
    monkeypatch.setattr(run_pilot, "planned", lambda p: plan)
    monkeypatch.setattr(pilot_check, "load_experiment",
                        lambda p: {"pilot_of": "../x.yaml", "name": "x_pilot"})
    folder = tmp_path / "root" / "x_pilot"
    os.makedirs(folder / "runs" / "m" / "bounded")
    (folder / "provenance.json").write_text("{}")
    (folder / "experiment.yaml").write_text("")
    (folder / "standin_calls.jsonl").write_text(json.dumps(
        {"provider": "gemini", "role": "user", "model": "g", "prompt_tokens": 900,
         "completion_tokens": 20}) + "\n")
    for i, rec in enumerate(recs):
        (folder / "runs" / "m" / "bounded" / f"{i}.json").write_text(json.dumps(rec))
    return pilot_check.check_pilot("x_pilot", str(path), str(tmp_path / "root"))


def _verdicts(report):
    return {c["criterion"]: c["verdict"] for c in report["criteria"]}


def test_the_check_passes_a_clean_pilot_and_fails_what_it_must(tmp_path, monkeypatch):
    plan = [(SYSTEM, "bounded", "q1", "cooperative", 1)]
    v = _verdicts(_fake_pilot(tmp_path, monkeypatch, [_record("q1")], plan))
    assert v["dialogues"] == v["system errors"] == v["records"] == "OK"
    assert v["calls cut"] == "OK" and v["stand-ins"] == "OK" and v["faults"] == "n/a"


def test_the_check_fails_a_missing_dialogue_a_system_error_and_a_fault_not_injected(
        tmp_path, monkeypatch):
    plan = [(SYSTEM, "bounded", "q1", "cooperative", 1), (SYSTEM, "bounded", "q2",
                                                          "cooperative", 1)]
    recs = [_record("q1", status="timeout", outcome="error",
                    faults=[{"turn": 1, "kind": "error"}])]
    report = _fake_pilot(tmp_path, monkeypatch, recs, plan)
    v = _verdicts(report)
    assert v["dialogues"] == "FAIL" and v["faults"] == "FAIL"
    assert v["system errors"] == "OK"     # injected: the error is the planned one
    recs = [_record("q1", status="timeout"), _record("q2", skills=[
        {"skill": "begin", "dialogue_id": "d"},
        {"skill": "x", "dialogue_id": "d", "llm_timeouts": 2}])]
    v = _verdicts(_fake_pilot(tmp_path / "b", monkeypatch, recs, plan))
    assert v["system errors"] == "FAIL" and v["calls cut"] == "LOOK"


def test_the_check_fails_a_call_that_did_not_go_to_the_standin(tmp_path, monkeypatch):
    plan = [(SYSTEM, "bounded", "q1", "cooperative", 1)]
    rec = _record("q1", user_log=[{"turn": 1, "model_version": "gemini-2.5-flash-lite-001"}])
    assert _verdicts(_fake_pilot(tmp_path, monkeypatch, [rec], plan))["stand-ins"] == "FAIL"


# ---------------------------------------------------------------- the cloud smoke check
def test_the_smoke_check_calls_every_cloud_model_and_refuses_standins_and_pins(monkeypatch):
    exp = load_yaml(os.path.join(EXPERIMENTS, "e1_role_swap.yaml"))
    calls = cloud_smoke.planned_calls(exp, "gemini/gemini-2.5-flash")
    assert [r for r, _, _ in calls] == ["system (schema)", "system (streamed)",
                                        "simulated user", "judge"]
    assert any("not pinned" in p for p in cloud_smoke.problems(exp))
    exp = {"models": ["qwen3:8b", "openai/gpt-x"], "simulator": {"provider": "gemini",
                                                                 "model": "g"}}
    assert cloud_smoke.problems(exp) == []
    monkeypatch.setenv("INTERACTION_PILOT_OPENAI_URL", "http://127.0.0.1:1/v1")
    assert any("real APIs" in p for p in cloud_smoke.problems(exp))
    assert cloud_smoke.planned_calls({"models": ["qwen3:8b"], "simulator": {}}) == []


def test_the_e0b_chain_reports_with_the_local_judge_before_the_cloud_judges(tmp_path, monkeypatch):
    """The pilot's E0b chain follows E0b: local labels, sheets, the report with the local judge;
    then (always in the free pilot) every cloud judge on the sheets and the report again."""
    import run_e0b
    folder = tmp_path / "e0b_pilot"
    os.makedirs(folder / "e0")
    cmds = []

    def fake_run(cmd, env, log):
        cmds.append(cmd)
        if "sheets" in cmd:
            for name in ("a", "b"):
                (folder / "e0" / f"sheet_{name}.csv").write_text(
                    "id,in_frame (1/0),unsupported (1/0)\n1,,\n2,,\n")
            (folder / "e0" / "key.json").write_text("{}")
        return 0
    monkeypatch.setattr(run_pilot, "run", fake_run)
    assert run_pilot.e0b_chain("e0b_judge_dialogues_pilot", str(folder), {}, None) == 0
    kinds = [c[2] if "judge_validation.py" in c[1] else
             c[c.index("--tag") + 1] for c in cmds]
    cloud = [t for _, _, t in run_e0b.CLOUD_JUDGES + run_e0b.OPENAI_JUDGES]
    assert kinds == ["split", "qwen2_5_32b", "qwen2_5_32b", "sheets", "report"] + cloud + \
        ["report"]
    assert all("--only-key" in c and "--allow-cloud" in c for c in cmds if
               "--tag" in c and c[c.index("--tag") + 1] in cloud)
    filled = (folder / "e0" / "sheet_a_synthetic.csv").read_text().splitlines()
    assert filled[1].split(",")[1] in ("0", "1")
