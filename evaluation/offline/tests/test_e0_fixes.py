# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Tests of the fixes of the E0a pipeline on the phase A side, without any LLM, server or network
(fake transports and a fake requests module):

    cd evaluation/offline && python3 -m pytest -q tests

- the gate: the no-answer rate and the case errors per language, rescoring the saved answers
  with the current dataset, the fallback rule, the two passes (repetition 1, then 2-3);
- the transport: transport errors apart from the model's no-answers, unparseable structured
  answers counted, the reasoning allowance;
- the server: unload (keep_alive 0), remove (unload first, 404 = gone), the persisted pulls;
- qa_answer: the key facts of the answer.
"""

import json
import os
import sys

import pytest
import requests

from core import gate as gate_mod
from core import llm_adapter, ollama_models, phase_a
from core.gate import compute_gate, finalists, gate_model, language_finalists, rescore
from core.path_setup import resolve_path


def _case(cid, lang, crit=None, calls=1, no_answer=0, error=None):
    c = {"id": cid, "language": lang, "critical": crit, "latency_s": 0.2, "got": {"x": 1},
         "calls": calls, "no_answer": no_answer, "flags": [], "correct": crit is None}
    if error:
        c["error"] = error
        c["flags"] = ["case_error"]
    return c


def _results(cases, languages=("en", "es")):
    calls = sum(c["calls"] for c in cases)
    return {"languages": list(languages), "total_cases": len(cases),
            "global": {"accuracy": 1.0, "trap_accuracy": 1.0, "flags": {}, "calls": calls,
                       "no_answer": sum(c["no_answer"] for c in cases)},
            "by_language": {lang: {"accuracy": 1.0} for lang in languages}, "cases": cases}


def _write(out, model, rep, bench, results):
    path = gate_mod.run_path(str(out), model, rep, bench)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f)


# ==== The gate ====

def test_no_answer_rate_is_per_language():
    # 20 calls in English all answered, 4 Spanish calls with 2 without answer: the global rate
    # (2/24 = 8 %) would fail both; per language only Spanish fails (50 %)
    cases = ([_case(f"e{i}", "en", calls=5) for i in range(4)]
             + [_case("s1", "es", calls=2, no_answer=2), _case("s2", "es", calls=2)])
    g = gate_model({(1, "step_turn"): _results(cases)}, ["en", "es"], 7.0)
    assert g["no_answer_rate_by_language"] == {"en": 0.0, "es": 0.5}
    assert g["passed_by_language"] == {"en": True, "es": False}
    assert not g["passed"]  # the global rate (2/24) is above 5 %


def test_case_errors_block_the_gate_per_language():
    cases = [_case("a", "en"), _case("b", "es", error="KeyError: 'x'")]
    g = gate_model({(1, "confirmation"): _results(cases)}, ["en", "es"], 3.0)
    assert g["case_errors"] == 1 and g["case_errors_by_language"] == {"en": 0, "es": 1}
    assert not g["passed"] and g["passed_by_language"] == {"en": True, "es": False}
    text = gate_mod.report({"m:3b": g, "_finalists": {}, "_datasets": {}}, ["confirmation"], 1,
                           ["en", "es"])
    assert "no (case errors)" in text


def test_fallback_when_fewer_than_five_pass(tmp_path):
    # Spanish: nobody passes; the five with the fewest distinct critical cases of repetition 1
    # (a case repeated in several repetitions counts once), ties by size, then name
    bench = "step_turn"
    plan = {"a:1b": 3, "b:2b": 1, "c:3b": 1, "d:4b": 0, "e:5b": 2, "f:6b": 4, "g:7b": 5,
            "silent:1b": 0}
    for model, n_crit in plan.items():
        for rep in (1, 2):
            cases = [_case(f"k{i}", "es", "false_step_advance") for i in range(n_crit)]
            cases += [_case("ok", "es", no_answer=1 if model == "silent:1b" else 0)]
            if model == "d:4b":  # zero in Spanish, but fails in rep 2 with a new case
                cases += [_case("late", "es", "false_step_advance")] if rep == 2 else []
            _write(tmp_path, model, rep, bench, _results(cases, ("es",)))
    models = list(plan)
    sizes = {m: {"size_b": float(m.split(":")[1][:-1])} for m in models}
    g = compute_gate(str(tmp_path), models, 2, [bench], ["es"], sizes)
    info = g["_finalists"]["es"]
    assert info["fallback"] and info["passed"] == []
    # silent:1b does not answer: not comparable. d (0 in rep 1), b and c (1), e (2), a (3); not
    # f (4) nor g (5)
    five = ["a:1b", "b:2b", "c:3b", "d:4b", "e:5b"]
    assert info["models"] == five
    assert "no model passed the safety gate in es" in info["note"]
    assert "rule fixed before the gate" in info["note"]
    assert finalists(g, "es") == five
    gate_mod.write_outputs(str(tmp_path), g, [bench], 2, ["es"])
    lines = (tmp_path / "finalists_es.txt").read_text().splitlines()
    assert lines[0].startswith("# fallback: true") and lines[1:] == five
    assert "fallback to the 5 with the fewest distinct critical cases" in (
        tmp_path / "report.md").read_text()
    saved = json.loads((tmp_path / "gate.json").read_text())
    assert saved["_finalists"]["es"]["fallback"] is True


def test_no_fallback_when_five_pass():
    ok = {(1, "step_turn"): _results([_case("a", "en")], ("en",))}
    gate = {f"m{i}:{i}b": gate_model(ok, ["en"], float(i)) for i in range(1, 6)}
    info = language_finalists(gate, "en")
    assert not info["fallback"] and info["models"] == [f"m{i}:{i}b" for i in range(1, 6)]


def test_those_that_passed_go_first_in_the_fallback():
    # Two pass; a smaller model with no critical case in repetition 1 but one in repetition 2
    # (it failed) does not push a model that passed out of the five
    ok = {(1, "step_turn"): _results([_case("a", "en")], ("en",)),
          (2, "step_turn"): _results([_case("a", "en")], ("en",))}
    late = {(1, "step_turn"): _results([_case("a", "en")], ("en",)),
            (2, "step_turn"): _results([_case("b", "en", "false_step_advance")], ("en",))}
    gate = {"big:30b": gate_model(ok, ["en"], 30.0), "huge:32b": gate_model(ok, ["en"], 32.0)}
    gate.update({f"s{i}:{i}b": gate_model(late, ["en"], float(i)) for i in range(1, 6)})
    info = language_finalists(gate, "en")
    assert info["fallback"] and info["passed"] == ["big:30b", "huge:32b"]
    assert "big:30b" in info["models"] and "huge:32b" in info["models"]
    assert len(info["models"]) == 5


def test_rescore_uses_the_current_dataset():
    # The saved run says "correct"; the dataset now labels the case so that the same answer is
    # critical; a case removed from the dataset no longer counts
    saved = _results([_case("c1", "en"), _case("gone", "en")], ("en",))
    saved["cases"][0]["got"] = {"intent": "confirms"}
    dataset = {("c1", "en"): {"id": "c1", "language": "en", "expected": "corrects"}}

    def score(case, got):
        bad = got["intent"] == "confirms" and case["expected"] != "confirms"
        return {"correct": not bad, "critical": "false_confirmation" if bad else None,
                "flags": []}

    out = rescore(saved, dataset, score, extra=lambda records: {"extra_figure": len(records)})
    assert [c["id"] for c in out["cases"]] == ["c1"]
    assert out["cases"][0]["critical"] == "false_confirmation"
    assert out["global"]["critical_total"] == 1 and out["global"]["extra_figure"] == 1
    assert out["by_language"]["en"]["critical_total"] == 1


def test_dataset_rescorer_records_the_hash(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"cases": [{"id": "c1", "language": "en"}]}))
    rescorer, hashes = gate_mod.dataset_rescorer(
        {"b": (lambda c, g: {"correct": True, "critical": None, "flags": []}, str(path), None)})
    assert len(hashes["b"]) == 16
    out = rescorer("b", _results([_case("c1", "en")], ("en",)))
    assert out["cases"][0]["correct"]
    assert rescorer("other", {"x": 1}) == {"x": 1}  # a benchmark without a dataset: as saved


def test_two_passes_only_survivors_run_repetitions_2_and_3(tmp_path, monkeypatch):
    import run_models

    bench = "confirmation"
    # rep 1: good passes both languages, half fails English only, bad fails both
    _write(tmp_path, "good:1b", 1, bench, _results([_case("a", "en"), _case("b", "es")]))
    _write(tmp_path, "half:2b", 1, bench, _results([_case("a", "en", "false_confirmation"),
                                                    _case("b", "es")]))
    _write(tmp_path, "bad:3b", 1, bench, _results([_case("a", "en", "false_confirmation"),
                                                   _case("b", "es", "false_confirmation")]))
    models = ["good:1b", "half:2b", "bad:3b"]
    assert run_models.survivors_after_rep1(str(tmp_path), models, [bench], ["en", "es"]) == [
        "good:1b", "half:2b"]
    for m in ("good:1b", "half:2b"):
        for rep in (2, 3):
            _write(tmp_path, m, rep, bench, _results([_case("a", "en"), _case("b", "es")]))
    g = compute_gate(str(tmp_path), models, 3, [bench], ["en", "es"],
                     reps_by_model={"good:1b": 3, "half:2b": 3, "bad:3b": 1})
    assert g["bad:3b"]["complete"] and g["bad:3b"]["reps_run"] == [1]
    assert g["bad:3b"]["stopped"] == gate_mod.STOPPED_AFTER_REP1
    assert g["good:1b"]["passed"] and g["good:1b"]["reps_run"] == [1, 2, 3]
    assert g["half:2b"]["passed_by_language"] == {"en": False, "es": True}


def test_run_models_runs_rep1_for_all_then_the_rest_for_survivors(tmp_path, monkeypatch):
    import run_models

    calls = []

    def fake_run_model(cfg, model, args, out, benchmarks, ollama, base, info, pulled,
                       wanted_think=False, reps=None):
        calls.append((model, tuple(reps)))

    monkeypatch.setattr(run_models, "run_model", fake_run_model)
    monkeypatch.setattr(run_models, "survivors_after_rep1", lambda *a, **k: ["b:2b"])
    monkeypatch.setattr(run_models, "rescore_specs", lambda benchmarks: {})
    monkeypatch.setattr(run_models.provenance, "record", lambda *a, **k: None)
    logged = []
    monkeypatch.setattr(run_models.provenance, "catalog", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(sys, "argv", [
        "run_models.py", "--config", resolve_path("config_ollama.yaml"), "--models",
        "a:1b,b:2b", "--reps", "3", "--benchmarks", "confirmation", "--out", str(tmp_path)])
    run_models.main()
    assert calls == [("a:1b", (1,)), ("b:2b", (1,)), ("b:2b", (2, 3))]
    assert logged[-1][0] == "finish" and logged[-1][2]["reps_2_on"] == ["b:2b"]
    assert "finalists_by_language" in logged[-1][2]


# ==== The transport ====

class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data or {}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.text, response=self)

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def test_transport_errors_are_raised_and_4xx_is_no_answer(monkeypatch):
    infer = llm_adapter.make_ollama_infer_fn("m", think=False)

    def down(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(llm_adapter.TransportError):
        infer("s", "p", 30, None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(503, text="loading"))
    with pytest.raises(llm_adapter.TransportError):
        infer("s", "p", 30, None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(400, text="bad option"))
    assert infer("s", "p", 30, None) is None
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, {"response": " hi "}))
    assert infer("s", "p", 30, None) == "hi"


def test_reasoning_allowance_only_when_the_model_thinks(monkeypatch):
    sent = []
    monkeypatch.setattr(requests, "post",
                        lambda url, json=None, timeout=None: sent.append(json) or _Resp(
                            200, {"response": "x"}))
    llm_adapter.make_ollama_infer_fn("m", think=False)("s", "p", 30, None)
    llm_adapter.make_ollama_infer_fn("m", think="low")("s", "p", 30, None)
    llm_adapter.make_ollama_infer_fn("m", think=True)("s", "p", 0, None)
    assert sent[0]["options"]["num_predict"] == 30
    assert sent[1]["options"]["num_predict"] == 30 + llm_adapter.REASONING_ALLOWANCE
    assert "options" not in sent[2]  # no cap: none added
    assert llm_adapter.thinks("false") is False and llm_adapter.thinks("low") is True


def test_counting_infer_keeps_transport_apart_and_counts_bad_json():
    answers = iter([llm_adapter.TransportError("cut"), "not json at all", '{"intent": "x"}',
                    "", "free text is fine without a schema"])

    def infer(session_id, prompt, max_tokens, schema):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    t = phase_a.CountingInfer(infer)
    schema = {"type": "object"}
    assert t("s", "p", 10, schema) is None
    t("s", "p", 10, schema)
    t("s", "p", 10, schema)
    t("s", "p", 10, None)
    t("s", "p", 10, None)
    assert t.transport_errors == 1
    assert t.calls == 4 and t.no_answer == 2  # the unparseable JSON and the empty answer


def test_a_run_with_transport_errors_is_redone_then_stops(tmp_path, monkeypatch):
    import run_models

    lost = {"global": {"transport_errors": 2, "calls": 3, "no_answer": 0}}
    runs = []

    class Bench:
        @staticmethod
        def run(cfg, path, verbose=False):
            runs.append(1)
            return dict(lost)

    monkeypatch.setattr(run_models.importlib, "import_module", lambda name: Bench)
    monkeypatch.setattr(run_models.ollama_models, "wait_for_server", lambda base: True)
    monkeypatch.setattr(run_models.ollama_models, "unload", lambda base, model: True)

    class Args:
        reps, pull, remove_pulled = 1, False, False

    with pytest.raises(SystemExit) as stop:
        run_models.run_model({"llm": {}}, "m:1b", Args, str(tmp_path), ["confirmation"],
                             False, "http://x", {}, [], reps=[1])
    assert "not saved" in str(stop.value)
    assert len(runs) == run_models.TRANSPORT_RETRIES + 1
    assert not os.path.exists(gate_mod.run_path(str(tmp_path), "m:1b", 1, "confirmation"))


# ==== The server ====

class FakeServer:
    """/api/ps, /api/tags, /api/generate (keep_alive 0 unloads), /api/delete."""

    def __init__(self, installed=(), loaded=()):
        self.installed, self.loaded, self.log = set(installed), set(loaded), []

    def get(self, url, timeout=None):
        if url.endswith("/api/ps"):
            return _Resp(200, {"models": [{"name": m} for m in self.loaded]})
        return _Resp(200, {"models": [{"name": m} for m in self.installed]})

    def post(self, url, json=None, timeout=None):
        self.log.append(("post", json))
        if json.get("keep_alive") == 0:
            if json["model"] not in self.installed:
                return _Resp(404, text="not found")
            self.loaded.discard(json["model"])
        return _Resp(200, {})

    def delete(self, url, json=None, timeout=None):
        self.log.append(("delete", json["model"]))
        if json["model"] not in self.installed:
            return _Resp(404, text="not found")
        self.installed.discard(json["model"])
        return _Resp(200, {})


@pytest.fixture
def server(monkeypatch):
    srv = FakeServer(installed={"a:1b", "b:2b", "llama3.2:3b"}, loaded={"a:1b", "llama3.2:3b"})
    monkeypatch.setattr(ollama_models.requests, "get", srv.get)
    monkeypatch.setattr(ollama_models.requests, "post", srv.post)
    monkeypatch.setattr(ollama_models.requests, "delete", srv.delete)
    return srv


def test_unload_frees_memory_and_remove_unloads_first(server):
    assert ollama_models.unload("http://x", "a:1b") and "a:1b" not in server.loaded
    server.loaded.add("b:2b")
    assert ollama_models.remove("http://x", "b:2b")
    assert server.log[-2:] == [("post", {"model": "b:2b", "keep_alive": 0}), ("delete", "b:2b")]
    assert "b:2b" not in server.loaded and "b:2b" not in server.installed


def test_remove_of_a_model_already_gone_is_not_an_error(server):
    assert ollama_models.remove("http://x", "gone:7b") is True


def test_unload_all_touches_only_the_given_models(server):
    ollama_models.unload_all("http://x", ["a:1b", "b:2b"])
    assert server.loaded == {"llama3.2:3b"}  # another work's model stays


def test_pulled_models_persist(tmp_path):
    path = str(tmp_path / "sub" / "pulled.json")
    assert ollama_models.load_pulled(path) == []
    ollama_models.save_pulled(path, ["b:2b", "a:1b", "a:1b"])
    assert ollama_models.load_pulled(path) == ["a:1b", "b:2b"]


def test_phase_a_unloads_each_model_after_its_runs(tmp_path, monkeypatch):
    import run_models

    events = []
    monkeypatch.setattr(run_models.ollama_models, "wait_for_server", lambda base: True)
    monkeypatch.setattr(run_models.ollama_models, "installed", lambda base: {"m:1b"})
    monkeypatch.setattr(run_models.ollama_models, "warm_up", lambda *a: False)
    monkeypatch.setattr(run_models.ollama_models, "show_size", lambda *a: 1.0)
    monkeypatch.setattr(run_models.ollama_models, "unload",
                        lambda base, model: events.append(("unload", model)))
    monkeypatch.setattr(run_models.provenance, "add_models", lambda *a: None)

    class Bench:
        @staticmethod
        def run(cfg, path, verbose=False):
            return {"global": {"calls": 1, "no_answer": 0}}

    monkeypatch.setattr(run_models.importlib, "import_module", lambda name: Bench)

    class Args:
        reps, pull, remove_pulled = 1, True, True

    run_models.run_model({"llm": {}}, "m:1b", Args, str(tmp_path), ["confirmation"], True,
                         "http://x", {}, [], reps=[1])
    assert events == [("unload", "m:1b")]


# ==== qa_answer: the content of the answer ====

def _qa():
    import importlib
    return importlib.import_module("benchmarks.bench_qa_answer")


CASE = {"expected_kind": "answerable", "user_text": "When's the last ferry?",
        "material": [{"name": "i", "content": "Ferries leave at 8:30, 12:00 and 17:15."}],
        "key_facts": ["17:15"]}


def _answered(text):
    return {"kind": "answerable", "answer": text, "verified": True, "resolution": "answered"}


def test_qa_answer_with_the_key_fact_is_correct():
    r = _qa().score(CASE, _answered("The last one leaves at 5:15 pm."))
    assert r["correct"] and r["critical"] is None and r["flags"] == []


def test_qa_answer_missing_the_key_fact_is_incomplete_not_critical():
    r = _qa().score(CASE, _answered("There are three ferries, at 8:30, 12:00 and later."))
    assert not r["correct"] and r["critical"] is None and r["flags"] == ["incomplete_answer"]


def test_qa_answer_with_a_value_not_in_the_material_is_critical():
    r = _qa().score(CASE, _answered("The last ferry leaves at 19:40."))
    assert r["critical"] == "unsupported_answer_accepted" and "contradicting_value" in r["flags"]


def test_qa_answer_unsure_is_flagged_for_review_never_failed():
    case = dict(CASE, key_facts=["17:15 last ferry"])
    r = _qa().score(case, _answered("The last ferry leaves a quarter past five."))
    assert r["correct"] and r["flags"] == ["answer_review"]
    # A 12-hour time without pm for a 24-hour one in the material: review, not critical
    r = _qa().score(CASE, _answered("The last one is at 5:15."))
    assert r["critical"] is None and "answer_review" in r["flags"]


def test_qa_answer_opposite_polarity_is_critical():
    case = dict(CASE, key_facts=["forbidden|not allowed"], expected_polarity="no",
                material=[{"name": "i", "content": "Campfires are forbidden."}])
    r = _qa().score(case, _answered("Yes, you can light one by your tent."))
    assert r["critical"] == "unsupported_answer_accepted"
    r = _qa().score(case, _answered("No, campfires are not allowed anywhere."))
    assert r["correct"] and r["critical"] is None


def test_every_answerable_case_has_key_facts():
    with open(resolve_path("datasets/phase_a/qa_answer.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    answerable = [c for c in cases if c["expected_kind"] == "answerable"]
    assert answerable and all(c.get("key_facts") for c in answerable)
    assert {c["language"] for c in answerable} == {"en", "es"}
    assert not any(c.get("key_facts") for c in cases if c["expected_kind"] != "answerable")


def test_cancel_detection_is_reported_not_critical():
    assert "cancel_detect" not in phase_a.CRITICAL_CATEGORIES["false_cancel"]


def test_a_reasoning_variant_is_the_same_model_with_selective_reasoning():
    from run_models import split_variant
    assert split_variant("qwen3:14b") == ("qwen3:14b", "none")
    assert split_variant("qwen3:14b@critical") == ("qwen3:14b", "critical")
    import pytest
    with pytest.raises(ValueError):
        split_variant("qwen3:14b@always")


def test_the_transport_passes_the_reasoning_of_a_call():
    from core.phase_a import CountingInfer
    seen = []
    transport = CountingInfer(lambda s, p, m, sch, **kw: seen.append(kw) or '{"a": 1}')
    transport("s", "p", 10, {"type": "object"}, think=True)
    transport("s", "p", 10, {"type": "object"})
    assert seen == [{"think": True}, {}]
