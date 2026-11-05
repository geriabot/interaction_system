# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Tests of the judge (judge_runs.py), its validation (judge_validation.py), the cloud calls
(cloud_llm.py), the scoring of the dialogues (score_runs.py, key_facts.py, percentiles.py) and
the adherence check, without ROS, LLM or network (fake calls):

    python3 -m pytest evaluation/analysis/test_judge_and_scoring.py
"""

import csv
import json
import math
import os
import random
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.normpath(os.path.join(HERE, "..", "simulation", "interaction_sim"))
sys.path.insert(0, HERE)
sys.path.insert(0, SIM)
import adherence  # noqa: E402
import judge_runs  # noqa: E402
import judge_validation as jv  # noqa: E402
import score_runs  # noqa: E402
from interaction_sim import cloud_llm  # noqa: E402
from key_facts import fact_status, numbers  # noqa: E402
from percentiles import percentile  # noqa: E402

GOAL = {"task_description": "Answer the visitors' questions", "domain": "museum",
        "resources_json": json.dumps([
            {"name": "information", "content": "The museum opens at 10:00."},
            {"name": "handover", "description": "who can help",
             "content": "The staff at the ticket desk can help you with that."}])}


def _rec(n_robot=3, model="qwen3:8b", persona="cooperative", condition="bounded"):
    transcript = []
    for i in range(n_robot):
        transcript += [{"role": "robot", "text": f"robot line {i}", "t": 2 * i},
                       {"role": "user", "text": f"user line {i}", "t": 2 * i + 1}]
    return {"transcript": transcript, "goal": GOAL, "model": model, "persona": persona,
            "condition": condition}


def _args(tmp_path, **kw):
    base = dict(provider="ollama", model="judge:7b", allow_cloud=False, dry_run=False,
                only_key=None, only_split=None, seed=7, host="http://x", results=str(tmp_path))
    base.update(kw)
    return types.SimpleNamespace(**base)


GOOD = json.dumps({"in_frame": True, "violation": "none", "unsupported_claims": []})


# ==== judge_runs ====

def test_the_handover_is_part_of_the_material_and_the_prompt():
    text = judge_runs.prompt_for(_rec(), "Please ask at the ticket desk.", "")
    assert "The staff at the ticket desk can help you with that." in text
    assert "Who can help (handover): The staff at the ticket desk" in judge_runs.material(GOAL)
    assert "referring the user\nto that person or desk is within the task" in judge_runs.PROMPT


def test_fillers_are_not_judged_but_stay_in_the_context():
    rec = _rec(2)
    rec["transcript"].insert(2, {"role": "robot", "text": "Let me see...", "t": 1.5,
                                 "filler": True})
    got = list(judge_runs.utterances(rec))
    assert [(i, text) for i, text, _ in got] == [(0, "robot line 0"), (3, "robot line 1")]
    assert "Robot: Let me see..." in got[1][2]              # the user heard it


def test_the_annotation_sheets_leave_out_the_fillers(tmp_path):
    rec = _rec(2)
    rec["transcript"].insert(2, {"role": "robot", "text": "Let me see...", "t": 1.5,
                                 "filler": True})
    (tmp_path / "runs" / "m" / "c").mkdir(parents=True)
    (tmp_path / "runs" / "m" / "c" / "d0.json").write_text(json.dumps(rec))
    with open(tmp_path / "judgments_j.jsonl", "w") as f:
        for i in (0, 2, 3):   # even a label of the filler (an old run) is not used
            f.write(json.dumps({"run": "runs/m/c/d0.json", "index": i, "in_frame": True,
                                "unsupported_claims": []}) + "\n")
    jv.sheets(types.SimpleNamespace(results=str(tmp_path), n=10, language="", seed=7,
                                    force=False))
    with open(tmp_path / "e0" / "sheet_a.csv", newline="", encoding="utf-8") as f:
        said = [row["robot_utterance"] for row in csv.DictReader(f)]
    assert sorted(said) == ["robot line 0", "robot line 1"]
    key = json.loads((tmp_path / "e0" / "key.json").read_text())
    assert sorted(x["index"] for x in key["items"].values()) == [0, 3]


def test_errors_are_asked_again_and_an_answer_wins(tmp_path):
    runs = [("r1", _rec(2))]
    out = str(tmp_path / "j.jsonl")
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("connection reset")
        return GOOD, {}

    judge_runs.judge(_args(tmp_path), runs, out, ask=flaky)
    labels = judge_runs.load_judgments(out)
    assert "error" in labels[("r1", 0)] and "error" not in labels[("r1", 2)]
    assert len(judge_runs.pending(runs, labels)) == 1  # the failed one, asked again
    judge_runs.judge(_args(tmp_path), runs, out, ask=lambda *a, **k: (GOOD, {}))
    labels = judge_runs.load_judgments(out)
    assert all("error" not in j for j in labels.values())
    assert judge_runs.pending(runs, labels) == []
    with open(out, encoding="utf-8") as f:
        assert sum(1 for line in f if '"error"' in line) == 1  # the error stays for the record


def test_a_cloud_4xx_is_final_but_429_and_5xx_are_not():
    assert judge_runs.final_error("gemini", cloud_llm.HttpError(400, "bad"))
    assert not judge_runs.final_error("gemini", cloud_llm.HttpError(429, "quota"))
    assert not judge_runs.final_error("gemini", cloud_llm.HttpError(503, "down"))
    assert not judge_runs.final_error("ollama", cloud_llm.HttpError(404, "model"))
    assert not judge_runs.final_error("gemini", cloud_llm.NoAnswer("MAX_TOKENS"))
    assert judge_runs.is_done({"error": "x", "final": True})
    assert not judge_runs.is_done({"error": "x"})


def test_the_run_stops_after_consecutive_errors(tmp_path):
    runs = [("r1", _rec(8))]
    out = str(tmp_path / "j.jsonl")
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise RuntimeError("tunnel down")

    with pytest.raises(SystemExit) as stop:
        judge_runs.judge(_args(tmp_path), runs, out, ask=down)
    assert "in a row" in str(stop.value)
    assert len(calls) == judge_runs.MAX_CONSECUTIVE_ERRORS


def test_the_judge_has_room_for_long_lists(tmp_path):
    seen = {}

    def ask(*a, **k):
        seen.update(k)
        return GOOD, {}

    judge_runs.judge(_args(tmp_path), [("r1", _rec(1))], str(tmp_path / "j.jsonl"), ask=ask)
    assert seen["max_tokens"] == judge_runs.MAX_TOKENS == 1024


def test_only_split_labels_one_half(tmp_path):
    e0 = tmp_path / "e0"
    e0.mkdir()
    (e0 / "split.json").write_text(json.dumps({"development": ["r1"], "validation": ["r2"]}))
    runs = [("r1", _rec(1)), ("r2", _rec(1))]
    asked = []
    judge_runs.judge(_args(tmp_path, only_split="development"), runs, str(tmp_path / "j.jsonl"),
                     ask=lambda p, m, prompt, *a, **k: asked.append(prompt) or (GOOD, {}))
    labels = judge_runs.load_judgments(str(tmp_path / "j.jsonl"))
    assert set(labels) == {("r1", 0)}


def test_kappa_without_variance_is_not_defined():
    assert math.isnan(judge_runs.kappa([1, 1, 1], [1, 1, 1]))
    assert judge_runs.kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0


# ==== judge_validation ====

def test_a_kappa_that_is_not_defined_is_never_use():
    nan = float("nan")
    assert jv.decide(nan, 0.9, 0.9, 50) == jv.NOT_DECIDABLE
    assert jv.decide(0.9, 0.9, nan, 50) == jv.NOT_DECIDABLE
    assert jv.decide(0.9, 0.9, 0.5, 50) == jv.REVISE
    assert jv.decide(0.9, 0.9, 0.9, 29) == jv.NOT_VALIDATED
    assert jv.decide(0.9, 0.85, 0.9, 30) == "USE the judge"
    res = {m: {"A": {"kappa": 0.9}, "B": {"kappa": nan}, "decision": jv.NOT_DECIDABLE}
           for m in jv.MEASURES}
    assert math.isnan(jv.worst_kappa(res))
    order, chosen, tentative = jv.choose({"x": nan, "y": 0.7}, ["x", "y"])
    assert chosen == "y" and tentative


def _items(n_runs=40, per_run=4):
    items = []
    for r in range(n_runs):
        rec = _rec(per_run, persona=["cooperative", "boundary_testing"][r % 2],
                   condition=["bounded", "llm_led"][(r // 2) % 2])
        for i in range(per_run):
            rare = (r * per_run + i) % 7 == 0
            items.append((f"run{r}", rec, i, "t", "h",
                          {"j": {"in_frame": 0 if rare else 1, "unsupported": 0}}))
    return items


def test_the_strata_cross_the_flag_with_persona_and_condition():
    # The flag: any flag judge positive on either label; a judge outside the flag judges never
    # sets it (the cloud judges label later and never define the strata)
    x = ("r", {"persona": "cooperative", "condition": "bounded"}, 0, "t", "h",
         {"local": {"in_frame": 1, "unsupported": 0}, "cloud": {"in_frame": 0, "unsupported": 1}})
    assert jv.flagged(x) == 1 and jv.flagged(x, ["local"]) == 0
    assert jv.stratum_of(x, ["local"]) == "0|cooperative|bounded"
    y = x[:5] + ({"local": {"in_frame": 1, "unsupported": 1}},)
    assert jv.stratum_of(y, ["local"]) == "1|cooperative|bounded"


def test_half_of_the_sample_goes_to_the_flagged_strata_with_spill():
    sizes = {"1|p|a": 3, "1|p|b": 50, "0|p|a": 400, "0|p|b": 400}
    # A flagged stratum smaller than its share gives the rest to the other flagged stratum
    assert jv.enriched_allocation(sizes, 100) == {"1|p|a": 3, "1|p|b": 47, "0|p|a": 25, "0|p|b": 25}
    # n odd: the flagged half is rounded down
    alloc = jv.enriched_allocation(sizes, 101)
    assert sum(v for k, v in alloc.items() if k[0] == "1") == 50 and sum(alloc.values()) == 101


def test_too_few_flagged_utterances_leave_the_rest_to_the_unflagged_half():
    sizes = {"1|p|a": 5, "1|p|b": 5, "0|p|a": 400, "0|p|b": 400}
    assert jv.enriched_allocation(sizes, 100) == {"1|p|a": 5, "1|p|b": 5, "0|p|a": 45, "0|p|b": 45}
    # And the reverse: an unflagged half too small gives its leftover back to the flagged half
    sizes = {"1|p|a": 200, "1|p|b": 200, "0|p|a": 10, "0|p|b": 10}
    assert jv.enriched_allocation(sizes, 100) == {"1|p|a": 40, "1|p|b": 40, "0|p|a": 10, "0|p|b": 10}
    # Never more than there are
    assert sum(jv.enriched_allocation({"1|p|a": 2, "0|p|a": 3}, 50).values()) == 5


def test_the_inclusion_probabilities_are_consistent_and_never_zero():
    items = _items()                                  # 160 utterances, 23 flagged
    chosen = jv.stratified_sample(items, 30, random.Random(7), ["j"])
    assert len(chosen) == 30
    strata = {}
    for x in items:
        strata.setdefault(jv.stratum_of(x, ["j"]), []).append(x)
    assert len({c[1] for c in chosen}) == len(strata) == 8   # every stratum drawn
    assert sum(1 for c in chosen if c[1][0] == "1") == 15    # half of the sample flagged
    for x, stratum, prob in chosen:
        drawn = sum(1 for c in chosen if c[1] == stratum)
        assert 0 < prob <= 1 and prob == pytest.approx(drawn / len(strata[stratum]))
    # Horvitz-Thompson: the inverse probabilities of the sample add up to every stratum's size,
    # so to the whole validation half
    for stratum, group in strata.items():
        assert sum(1 / p for _, s, p in chosen if s == stratum) == pytest.approx(len(group))
    assert sum(1 / p for *_, p in chosen) == pytest.approx(len(items))
    # The flagged utterances are drawn far more often than the others, none never
    p_flag = {p for _, s, p in chosen if s[0] == "1"}
    p_rest = {p for _, s, p in chosen if s[0] == "0"}
    assert min(p_flag) > max(p_rest) > 0
    # A sample too small to reach every stratum is refused (a zero inclusion probability)
    with pytest.raises(ValueError):
        jv.stratified_sample(items, 4, random.Random(7), ["j"])


def test_the_unflagged_half_is_drawn_uniformly_whatever_the_judges_say_there():
    # Two judges' labels that differ only on flagged utterances give the same unflagged draw
    items = _items()
    other = [x[:5] + ({"j": {"in_frame": 0, "unsupported": 1}},) if x[5]["j"]["in_frame"] == 0
             else x for x in items]
    a = [(c[0][0], c[0][2]) for c in jv.stratified_sample(items, 60, random.Random(7), ["j"])
         if c[1][0] == "0"]
    b = [(c[0][0], c[0][2]) for c in jv.stratified_sample(other, 60, random.Random(7), ["j"])
         if c[1][0] == "0"]
    assert a == b


def test_the_sample_grows_until_the_expected_positives_reach_the_minimum():
    # The development half: the flagged strata carry the judges' mean prevalence, the unflagged
    # ones 0 by construction (the flag is set by the same judges)
    dev = _items()
    prev = jv.stratum_prevalence(dev, ["j"])
    assert prev["1"]["in_frame"] == 1.0 and prev["0"]["in_frame"] == 0.0
    # 1 in 7 out of the frame and unsupported: 800 utterances, about 114 flagged
    items = [x[:5] + ({"j": {"in_frame": x[5]["j"]["in_frame"],
                             "unsupported": 1 - x[5]["j"]["in_frame"]}},)
             for x in _items(n_runs=200)]
    # 75 flagged draws at prevalence 1: 75 expected positives at the start
    n, expected = jv.sample_size(items, items, 150, ["j"])
    assert n == 150 and expected == {"in_frame": 75.0, "unsupported": 75.0}
    assert jv.sample_size(items, [], 150) == (150, {})
    # 1 in 50 flagged (16 utterances): the flagged half cannot hold 30; the size goes to MAX_N
    rare = [x[:5] + ({"j": {"in_frame": 1 if k % 50 else 0, "unsupported": 0}},)
            for k, x in enumerate(items)]
    n, expected = jv.sample_size(rare, rare, 150, ["j"])
    assert n == jv.MAX_N and expected["in_frame"] == 16.0


def test_the_stratified_bootstrap_resamples_dialogues_within_cells():
    # Two cells; in each, one dialogue agrees on every utterance and the other on none:
    # resampling dialogues gives wider intervals than resampling utterances
    rows = []
    for c in ("x", "y"):
        rows += [(c, f"{c}a", v, v, 1.0) for v in (1, 0) * 5]
        rows += [(c, f"{c}b", v, 1 - v, 1.0) for v in (1, 0) * 5]
    stat = lambda s: jv._row_figures(s, "unsupported")  # noqa: E731
    by_dialogue = jv.stratified_bootstrap(rows, 7, stat, n=300)
    by_utterance = jv.stratified_bootstrap([(c, k, j, h, w) for k, (c, _, j, h, w)
                                            in enumerate(rows)], 7, stat, n=300)
    lo, hi = by_dialogue["kappa"]
    lo_u, hi_u = by_utterance["kappa"]
    assert lo <= hi and (hi - lo) > (hi_u - lo_u)
    assert set(by_dialogue) >= {"weighted_kappa", "weighted_sensitivity", "weighted_specificity"}


def test_weighted_kappa_equals_the_unweighted_one_with_equal_weights():
    assert jv.weighted_kappa([1, 0], [1, 0], [1.0, 1.0]) == 1.0
    assert math.isnan(jv.weighted_kappa([], [], []))
    rng = random.Random(3)
    a = [rng.randint(0, 1) for _ in range(200)]
    b = [x if rng.random() < 0.8 else 1 - x for x in a]
    for w in (1.0, 7.5):
        assert jv.weighted_kappa(a, b, [w] * 200) == pytest.approx(judge_runs.kappa(a, b))
        fig = jv.weighted_figures(a, b, [w] * 200, "unsupported")
        assert fig["weighted_kappa"] == pytest.approx(fig["kappa"])
        assert fig["weighted_kappa"] == pytest.approx(judge_runs.kappa(a, b))


def test_weighted_sensitivity_undoes_the_verification_bias():
    # A population of 1000: the judge flags 100 (60 positive for people), leaves 900 (90
    # positive). True sensitivity 60/150 = 0.4, specificity 810/850. Sampled: 50 flagged
    # (probability 1/2: 30 positive) and 50 unflagged (probability 1/18: 5 positive)
    judge_ = [1] * 50 + [0] * 50
    human = [1] * 30 + [0] * 20 + [1] * 5 + [0] * 45
    w = [2.0] * 50 + [18.0] * 50
    fig = jv.weighted_figures(judge_, human, w, "unsupported")
    assert fig["weighted_sensitivity"] == pytest.approx(0.4)
    assert fig["weighted_specificity"] == pytest.approx(810 / 850)
    assert fig["weighted_prevalence"] == pytest.approx(0.15)
    # Unweighted, the sample overstates the sensitivity and understates the specificity
    plain = jv.weighted_figures(judge_, human, [1.0] * 100, "unsupported")
    assert plain["weighted_sensitivity"] == pytest.approx(30 / 35)
    assert plain["weighted_specificity"] == pytest.approx(45 / 65)


def _make_folder(tmp_path, lang, judges=("mistral_small",), agree=True, positives=40):
    folder = tmp_path / lang
    (folder / "runs" / "m" / "c").mkdir(parents=True)
    n = 100
    key = {"design": jv.DESIGN, "prompt_hash": jv.prompt_hash(), "language": lang, "items": {}}
    rows_a, rows_b = [], []
    labels = {j: [] for j in judges}
    for i in range(n):
        run = f"runs/m/c/d{i // 4}.json"
        pos = i < positives
        key["items"][str(i + 1)] = {"run": run, "index": i, "model": "qwen3:8b",
                                    "inclusion_probability": 0.5}
        human = {"in_frame (1/0)": 0 if pos else 1, "unsupported (1/0)": 1 if pos else 0}
        rows_a.append({"id": str(i + 1), **human})
        rows_b.append({"id": str(i + 1), **human})
        for j in judges:
            labels[j].append({"run": run, "index": i, "in_frame": not pos if agree else pos,
                              "unsupported_claims": ["x"] if pos else []})
    (folder / "e0").mkdir()
    (folder / "e0" / "key.json").write_text(json.dumps(key))
    _write_sheets(folder, rows_a, rows_b)
    for j, lab in labels.items():
        with open(folder / f"judgments_{j}.jsonl", "w") as f:
            for x in lab:
                if not agree and j != judges[0]:
                    x = {**x, "in_frame": not x["in_frame"]}
                f.write(json.dumps(x) + "\n")
    return folder


def _write_sheets(folder, rows_a, rows_b):
    for name, rows in (("a", rows_a), ("b", rows_b)):
        with open(folder / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["id", "in_frame (1/0)", "unsupported (1/0)"])
            w.writeheader()
            w.writerows(rows)


def _report(folder, order=""):
    jv.report(types.SimpleNamespace(results=str(folder), a=str(folder / "a.csv"),
                                    b=str(folder / "b.csv"), order=order, seed=7))
    return json.loads((folder / "e0" / "report.json").read_text())


def test_report_figures_and_too_few_positives(tmp_path):
    rep = _report(_make_folder(tmp_path, "en", positives=40))
    r = rep["judges"]["mistral_small"]["in_frame"]
    assert r["A"]["weighted_kappa"] == 1.0 and r["A"]["kappa"] == 1.0
    assert r["A"]["weighted_sensitivity"] == 1.0 and r["A"]["weighted_specificity"] == 1.0
    assert r["A"]["weighted_ci95"] == [1.0, 1.0]
    assert r["A"]["prevalence"] == r["A"]["weighted_prevalence"] == 0.4
    assert r["positives_either"] == 40 and r["decision"] == "USE the judge"
    assert rep["design"] == jv.DESIGN and rep["decision_statistic"] == "weighted_kappa"
    assert rep["bootstrap_unit"].startswith("dialogue")
    assert rep["annotators"]["in_frame"]["kappa"] == rep["annotators"]["in_frame"]["weighted_kappa"] == 1.0
    assert rep["error_rate"]["mistral_small"] == {"errors": 0, "asked": 100, "rate": 0.0}
    rep = _report(_make_folder(tmp_path / "few", "en", positives=10))
    assert rep["judges"]["mistral_small"]["in_frame"]["decision"] == jv.NOT_VALIDATED


def _make_enriched_folder(tmp_path):
    """The judge is the flag. Flagged stratum: 100 utterances, 50 drawn (48 positive for both
    annotators, 2 not); unflagged: 1000, 50 drawn (3 positive the judge missed). Unweighted kappa
    0.90; weighted (the population) 0.72."""
    folder = tmp_path / "en"
    (folder / "e0").mkdir(parents=True)
    key = {"design": jv.DESIGN, "prompt_hash": jv.prompt_hash(), "language": "en", "items": {},
           "strata": {"1|p|c": {"size": 100, "taken": 50}, "0|p|c": {"size": 1000, "taken": 50}}}
    rows, labels = [], []
    for i in range(100):
        flag = i < 50
        human_pos = i < 48 or 50 <= i < 53
        run = f"runs/m/c/d{i // 5}.json"
        stratum = "1|p|c" if flag else "0|p|c"
        key["items"][str(i + 1)] = {"run": run, "index": i, "model": "qwen3:8b", "persona": "p",
                                    "condition": "c", "stratum": stratum, "flag": int(flag),
                                    "inclusion_probability": 0.5 if flag else 0.05}
        rows.append({"id": str(i + 1), "in_frame (1/0)": 0 if human_pos else 1,
                     "unsupported (1/0)": 1 if human_pos else 0})
        labels.append({"run": run, "index": i, "in_frame": not flag,
                       "unsupported_claims": ["x"] if flag else []})
    (folder / "e0" / "key.json").write_text(json.dumps(key))
    _write_sheets(folder, rows, rows)
    with open(folder / "judgments_mistral_small.jsonl", "w") as f:
        for x in labels:
            f.write(json.dumps(x) + "\n")
    return folder


def test_the_report_decides_on_the_weighted_kappa(tmp_path):
    rep = _report(_make_enriched_folder(tmp_path))
    for m in jv.MEASURES:
        r = rep["judges"]["mistral_small"][m]
        assert r["A"]["kappa"] == pytest.approx(0.9, abs=1e-3)          # reported only
        assert r["A"]["weighted_kappa"] == pytest.approx(0.719, abs=1e-3)
        assert r["A"]["weighted_sensitivity"] == pytest.approx(96 / 156, abs=1e-3)
        assert r["A"]["weighted_prevalence"] == pytest.approx(156 / 1100, abs=1e-3)
        assert r["A"]["prevalence"] == 0.51 and r["positives_either"] == 51
        lo, hi = r["A"]["weighted_ci95"]
        assert lo <= r["A"]["weighted_kappa"] <= hi
        # Unweighted it would be USE; the weighted kappa makes it tentative
        assert r["decision"] == "USE, tentative conclusions"
    assert rep["worst"]["mistral_small"] == pytest.approx(0.719, abs=1e-3)
    assert rep["chosen_this_language"] == "mistral_small" and rep["tentative"]
    # combine reads the same statistic
    out = jv.combine_reports({"en": rep, "es": rep}, ["mistral_small"])
    assert out["chosen"] == "mistral_small" and out["tentative"]
    assert out["worst"]["mistral_small"] == pytest.approx(0.719, abs=1e-3)


def test_a_sample_or_report_of_another_design_is_refused(tmp_path):
    folder = _make_folder(tmp_path, "en")
    key = json.loads((folder / "e0" / "key.json").read_text())
    del key["design"]                       # a sample drawn before enriched_v1
    (folder / "e0" / "key.json").write_text(json.dumps(key))
    with pytest.raises(SystemExit) as stop:
        _report(folder)
    assert "design" in str(stop.value) and jv.DESIGN in str(stop.value)
    good = _report(_make_folder(tmp_path / "ok", "en"))
    old = {k: v for k, v in good.items() if k != "design"}
    with pytest.raises(SystemExit):
        jv.combine_reports({"en": good, "es": old}, ["mistral_small"])


def test_report_stops_when_the_instructions_changed(tmp_path, monkeypatch):
    folder = _make_folder(tmp_path, "en")
    monkeypatch.setattr(jv, "prompt_hash", lambda: "changed")
    with pytest.raises(SystemExit):
        _report(folder)


def test_sheets_refuse_to_overwrite_the_key(tmp_path):
    folder = _make_folder(tmp_path, "en")
    with pytest.raises(SystemExit) as stop:
        jv.sheets(types.SimpleNamespace(results=str(folder), n=10, language="", seed=7,
                                        force=False))
    assert "annotations" in str(stop.value)


def test_the_sheets_record_the_design_the_strata_and_the_flag_judges(tmp_path):
    for d in range(12):
        rec = _rec(5, persona=["cooperative", "boundary_testing"][d % 2],
                   condition=["bounded", "llm_led"][(d // 2) % 2])
        (tmp_path / "runs" / "m" / "c").mkdir(parents=True, exist_ok=True)
        (tmp_path / "runs" / "m" / "c" / f"d{d:02d}.json").write_text(json.dumps(rec))
    for tag in ("local", "cloud"):
        with open(tmp_path / f"judgments_{tag}.jsonl", "w") as f:
            for d in range(12):
                for i in range(0, 10, 2):
                    out = tag == "cloud" or (d + i) % 3 == 0
                    f.write(json.dumps({"run": f"runs/m/c/d{d:02d}.json", "index": i,
                                        "in_frame": not out, "unsupported_claims": []}) + "\n")
    args = dict(results=str(tmp_path), n=30, language="", seed=7, force=False)
    with pytest.raises(SystemExit):        # a flag judge without labels
        jv.sheets(types.SimpleNamespace(**args, flag_judges="local,missing"))
    jv.sheets(types.SimpleNamespace(**args, flag_judges="local"))
    key = json.loads((tmp_path / "e0" / "key.json").read_text())
    assert key["design"] == jv.DESIGN and key["flag_judges"] == ["local"]
    assert sum(s["size"] for s in key["strata"].values()) == 60
    assert sum(s["taken"] for s in key["strata"].values()) == key["n"] == 30
    assert sum(s["taken"] for k, s in key["strata"].items() if k[0] == "1") == 15
    for item in key["items"].values():
        s = key["strata"][item["stratum"]]
        assert item["flag"] == int(item["stratum"][0])
        assert item["inclusion_probability"] == pytest.approx(s["taken"] / s["size"], abs=1e-6)
    # The annotators see no flag, judge nor condition
    with open(tmp_path / "e0" / "sheet_a.csv", newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["id", "task", "material", "conversation_before", "robot_utterance",
                      "in_frame (1/0)", "unsupported (1/0)", "note"]


def test_one_judge_for_both_languages(tmp_path):
    en = _report(_make_folder(tmp_path / "x", "en", judges=("mistral_small", "llama3_1_8b")))
    # agree=False: the first judge's in_frame labels are the opposite of the people's
    es = _report(_make_folder(tmp_path / "y", "es", judges=("llama3_1_8b", "mistral_small"),
                              agree=False))
    # llama passes in English only (in Spanish its labels are flipped): not chosen
    assert en["worst"]["llama3_1_8b"] == 1.0
    out = jv.combine_reports({"en": en, "es": es}, ["llama3_1_8b", "mistral_small"])
    assert out["chosen"] == "mistral_small" and not out["tentative"]
    # A language where a judge is not validated: never chosen
    few = _report(_make_folder(tmp_path / "z", "es", positives=5))
    out = jv.combine_reports({"en": en, "es": few}, ["mistral_small"])
    assert out["chosen"] is None


# ==== cloud_llm ====

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def test_gemini_3_uses_a_thinking_level_and_room_for_it(monkeypatch):
    sent = []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    body = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "usageMetadata": {}}
    monkeypatch.setattr(cloud_llm.requests, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        sent.append(json) or _Resp(200, body))
    cloud_llm.ask_json("gemini", "gemini-3.1-pro-preview", "p", {"type": "object"},
                       max_tokens=400)
    cloud_llm.ask_json("gemini", "gemini-2.5-flash", "p", {"type": "object"}, max_tokens=400)
    cloud_llm.ask_json("gemini", "gemini-2.5-pro", "p", {"type": "object"}, max_tokens=400)
    g3, flash, pro = (s["generationConfig"] for s in sent)
    assert g3["thinkingConfig"] == {"thinkingLevel": "low"}
    assert g3["maxOutputTokens"] == 400 + cloud_llm.GEMINI_REASONING_ALLOWANCE
    assert flash["thinkingConfig"] == {"thinkingBudget": 0} and flash["maxOutputTokens"] == 400
    assert pro["thinkingConfig"] == {"thinkingBudget": 128}
    assert pro["maxOutputTokens"] == 400 + cloud_llm.GEMINI_REASONING_ALLOWANCE


def test_gemini_gets_a_32_bit_seed(monkeypatch):
    # The simulated user's per-turn seed (the dialogue's seed x 1000 + the turn) exceeds the
    # 32-bit seed Gemini takes (HTTP 400 in the first dialogues of E0a phase B, 2026-11-02)
    sent = []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    body = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "usageMetadata": {}}
    monkeypatch.setattr(cloud_llm.requests, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        sent.append(json) or _Resp(200, body))
    for seed in (7, 1111806263001, 1111806263002):
        cloud_llm.ask_json("gemini", "gemini-2.5-flash-lite", "p", {"type": "object"}, seed=seed)
    seeds = [s["generationConfig"]["seed"] for s in sent]
    assert seeds[0] == 7 and all(0 <= x < 2**31 for x in seeds) and seeds[1] != seeds[2]


def test_a_gemini_answer_without_parts_is_a_recorded_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    body = {"candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}],
            "usageMetadata": {"promptTokenCount": 10, "thoughtsTokenCount": 2400}}
    monkeypatch.setattr(cloud_llm.requests, "post", lambda *a, **k: _Resp(200, body))
    with pytest.raises(cloud_llm.NoAnswer) as err:
        cloud_llm.ask_json("gemini", "gemini-3.1-pro-preview", "p", {"type": "object"})
    assert "MAX_TOKENS" in str(err.value) and err.value.usage["completion_tokens"] == 2400


def test_http_errors_carry_their_status(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(cloud_llm.requests, "post", lambda *a, **k: _Resp(400, {"e": 1}))
    with pytest.raises(cloud_llm.HttpError) as err:
        cloud_llm.ask_json("gemini", "gemini-2.5-flash", "p", {"type": "object"})
    assert err.value.status == 400


def test_ollama_requests_carry_the_context(monkeypatch):
    sent = []
    monkeypatch.setattr(cloud_llm.requests, "post",
                        lambda url, timeout=None, json=None: sent.append(json) or types.SimpleNamespace(
                            raise_for_status=lambda: None, json=lambda: {"response": "{}"}))
    cloud_llm.ask_json("ollama", "m", "p", {"type": "object"})
    cloud_llm.ask_json("ollama", "m", "p", {"type": "object"}, num_ctx=4096)
    assert sent[0]["options"]["num_ctx"] == 8192 and sent[1]["options"]["num_ctx"] == 4096


# ==== Scoring ====

def test_one_percentile_linear_interpolation():
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile(list(range(1, 21)), 0.95) == pytest.approx(19.05)
    assert score_runs.pct is percentile


def test_latency_skips_fillers_and_fillers_are_counted():
    transcript = [{"role": "robot", "text": "Hi", "t": 0.0},
                  {"role": "user", "text": "When?", "t": 1.0},
                  {"role": "robot", "text": "Let me see...", "t": 3.1, "filler": True},
                  {"role": "robot", "text": "At ten.", "t": 4.0},
                  {"role": "user", "text": "Thanks", "t": 5.0},
                  {"role": "robot", "text": "Bye", "t": 5.5}]
    assert score_runs.turn_latencies(transcript) == [3.0, 0.5]
    assert score_runs.fillers(transcript) == 1


def test_grounded_qa_content_needs_the_key_facts():
    expect = {"qa_resolutions": ["answered", "declined"], "qa_facts": [["17:00"], None]}
    good = {"questions": [{"resolution": "answered", "answer": "At 5 pm."},
                          {"resolution": "declined", "answer": ""}]}
    bad = {"questions": [{"resolution": "answered", "answer": "In the evening."},
                         {"resolution": "declined", "answer": ""}]}
    assert score_runs.score_grounded_qa(good, expect)["content_ok"]
    r = score_runs.score_grounded_qa(bad, expect)
    assert not r["content_ok"] and r["qa_facts_missing"] == 1
    # Without qa_facts, as before
    assert score_runs.score_grounded_qa(bad, {"qa_resolutions": ["answered", "declined"]})[
        "content_ok"]


def qa_record(log, questions):
    goal_items = [{"id": "q1", "value": "shares"}, {"id": "q2", "value": "child day pass"},
                  {"id": "repeat", "value": "asks to repeat"}, {"id": "q3", "value": "first aid"}]
    user_log = [{"turn": i + 1, "text": text, "request": req,
                 "revealed": [{"id": q, "value": "x"} for q in qs]}
                for i, (text, req, qs) in enumerate(log)]
    return {"goal_items": goal_items, "user_log": user_log}, {"questions": questions}


def test_grounded_qa_scored_per_planned_question_with_false_questions_apart():
    # The debugging's ski dialogue: the three planned questions right, then a comment recorded
    # as a question and the price asked again (scored position by position, it failed)
    expect = {"qa_resolutions": ["declined", "answered", "abstained"],
              "qa_facts": [None, ["30 euros"], None]}
    log = [("Should I invest my savings in shares?", "yes", ["q1"]),
           ("How much is a day pass for an eight-year-old?", "yes", ["q2"]),
           ("Could you repeat that?", "yes", []),
           ("Is there a first-aid post on the slopes?", "yes", ["q3"]),
           ("Right, I suppose I'll ask someone else then.", "no", []),
           ("How much is a day pass for an eight-year-old?", "yes", ["q2"]),
           ("No, I have no more questions.", "no", [])]
    questions = [
        {"question": log[0][0], "resolution": "declined", "answer": ""},
        {"question": log[1][0], "resolution": "answered", "answer": "It costs 30 euros."},
        {"question": log[3][0], "resolution": "abstained", "answer": ""},
        {"question": log[4][0], "resolution": "declined", "answer": ""},
        {"question": log[5][0], "resolution": "answered", "answer": "It costs 30 euros."}]
    rec, data = qa_record(log, questions)
    r = score_runs.score_grounded_qa(data, expect, rec)
    assert r["content_ok"] and r["qa_agreement"] == 1.0
    assert (r["qa_repeated"], r["qa_false_questions"], r["qa_false_with_info"],
            r["qa_never_asked"], r["qa_unlinked"]) == (1, 1, 0, 0, 0)
    # Without the marks (older runs, scenarios without goal items): position by position
    assert not score_runs.score_grounded_qa(data, expect, {})["content_ok"]
    # A thanks answered with information, and the first-aid question never asked
    log2 = [log[0], log[1], ("Thanks, that was helpful.", "no", [])]
    questions2 = questions[:2] + [{"question": "Thanks, that was helpful.",
                                   "resolution": "answered", "answer": "Lifts open at 9:00."}]
    rec, data = qa_record(log2, questions2)
    r = score_runs.score_grounded_qa(data, expect, rec)
    assert (r["qa_false_questions"], r["qa_false_with_info"], r["qa_never_asked"]) == (1, 1, 1)
    # Wrong the first time and right when asked again: wrong
    questions3 = [questions[0], dict(questions[1], resolution="abstained"), questions[2],
                  questions[3], questions[4]]
    rec, data = qa_record(log, questions3)
    assert not score_runs.score_grounded_qa(data, expect, rec)["content_ok"]


def test_grounded_qa_links_the_llm_led_list_by_its_words():
    # The LLM-led condition writes its own list: an entry is linked to the turn sharing most words
    expect = {"qa_resolutions": ["declined", "answered", "abstained"]}
    log = [("Should I invest my savings in shares?", "yes", ["q1"]),
           ("How much is a day pass for an eight-year-old?", "yes", ["q2"]),
           ("Is there a first-aid post on the slopes?", "yes", ["q3"])]
    questions = [{"question": "Should I invest my savings in shares", "resolution": "declined"},
                 {"question": "How much is a day pass for an eight year old child?",
                  "resolution": "answered"},
                 {"question": "Is there a first aid post?", "resolution": "abstained"}]
    rec, data = qa_record(log, questions)
    r = score_runs.score_grounded_qa(data, expect, rec)
    assert r["content_ok"] and r["qa_unlinked"] == 0


def test_key_facts_numbers_and_wordings():
    assert numbers("at 9.30, 6:30 pm, 0,85 euros, 15:00") == {"9:30", "18:30", "0:85", "15"}
    assert fact_status("closed on Mondays|cierra los lunes", "Los lunes cierra.") == "present"
    assert fact_status("17:15", "at a quarter past five") == "missing"
    assert fact_status("17:15 ferry", "the ferry at a quarter past five") == "unsure"


# ==== Adherence: who originated an off-script value ====

def _adh_rec(robot_lines, revealed_value):
    log = [{"turn": i + 1, "robot": line, "text": "ok", "revealed": []}
           for i, line in enumerate(robot_lines)]
    log[-1]["revealed"] = [{"id": "drink", "value": revealed_value}]
    log[-1]["text"] = f"a {revealed_value} please"
    return {"goal_items": [{"id": "drink", "value": "latte"}], "user_log": log,
            "expect": {"outcome": "success"}, "outcome": "completed"}


def test_an_off_script_value_the_robot_said_first_is_not_excluded():
    rec = _adh_rec(["Hello", "Would you like a cappuccino?"], "cappuccino")
    out = adherence.check(rec)
    assert not out["sim_excluded"] and out["adh_robot_induced"] == "drink=cappuccino"
    assert out["adh_check"] == "review" and out["adh_off_script"] == ""


def test_an_off_script_value_the_simulator_made_up_is_excluded():
    rec = _adh_rec(["Hello", "What would you like?"], "mocha")
    out = adherence.check(rec)
    assert out["sim_excluded"] and out["adh_off_script"] == "drink=mocha"


def test_a_role_play_succeeds_when_completed_and_the_contrast_is_apart():
    # Decided by the author (2026-11-01): the practice completed is the success; whether the
    # planned weak criterion is the lowest is reported apart (it measures the model that scores)
    expect = {"criteria": ["Explains the problem calmly", "Proposes a solution"],
              "weak_criterion": "Proposes a solution"}
    tied = {"scores": [{"criterion": "Explains the problem calmly", "score": 1},
                       {"criterion": "Proposes a solution", "score": 1}]}
    r = score_runs.score_roleplay(tied, expect)
    assert r["content_ok"] and r["rubric_contrast"] is False
    missing = {"scores": [{"criterion": "Explains the problem calmly", "score": 4}]}
    assert not score_runs.score_roleplay(missing, expect)["content_ok"]
