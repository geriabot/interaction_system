# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Tests of the plans of the real sessions, without ROS:

    python3 -m pytest evaluation/real/test_sessions_common.py
"""

import collections
import json
import math
import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sessions_common as sc  # noqa: E402


def test_every_card_detour_is_an_event_of_its_scenario():
    """The simulated twin says the card's detour: same kind, once per scenario."""
    for scenario in sc.load_sessions()["scenarios"]:
        detours = [e for e in scenario["definition"].get("events", [])
                   if e.get("kind") == "detour"]
        assert len(detours) == 1, scenario["id"]
        assert detours[0]["detour"] == scenario["detour"]["kind"], scenario["id"]
        assert detours[0]["text"].strip(), scenario["id"]


def sessions(conditions=2, trials=1):
    s = copy.deepcopy(sc.load_sessions())
    names = list(s["conditions"])[:conditions]
    s["enabled"] = {n: dict(s["conditions"][n], enabled=True) for n in names}
    s["trials_per_condition"] = trials
    return s


def counts(s, n):
    plans = [sc.make_plan(s, p)["trials"] for p in range(1, n + 1)]
    flat = [t for plan in plans for t in plan]
    return (collections.Counter((t["scenario"], t["condition"]) for t in flat),
            collections.Counter((t["condition"], t["k"]) for t in flat),
            collections.Counter((t["scenario"], t["k"]) for t in flat),
            collections.Counter((a["condition"], b["condition"]) for plan in plans
                                for a, b in zip(plan, plan[1:])))


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_williams_is_a_balanced_latin_square(n):
    rows = sc.williams(n)
    assert len(rows) == (n if n % 2 == 0 else 2 * n)
    for row in rows:
        assert sorted(row) == list(range(n))
    positions = collections.Counter((c, i) for row in rows for i, c in enumerate(row))
    assert len(set(positions.values())) == 1
    follows = collections.Counter((a, b) for row in rows for a, b in zip(row, row[1:]))
    assert len(follows) == n * (n - 1) and len(set(follows.values())) == 1


@pytest.mark.parametrize("conditions,rows", [(2, 2), (3, 6)])
def test_every_block_is_fully_balanced(conditions, rows):
    # A block is B = lcm(scenarios, Williams rows) participants (or 2B)
    s = sessions(conditions)
    block = math.lcm(len(s["scenarios"]), rows)
    for n in (block, 2 * block):
        for table in counts(s, n):
            assert len(set(table.values())) == 1, table


@pytest.mark.parametrize("conditions", [2, 3])
def test_a_participant_gets_every_condition_and_never_a_scenario_twice(conditions):
    s = sessions(conditions)
    for p in range(1, 61):
        plan = sc.make_plan(s, p)["trials"]
        assert sorted(t["condition"] for t in plan) == sorted(s["enabled"])
        assert len({t["scenario"] for t in plan}) == len(plan)


def test_the_order_is_balanced_every_two_participants():
    s = sessions(2)
    for p in range(1, 40, 2):
        orders = {tuple(sc.make_plan(s, q)["order"]) for q in (p, p + 1)}
        assert len(orders) == 2


def test_two_trials_per_condition():
    s = sessions(2, trials=2)
    plan = sc.make_plan(s, 1)["trials"]
    assert len(plan) == 4 and len({t["scenario"] for t in plan}) == 4
    assert plan[0]["condition"] == plan[1]["condition"] != plan[2]["condition"]


def test_one_condition_four_trials():
    # E4: only the method; each participant has four scenarios, none twice, and every block of
    # 8 participants puts every scenario in every position once
    s = sessions(conditions=1, trials=4)
    assert sc.williams(1) == [[0]]
    for number in range(1, 9):
        scenarios = [t["scenario"] for t in sc.make_plan(s, number)["trials"]]
        assert len(scenarios) == 4 and len(set(scenarios)) == 4
    for table in counts(s, 8):
        assert len(set(table.values())) == 1, table


def test_participant_codes():
    assert sc.participant("7") == ("P07", 7)
    assert sc.participant("p07") == ("P07", 7)
    assert sc.participant("P12") == ("P12", 12)
    for bad in ("0", "x7", "P"):
        with pytest.raises(ValueError):
            sc.participant(bad)


def test_an_existing_plan_is_kept(tmp_path):
    s = sessions(2)
    plan, what = sc.write_plan(s, "P03", str(tmp_path))
    assert what == "plan created" and plan["participant"] == "P03"
    again, what = sc.write_plan(s, "3", str(tmp_path))
    assert what == "existing plan" and again["trials"] == plan["trials"]
    # Another design: the existing plan is kept, and said so
    other = sessions(3)
    kept, what = sc.write_plan(other, "P03", str(tmp_path))
    assert kept["trials"] == plan["trials"] and "another plan" in what
    # Replaced only while no trial is stored
    open(sc.record_path("P03", plan["trials"][0], str(tmp_path)), "w").write("{}")
    with pytest.raises(ValueError):
        sc.write_plan(other, "P03", str(tmp_path), replace=True)


def test_the_card_has_the_detour_and_the_footer():
    s = sessions(2)
    trial = sc.make_plan(s, 1)["trials"][0]
    text = sc.card_text(trial, s)
    assert trial["detour"]["text"] in text and s["detour_label"] in text
    assert all(line in text for line in s["card_footer"])


def test_review_replaces_everywhere_and_marks_the_record():
    rec = {"persona": "real", "data": {"customer_name": "Juan García"},
           "transcript": [{"role": "user", "text": "Me llamo Juan García"},
                          {"role": "robot", "text": "Gracias, juan garcía."}],
           "heard": ["me llamo JUAN GARCÍA"]}
    out, n = sc.review_record(rec, [("Juan García", sc.PLACEHOLDER)], "RPR", "2026-10-29 18:00")
    assert n == 4 and "garcía" not in json.dumps(out, ensure_ascii=False).lower()
    assert sc.is_reviewed(out) and not sc.is_reviewed(rec)
    assert out["reviewed"] == {"by": "RPR", "time": "2026-10-29 18:00", "replacements": 4}


def test_the_judge_refuses_real_sessions_not_reviewed(tmp_path):
    pytest.importorskip("interaction_sim")   # judge_runs needs the workspace sourced
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "analysis"))
    import judge_runs
    os.makedirs(tmp_path / "P01")
    (tmp_path / "P01" / "trial_1_x_bounded.json").write_text(json.dumps({"persona": "real"}))
    with pytest.raises(SystemExit):
        judge_runs.load_runs(str(tmp_path))
    (tmp_path / "P01" / "trial_1_x_bounded.json").write_text(
        json.dumps({"persona": "real", "reviewed": {"by": "RPR"}}))
    assert len(judge_runs.load_runs(str(tmp_path))) == 1


def test_a_critical_variant_is_its_model_on_the_server_with_selective_reasoning():
    assert sc.split_model("qwen3:32b@critical") == ("ollama", "qwen3:32b")
    assert sc.reasoning_of("qwen3:32b@critical") == "critical"
    assert sc.split_model("gpt-oss:20b") == ("ollama", "gpt-oss:20b")
    assert sc.reasoning_of("gpt-oss:20b") == "none"
    with pytest.raises(ValueError):
        sc.reasoning_of("qwen3:32b@fast")
