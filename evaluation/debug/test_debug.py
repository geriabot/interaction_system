# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The tools of the debugging before E0a (doc/DEBUGGING.md), without ROS or a model: the
coverage matrix of layer 1, the debugging files of layer 2, the attribution tool (triage.py) on
synthetic dialogue records, and the plan of an oracle replay (replay.py)."""

import json
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(EVAL, "scripts"))
sys.path.insert(0, os.path.join(EVAL, "simulation", "interaction_sim"))
import coverage_matrix as coverage  # noqa: E402
import replay  # noqa: E402
import triage  # noqa: E402
from experiment_files import condition_scenarios, select_scenarios  # noqa: E402

DEBUG_FILES = os.path.join(EVAL, "experiments", "debug")


# ==== LAYER 1: THE COVERAGE MATRIX ====


def test_every_test_of_the_matrix_exists_and_no_row_has_a_gap():
    with open(coverage.MATRIX, encoding="utf-8") as f:
        matrix = yaml.safe_load(f)
    errors, rows = coverage.check(matrix)
    assert errors == []
    assert [r["id"] for r in rows if r["gap"]] == []


def test_the_matrix_has_every_mechanism_of_the_audit():
    with open(coverage.MATRIX, encoding="utf-8") as f:
        ids = {m["id"] for m in yaml.safe_load(f)["mechanisms"]}
    audit = os.path.join(EVAL, "..", "paper", "MECHANISM_COVERAGE_AUDIT_2026-10-30.md")
    if not os.path.exists(audit):
        pytest.skip("the audit is in the private paper folder")
    import re
    with open(audit, encoding="utf-8") as f:
        rows = set(re.findall(r"^\| ([A-Z]+[0-9]+b?) \|", f.read(), re.M))
    # Planned or not part of the method (the barge-in is covered; the planned parts are not
    # implemented; the ablations, the noise and the injector are evaluation tools)
    outside = {"PL1", "PL2", "C12", "EX9", "SYS2", "SYS3", "SYS5", "SYS6", "SYS7", "SYS8"}
    assert rows - ids - outside == set()


# ==== LAYER 2: THE DEBUGGING FILES ====


def debug_files():
    return sorted(f for f in os.listdir(DEBUG_FILES) if f.endswith(".yaml"))


@pytest.mark.parametrize("name", debug_files())
def test_a_debugging_file_runs_the_method_locally_with_the_calls_recorded(name):
    with open(os.path.join(DEBUG_FILES, name), encoding="utf-8") as f:
        exp = yaml.safe_load(f)
    assert exp["name"] == name[:-5] and exp["name"].startswith("debug_")
    # The two families the prompts are fixed with, and the held-out family (never used to fix them)
    assert exp["models"] == ["qwen3:8b", "llama3.1:8b", "mistral-nemo:12b"]
    assert list(exp["conditions"]) == ["bounded"] and not exp["conditions"]["bounded"]
    assert exp["repetitions"] == 1 and exp["record_model_calls"] is True
    assert exp["simulator"] == {"provider": "ollama", "model": "gemma3:12b", "temperature": 0.7}
    with open(os.path.join(DEBUG_FILES, exp["personas_file"]), encoding="utf-8") as f:
        personas = yaml.safe_load(f)["personas"]
    assert set(exp["personas"]) <= set(personas)
    with open(os.path.join(DEBUG_FILES, exp["scenarios"]), encoding="utf-8") as f:
        scenarios = yaml.safe_load(f)["scenarios"]
    assert select_scenarios(scenarios, exp)
    assert exp["language"] == ("Spanish" if exp["scenarios"].endswith("_es.yaml") else "English")


def test_the_debugging_files_cover_what_the_spec_asks():
    files = {}
    for name in debug_files():
        with open(os.path.join(DEBUG_FILES, name), encoding="utf-8") as f:
            exp = yaml.safe_load(f)
        files[os.path.basename(exp["scenarios"])] = exp
    assert set(files) == {"e0a_dev.yaml", "e0a_dev_es.yaml", "e3.yaml", "e3_es.yaml",
                          "e3_failures.yaml"}
    total = 0
    for exp in files.values():
        with open(os.path.join(DEBUG_FILES, exp["scenarios"]), encoding="utf-8") as f:
            scenarios = select_scenarios(yaml.safe_load(f)["scenarios"], exp)
        total += len(condition_scenarios(scenarios, {})) * len(exp["personas"])
    assert total == 221  # per system model (doc/DEBUGGING.md)


def test_the_debugging_files_are_not_experiments_of_the_panel():
    # The control panel lists experiments/*.yaml only
    top = os.listdir(os.path.join(EVAL, "experiments"))
    assert not any(f.startswith("debug_") for f in top)


# ==== THE ATTRIBUTION TOOL ====


def record(scenario="sf_x", persona="cooperative", model="qwen3:8b", data=None, calls=True,
           language="English"):
    rec = {
        "scenario": scenario, "strategy": "slot_filling", "persona": persona, "rep": 1,
        "seed": 1, "model": model, "condition": "bounded", "language": language,
        "status": "succeeded", "success": True, "outcome": "completed", "failure_reason": "",
        "data": data if data is not None else {"drink": "tea"}, "total_turns": 2,
        "duration_s": 10.0, "expect": {"outcome": "success", "frame": {"drink": "tea"}},
        "events": [], "faults": [], "goal": {"task_description": "Take a drink order"},
        "user_goal": "You want a tea.", "goal_items": [],
        "transcript": [
            {"role": "robot", "text": "What would you like?", "t": 0.5},
            {"role": "user", "text": "A tea, please.", "t": 1.0},
            {"role": "robot", "text": "Hmm…", "t": 3.0, "filler": True},
            {"role": "robot", "text": "Noted. Is a tea right?", "t": 4.0},
            {"role": "user", "text": "Yes.", "t": 5.0},
            {"role": "robot", "text": "Done.", "t": 6.0}],
        "skills": [{"skill": "slot_filling.open", "label": "", "success": True, "t": 0.4},
                   {"skill": "cancel_check", "label": "none", "success": True, "t": 1.5},
                   {"skill": "slot_filling.update", "label": "values", "success": True,
                    "t": 3.9}],
        "user_log": [{"turn": 1, "robot": "What would you like?", "text": "A tea, please.",
                      "action": "say"},
                     {"turn": 2, "robot": "Noted. Is a tea right?", "text": "Yes.",
                      "action": "say"}],
        "feedback": [], "barge_ins": [], "faults_applied": [],
    }
    if calls:
        rec["model_calls"] = [
            {"turn": 0, "skill": "slot_filling.open", "question": "get_opening",
             "prompt": "Open", "schema": None, "max_tokens": 100,
             "answer": "What would you like?", "error": None, "latency_s": 0.4, "t": 1.0},
            {"turn": 1, "skill": "cancel_check", "question": "detect_cancel_intent",
             "prompt": "Is 'A tea, please.' a request to stop?", "schema": {"type": "object"},
             "max_tokens": 30, "answer": '{"cancel": false}', "error": None,
             "latency_s": 0.2, "t": 2.0},
            {"turn": 1, "skill": "slot_filling.update", "question": "extract_turn",
             "prompt": "Extract", "schema": {"type": "object"}, "max_tokens": 300,
             "answer": '{"act": "values", "extractions": [{"slot": "drink", "value": "tea"}]}',
             "error": None, "latency_s": 0.9, "t": 2.5},
            {"turn": 2, "skill": "slot_filling.confirm", "question": "classify_intent",
             "prompt": "Confirm?", "schema": {"type": "object"}, "max_tokens": 200,
             "answer": None, "error": "RuntimeError: down", "latency_s": 0.1, "t": 3.0}]
    return rec


def results_with(tmp_path, recs):
    for rec in recs:
        d = tmp_path / "runs" / rec["model"].replace(":", "_") / rec["condition"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['scenario']}__{rec['persona']}__r1.json").write_text(json.dumps(rec))
    (tmp_path / "experiment_local.yaml").write_text(yaml.safe_dump({
        "name": "debug_dev_en", "scenarios": "../scenarios/e0a_dev.yaml",
        "personas": ["cooperative"], "models": ["qwen3:8b"], "language": "English",
        "conditions": {"bounded": {}}, "simulator": {"model": "gemma3:12b"},
        "record_model_calls": True}))
    return str(tmp_path)


def test_a_failed_dialogue_is_flagged_and_a_pass_is_spot_checked(tmp_path):
    results = results_with(tmp_path, [record("sf_wrong", data={"drink": "coffee"}),
                                      record("sf_right")])
    data = triage.flag(results, spot=1)
    by_id = {d["scenario"]: d for d in data["dialogues"]}
    assert by_id["sf_wrong"]["why"][0].startswith("task_success false (content_ok")
    assert by_id["sf_right"]["why"] == ["spot-check of a pass"]
    sheet = (tmp_path / "triage" / by_id["sf_wrong"]["sheet"]).read_text()
    assert "### Turn 1" in sheet and "**USER:** A tea, please." in sheet
    assert "`extract_turn` in `slot_filling.update`, 0.9 s" in sheet
    assert "cancel_check→none" in sheet and "slot_filling.update→values" in sheet
    assert "ERROR RuntimeError: down" in sheet
    assert "(fillers: Hmm…)" in sheet and "**ROBOT:** Noted. Is a tea right?" in sheet


def test_a_descriptive_column_is_not_flagged(monkeypatch):
    # key_points_from_llm False only says that the request gave the key points: no failure
    monkeypatch.setattr(triage.strategy_checks, "columns",
                        lambda rec: {"key_points": 3, "key_points_from_llm": False,
                                     "tb_marked_wrong": False})
    rec = record()
    row = triage.score_runs.score(rec)
    why = triage.flags_of(rec, row)
    assert "strategy check tb_marked_wrong false" in why
    assert not any("key_points_from_llm" in w for w in why)


def test_the_classification_is_kept_and_the_exit_criterion_counted(tmp_path):
    results = results_with(tmp_path, [record("sf_wrong", data={"drink": "coffee"}),
                                      record("sf_right")])
    triage.flag(results)
    holds, lines = triage.summary(results)
    assert not holds and "unclassified 2" in lines[0]
    path = tmp_path / "triage" / triage.SHEET
    sheet = yaml.safe_load(path.read_text())
    for d in sheet["dialogues"]:
        d["class"] = "M" if d["scenario"] == "sf_wrong" else "OK"
    path.write_text(yaml.safe_dump(sheet))
    triage.flag(results)  # again: what the reviewer filled is kept
    holds, lines = triage.summary(results)
    assert holds and "M 1" in lines[0] and "OK 1" in lines[0]
    sheet = yaml.safe_load(path.read_text())
    sheet["dialogues"][0]["class"] = "P"
    path.write_text(yaml.safe_dump(sheet))
    holds, lines = triage.summary(results)
    assert not holds and any(line.strip().startswith("P ") for line in lines)


def test_without_recorded_calls_the_sheet_says_so(tmp_path):
    rec = record(calls=False)
    assert "were not recorded" in triage.sheet(rec)


def test_the_cross_family_comparison(tmp_path):
    results = results_with(tmp_path, [record("sf_x", model="qwen3:8b"),
                                      record("sf_x", model="llama3.1:8b"),
                                      record("sf_alone", model="qwen3:8b")])
    written = triage.compare(results)
    assert len(written) == 1
    text = open(written[0]).read()
    assert "llama3.1:8b vs qwen3:8b" in text and "`extract_turn` →" in text


# ==== THE ORACLE REPLAY ====


def test_the_replay_pins_the_answers_and_corrects_one(tmp_path):
    results = results_with(tmp_path, [record("sf_x")])
    path = os.path.join(results, "runs", "qwen3_8b", "bounded", "sf_x__cooperative__r1.json")
    correct = '{"act": "values", "extractions": [{"slot": "drink", "value": "tea"}]}'
    exp_path = replay.plan(path, 1, "extract_turn", correct)
    with open(exp_path, encoding="utf-8") as f:
        exp = yaml.safe_load(f)
    assert exp["models"] == ["qwen3:8b"] and exp["personas"] == ["cooperative"]
    assert exp["record_model_calls"] is True and list(exp["conditions"]) == ["bounded"]
    with open(exp["scenarios"], encoding="utf-8") as f:
        (scenario,) = yaml.safe_load(f)["scenarios"]
    assert scenario["events"] == [{"turn": 1, "kind": "text", "text": "A tea, please."}]
    assert scenario["faults"] == [
        {"turn": 0, "kind": "answer", "questions": ["get_opening"],
         "answer": ["What would you like?"]},
        {"turn": 1, "kind": "answer", "questions": ["detect_cancel_intent"],
         "answer": ['{"cancel": false}']},
        {"turn": 1, "kind": "answer", "questions": ["extract_turn"], "answer": correct}]
    # The plan is valid for the runner (the faults of kind answer)
    from interaction_sim.llm_faults import validate_faults
    assert len(validate_faults(scenario["faults"])) == 3


def test_a_failed_call_is_pinned_as_a_failure(tmp_path):
    rec = record("sf_x")
    faults = replay.pinned_faults(rec, 2, "get_opening", "x")
    assert {"turn": 2, "kind": "error", "questions": ["classify_intent"]} in faults


def test_the_answer_fault_gives_its_answers_in_order(tmp_path):
    from interaction_sim.llm_faults import FaultPlan, FaultSwitch, faulty_infer
    switch_path = str(tmp_path / "faults.json")
    plan = FaultPlan([{"turn": 1, "kind": "answer", "questions": [], "answer": ["a", "b"]}],
                     switch_path)
    plan.start()
    plan.user(lambda r, t: None)("x", [])
    infer = faulty_infer(lambda *a: "live", FaultSwitch(switch_path))
    assert [infer("s", "p", 1, None) for _ in range(3)] == ["a", "b", "b"]
    with pytest.raises(ValueError):
        FaultPlan([{"turn": 1, "kind": "answer"}], switch_path)
