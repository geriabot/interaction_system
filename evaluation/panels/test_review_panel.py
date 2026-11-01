# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Tests of the review panel without a browser, LLM or network: loading, saving and resuming,
apply with backup and changelogs, the blind adjudication list, the seeded sample, the Wilson
interval, the sheets (ownership and the CSV read back by judge_validation.read_sheet), and the
page served. Every file goes to a temporary folder.

    python3 -m pytest -q panels
"""

import csv
import json
import os
import shutil
import sys
import threading
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(EVAL, "analysis"))
sys.path.insert(0, os.path.join(EVAL, "simulation", "interaction_sim"))
import review_panel as rp  # noqa: E402

REAL_DATASETS = os.path.join(EVAL, "offline", "datasets", "phase_a")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The panel's folders in a temporary place, two real datasets copied there, and the catalog
    collected in a list."""
    results, datasets = tmp_path / "results", tmp_path / "phase_a"
    datasets.mkdir()
    for name in ("confirmation.json", "cancel_detect.json"):
        shutil.copyfile(os.path.join(REAL_DATASETS, name), datasets / name)
    monkeypatch.setattr(rp, "RESULTS", str(results))
    monkeypatch.setattr(rp, "REVIEWS", str(results / "reviews"))
    monkeypatch.setattr(rp, "E0B", str(results / "e0b"))
    monkeypatch.setattr(rp, "GATE", str(results / "e0a_phase_a" / "gate.json"))
    monkeypatch.setattr(rp, "DATASETS", str(datasets))
    events = []
    monkeypatch.setattr(rp, "catalog", lambda event, folder, details=None:
                        events.append((event, details or {})))
    return {"results": results, "datasets": datasets, "events": events}


