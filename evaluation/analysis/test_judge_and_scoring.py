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


def test_the_sample_is_stratified_with_inclusion_probabilities():
    items = _items()
    chosen = jv.stratified_sample(items, 60, random.Random(7))
    assert len(chosen) == 60
    strata = {}
    for x in items:
        strata.setdefault(f"{x[1]['persona']}|{x[1]['condition']}", []).append(x)
    for x, stratum, prob in chosen:
        drawn = sum(1 for c in chosen if c[1] == stratum)
        assert drawn == 15 and prob == pytest.approx(drawn / len(strata[stratum]))
    assert len({c[1] for c in chosen}) == 4  # every stratum


def test_no_judge_chooses_the_utterances():
    # The same items with other judge's labels give the same sample (author, 2026-11-02)
    items = _items()
    flipped = [x[:5] + ({"j": {"in_frame": 1 - x[5]["j"]["in_frame"], "unsupported": 1}},)
               for x in items]
    a = [(c[0][0], c[0][2]) for c in jv.stratified_sample(items, 60, random.Random(7))]
    b = [(c[0][0], c[0][2]) for c in jv.stratified_sample(flipped, 60, random.Random(7))]
    assert a == b


def test_the_sample_grows_until_the_expected_positives_reach_the_minimum():
    # 800 utterances, 1 in 7 out of the frame and unsupported by the judge
    items = [x[:5] + ({"j": {"in_frame": x[5]["j"]["in_frame"],
                             "unsupported": 1 - x[5]["j"]["in_frame"]}},)
             for x in _items(n_runs=200)]
    n, expected = jv.sample_size(items, items, 150)
    # 150 x 1/7 = 21 < 30; 200 x 1/7 = 28.6 < 30; 250 x 1/7 = 35.7: the first size that reaches it
    assert n == 250 and expected["in_frame"] >= jv.MIN_POSITIVES
    # Never more than MAX_N; and without a development half, the size asked for
    assert jv.sample_size(items, [], 150) == (150, {})
    rare = [x[:5] + ({"j": {"in_frame": 1 if k % 50 else 0, "unsupported": 0}},)
            for k, x in enumerate(items)]
    assert jv.sample_size(rare, rare, 150)[0] == jv.MAX_N


def test_the_bootstrap_resamples_dialogues():
    # Two dialogues, all of one's utterances agree, none of the other's: resampling dialogues
    # gives kappas of whole dialogues (here only nan or 1 or -1-ish), never a mix of utterances
    pairs = [(1, 1), (0, 0)] * 5 + [(1, 0), (0, 1)] * 5
    clusters = ["a"] * 10 + ["b"] * 10
    lo, hi = jv.interval(pairs, 7, clusters, n=200)
    assert lo <= hi
    lo_u, hi_u = jv.interval(pairs, 7, None, n=200)
    assert (hi - lo) >= (hi_u - lo_u)  # wider: the dialogue is the unit


def test_weighted_kappa_undoes_the_oversampling():
    assert jv.weighted_kappa([1, 0], [1, 0], [1.0, 1.0]) == 1.0
    assert math.isnan(jv.weighted_kappa([], [], []))


def _make_folder(tmp_path, lang, judges=("mistral_small",), agree=True, positives=40):
    folder = tmp_path / lang
    (folder / "runs" / "m" / "c").mkdir(parents=True)
    n = 100
    key = {"prompt_hash": jv.prompt_hash(), "language": lang, "items": {}}
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
    for name, rows in (("a", rows_a), ("b", rows_b)):
        with open(folder / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["id", "in_frame (1/0)", "unsupported (1/0)"])
            w.writeheader()
            w.writerows(rows)
    for j, lab in labels.items():
        with open(folder / f"judgments_{j}.jsonl", "w") as f:
            for x in lab:
                if not agree and j != judges[0]:
                    x = {**x, "in_frame": not x["in_frame"]}
                f.write(json.dumps(x) + "\n")
    return folder


def _report(folder, order=""):
    jv.report(types.SimpleNamespace(results=str(folder), a=str(folder / "a.csv"),
                                    b=str(folder / "b.csv"), order=order, seed=7))
    return json.loads((folder / "e0" / "report.json").read_text())


def test_report_figures_and_too_few_positives(tmp_path):
    rep = _report(_make_folder(tmp_path, "en", positives=40))
    r = rep["judges"]["mistral_small"]["in_frame"]
    assert r["A"]["kappa"] == 1.0 and r["A"]["sensitivity"] == 1.0 and r["A"]["specificity"] == 1.0
    assert r["positives_either"] == 40 and r["decision"] == "USE the judge"
    assert rep["bootstrap_unit"] == "dialogue"
    assert rep["error_rate"]["mistral_small"] == {"errors": 0, "asked": 100, "rate": 0.0}
    rep = _report(_make_folder(tmp_path / "few", "en", positives=10))
    assert rep["judges"]["mistral_small"]["in_frame"]["decision"] == jv.NOT_VALIDATED


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
