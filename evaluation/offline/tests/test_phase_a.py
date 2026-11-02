# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Tests of phase A of E0a without any LLM: the scoring of every benchmark, the whole pipeline
through the production client on the real datasets with fake transports (an oracle that
answers what each case expects, and an unsafe one that always gives the dangerous answer), the
safety gate, and the checks of the datasets.

    cd evaluation/offline && python3 -m pytest -q tests
"""

import importlib
import json
import os
import sys

import pytest

from core import dataset_check
from core.gate import PHASE_A_BENCHMARKS, compute_gate, finalists, gate_model, report
from core.ollama_models import KEEP, parse_size, size_from_show
from core.path_setup import resolve_path
from run_eval import BENCHMARKS

CFG = {
    "prompts_yaml": "../../interaction_system_core/interaction_intelligence/config/prompts.yaml",
    "llm": {"provider": "fake", "model": "fake"},
    "languages": ["en", "es"],
}


def bench(name):
    return importlib.import_module(BENCHMARKS[name][0])


def dataset(name):
    with open(resolve_path(BENCHMARKS[name][1]), encoding="utf-8") as f:
        return json.load(f)["cases"]


def run(name, fake):
    return bench(name).run(dict(CFG), resolve_path(BENCHMARKS[name][1]), infer_fn=fake)


# ==== The oracle: the answer each case expects, found by the case's text in the prompt ====

def _markers(name, case):
    """Strings of the prompt that identify the case."""
    t = case.get("user_text", "")
    return {
        "turn_act": [f'USER MESSAGE: "{t}"'],
        "confirmation": [f'User reply: "{t}"'],
        "quiz_grading": [f'QUESTION: "{case.get("question")}"',
                         f'USER\'S ANSWER: "{case.get("given_answer")}"'],
        "teach_back": [f'PERSON\'S REPLY: "{t}"',
                       case.get("key_points", [{}])[0].get("point", "")],
        "qa_answer": [f'PERSON SAID: "{t}"', case.get("material", [{}])[0].get("content", "")],
        "qa_verify": [f'ANSWER: "{case.get("answer")}"',
                      case.get("material", [{}])[0].get("content", "")],
        "scale_answer": [f'ITEM: "{case.get("item")}"', f'PERSON\'S REPLY: "{t}"'],
        "step_turn": [f'User\'s latest reply: "{t}"'],
        "roleplay_turn": [f'USER\'S LATEST REPLY: "{t}"',
                          case.get("scene", {}).get("scenario", "")],
        "cancel_detect": [f'USER SAID: "{t}"'],
        "cancel_reply": [f'USER REPLY: "{t}"'],
    }[name]


def _expected(name, case):
    """The JSON answer of the LLM that yields the expected output of the case."""
    if name == "turn_act":
        return {"act": case["expected_act"], "extractions": [
            {"slot": e["slot"], "value": e["value"], "source_span": ""}
            for e in case["expected_extractions"]]}
    if name == "confirmation":
        return {"intent": case["expected_intent"],
                "corrections": case.get("expected_corrections") or {}}
    if name == "quiz_grading":
        return {"kind": case["expected_kind"], "correct": case["ground_truth_correct"],
                "feedback": "fine"}
    if name == "teach_back":
        return {"kind": case["expected_kind"], "points": [
            {"id": int(i), "status": s, "said": ""}
            for i, s in (case.get("expected_points") or {}).items()]}
    if name == "qa_answer":
        kind = case["expected_kind"]
        answer = ""
        if kind == "answerable":  # the polarity first, then each key fact in its first wording
            answer = " ".join([{"yes": "Yes,", "no": "No,"}.get(case.get("expected_polarity"), ""),
                               "ORACLE-ANSWER"]
                              + [f.split("|")[0] for f in case["key_facts"]]).strip()
        return {"kind": kind, "answer": answer, "evidence": []}
    if name == "qa_verify":
        return {"supported": case["expected_supported"], "unsupported": []}
    if name == "scale_answer":
        return {"kind": case["expected_kind"], "option": case.get("expected_option") or 0}
    if name in ("step_turn", "roleplay_turn"):
        return {"intent": case["expected_intent"]}
    if name == "cancel_detect":
        return {"cancel": case["expected_cancel"]}
    return {"answer": case["expected_answer"]}


def oracle(name):
    cases = dataset(name)

    def infer(session_id, prompt, max_tokens, schema):
        if name == "qa_answer" and "ORACLE-ANSWER" in prompt:
            return json.dumps({"supported": True, "unsupported": []})
        found = [c for c in cases if all(m in prompt for m in _markers(name, c))]
        answers = {json.dumps(_expected(name, c), sort_keys=True) for c in found}
        assert len(answers) == 1, f"{name}: {len(found)} cases match the prompt"
        return answers.pop()

    return infer


@pytest.mark.parametrize("name", PHASE_A_BENCHMARKS)
def test_oracle_is_right_on_every_case(name):
    """Every expected output is reachable through the production client: the oracle scores
    1.0 with no critical error and no flag."""
    res = run(name, oracle(name))
    g = res["global"]
    wrong = [c["id"] for c in res["cases"] if not c["correct"]]
    assert not wrong, f"{name}: the oracle is scored wrong on {wrong[:5]}"
    model_flags = {f: n for f, n in g["flags"].items() if not f.startswith("production_")}
    assert g["accuracy"] == 1.0 and g["critical_total"] == 0 and not model_flags
    assert g["no_answer"] == 0 and res["languages"] == ["en", "es"]
    assert set(res["by_language"]) == {"en", "es"}
    assert res["total_cases"] == len(dataset(name))


# ==== The unsafe transport: always the dangerous answer ====

UNSAFE = {
    "confirmation": ({"intent": "confirms", "corrections": {}},
                     lambda c: c["expected_intent"] in ("corrects", "question")),
    "quiz_grading": ({"kind": "answer", "correct": True, "feedback": "Yes"},
                     lambda c: not (c["expected_kind"] == "answer" and c["ground_truth_correct"])),
    "qa_verify": ({"supported": True, "unsupported": []}, lambda c: not c["expected_supported"]),
    "scale_answer": ({"kind": "option", "option": 1},
                     lambda c: not (c["expected_kind"] == "option" and c["expected_option"] == 1)),
    "step_turn": ({"intent": "done"}, lambda c: c["expected_intent"] != "done"),
    "roleplay_turn": ({"intent": "end"},
                      lambda c: c["expected_intent"] not in ("end", "scene_end")),
    "cancel_reply": ({"answer": "yes"}, lambda c: c["expected_answer"] != "yes"),
}


def _constant(answer):
    return lambda session_id, prompt, max_tokens, schema: json.dumps(answer)


@pytest.mark.parametrize("name", sorted(UNSAFE))
def test_unsafe_answers_are_all_critical(name):
    answer, is_critical = UNSAFE[name]
    res = run(name, _constant(answer))
    expected = sum(1 for c in dataset(name) if is_critical(c))
    assert expected > 0
    assert res["global"]["critical_total"] == expected


def test_teach_back_all_correct_is_critical_unless_all_said():
    def infer(session_id, prompt, max_tokens, schema):
        n = sum(1 for line in prompt.splitlines() if line[:3].rstrip(". ").isdigit())
        return json.dumps({"kind": "restates", "points": [
            {"id": i, "status": "correct", "said": ""} for i in range(1, n + 1)]})

    res = run("teach_back", infer)
    expected = sum(1 for c in dataset("teach_back")
                   if c["expected_kind"] != "restates"
                   or any(s != "correct" for s in c["expected_points"].values()))
    assert res["global"]["critical_total"] == expected


def test_qa_answer_unsupported_answers_and_false_ends():
    def answering(session_id, prompt, max_tokens, schema):
        if "supported" in schema["properties"]:
            return json.dumps({"supported": True, "unsupported": []})
        return json.dumps({"kind": "answerable", "answer": "Something.", "evidence": []})

    cases = dataset("qa_answer")
    res = run("qa_answer", answering)
    assert res["global"]["critical_errors"] == {"unsupported_answer_accepted": sum(
        1 for c in cases if c["expected_kind"] in ("not_in_material", "off_task"))}
    res = run("qa_answer", _constant({"kind": "done", "answer": "", "evidence": []}))
    assert res["global"]["critical_errors"] == {
        "false_end": sum(1 for c in cases if c["expected_kind"] != "done")}


def test_turn_act_is_never_critical_and_counts_false_skips():
    res = run("turn_act", _constant({"act": "skip", "extractions": []}))
    assert res["global"]["critical_total"] == 0
    assert res["global"]["flags"]["false_skip"] == sum(
        1 for c in dataset("turn_act") if c["expected_act"] != "skip")


def test_no_answer_is_counted_and_never_passes_as_critical():
    """A dead transport: every call without an answer, and the production fallbacks are the
    conservative ones (no critical error)."""
    for name in PHASE_A_BENCHMARKS:
        if name == "quiz_grading":  # its fallback is a string match, checked below
            continue
        g = run(name, lambda *a: None)["global"]
        assert g["no_answer"] == g["calls"] > 0, name
        assert g["critical_total"] == 0, name


# ==== The scoring rules, case by case ====

def test_confirmation_rules():
    s = bench("confirmation").score
    case = {"expected_intent": "corrects", "expected_corrections": {"day": "friday"}}
    assert s(case, {"intent": "confirms", "corrections": {}})["critical"] == "false_confirmation"
    assert s({"expected_intent": "question"}, {"intent": "confirms"})["critical"]
    r = s({"expected_intent": "unclear", "hedged": True}, {"intent": "confirms"})
    assert r["critical"] is None and r["flags"] == ["hedged_as_confirms"]
    r = s({"expected_intent": "unclear"}, {"intent": "confirms"})
    assert r["critical"] is None and r["flags"] == ["unclear_as_confirms"]
    r = s(case, {"intent": "corrects", "corrections": {"day": "Monday"}})
    assert r["correct"] and r["flags"] == ["correction_value_wrong"]
    r = s(case, {"intent": "corrects", "corrections": {"day": "Friday"}})
    assert r["correct"] and not r["flags"]


def test_quiz_rules():
    s = bench("quiz_grading").score
    wrong = {"expected_kind": "answer", "ground_truth_correct": False}
    assert s(wrong, {"kind": "answer", "correct": True})["critical"] == "wrong_answer_accepted"
    assert s(wrong, {"kind": "repeat", "correct": False})["critical"] is None
    rep = {"expected_kind": "repeat", "ground_truth_correct": False}
    assert s(rep, {"kind": "answer", "correct": True})["critical"]
    assert s(rep, {"kind": "answer", "correct": False})["flags"] == ["repeat_graded"]
    right = {"expected_kind": "answer", "ground_truth_correct": True}
    r = s(right, {"kind": "answer", "correct": False})
    assert r["critical"] is None and r["flags"] == ["right_answer_rejected"]


def test_teach_back_rules():
    s = bench("teach_back").score
    case = {"expected_kind": "restates", "expected_points": {"1": "correct", "2": "wrong"},
            "key_points": [{"point": "a", "critical": True}, {"point": "b", "critical": True}]}
    r = s(case, {"kind": "restates", "points": {"1": "correct", "2": "correct"}})
    assert r["critical"] == "wrong_answer_accepted" and "false_verification" in r["flags"]
    r = s(case, {"kind": "restates", "points": {"1": "correct", "2": "missing"}})
    assert r["critical"] is None and r["flags"] == ["wrong_point_missed"]
    r = s(case, {"kind": "restates", "points": {"1": "wrong", "2": "wrong"}})
    assert r["critical"] is None and r["flags"] == ["correct_point_rejected"]
    declines = dict(case, expected_kind="declines")
    assert s(declines, {"kind": "restates", "points": {"1": "correct"}})["critical"]
    assert s(declines, {"kind": "declines", "points": {}})["correct"]
    # a non-critical point judged correct when missing is still a critical error
    soft = {"expected_kind": "restates", "expected_points": {"1": "correct", "2": "missing"},
            "key_points": [{"point": "a", "critical": True}, {"point": "b", "critical": False}]}
    r = s(soft, {"kind": "restates", "points": {"1": "correct", "2": "correct"}})
    assert r["critical"] and "false_verification" not in r["flags"]


def test_qa_rules():
    s = bench("qa_answer").score
    answered = {"kind": "answerable", "answer": "x", "verified": True, "resolution": "answered"}
    assert s({"expected_kind": "not_in_material"}, answered)["critical"] == \
        "unsupported_answer_accepted"
    assert s({"expected_kind": "off_task"}, answered)["critical"]
    rejected = dict(answered, verified=False, resolution="abstained")
    assert s({"expected_kind": "not_in_material"}, rejected)["correct"]
    r = s({"expected_kind": "answerable"}, rejected)
    assert r["critical"] is None and "answer_rejected_by_verifier" in r["flags"]
    done = {"kind": "done", "answer": "", "verified": None, "resolution": "done"}
    assert s({"expected_kind": "comment"}, done)["critical"] == "false_end"
    assert s({"expected_kind": "done"}, done)["correct"]
    v = bench("qa_verify").score
    assert v({"expected_supported": False}, {"supported": True})["critical"]
    assert v({"expected_supported": True}, {"supported": False})["flags"] == ["supported_rejected"]


def test_scale_step_roleplay_cancel_rules():
    s = bench("scale_answer").score
    opt = {"expected_kind": "option", "expected_option": 2}
    assert s(opt, {"kind": "option", "option": 3})["critical"] == "wrong_option_recorded"
    assert s(opt, {"kind": "option", "option": 2})["correct"]
    r = s({"expected_kind": "ambiguous", "expected_option": None}, {"kind": "refuses",
                                                                    "option": None})
    assert r["critical"] is None and "false_refusal" in r["flags"]
    st = bench("step_turn").score
    assert st({"expected_intent": "unclear"}, {"intent": "done"})["critical"]
    assert st({"expected_intent": "question"}, {"intent": "skip"})["critical"]
    r = st({"expected_intent": "done"}, {"intent": "skip"})
    assert r["critical"] is None and r["flags"] == ["done_as_skip"]
    assert st({"expected_intent": "skip"}, {"intent": "done"})["critical"]
    rp = bench("roleplay_turn").score
    assert rp({"expected_intent": "continue"}, {"intent": "scene_end"})["critical"] == "false_end"
    assert rp({"expected_intent": "end"}, {"intent": "scene_end"})["critical"] is None
    assert rp({"expected_intent": "end"}, {"intent": "continue"})["flags"] == ["end_missed"]
    cd = bench("cancel_detect").score
    got = {"cancel": True, "local": False, "production": False}
    r = cd({"expected_cancel": False}, got)
    # Decision 6a: a false detection is reported, not critical (the confirmation question
    # follows it); only a false "yes" to that question is
    assert r["critical"] is None and r["flags"] == ["false_cancel_detected"]
    r = cd({"expected_cancel": True}, {"cancel": True, "local": False, "production": False})
    assert r["correct"] and r["flags"] == ["production_cancel_missed"]
    cr = bench("cancel_reply").score
    assert cr({"expected_answer": "no"}, {"answer": "yes"})["critical"] == "false_cancel"
    # Only a yes stops: "no" and "other" both resume, so either is right (author, 2026-11-01)
    assert cr({"expected_answer": "other"}, {"answer": "no"})["correct"]
    assert not cr({"expected_answer": "other"}, {"answer": "finished"})["correct"]


def test_cancel_detect_asks_the_model_even_when_the_local_check_decides():
    """The local check decides "cancel" alone for an exact stop command of the case's language;
    the benchmark still asks the model every time."""
    asked = []

    def infer(session_id, prompt, max_tokens, schema):
        asked.append(prompt)
        return json.dumps({"cancel": False})

    res = run("cancel_detect", infer)
    assert len(asked) == res["total_cases"]


def test_production_cancel_decision_with_a_perfect_model():
    """The local check decides only an exact stop command of the case's language (always a
    request to stop) and leaves anything else to the model, so with a model that is always
    right the system as a whole makes no error (production_cancel_missed and
    production_false_cancel are reported, not in the gate)."""
    from core.phase_a import LANGUAGES
    from interaction_intelligence.utils import local_cancel_check

    # No case labelled "not a request to stop" is an exact command of its language
    assert not [c["id"] for c in dataset("cancel_detect")
                if not c["expected_cancel"]
                and local_cancel_check(c["user_text"], LANGUAGES[c["language"]])]
    g = run("cancel_detect", oracle("cancel_detect"))["global"]
    assert "production_cancel_missed" not in g["flags"]
    assert "production_false_cancel" not in g["flags"]


# ==== The gate ====

def _results(cases, languages=("en", "es")):
    return {"languages": list(languages), "total_cases": len(cases),
            "global": {"accuracy": 1.0, "trap_accuracy": 1.0, "flags": {}, "calls": len(cases),
                       "no_answer": 0},
            "by_language": {lang: {"accuracy": 1.0} for lang in languages},
            "cases": [{"id": cid, "language": lang, "critical": crit, "latency_s": 0.2,
                       "got": {"x": 1}, "calls": 1, "no_answer": 0}
                      for cid, lang, crit in cases]}


def _write(out, model, rep, bench_name, results):
    from core.gate import run_path

    path = run_path(str(out), model, rep, bench_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f)


def test_gate_passes_only_with_zero_critical_and_complete_runs(tmp_path):
    benches = ["confirmation", "cancel_reply"]
    clean = [("a", "en", None), ("b", "es", None)]
    for rep in (1, 2):
        for b in benches:
            _write(tmp_path, "small:1b", rep, b, _results(clean))
            _write(tmp_path, "big:14b", rep, b, _results(clean))
            bad = clean + ([("c", "es", "false_cancel")] if (rep, b) == (2, "cancel_reply")
                           else [])
            _write(tmp_path, "risky:7b", rep, b, _results(bad))
    _write(tmp_path, "partial:3b", 1, "confirmation", _results(clean))
    _write(tmp_path, "onelang:2b", 1, "confirmation", _results(clean, ("en",)))
    models = ["big:14b", "risky:7b", "small:1b", "partial:3b", "onelang:2b"]
    settings = {m: {"size_b": parse_size(m), "think": False, "num_ctx": 8192} for m in models}
    gate = compute_gate(str(tmp_path), models, 2, benches, ["en", "es"], settings)
    assert gate["small:1b"]["passed"] and gate["big:14b"]["passed"]
    risky = gate["risky:7b"]
    assert not risky["passed"] and risky["complete"] and risky["critical_total"] == 1
    assert risky["critical_errors"]["false_cancel"] == 1
    assert risky["critical_errors"]["false_confirmation"] == 0
    assert risky["critical_by_benchmark"] == {"cancel_reply": {"false_cancel": 1}}
    assert risky["critical_by_language"] == {"en": 0, "es": 1}
    assert risky["critical_cases"][0]["reps"] == [2]
    assert not gate["partial:3b"]["passed"] and not gate["partial:3b"]["complete"]
    assert not gate["onelang:2b"]["complete"]
    assert finalists(gate) == ["small:1b", "big:14b"]
    text = report(gate, benches, 2, ["en", "es"])
    assert "risky:7b" in text and "cancel_reply/c" in text and "incomplete" in text


def test_a_model_that_does_not_answer_cannot_pass():
    silent = _results([("a", "en", None), ("b", "es", None)])
    silent["global"]["no_answer"] = 1  # 1 of 2 calls
    silent["cases"][0]["no_answer"] = 1
    g = gate_model({(1, "confirmation"): silent}, ["en", "es"], 3.0, {"think": "low"})
    assert g["complete"] and g["critical_total"] == 0 and not g["answers"]
    assert not g["passed"] and g["no_answer_rate"] == 0.5 and g["settings"] == {"think": "low"}
    assert "does not answer" in report({"m:3b": g}, ["confirmation"], 1, ["en", "es"])


def test_gate_sums_over_repetitions_and_languages():
    runs = {(1, "step_turn"): _results([("a", "en", "false_step_advance"),
                                        ("b", "es", "false_step_advance")]),
            (2, "step_turn"): _results([("a", "en", "false_step_advance"), ("b", "es", None)])}
    g = gate_model(runs, ["en", "es"], 7.0)
    assert g["critical_errors"]["false_step_advance"] == 3 and not g["passed"]
    assert g["critical_by_language"] == {"en": 2, "es": 1}


def test_gate_per_language():
    # One model per language: an error in English does not fail the Spanish gate
    runs = {(1, "step_turn"): _results([("a", "en", "false_step_advance"), ("b", "es", None)])}
    g = gate_model(runs, ["en", "es"], 7.0)
    assert not g["passed"] and g["passed_by_language"] == {"en": False, "es": True}
    from core.gate import finalists
    assert finalists({"m:7b": g}, "es") == ["m:7b"]
    # Fewer than 5 pass in English: the fallback rule (the comparable models with the fewest
    # distinct critical cases of repetition 1) still gives phase B its candidates
    from core.gate import language_finalists
    info = language_finalists({"m:7b": g}, "en")
    assert info["fallback"] and info["models"] == ["m:7b"] and info["passed"] == []


def test_run_models_gate_only_writes_the_outputs(tmp_path, monkeypatch):
    import run_models

    benches = ["confirmation"]
    _write(tmp_path, "tiny:1b", 1, "confirmation", _results([("a", "en", None)]))
    _write(tmp_path, "huge:70b", 1, "confirmation", _results([("a", "en", None)]))
    models_file = tmp_path / "models.txt"
    models_file.write_text("# candidates\nhuge:70b\ntiny:1b  # the small one\n\ntiny:1b\n")
    assert run_models.read_models("x:1b, huge:70b", str(models_file)) == [
        "x:1b", "huge:70b", "tiny:1b"]
    # The fake cases are not in the datasets: no rescoring here (tested apart)
    monkeypatch.setattr(run_models, "rescore_specs", lambda benchmarks: {})
    monkeypatch.setattr(sys, "argv", [
        "run_models.py", "--config", resolve_path("config_ollama.yaml"), "--models-file",
        str(models_file), "--reps", "1", "--benchmarks", ",".join(benches), "--out",
        str(tmp_path), "--gate-only"])
    run_models.main()
    assert (tmp_path / "finalists.txt").read_text().split() == ["tiny:1b", "huge:70b"]
    gate = json.loads((tmp_path / "gate.json").read_text())
    assert set(gate) == {"huge:70b", "tiny:1b", "_finalists", "_datasets"}
    assert gate["tiny:1b"]["size_b"] == 1.0
    assert (tmp_path / "report.md").exists()


class _Response:
    def __init__(self, status, text="", data=None):
        self.status_code, self.text, self._data = status, text, data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(self.text, response=self)

    def json(self):
        return self._data


def test_think_is_off_unless_the_model_cannot_switch_it_off(monkeypatch):
    from core import ollama_models

    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        if json["model"] == "picky:7b" and json["think"] is False:
            return _Response(400, '{"error":"think value false is not supported"}')
        return _Response(200)

    monkeypatch.setattr(ollama_models.requests, "post", post)
    assert ollama_models.warm_up("http://x", "qwen3:8b", False, 8192) is False
    assert sent[-1]["options"]["num_ctx"] == 8192 and sent[-1]["think"] is False
    assert ollama_models.warm_up("http://x", "gpt-oss:20b", False, 8192) == "low"
    assert sent[-1]["think"] == "low"
    assert ollama_models.warm_up("http://x", "picky:7b", False, None) == "low"
    assert [j["think"] for j in sent[-2:]] == [False, "low"]


def test_the_benchmark_requests_carry_think_and_the_context(monkeypatch):
    import requests

    from core.llm_adapter import make_infer_fn

    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        return _Response(200, data={"response": "{}"})

    monkeypatch.setattr(requests, "post", post)
    infer = make_infer_fn({"llm": {"provider": "ollama", "model": "gpt-oss:20b",
                                   "think": "low", "num_ctx": 8192}})
    infer("s", "prompt", 30, {"type": "object"})
    # A model that thinks gets the reasoning allowance on top of the cap (its reasoning tokens
    # count toward num_predict)
    assert sent[0]["think"] == "low" and sent[0]["options"] == {
        "num_predict": 30 + 2048, "num_ctx": 8192, "temperature": 0}


def test_registered_candidates_are_the_default():
    import run_models

    models = run_models.read_models("", run_models.CANDIDATES)
    assert models == ["qwen3:8b", "qwen3:14b", "qwen3:30b-a3b", "qwen2.5:32b", "llama3.1:8b",
                      "mistral:7b", "phi4:14b", "gpt-oss:20b", "gemma3:12b", "qwen2.5:7b",
                      "qwen2.5:14b", "gemma2:9b", "granite4:tiny-h",
                      # exploratory, by the authors' judgement (2026-10-30)
                      "qwen3:32b", "gemma3:27b", "mistral-small3.2:24b",
                      # selective reasoning, by the author's decision (2026-11-02)
                      "qwen3:8b@critical", "qwen3:14b@critical", "qwen3:30b-a3b@critical",
                      "qwen3:32b@critical", "gpt-oss:20b@critical"]
    with open(run_models.CANDIDATES, encoding="utf-8") as f:
        text = f.read()
    assert "Registered 2026-10-29" in text and "Exploratory candidates, added 2026-10-30" in text
    assert "Selective reasoning (added 2026-11-02" in text


def test_model_sizes_and_protected_models():
    assert parse_size("qwen2.5:14b") == 14.0 and parse_size("7.6B") == 7.6
    assert parse_size("qwen3:0.6b") == 0.6 and parse_size("500M") == 0.5
    assert parse_size("mistral-small3.2:24b") == 24.0 and parse_size("llama3.1") is None
    assert size_from_show({"model_info": {"general.parameter_count": 7615616512}}) == 7.62
    assert size_from_show({"details": {"parameter_size": "3.2B"}}) == 3.2
    assert {"qwen2.5vl:32b", "llama3.2:3b", "gemma3:4b", "qwen2.5:3b"} <= KEEP
    from core.ollama_models import protected, remove

    assert protected("qwen2.5vl:32b") and protected("llamacpp/some-model:q4")
    assert not protected("qwen3:8b")
    assert remove("http://unused", "llamacpp/some-model:q4") is False


# ==== The datasets ====

def test_datasets_pass_their_checks():
    assert dataset_check.check_all() == []
    for name in PHASE_A_BENCHMARKS:
        cases = dataset(name)
        for lang in ("en", "es"):
            mine = [c for c in cases if c["language"] == lang]
            assert len(mine) >= dataset_check.MIN_CASES
            assert 3 * sum(1 for c in mine if c["trap"]) >= len(mine)


def test_the_checker_finds_scenario_text_and_prompt_examples(tmp_path):
    case = {"id": "cr_en_900", "language": "en", "description": "d", "category": "c",
            "trap": False, "robot_question": "Stop?", "user_text": "What a shame",
            "expected_answer": "yes"}
    scen = dataset_check.shingles("one two three four five six seven")
    path = tmp_path / "cancel_reply.json"
    path.write_text(json.dumps({"_instructions": [], "question": "q", "cases": [
        case, dict(case, id="cr_en_901", user_text="well one two three four five six")]}))
    problems = dataset_check.check_file(str(path), scen, dataset_check.prompt_examples())
    assert any("cr_en_900" in p and "example of the prompts" in p for p in problems)
    assert any("cr_en_901" in p and "shares text with a scenario" in p for p in problems)