def cases(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["cases"]


# ==== DATASET REVIEW ====

def test_traps_only_by_default_and_filters(env):
    d = env["datasets"]
    all_cases = cases(d / "cancel_detect.json") + cases(d / "confirmation.json")
    traps = [c for c in all_cases if c["trap"]]
    review = rp.DatasetReview("Ana García")
    assert review.reviewer == "ana_garcia"
    assert len(review.items()) == len(traps)
    assert all(c["trap"] for _, c in review.items())
    assert len(rp.DatasetReview("Ana", traps=False).items()) == len(all_cases)
    es = rp.DatasetReview("Ana", language="es").items()
    assert es and all(c["language"] == "es" and c["trap"] for _, c in es)
    only = rp.DatasetReview("Ana", files=["confirmation"], traps=False).items()
    assert {s for s, _ in only} == {"confirmation"}
    # Files in order, cases in file order (no shuffle)
    stems = [s for s, _ in review.items()]
    assert stems == sorted(stems)
    item = review.item(0)
    assert item["fields"][0]["main"] and item["fields"][0]["choices"]
    assert all(len(ch["text"]) == 2 for ch in item["fields"][0]["choices"])   # es, en


def test_marks_are_saved_resumed_and_invalidated_by_an_edit(env):
    review = rp.DatasetReview("Ana", traps=False)
    items = review.items()
    (s0, c0), (s1, c1), (s2, c2) = items[0], items[1], items[2]
    assert s0 == "cancel_detect"
    ok = review.mark({"stem": s0, "id": c0["id"], "hash": rp.case_hash(c0), "verdict": "ok"})
    assert ok["error"] == ""
    flip = {"expected_cancel": not c1["expected_cancel"]}
    assert review.mark({"stem": s1, "id": c1["id"], "hash": rp.case_hash(c1), "verdict": "wrong",
                        "correction": flip, "note": "a doubt"})["error"] == ""
    assert review.mark({"stem": s2, "id": c2["id"], "hash": rp.case_hash(c2),
                        "verdict": "unsure"})["error"] == ""
    # Rejected: a correction that changes nothing, a hash of another version of the case
    assert review.mark({"stem": s0, "id": c0["id"], "hash": rp.case_hash(c0), "verdict": "wrong",
                        "correction": {"expected_cancel": c0["expected_cancel"]}})["error"]
    assert review.mark({"stem": s0, "id": c0["id"], "hash": "x", "verdict": "ok"})["error"]
    path = rp.reviews_path("cancel_detect", "ana")
    assert path.startswith(str(env["results"]))          # private, never in the repository
    store = rp.read_json(path)
    assert store["panel_version"] == rp.PANEL_VERSION and store["first_saved"] <= store["last_saved"]
    # Resumed by a new instance
    again = rp.DatasetReview("Ana", traps=False)
    st = again.state()
    assert st["statuses"][:4] == ["ok", "wrong", "unsure", "pending"] and st["done"] == 3
    assert st["doubts"] == [1, 2]
    # Editing a case invalidates its review
    data = rp.read_json(env["datasets"] / "cancel_detect.json")
    data["cases"][0]["user_text"] += " (edited)"
    with open(env["datasets"] / "cancel_detect.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    assert rp.DatasetReview("Ana", traps=False).state()["statuses"][0] == "stale"


def test_an_invalid_label_is_refused(env):
    review = rp.DatasetReview("Ana", files=["confirmation"], traps=False)
    stem, case = review.items()[0]
    bad = review.mark({"stem": stem, "id": case["id"], "hash": rp.case_hash(case),
                       "verdict": "wrong", "correction": {"expected_intent": "maybe"}})
    assert bad["code"] == "invalid_correction"
    # 'corrects' needs the corrections too (the benchmark's own check)
    if case["expected_intent"] != "corrects":
        bad = review.mark({"stem": stem, "id": case["id"], "hash": rp.case_hash(case),
                           "verdict": "wrong", "correction": {"expected_intent": "corrects",
                                                              "expected_corrections": {}}})
        assert "expected_corrections" in bad["error"]


def test_apply_writes_backup_changelogs_and_catalog(env):
    d = env["datasets"]
    with open(d / "cancel_detect.json", encoding="utf-8") as f:
        original = f.read()
    review = rp.DatasetReview("Ana García", traps=False)
    stem, case = review.items()[0]
    new = not case["expected_cancel"]
    review.mark({"stem": stem, "id": case["id"], "hash": rp.case_hash(case), "verdict": "wrong",
                 "correction": {"expected_cancel": new}, "note": "the user asks to stop"})
    assert rp.apply_reviews("Ana García", dry_run=True, run_check=False, log=lambda s: None)
    with open(d / "cancel_detect.json", encoding="utf-8") as f:
        assert f.read() == original                       # a dry run changes nothing
    changes = rp.apply_reviews("Ana García", run_check=False, log=lambda s: None)
    assert len(changes) == 1
    data = rp.read_json(d / "cancel_detect.json")
    assert data["cases"][0]["expected_cancel"] is new
    with open(d / "cancel_detect.json", encoding="utf-8") as f:
        text = f.read()
    assert text.rstrip("\n") == json.dumps(data, indent=1, ensure_ascii=False)  # same format
    backups = os.listdir(env["results"] / "reviews" / "backups")
    assert len(backups) == 1
    with open(env["results"] / "reviews" / "backups" / backups[0], encoding="utf-8") as f:
        assert f.read() == original
    with open(d / "CHANGELOG.jsonl", encoding="utf-8") as f:
        public = [json.loads(line) for line in f]
    assert public[0]["case_id"] == case["id"] and public[0]["new"] == {"expected_cancel": new}
    assert public[0]["reviewed_by"] == "an author" and "Ana" not in json.dumps(public)
    assert "ana" not in json.dumps(public)
    with open(env["results"] / "reviews" / "changelog.jsonl", encoding="utf-8") as f:
        private = [json.loads(line) for line in f]
    assert private[0]["reviewer"] == "ana_garcia" and private[0]["note"]
    store = rp.read_json(rp.reviews_path("cancel_detect", "ana_garcia"))
    entry = store["reviews"][case["id"]]
    assert entry["applied"] and entry["hash"] == rp.case_hash(data["cases"][0])
    ev = [e for e in env["events"] if e[0] == "dataset_apply"]
    assert ev and ev[0][1]["corrections"] == 1
    assert ev[0][1]["datasets_hash_before"] != ev[0][1]["datasets_hash_after"]
    # Applied once only; the report counts it
    assert rp.apply_reviews("Ana García", run_check=False, log=lambda s: None) == []
    row = next(r for r in rp.dataset_report("Ana García") if r["file"] == "cancel_detect.json")
    assert (row["reviewed"], row["corrected"], row["applied"]) == (1, 1, 1)


# ==== ADJUDICATION ====

def write_gate(env, failures):
    gate = {}
    for model, failed in failures.items():
        gate[model] = {"calls": 10, "critical_cases": [
            {"benchmark": b, "id": i, "language": "en", "category": "x", "reps": [1], "got": []}
            for b, i in failed]}
    path = env["results"] / "e0a_phase_a" / "gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gate), encoding="utf-8")


def test_adjudication_takes_half_or_more_blind_and_frozen(env):
    ids = [c["id"] for c in cases(env["datasets"] / "cancel_detect.json")]
    x, y, z = ("cancel_detect", ids[0]), ("cancel_detect", ids[1]), ("cancel_detect", ids[2])
    write_gate(env, {"modelalpha": [x, y, z], "modelbeta": [x, z], "modelgamma": [z],
                     "modeldelta": []})
    review = rp.DatasetReview("Ana", adjudicate=True)
    assert [c["id"] for _, c in review.items()] == [ids[0], ids[2]]   # 2/4 and 3/4; not 1/4
    sel = rp.read_json(env["results"] / "reviews" / "adjudication" / "selection.json")
    text = json.dumps(sel) + json.dumps(review.item(0)) + json.dumps(review.state())
    assert not any(m in text for m in ("modelalpha", "modelbeta", "modelgamma"))  # never which
    assert '"reps"' not in text and '"got"' not in text                  # nor how many
    assert sel["evaluated_models"] == 4 and set(sel) >= {"cases", "source_sha256", "rule"}
    # Frozen: a new gate does not change the list
    write_gate(env, {"modelalpha": [y], "modelbeta": [y]})
    assert [c["id"] for _, c in rp.DatasetReview("Ana", adjudicate=True).items()] == [ids[0],
                                                                                       ids[2]]
    stem, case = review.items()[0]
    review.mark({"stem": stem, "id": case["id"], "hash": rp.case_hash(case), "verdict": "wrong",
                 "correction": {"expected_cancel": not case["expected_cancel"]}})
    assert rp.reviews_path(stem, "ana", True).startswith(str(env["results"] / "reviews" /
                                                             "adjudication"))
    rp.apply_reviews("Ana", adjudicate=True, run_check=False, log=lambda s: None)
    with open(env["datasets"] / "CHANGELOG.jsonl", encoding="utf-8") as f:
        assert json.loads(f.readline())["source"] == "adjudication, reviewed blind"


def test_adjudication_skips_the_underscore_keys_of_the_gate(env):
    ids = [c["id"] for c in cases(env["datasets"] / "cancel_detect.json")]
    x = ("cancel_detect", ids[0])
    write_gate(env, {"modelalpha": [x], "modelbeta": []})
    path = env["results"] / "e0a_phase_a" / "gate.json"
    gate = json.loads(path.read_text(encoding="utf-8"))
    # Written by offline/core/gate.py: not models, even if they look like one
    gate["_finalists"] = {"en": {"models": ["modelalpha"], "passed": [], "fallback": True,
                                 "calls": 10, "critical_cases": []}}
    gate["_datasets"] = {"cancel_detect": "abc", "calls": 1}
    gate["_extra"] = {"calls": 10, "critical_cases": []}
    path.write_text(json.dumps(gate), encoding="utf-8")
    sel = rp.adjudication_selection()
    assert sel["evaluated_models"] == 2                   # only modelalpha and modelbeta
    assert sel["cases"] == [list(x)]                      # 1 of 2 failed it: half


def test_adjudication_from_the_saved_runs(env):
    ids = [c["id"] for c in cases(env["datasets"] / "cancel_detect.json")]
    out = env["results"] / "e0a_phase_a"
    for model, crit in (("m1", True), ("m2", False)):
        (out / model / "rep1").mkdir(parents=True)
        (out / model / "rep1" / "cancel_detect.json").write_text(json.dumps(
            {"cases": [{"id": ids[0], "critical": "false_cancel" if crit else None}]}))
    assert rp.adjudication_selection()["cases"] == [["cancel_detect", ids[0]]]


# ==== ADHERENCE AUDIT ====

def record(lang, persona, model, said="A medium latte, please."):
    return {"language": lang, "persona": persona, "model": model, "scenario": "coffee",
            "outcome": "success", "expect": {},
            "goal_items": [{"id": "drink", "value": "latte"}, {"id": "size", "value": "medium"},
                           {"id": "name", "value": "Laura"}],
            "user_log": [{"turn": 1, "robot": "What would you like?", "text": said,
                          "revealed": [{"id": "drink", "value": "latte"},
                                       {"id": "size", "value": "medium"}]}],
            "transcript": [{"role": "robot", "text": "What would you like?"},
                           {"role": "user", "text": said}]}


def make_runs(root, per=5):
    for model in ("m1", "m2"):
        for persona in ("cooperative", "terse"):
            folder = root / "runs" / model / "single"
            folder.mkdir(parents=True, exist_ok=True)
            for k in range(per):
                lang = "English" if k % 2 else "Spanish"
                (folder / f"s{k}__{persona}__r1.json").write_text(
                    json.dumps(record(lang, persona, model)), encoding="utf-8")
    (root / "runs" / "m1" / "single" / "nogoal.json").write_text(json.dumps({"user_log": []}))


def test_the_sample_is_seeded_and_stratified(env):
    exp = env["results"] / "exp"
    make_runs(exp)
    pop = rp.eligible(str(exp))
    assert len(pop) == 20                                    # the record without goal items: n/a
    a, strata = rp.draw_sample(pop, 8, seed=3)
    b, _ = rp.draw_sample(pop, 8, seed=3)
    c, _ = rp.draw_sample(pop, 8, seed=4)
    assert a == b and a != c and len(a) == 8 and len(set(a)) == 8
    assert sum(t for _, t in strata.values()) == 8
    assert all(abs(t - 8 * p / 20) < 1 for p, t in strata.values())   # proportional
    audit = rp.AdherenceAudit(str(exp), "Bea", n=8, seed=3)
    saved = rp.read_json(rp.audit_path(str(exp), "bea"))
    assert saved["sample"] == a and (saved["seed"], saved["n"]) == (3, 8)
    assert saved["panel_version"] == rp.PANEL_VERSION and saved["first_saved"]
    assert rp.AdherenceAudit(str(exp), "Bea").audit["sample"] == a       # resumed as drawn
    with pytest.raises(SystemExit):
        rp.AdherenceAudit(str(exp), "Bea", n=9)
    assert audit.state()["total"] == 8


def test_audit_marks_and_report(env):
    exp = env["results"] / "exp"
    make_runs(exp, per=1)
    audit = rp.AdherenceAudit(str(exp), "Bea", n=4, seed=1)
    for i, rel in enumerate(audit.audit["sample"]):
        item = audit.item(i)
        keys = [x["key"] for x in item["items"]]
        assert keys == ["d0", "d1", "u:name", "verdict"]
        for key in keys:
            mark = "incorrect" if (i == 0 and key == "d1") else "correct"
            assert audit.mark({"run": rel, "key": key, "mark": mark})["error"] == ""
    audit.mark({"run": audit.audit["sample"][1], "key": "d0", "mark": "unsure"})
    assert audit.state()["done"] == 4 and audit.state()["doubts"] == [1]
    rep = rp.adherence_report(rp.read_json(audit.path))
    assert rep["items"]["n"] == 4 * 3 - 1 and rep["items"]["correct"] == 4 * 3 - 2
    assert rep["items:declared"]["n"] == 7 and rep["items:unrevealed"]["n"] == 4
    assert rep["verdict"]["n"] == 4 and rep["verdict"]["accuracy"] == 1.0
    assert rep["unsure_marks"] == 1
    lo, hi = rp.wilson(10, 11)
    assert rep["items"]["ci95"] == [round(lo, 4), round(hi, 4)]
    assert rep["dialogues_every_item_correct"]["n"] == 3     # one dialogue has an unsure item


def test_wilson_interval():
    lo, hi = rp.wilson(8, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.4902, 0.9433)
    lo, hi = rp.wilson(0, 10)
    assert lo == 0.0 and round(hi, 4) == 0.2775
    lo, hi = rp.wilson(10, 10)
    assert hi == pytest.approx(1.0) and round(lo, 4) == 0.7225
    assert all(v != v for v in rp.wilson(0, 0))


# ==== ANNOTATION SHEETS ====

def write_sheet(path, n=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(rp.SHEET_COLUMNS)
        for k in range(1, n + 1):
            w.writerow([k, "Museum desk", "Resources: closed on Mondays",
                        "User: Hi\nRobot: Hello, how can I help?", f"Utterance {k}, «ñ»", "", "",
                        ""])


def test_sheet_round_trip_with_read_sheet_and_ownership(env):
    import judge_validation
    a, b = rp.sheet_path("es", "a"), rp.sheet_path("es", "b")
    write_sheet(__import__("pathlib").Path(a))
    write_sheet(__import__("pathlib").Path(b))
    body = {"name": "Ana García", "task": "annotate", "lang": "es", "sheet": "a"}
    summary = rp.open_session(body, {}, dry=True)
    assert summary["total"] == 3 and summary["done"] == 0 and not os.path.exists(
        os.path.join(rp.REVIEWS, "registry.json"))                # a dry run writes nothing
    session = rp.open_session(body, {})
    app = session.app
    assert app.mark({"index": 0, "id": "1", "in_frame": "1", "unsupported": "0"})["error"] == ""
    assert app.mark({"index": 1, "id": "2", "in_frame": "0"})["error"] == ""
    assert app.mark({"index": 2, "id": "3", "in_frame": "1", "unsupported": "1",
                     "note": "unsure, «quoted», a, comma"})["error"] == ""
    assert app.mark({"index": 0, "id": "9", "in_frame": "1"})["code"] == "changed_on_disk"
    assert app.mark({"index": 0, "id": "1", "in_frame": "2"})["error"]
    got = judge_validation.read_sheet(a)
    assert got == {"1": {"in_frame": 1, "unsupported": 0}, "3": {"in_frame": 1, "unsupported": 1}}
    header, rows = rp.read_csv(a)
    assert header == rp.SHEET_COLUMNS and rows[2]["note"] == "unsure, «quoted», a, comma"
    assert rows[0]["conversation_before"] == "User: Hi\nRobot: Hello, how can I help?"
    assert app.state()["statuses"] == ["done", "partial", "done"]
    assert app.state()["doubts"] == [2]
    progress = rp.read_json(app.progress_path())
    assert progress["panel_version"] == rp.PANEL_VERSION and progress["done"] == 2
    # Resumed: a new session sees the answers
    again = rp.open_session(body, {})
    assert again.resumed and again.app.state()["done"] == 2
    # Ownership: the sheet is Ana's; Ana cannot take the other sheet of the language
    with pytest.raises(rp.PanelError) as e:
        rp.open_session({"name": "Bea", "task": "annotate", "lang": "es", "sheet": "a"}, {})
    assert e.value.code == "sheet_taken"
    with pytest.raises(rp.PanelError) as e:
        rp.open_session(dict(body, sheet="b"), {}, dry=True)
    assert e.value.code == "other_sheet" and e.value.params == {"sheet": "A"}
    assert rp.open_session({"name": "Bea", "task": "annotate", "lang": "es", "sheet": "b"}, {})
    # A locked panel accepts its own assignment only
    preset = {"task": "annotate", "name": "Bea", "lang": "es", "sheet": "b", "locked": True}
    with pytest.raises(rp.PanelError) as e:
        rp.open_session(dict(body, name="Bea", sheet="a"), preset, dry=True)
    assert e.value.code == "locked"
    with pytest.raises(rp.PanelError) as e:
        rp.open_session({"name": "Bea", "task": "annotate", "lang": "en", "sheet": "a"}, {})
    assert e.value.code == "sheet_missing"
    # The catalog: starts and the end, with counts
    done = rp.finish_session(again)
    assert any(f.endswith("sheet_a.csv") for f in done["files"])
    names = [e for e, _ in env["events"]]
    assert names.count("review_start") == 3 and names[-1] == "review_done"
    assert env["events"][-1][1]["done"] == 2 and env["events"][-1][1]["total"] == 3


def test_names_and_sessions(env):
    assert rp.slug("  José Ángel  Núñez ") == "jose_angel_nunez"
    with pytest.raises(rp.PanelError):
        rp.person(" ")
    s = rp.open_session({"name": "Ana", "task": "dataset", "scope": "traps"}, {})
    assert s.seconds_per_item() == rp.DEFAULT_SECONDS["dataset"]
    st = s.state()
    assert st["eta_s"] == st["total"] * rp.DEFAULT_SECONDS["dataset"]
    s.times = [0, 10, 20, 30]
    assert s.seconds_per_item() == 10


def test_the_spanish_guide_translates_the_current_section():
    text, current = rp.guide_es()
    assert current, "annotation_guide_es.md: update the translation and its source-sha256"
    html = rp.md_to_html(text)
    assert 'id="examples"' in html and "<table" in html and "<!--" not in html
    assert 'id="examples"' in rp.md_to_html(rp.guide_section())


def test_the_page_is_served(env):
    rp.PAGE = rp.load_page()
    server = rp.bind(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "<title>" in page and "TUTORIAL" in page
        cfg = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config").read())
        assert cfg["version"] == rp.PANEL_VERSION and "examples" in cfg["guide"]["es"]
        state = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state").read())
        assert state["code"] == "no_session"                    # nothing without a session
    finally:
        server.shutdown()
        server.server_close()


def test_a_whole_session_over_http(env):
    rp.PAGE = rp.load_page()
    server = rp.bind(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, cookie=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(
            body).encode(), headers={"Content-Type": "application/json",
                                     **({"Cookie": cookie} if cookie else {})})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), r.headers.get("Set-Cookie")
    try:
        start = {"name": "Ana", "task": "dataset", "scope": "traps", "case_language": "es"}
        summary, _ = call("/api/check", start)
        assert summary["summary"]["total"] > 0 and not os.path.exists(rp.REVIEWS)
        started, cookie = call("/api/start", start)
        cookie = cookie.split(";")[0]
        assert started["state"]["done"] == 0 and started["state"]["eta_s"] > 0
        item, _ = call("/api/item?i=0", cookie=cookie)
        assert item["case"]["language"] == "es" and item["case"]["trap"]
        marked, _ = call("/api/mark", {"stem": item["stem"], "id": item["case"]["id"],
                                       "hash": item["hash"], "verdict": "ok"}, cookie)
        assert marked["error"] == "" and marked["state"]["done"] == 1
        done, _ = call("/api/finish", {}, cookie)
        assert done["counts"]["ok"] == 1 and done["files"]
        assert [e for e, _ in env["events"]] == ["review_start", "review_done"]
        reg = rp.read_json(os.path.join(rp.REVIEWS, "registry.json"))
        assert reg["people"]["ana"]["tasks"]["dataset:traps:es"]["finished"]
        assert call("/api/item?i=0")[0]["code"] == "no_session"          # no cookie, nothing
    finally:
        server.shutdown()
        server.server_close()


def test_the_assistants_prereview_is_shown_but_never_in_the_blind_adjudication(tmp_path,
                                                                                monkeypatch):
    # Decided by the author (2026-11-01): the pre-review (verdict and note per case) is shown
    # next to the proposed answer; the adjudication stays blind
    import review_panel as rp
    pre = tmp_path / "prereview.json"
    pre.write_text('{"cd_en_014": {"verdict": "doubt", "note": "check"}}')
    monkeypatch.setattr(rp, "PREREVIEW", str(pre))
    assert rp.load_prereview()["cd_en_014"]["verdict"] == "doubt"
    monkeypatch.setattr(rp, "PREREVIEW", str(tmp_path / "missing.json"))
    assert rp.load_prereview() == {}
