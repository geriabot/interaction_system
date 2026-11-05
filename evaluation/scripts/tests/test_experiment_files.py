# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""What an experiment file selects (experiment_files.py), shared by run_experiment.py and the
control panel: the scenarios of the experiment and those of each condition."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from experiment_files import condition_scenarios, select_scenarios  # noqa: E402

SCENARIOS = [{"id": "a", "strategy": "quiz"}, {"id": "b", "strategy": "quiz"},
             {"id": "c", "strategy": "procedure"}]


def test_the_experiment_selects_by_id_then_per_strategy():
    assert [s["id"] for s in select_scenarios(SCENARIOS, {"scenarios_per_strategy": 1})] == ["a", "c"]
    assert [s["id"] for s in select_scenarios(SCENARIOS, {"scenario_ids": ["b", "c"]})] == ["b", "c"]


def test_a_condition_runs_its_own_scenarios_or_all():
    assert [s["id"] for s in condition_scenarios(SCENARIOS, {"scenario_ids": ["c"]})] == ["c"]
    assert condition_scenarios(SCENARIOS, {}) == SCENARIOS
    assert condition_scenarios(SCENARIOS, None) == SCENARIOS


def test_the_role_swap_and_the_noise_keep_four_per_strategy_with_the_unique_mechanisms():
    """The role swap of E1 and the noise of E3 list their scenarios by id (I-11 of the coverage
    audit, 2026-10-30): four per strategy, among them those that are the only ones of E1 to provoke
    a mechanism (failed explanation, stuck step, repeated step, step back, coach hint, repeated
    questionnaire item, planned slot correction), the same in both files (E1 and E3 run in English
    only since 2026-10-30)."""
    import yaml
    experiments = os.path.join(os.path.dirname(__file__), "..", "..", "experiments")
    unique = {"ex_walker", "pr_alarm", "pr_breathing", "pr_plants", "rp_return",
              "qn_event_feedback", "qn_loneliness", "sf_library"}
    chosen = []
    for name in ("e1_role_swap.yaml", "e3_noise.yaml"):
        with open(os.path.join(experiments, name), encoding="utf-8") as f:
            exp = yaml.safe_load(f)
        assert "scenarios_per_strategy" not in exp, name
        with open(os.path.join(experiments, exp["scenarios"]), encoding="utf-8") as f:
            scenarios = yaml.safe_load(f)["scenarios"]
        selected = select_scenarios(scenarios, exp)
        assert len(selected) == len(exp["scenario_ids"]) == 28, name
        per = {}
        for s in selected:
            per[s["strategy"]] = per.get(s["strategy"], 0) + 1
        assert set(per.values()) == {4} and len(per) == 7, (name, per)
        assert unique <= {s["id"] for s in selected}, name
        chosen.append([s["id"] for s in selected])
    assert chosen[0] == chosen[1]


def test_no_role_play_scene_is_written_in_the_second_person():
    # The scene goes to the model next to "your character" (the role it plays): a "you" in the
    # scene, meaning the user, made models of three families swap the roles at the opening
    import json
    import re
    import yaml
    second = re.compile(r"\b(you|your|yours|yourself|tú|tu|tus|te|ti|contigo|os|vosotros)\b", re.I)
    folder = os.path.join(os.path.dirname(__file__), "..", "..", "experiments", "scenarios")
    scenes = 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".yaml"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for s in data.get("scenarios") or []:
            goal = s.get("goal") or {}
            for res in json.loads(goal.get("resources_json") or "[]"):
                if res.get("name") != "scene":
                    continue
                scene = json.loads(res["content"])["scenario"]
                scenes += 1
                assert not second.search(scene), f"{name} {s['id']}: {scene}"
    assert scenes > 0
