# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Tests of the runners of E0a and E0b (run_e0a.py, run_e0b.py), the GPU lock, the catalog of the
results and the checks of run_experiment.py, without ROS, GPU server, LLM or network: the
servers, the subprocesses and the ROS modules are stubs.

    cd evaluation && python3 -m pytest -q scripts/tests
"""

import json
import os
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, ".."))
EVAL = os.path.normpath(os.path.join(SCRIPTS, ".."))
for path in (SCRIPTS, os.path.join(EVAL, "analysis"), os.path.join(EVAL, "offline", "core"),
             os.path.join(EVAL, "simulation", "interaction_sim")):
    if path not in sys.path:
        sys.path.insert(0, path)

import gpu_lock  # noqa: E402
import ollama_models  # noqa: E402
import provenance  # noqa: E402
import run_e0a  # noqa: E402
import run_e0b  # noqa: E402


@pytest.fixture(autouse=True)
def catalog_in_tmp(tmp_path, monkeypatch):
    """Every test writes its catalog in a temporary folder, never in evaluation/results."""
    monkeypatch.setattr(provenance, "RESULTS", str(tmp_path / "results"))
    monkeypatch.setattr(provenance, "CATALOG", str(tmp_path / "results" / "CATALOG.jsonl"))
    return tmp_path / "results" / "CATALOG.jsonl"


@pytest.fixture(autouse=True)
def lock_in_tmp(tmp_path, monkeypatch):
    """The GPU lock of every test in a temporary folder."""
    held = gpu_lock.held
    monkeypatch.setattr(gpu_lock, "held", lambda what, path=None: held(
        what, str(tmp_path / ".gpu_run.lock")))


def test_phase_a_resumes_with_other_code_only_when_asked(monkeypatch):
    # --force-provenance: phase A begun before a change that cannot alter its saved runs (the
    # selective-reasoning variants added after the freeze, 2026-11-02)
    commands = []
    monkeypatch.setattr(run_e0a, "run", lambda cmd, cwd=None: commands.append(cmd))
    run_e0a.phase_a(types.SimpleNamespace())
    run_e0a.phase_a(types.SimpleNamespace(force_provenance=True))
    assert "--force-provenance" not in commands[0] and commands[1][-1] == "--force-provenance"


def _catalog(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# ==== The catalog ====

def test_catalog_lines_have_time_event_folder_and_details(catalog_in_tmp, tmp_path):
    provenance.catalog("finish", str(tmp_path / "results" / "e0a_dialogues"), {"saved": 3})
    entry = _catalog(catalog_in_tmp)[-1]
    assert entry["event"] == "finish" and entry["folder"] == "e0a_dialogues"
    assert entry["saved"] == 3 and "T" in entry["time"]


def test_provenance_records_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "code_hash", lambda: "h")
    monkeypatch.setattr(provenance, "git_state", lambda: {"head": "x", "dirty": False})
    monkeypatch.setattr(provenance.requests, "get", lambda url, timeout=None: types.SimpleNamespace(
        json=lambda: {"version": "0.12.6"} if url.endswith("/api/version") else {"models": []}))
    prov = provenance.record(str(tmp_path / "run"), ["m:1b"], "http://x")
    env = prov["environment"]
    assert env["ollama_version"] == "0.12.6" and env["python"]
    assert "unknown" in env["server_ollama_env"]


# ==== The GPU lock ====

def test_the_lock_refuses_a_second_campaign_and_takes_over_a_stale_one(tmp_path):
    lock = str(tmp_path / ".gpu_run.lock")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with open(lock, "w") as f:
            json.dump({"pid": other.pid, "command": "run_e0b.py label"}, f)
        with pytest.raises(SystemExit) as stop:
            gpu_lock.acquire("run_e0a.py phase-b", lock)
        assert "run_e0b.py label" in str(stop.value)
    finally:
        other.kill()
        other.wait()
    # The holder is gone: the lock is stale and taken over, then released
    with gpu_lock.held("run_e0a.py phase-b", lock):
        assert json.load(open(lock))["pid"] == os.getpid()
    assert not os.path.exists(lock)


# ==== run_e0a: the server ====

class Server:
    def __init__(self, installed=(), loaded=()):
        self.installed, self.loaded, self.events = set(installed), set(loaded), []


@pytest.fixture
def server(monkeypatch):
    srv = Server()
    for mod in (run_e0a, run_e0b):
        monkeypatch.setattr(mod.ollama_models, "installed", lambda base: set(srv.installed))
        monkeypatch.setattr(mod.ollama_models, "unload",
                            lambda base, m, **k: srv.events.append(("unload", m))
                            or srv.loaded.discard(m) or True)
        def unload_all(base, ms):
            for m in [m for m in ms if m in srv.loaded]:
                srv.events.append(("unload", m))
                srv.loaded.discard(m)

        monkeypatch.setattr(mod.ollama_models, "unload_all", unload_all)

        def remove(base, m):
            srv.events.append(("remove", m))
            srv.installed.discard(m)
            return True

        monkeypatch.setattr(mod.ollama_models, "remove", remove)

        def pull(base, m):
            srv.events.append(("pull", m))
            srv.installed.add(m)

        monkeypatch.setattr(mod.ollama_models, "pull", pull)
    return srv


def test_remove_of_a_model_already_gone_does_not_stop_a_resume(server, tmp_path, monkeypatch):
    monkeypatch.setattr(run_e0a, "PULLED", str(tmp_path / "pulled.json"))
    pulled = ["gone:7b"]
    run_e0a.remove("gone:7b", pulled)
    assert pulled == [] and ("remove", "gone:7b") not in server.events


def test_phase_b_unloads_each_finalist_and_removes_what_it_pulled(server, tmp_path, monkeypatch):
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    (phase_a / "finalists_en.txt").write_text("# fallback: true (note)\na:1b\nb:2b\n")
    (phase_a / "finalists_es.txt").write_text("b:2b\n")
    (phase_a / "gate.json").write_text(json.dumps({"a:1b": {}, "last:9b": {}, "_finalists": {}}))
    monkeypatch.setattr(run_e0a, "PHASE_A", str(phase_a))
    monkeypatch.setattr(run_e0a, "PULLED", str(tmp_path / "e0a" / "pulled.json"))
    server.installed = {"a:1b"}         # b must be pulled (by run_experiment's ensure_models)
    server.loaded = {"last:9b"}         # phase A's last model, still in memory
    commands = []

    def fake_run(cmd, cwd=None):
        commands.append(cmd)
        if "--models" in cmd:
            m = cmd[cmd.index("--models") + 1]
            server.installed.add(m)
            server.loaded.add(m)

    monkeypatch.setattr(run_e0a, "run", fake_run)
    run_e0a.phase_b(types.SimpleNamespace(allow_cloud=True))
    server.events = [e for e in server.events if "gemma3:12b" not in e[1]]  # the annotator
    assert server.events[0] == ("unload", "last:9b")
    # a: both of its runs then unloaded, kept (it was on the server); b: unloaded and removed
    assert ("unload", "a:1b") in server.events and ("remove", "a:1b") not in server.events
    assert server.events.index(("unload", "b:2b")) < server.events.index(("remove", "b:2b"))
    assert not server.loaded and json.load(open(tmp_path / "e0a" / "pulled.json")) == []
    runs = [c[c.index("--models") + 1] for c in commands if "--models" in c]
    assert runs == ["a:1b", "b:2b", "b:2b"]


def test_phase_b_handles_a_reasoning_variant_by_its_base_name(server, tmp_path, monkeypatch):
    # "<model>@critical" is the same model on the server: pulled once, unloaded by its base name
    # after each variant (it stayed in GPU memory beside the next one), removed after the last
    # (review before running phase B, 2026-11-02)
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    (phase_a / "finalists_en.txt").write_text("q:8b\nq:8b@critical\n")
    (phase_a / "finalists_es.txt").write_text("q:8b@critical\n")
    monkeypatch.setattr(run_e0a, "PHASE_A", str(phase_a))
    monkeypatch.setattr(run_e0a, "PULLED", str(tmp_path / "e0a" / "pulled.json"))
    server.installed = {"gemma3:12b"}

    def fake_run(cmd, cwd=None):
        if "--models" in cmd:
            server.installed.add("q:8b")
            server.loaded.add("q:8b")

    monkeypatch.setattr(run_e0a, "run", fake_run)
    run_e0a.phase_b(types.SimpleNamespace(allow_cloud=True))
    ev = [e for e in server.events if "gemma3:12b" not in e[1]]
    assert ev == [("unload", "q:8b"), ("unload", "q:8b"), ("remove", "q:8b")]
    assert not server.loaded and "q:8b" not in server.installed
    assert json.load(open(tmp_path / "e0a" / "pulled.json")) == []


def test_phase_b_annotates_its_dialogues_with_the_local_annotator(server, tmp_path, monkeypatch):
    # The grounded-QA scoring and the adherence check read the annotator's marks: phase B makes
    # them after the dialogues (pulling the annotator if missing, removing it after) and decide
    # refuses dialogues without them (review before running phase B, 2026-11-02)
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    (phase_a / "finalists_en.txt").write_text("a:1b\n")
    (phase_a / "finalists_es.txt").write_text("")
    monkeypatch.setattr(run_e0a, "PHASE_A", str(phase_a))
    monkeypatch.setattr(run_e0a, "PULLED", str(tmp_path / "e0a" / "pulled.json"))
    server.installed = {"a:1b"}
    commands = []
    monkeypatch.setattr(run_e0a, "run", lambda cmd, cwd=None: commands.append(cmd))
    run_e0a.phase_b(types.SimpleNamespace(allow_cloud=True))
    annotated = [c for c in commands if any(str(x).endswith("annotate_turns.py") for x in c)]
    assert len(annotated) == 1 and run_e0a.PHASE_B["en"][1] in annotated[0]
    assert commands.index(annotated[0]) > max(i for i, c in enumerate(commands)
                                              if "--models" in c)
    assert ("pull", "gemma3:12b") in server.events and ("remove", "gemma3:12b") in server.events
    out = tmp_path / "out"
    rec = out / "runs" / "a:1b" / "bounded" / "x.json"
    rec.parent.mkdir(parents=True)
    rec.write_text("{}")
    assert run_e0a.unannotated(str(out)) == [str(rec)]
    side = run_e0a.annotate_turns.sidecar(str(rec))
    os.makedirs(os.path.dirname(side))
    open(side, "w").write("{}")
    assert run_e0a.unannotated(str(out)) == []


def test_phase_b_crash_keeps_the_pull_for_the_resume(server, tmp_path, monkeypatch):
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    (phase_a / "finalists_en.txt").write_text("b:2b\n")
    (phase_a / "finalists_es.txt").write_text("")
    monkeypatch.setattr(run_e0a, "PHASE_A", str(phase_a))
    pulled_path = tmp_path / "e0a" / "pulled.json"
    monkeypatch.setattr(run_e0a, "PULLED", str(pulled_path))

    def crash(cmd, cwd=None):
        server.installed.add("b:2b")
        server.loaded.add("b:2b")
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(run_e0a, "run", crash)
    with pytest.raises(subprocess.CalledProcessError):
        run_e0a.phase_b(types.SimpleNamespace(allow_cloud=True))
    assert json.load(open(pulled_path)) == ["b:2b"] and not server.loaded  # unloaded anyway


def test_all_stops_after_phase_a(monkeypatch):
    with pytest.raises(SystemExit):  # phase B pays: never without --allow-cloud
        run_e0a.phase_b(types.SimpleNamespace(allow_cloud=False))
    done = []
    monkeypatch.setattr(run_e0a, "preflight", lambda args: done.append("preflight"))
    monkeypatch.setattr(run_e0a, "phase_a", lambda args: done.append("phase-a"))
    monkeypatch.setattr(run_e0a, "phase_b", lambda args: done.append("phase-b"))
    run_e0a.all_(types.SimpleNamespace(allow_cloud=False))
    assert done == ["preflight", "phase-a"]


# ==== run_e0a: the decision ====

def _units(successes, unsafe=()):
    """{(scenario, persona, rep): unit} from a list of 0/1 per unit (3 personas, 3 reps; the
    first unit excluded, the ones in `unsafe` with a silent unsafe error)."""
    out = {}
    for k, s in enumerate(successes):
        key = (f"s{k // 9}", f"p{(k // 3) % 3}", k % 3 + 1)
        row = {"model": "m", "scenario": key[0], "persona": key[1], "rep": key[2],
               "strategy": "quiz", "success": bool(s), "unsafe": ["x"] if k in unsafe else [],
               "ok": bool(s) and k not in unsafe, "excluded": "off_script" if k == 0 else "",
               "annotated": True}
        out[key] = {"success": s, "latencies": [1.0, 3.0], "excluded": k == 0, "fillers": 1,
                    "row": row}
    return out


def test_the_highest_success_among_those_that_fit_the_gpu():
    n = 18
    units = {"small:4b": _units([1] * 12 + [0] * 6),
             "mid:8b": _units([1] * 15 + [0] * 3),
             "big:32b": _units([1] * 18)}
    sizes = {"small:4b": 4.0, "mid:8b": 8.0, "big:32b": 32.0}
    fits = {"small:4b": {"fits": True}, "mid:8b": {"fits": True}, "big:32b": {"fits": False}}
    d = run_e0a.decide_language(list(units), units, n, sizes, fits)
    assert d["state"] == "chosen" and d["chosen"] == "mid:8b"  # big is better but offloads
    assert d["not_eligible"] == {"big:32b": "does not fit the GPU (CPU offload)"}
    diff = d["differences"]["small:4b"]
    # The excluded unit is left out of the pairs (and of the success)
    assert diff["d"] == pytest.approx(3 / 17, abs=1e-4) and diff["clusters"] == 6
    assert diff["ci95"][0] <= diff["d"] <= diff["ci95"][1]
    f = d["figures"]["mid:8b"]
    assert f["latency_p50_s"] == 2.0 and f["turns_over_2s"] == 0.5 and f["fillers"] == n
    assert f["sim_excluded_rate"] == round(1 / 18, 4) and f["excluded"] == 1
    assert f["success"] == round(14 / 17, 4) and f["success_all"] == round(15 / 18, 4)
    assert f["q"] == pytest.approx(100 * (1 + 1 + 0.5) / 3, abs=0.01)  # equal user-type weights
    # A model whose fit was not recorded is not eligible either
    d = run_e0a.decide_language(["mid:8b"], units, n, sizes, {})
    assert d["chosen"] is None and "unknown" in d["not_eligible"]["mid:8b"]


def test_ties_go_to_the_smaller_model_then_the_name():
    units = {m: _units([1] * 9 + [0] * 9) for m in ("b:8b", "a:8b", "c:4b")}
    fits = {m: {"fits": True} for m in units}
    d = run_e0a.decide_language(list(units), units, 18, {"b:8b": 8, "a:8b": 8, "c:4b": 4}, fits)
    assert d["chosen"] == "c:4b"
    d = run_e0a.decide_language(["b:8b", "a:8b"], units, 18, {"b:8b": 8, "a:8b": 8}, fits)
    assert d["chosen"] == "a:8b"


def test_q_is_reported_and_decides_when_asked_with_its_tie_break():
    fits = {m: {"fits": True} for m in ("a:8b", "b:4b", "c:4b")}
    # a has the highest success but two unsafe dialogues; b and c are close behind it
    units = {"a:8b": _units([1] * 18, unsafe=(3, 4)), "b:4b": _units([1] * 17 + [0]),
             "c:4b": _units([1] * 16 + [0] * 2)}
    sizes = {"a:8b": 8, "b:4b": 4, "c:4b": 4}
    critical = {"a:8b": 1, "b:4b": 5, "c:4b": 2}
    d = run_e0a.decide_language(list(units), units, 18, sizes, fits, critical)
    assert d["decided_by"] == "success" and d["chosen"] == d["by_success"] == "a:8b"
    bq = d["by_q"]
    assert bq["top"] == "b:4b" and set(bq["tied"]) == {"a:8b", "b:4b", "c:4b"}
    assert bq["chosen"] == "a:8b"  # tied by Q, and the fewest critical cases of phase A
    assert sum(bq["smaa_first"].values()) == pytest.approx(1)
    d = run_e0a.decide_language(list(units), units, 18, sizes, fits, critical, by="q")
    assert d["decided_by"] == "q" and d["chosen"] == "a:8b"
    # Without the critical counts of phase A the size decides among the tied, then the name
    d = run_e0a.decide_language(list(units), units, 18, sizes, fits, {}, by="q")
    assert d["chosen"] == "b:4b"


def test_an_incomplete_language_chooses_nothing():
    units = {"a:4b": _units([1] * 18), "b:8b": _units([1] * 17)}
    d = run_e0a.decide_language(list(units), units, 18, {}, {m: {"fits": True} for m in units})
    assert d["state"] == "incomplete" and d["short"] == {"b:8b": 17} and d["chosen"] is None


def test_the_planned_dialogues_come_from_the_experiment_files():
    assert run_e0a.planned_units("e0a_dialogues.yaml") == 189
    assert run_e0a.planned_units("e0a_dialogues_es.yaml") == 189


def _record(model, scenario, persona, rep, ok):
    return {"model": model, "condition": "bounded", "strategy": "slot_filling",
            "scenario": scenario, "persona": persona, "rep": rep, "status": "succeeded",
            "total_turns": 4, "duration_s": 10.0, "failure_reason": "", "success": True,
            "outcome": "completed", "expect": {"outcome": "success", "frame": {"x": "1"}},
            "data": {"x": "1" if ok else "2"},
            "transcript": [{"role": "user", "text": "u", "t": 0.0},
                           {"role": "robot", "text": "r", "t": 1.5}]}


def test_decide_end_to_end_on_synthetic_records(tmp_path, monkeypatch, catalog_in_tmp):
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    gate = {"a:4b": {"size_b": 4.0}, "b:8b": {"size_b": 8.0},
            "_finalists": {"en": {"fallback": True, "note": "no model passed the safety gate in "
                                  "en: fallback ..."}}}
    (phase_a / "gate.json").write_text(json.dumps(gate))
    (phase_a / "model_info.json").write_text(json.dumps(
        {"a:4b": {"gpu": {"size": 5, "size_vram": 5, "fits": True}},
         "b:8b": {"gpu": {"size": 9, "size_vram": 9, "fits": True}},
         "c:2b": {"gpu": {"size": 3, "size_vram": 3, "fits": True}}}))
    for lang in ("en", "es"):
        (phase_a / f"finalists_{lang}.txt").write_text("a:4b\nb:8b\nc:2b\n")
    folders = {}
    for lang in ("en", "es"):
        out = tmp_path / f"b_{lang}"
        # c is the best in Spanish only; it offloads to the CPU (phase B's record overrides
        # phase A's): not eligible
        for model, good in (("a:4b", 5), ("b:8b", 6 if lang == "en" else 4),
                            ("c:2b", 6 if lang == "es" else 1)):
            run_dir = out / "runs" / model.replace(":", "_") / "bounded"
            run_dir.mkdir(parents=True)
            for k in range(6):
                s, p, r = f"s{k // 3}", "cooperative", k % 3 + 1
                (run_dir / f"{s}__{p}__r{r}.json").write_text(
                    json.dumps(_record(model, s, p, r, k < good)))
        folders[lang] = ("x.yaml", str(out))
    (tmp_path / "b_es" / "model_fit.json").write_text(json.dumps(
        {"c:2b": {"size": 3, "size_vram": 1, "fits": False}}))
    monkeypatch.setattr(run_e0a, "PHASE_A", str(phase_a))
    monkeypatch.setattr(run_e0a, "PHASE_B", folders)
    monkeypatch.setattr(run_e0a, "OUT", str(tmp_path / "e0a"))
    monkeypatch.setattr(run_e0a, "planned_units", lambda experiment: 6)
    monkeypatch.setattr(run_e0a, "model_digests", lambda models: {m: "sha" for m in models})
    monkeypatch.setattr(run_e0a, "unannotated", lambda out: [])  # annotated (no marks needed)
    run_e0a.decide(types.SimpleNamespace())
    result = json.load(open(tmp_path / "e0a" / "decision.json"))
    assert result["languages"]["en"]["chosen"] == "b:8b"
    assert result["languages"]["es"]["chosen"] == "a:4b"   # c is better but offloads
    assert result["languages"]["es"]["gpu"]["c:2b"]["source"] == "phase B"
    assert result["digests"] == {"a:4b": "sha", "b:8b": "sha"}
    md = (tmp_path / "e0a" / "decision.md").read_text()
    assert "fallback" in md and "Shiwa et al. 2009" in md and "no (CPU offload)" in md
    assert "By Q:" in md and "Decided by: success" in md
    entry = _catalog(catalog_in_tmp)[-1]
    assert entry["event"] == "decision" and entry["chosen"] == {"en": "b:8b", "es": "a:4b"}


def test_apply_logs_the_files_and_warns_about_resumes(tmp_path, monkeypatch, capsys,
                                                       catalog_in_tmp):
    (tmp_path / "e0a").mkdir()
    (tmp_path / "e0a" / "decision.json").write_text(json.dumps(
        {"languages": {"en": {"chosen": "a:4b", "state": "chosen"},
                       "es": {"chosen": None, "state": "incomplete"}}}))
    target = tmp_path / "exp.yaml"
    target.write_text("models: [qwen3:8b]\n")
    monkeypatch.setattr(run_e0a, "OUT", str(tmp_path / "e0a"))
    monkeypatch.setattr(run_e0a, "EVAL", str(tmp_path))
    monkeypatch.setattr(run_e0a, "TARGETS", {"en": ["exp.yaml"], "es": []})
    run_e0a.apply(types.SimpleNamespace())
    assert target.read_text() == "models: [a:4b]\n"
    out = capsys.readouterr().out
    assert "cannot be resumed" in out and "incomplete" in out
    entry = _catalog(catalog_in_tmp)[-1]
    assert entry["event"] == "apply" and entry["files"] == ["exp.yaml"]


# ==== run_e0b ====

def test_a_gemma_system_skips_every_google_judge(monkeypatch):
    monkeypatch.setattr(run_e0b, "system_families", lambda: {"google"})
    local, cloud = run_e0b.ladder(types.SimpleNamespace(openai=True, allow_cloud=True))
    assert all(not j[2].startswith(("gemma", "gemini")) for j in local + cloud)
    assert [j[0] for j in cloud] == ["openai", "openai"]


def test_no_judge_of_another_family_stops(monkeypatch):
    monkeypatch.setattr(run_e0b, "LOCAL_JUDGES", [("ollama", "gemma3:27b", "gemma3_27b")])
    monkeypatch.setattr(run_e0b, "system_families", lambda: {"google"})
    with pytest.raises(SystemExit) as stop:
        run_e0b.ladder(types.SimpleNamespace(openai=False, allow_cloud=True))
    assert "No judge of another family" in str(stop.value)


def test_label_handles_one_judge_at_a_time(server, tmp_path, monkeypatch, catalog_in_tmp):
    monkeypatch.setattr(run_e0b, "PULLED", str(tmp_path / "e0b" / "pulled.json"))
    monkeypatch.setattr(run_e0b, "RESULTS", str(tmp_path / "e0b"))
    monkeypatch.setattr(run_e0b, "system_families", lambda: {"qwen"})
    monkeypatch.setattr(run_e0b, "LOCAL_JUDGES", [("ollama", "j1:7b", "j1"),
                                                  ("ollama", "j2:7b", "j2"),
                                                  ("ollama", "j3:7b", "j3")])
    monkeypatch.setattr(run_e0b, "pending", lambda tag, half=None: 0 if tag == "j2" else 5)
    monkeypatch.setattr(run_e0b.provenance, "add_models",
                        lambda folder, models, base: server.events.append(("digest", models[0])))
    server.installed = {"j3:7b"}
    cmds = []

    def fake_run(cmd):
        cmds.append(cmd)
        if "judge_runs.py" in " ".join(cmd):
            server.events.append(("label", cmd[cmd.index("--tag") + 1]))
        if "sheets" in cmd:
            folder = cmd[cmd.index("sheets") + 1]
            os.makedirs(os.path.join(folder, "e0"), exist_ok=True)

    monkeypatch.setattr(run_e0b, "run", fake_run)
    run_e0b.label(types.SimpleNamespace(allow_cloud=False, openai=False, keep_models=False,
                                        development_only=False, n=10))
    ev = server.events
    # j1: pulled, labelled in both languages, unloaded and removed before j3 starts
    assert ev[:2] == [("pull", "j1:7b"), ("digest", "j1:7b")]
    assert ev.index(("remove", "j1:7b")) < ev.index(("digest", "j3:7b"))
    assert ("pull", "j2:7b") not in ev                     # nothing to do: not pulled
    assert ("pull", "j3:7b") not in ev and ("remove", "j3:7b") not in ev  # it was there
    assert ("unload", "j3:7b") in ev
    assert json.load(open(tmp_path / "e0b" / "pulled.json")) == []
    entry = _catalog(catalog_in_tmp)[-1]
    assert entry["event"] == "label" and entry["judges"] == ["j1", "j3"]
    assert sorted(entry["sheets_made"]) == ["en", "es"]


def test_development_only_labels_one_half_and_stops(server, tmp_path, monkeypatch):
    monkeypatch.setattr(run_e0b, "PULLED", str(tmp_path / "pulled.json"))
    monkeypatch.setattr(run_e0b, "RESULTS", str(tmp_path / "e0b"))
    monkeypatch.setattr(run_e0b, "system_families", lambda: {"qwen"})
    monkeypatch.setattr(run_e0b, "LOCAL_JUDGES", [("ollama", "j1:7b", "j1")])
    seen = {}
    monkeypatch.setattr(run_e0b, "pending", lambda tag, half=None: seen.setdefault("half", half)
                        and 3)
    monkeypatch.setattr(run_e0b.provenance, "add_models", lambda *a: None)
    server.installed = {"j1:7b"}
    cmds = []
    monkeypatch.setattr(run_e0b, "run", lambda cmd: cmds.append(cmd))
    run_e0b.label(types.SimpleNamespace(allow_cloud=True, openai=False, keep_models=False,
                                        development_only=True, n=10))
    assert seen["half"] == "development"
    assert all("--only-split" in c and "development" in c for c in cmds)
    assert not any("sheets" in c for c in cmds)


def _decision(tmp_path, monkeypatch, chosen: dict, files: dict):
    """E0a's decision.json with these chosen models, and E0b files naming these models."""
    path = tmp_path / "e0a" / "decision.json"
    if chosen is not None:
        os.makedirs(path.parent, exist_ok=True)
        json.dump({"languages": {lang: {"chosen": m, "state": "chosen" if m else "none"}
                                 for lang, m in chosen.items()}}, open(path, "w"))
    monkeypatch.setattr(run_e0b, "DECISION_E0A", str(path))
    monkeypatch.setattr(run_e0b, "file_models",
                        lambda experiment: files[experiment])


def test_dialogues_take_the_model_e0a_decided_even_the_provisional_one(tmp_path, monkeypatch):
    """E0a chose qwen3:8b (the provisional name) for English and another model for Spanish, and
    apply wrote them: E0b runs, each language with its decided model, and nothing is scored."""
    _decision(tmp_path, monkeypatch, {"en": "qwen3:8b", "es": "gemma3:12b"},
              {"e0b_judge_dialogues.yaml": ["qwen3:8b"],
               "e0b_judge_dialogues_es.yaml": ["gemma3:12b"]})
    assert run_e0b.decision_problems() == []
    cmds = []
    monkeypatch.setattr(run_e0b, "run", lambda cmd: cmds.append(cmd))
    monkeypatch.setattr(run_e0b, "RESULTS", str(tmp_path / "e0b"))
    monkeypatch.setattr(run_e0b.ollama_models, "unload_all", lambda base, models: None)
    monkeypatch.setattr(run_e0b, "system_models", lambda: {"qwen3:8b", "gemma3:12b"})
    run_e0b.dialogues(types.SimpleNamespace(allow_cloud=True, allow_provisional=False))
    runs = [c for c in cmds if "run_parallel.py" in " ".join(c)]
    assert [c[c.index("--models") + 1] for c in runs] == ["qwen3:8b", "gemma3:12b"]
    assert not any("score_runs.py" in " ".join(c) for c in cmds)
    assert sum("split" in c for c in cmds) == 2


def test_dialogues_refuse_without_e0a_decision(tmp_path, monkeypatch):
    _decision(tmp_path, monkeypatch, None, {"e0b_judge_dialogues.yaml": ["qwen3:8b"],
                                            "e0b_judge_dialogues_es.yaml": ["qwen3:8b"]})
    with pytest.raises(SystemExit) as stop:
        run_e0b.dialogues(types.SimpleNamespace(allow_cloud=True, allow_provisional=False))
    assert "has not decided" in str(stop.value)


def test_dialogues_refuse_a_file_that_differs_from_the_decision(tmp_path, monkeypatch):
    """apply not run (the file still names the placeholder) or a file edited by hand: refused,
    saying which file, which model and how to fix it; the unchosen placeholder never runs."""
    _decision(tmp_path, monkeypatch, {"en": "llama3.1:8b", "es": "qwen3:8b"},
              {"e0b_judge_dialogues.yaml": ["qwen3:8b"],
               "e0b_judge_dialogues_es.yaml": ["qwen3:8b"]})
    with pytest.raises(SystemExit) as stop:
        run_e0b.dialogues(types.SimpleNamespace(allow_cloud=True, allow_provisional=False))
    why = str(stop.value)
    assert "e0b_judge_dialogues.yaml names ['qwen3:8b']" in why and "llama3.1:8b" in why
    assert "run_e0a.py apply" in why and "e0b_judge_dialogues_es" not in why
    # A language with no model chosen is refused too
    _decision(tmp_path, monkeypatch, {"en": "qwen3:8b", "es": None},
              {"e0b_judge_dialogues.yaml": ["qwen3:8b"],
               "e0b_judge_dialogues_es.yaml": ["qwen3:8b"]})
    assert any("no model for es" in p for p in run_e0b.decision_problems())


def test_preflight_calls_no_judge(monkeypatch):
    pinged = []
    monkeypatch.setattr(run_e0b, "run", lambda cmd: None)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(run_e0b, "ping", lambda provider, model: pinged.append(model))
    run_e0b.preflight(types.SimpleNamespace(allow_cloud=True))
    assert pinged == [run_e0b.SIMULATOR[1]]


def _report(tmp_path, monkeypatch, chosen, cloud_labelled=False):
    monkeypatch.setattr(run_e0b, "RESULTS", str(tmp_path / "e0b"))
    os.makedirs(tmp_path / "e0b", exist_ok=True)
    json.dump({"chosen": chosen, "cloud_labelled": cloud_labelled},
              open(tmp_path / "e0b" / "report.json", "w"))


def test_a_local_judge_that_passes_ends_e0b_without_a_cloud_call(tmp_path, monkeypatch):
    _report(tmp_path, monkeypatch, "mistral_small")
    assert run_e0b.report_state() == {"chosen": "mistral_small", "local": True,
                                      "cloud_labelled": False, "next": ""}
    pinged = []
    monkeypatch.setattr(run_e0b, "ping", lambda provider, model: pinged.append(model))
    with pytest.raises(SystemExit) as stop:
        run_e0b.label_cloud(types.SimpleNamespace(allow_cloud=True, openai=False))
    assert "no cloud judge is needed" in str(stop.value) and pinged == []


def test_no_local_judge_passes_then_the_cloud_judges_label_the_sheets(tmp_path, monkeypatch,
                                                                      catalog_in_tmp):
    _report(tmp_path, monkeypatch, None)
    assert run_e0b.report_state()["next"] == "label-cloud"
    with pytest.raises(SystemExit):                       # paid: needs --allow-cloud
        run_e0b.label_cloud(types.SimpleNamespace(allow_cloud=False, openai=False))
    for lang in ("en", "es"):
        os.makedirs(tmp_path / "e0b" / lang / "e0")
        json.dump({"items": {}}, open(tmp_path / "e0b" / lang / "e0" / "key.json", "w"))
    monkeypatch.setattr(run_e0b, "system_families", lambda: {"qwen"})
    pinged, cmds = [], []
    monkeypatch.setattr(run_e0b, "ping", lambda provider, model: pinged.append(model))
    monkeypatch.setattr(run_e0b, "run", lambda cmd: cmds.append(cmd))
    run_e0b.label_cloud(types.SimpleNamespace(allow_cloud=True, openai=False))
    assert pinged == [m for _, m, _ in run_e0b.CLOUD_JUDGES]
    assert all("--only-key" in c and "--allow-cloud" in c for c in cmds)
    assert len(cmds) == 2 * len(run_e0b.CLOUD_JUDGES)
    assert run_e0b.report_state()["cloud_labelled"] and run_e0b.report_state()["next"] == ""


def test_label_makes_no_cloud_call(server, tmp_path, monkeypatch):
    monkeypatch.setattr(run_e0b, "PULLED", str(tmp_path / "pulled.json"))
    monkeypatch.setattr(run_e0b, "RESULTS", str(tmp_path / "e0b"))
    monkeypatch.setattr(run_e0b, "system_families", lambda: {"qwen"})
    monkeypatch.setattr(run_e0b, "LOCAL_JUDGES", [("ollama", "j1:7b", "j1")])
    monkeypatch.setattr(run_e0b, "pending", lambda tag, half=None: 0)
    pinged, cmds = [], []
    monkeypatch.setattr(run_e0b, "ping", lambda provider, model: pinged.append(model))
    monkeypatch.setattr(run_e0b, "run", lambda cmd: cmds.append(cmd))
    run_e0b.label(types.SimpleNamespace(keep_models=False, development_only=False, n=10))
    assert pinged == [] and not any("--allow-cloud" in c for c in cmds)
    assert sum("sheets" in c for c in cmds) == 2


def test_report_combines_the_languages(tmp_path, monkeypatch, catalog_in_tmp):
    monkeypatch.setattr(run_e0b, "RESULTS", str(tmp_path / "e0b"))
    cmds = []

    def fake_run(cmd):
        cmds.append(cmd)
        if "combine" in cmd:
            out = cmd[cmd.index("--out") + 1]
            os.makedirs(os.path.dirname(out), exist_ok=True)
            json.dump({"chosen": "mistral_small", "tentative": False}, open(out, "w"))

    monkeypatch.setattr(run_e0b, "run", fake_run)
    run_e0b.report(types.SimpleNamespace(a_en="a", b_en="b", a_es="a", b_es="b"))
    assert [c[2] for c in cmds] == ["report", "report", "combine"]
    # No cloud judge labelled yet: the report records it (label-cloud only if none passed)
    assert json.load(open(tmp_path / "e0b" / "report.json"))["cloud_labelled"] is False
    entry = _catalog(catalog_in_tmp)[-1]
    assert entry["event"] == "report" and entry["chosen"] == "mistral_small"
    with pytest.raises(SystemExit):
        run_e0b.report(types.SimpleNamespace(a_en="a", b_en="b", a_es=None, b_es=None))


# ==== run_experiment, with ROS stubbed ====

@pytest.fixture
def run_experiment(monkeypatch):
    stubs = {
        "ament_index_python": types.ModuleType("ament_index_python"),
        "ament_index_python.packages": types.SimpleNamespace(
            get_package_share_directory=lambda name: "/nonexistent"),
        "ros2_dialog_interfaces": types.ModuleType("ros2_dialog_interfaces"),
        "ros2_dialog_interfaces.action": types.SimpleNamespace(DialogTask=types.SimpleNamespace(
            Goal=types.SimpleNamespace(USE_DEFAULT=0, USE_DEFAULT_SECONDS=0.0))),
        "interaction_sim.harness": types.SimpleNamespace(HarnessRunner=object,
                                                         UserTurn=object),
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    # Imported again on the stubs, and removed when the test ends (also when they were not
    # imported before: a module built on the stubbed harness must not leak into later tests)
    for name in ("run_experiment", "interaction_sim.llm_user"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.delitem(sys.modules, name)
    # The import also binds the package's attribute: restore it too
    import interaction_sim
    monkeypatch.setattr(interaction_sim, "llm_user", getattr(interaction_sim, "llm_user", None),
                        raising=False)
    import run_experiment as module
    return module


def test_a_model_of_the_run_before_still_unloading_is_waited_for(run_experiment, monkeypatch):
    answers = iter([["models of other work loaded: ['prev:8b']"], []])
    monkeypatch.setattr(run_experiment, "busy_servers", lambda *a: next(answers))
    slept = []
    assert run_experiment.wait_until_free({}, "local", [], {}, {}, wait_s=60,
                                          sleep=slept.append) == []
    assert slept == [5]


def test_the_servers_of_a_dialogue_and_a_dropped_one(run_experiment, monkeypatch):
    exp = {"ollama": {"host": "127.0.0.1", "port": 11434},
           "simulator": {"provider": "gemini", "model": "g"}}
    assert run_experiment.servers_of(exp, "qwen3:8b", {}) == ["http://127.0.0.1:11434"]
    assert run_experiment.servers_of(exp, "gemini/g", {}) == []
    monkeypatch.setattr(run_experiment.ollama_models, "server_up", lambda base: False)
    assert run_experiment.servers_down(["http://127.0.0.1:11434"]) == ["http://127.0.0.1:11434"]


def test_a_simulator_error_marks_the_dialogue(run_experiment):
    assert run_experiment.simulator_failed(types.SimpleNamespace(log=[{"turn": 1},
                                                                     {"error": "HTTP 429"}]))
    assert not run_experiment.simulator_failed(types.SimpleNamespace(log=[{"turn": 1}]))
    # A run that ends with dialogues unsaved is not complete: it exits with an error (the
    # credit-out drill, 2026-11-02: the credit ran out in the last two dialogues and it exited 0)
    assert run_experiment.unsaved_message({"simulator_failures": 0}) == ""
    assert "2 dialogue(s) not saved" in run_experiment.unsaved_message({"simulator_failures": 2})


def test_the_warm_up_uses_phase_a_rule_and_the_fit_is_recorded(run_experiment, monkeypatch,
                                                               tmp_path):
    seen = {}
    monkeypatch.setattr(run_experiment.ollama_models, "warm_up",
                        lambda base, model, think, num_ctx: seen.update(
                            model=model, think=think, num_ctx=num_ctx) or "low")
    monkeypatch.setattr(run_experiment.ollama_models, "gpu_fit",
                        lambda base, model: {"size": 10, "size_vram": 10, "fits": True})
    exp = {"ollama": {"host": "h", "port": 1}}
    assert run_experiment.warm_up(exp, "gpt-oss:20b") == "low"
    assert seen == {"model": "gpt-oss:20b", "think": False, "num_ctx": 8192}
    assert run_experiment.warm_up(exp, "gemini/x") is None
    assert run_experiment.record_fit(str(tmp_path), exp, "gpt-oss:20b")["fits"] is True
    saved = json.load(open(tmp_path / "model_fit.json"))
    assert saved["gpt-oss:20b"]["num_ctx"] == 8192


def test_gpu_fit_reads_size_and_size_vram(monkeypatch):
    listing = {"models": [{"name": "a:7b", "size": 100, "size_vram": 100},
                          {"name": "b:32b", "size": 100, "size_vram": 60}]}
    monkeypatch.setattr(ollama_models.requests, "get",
                        lambda url, timeout=None: types.SimpleNamespace(json=lambda: listing))
    assert ollama_models.gpu_fit("http://x", "a:7b")["fits"] is True
    assert ollama_models.gpu_fit("http://x", "b:32b")["fits"] is False
    assert ollama_models.gpu_fit("http://x", "c:1b")["fits"] is None


def test_the_injector_of_model_failures_is_loaded_where_a_scenario_plans_them(run_experiment):
    scenarios = [{"id": "a"}, {"id": "f", "faults": [{"turn": 1, "kind": "error"}]}]
    method, ablation = {}, {"skill_modules": ["interaction_ablations.skills"]}
    assert run_experiment.plans_faults(scenarios, method)
    assert not run_experiment.plans_faults(scenarios, {"scenario_ids": ["a"]})
    assert run_experiment.skill_modules(method) == [""]
    assert run_experiment.skill_modules(method, faults=True) == ["interaction_sim.fault_injection"]
    assert run_experiment.skill_modules(ablation, faults=True) == [
        "interaction_ablations.skills", "interaction_sim.fault_injection"]
    assert run_experiment.skill_modules(ablation) == ["interaction_ablations.skills"]


def test_the_recorder_of_the_model_calls_is_loaded_last_when_the_file_asks(run_experiment):
    ablation = {"skill_modules": ["interaction_ablations.skills"]}
    assert run_experiment.skill_modules(ablation, faults=True, record_calls=True) == [
        "interaction_ablations.skills", "interaction_sim.fault_injection",
        "interaction_sim.call_recording"]
    assert run_experiment.skill_modules({}, record_calls=True) == [
        "interaction_sim.call_recording"]
    assert run_experiment.records_calls({"record_model_calls": True}) is True
    assert run_experiment.records_calls({}) is False
    with pytest.raises(ValueError):
        run_experiment.records_calls({"record_model_calls": "yes"})


def test_the_failures_of_e3_are_well_formed_and_counted_by_the_dry_run(run_experiment, capsys,
                                                                        tmp_path):
    """e3_failures.yaml: every planned failure is valid (validate_faults), and the dry run counts
    its dialogues and paid calls, and says where the injector is loaded."""
    import yaml
    experiments = os.path.join(os.path.dirname(__file__), "..", "..", "experiments")
    with open(os.path.join(experiments, "scenarios", "e3_failures.yaml"), encoding="utf-8") as f:
        scenarios = yaml.safe_load(f)["scenarios"]
    for s in scenarios:
        assert s.get("faults") and s["expect"].get("ending"), s["id"]
        assert run_experiment.validate_faults(s["faults"]), s["id"]
        assert "faults_hit" in s["expect"]["mechanisms"], s["id"]
    argv = ["run_experiment.py", os.path.join(experiments, "e3_failures.yaml"), "--dry-run",
            "--out", str(tmp_path)]
    old = sys.argv
    try:
        sys.argv = argv
        run_experiment.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert "e3_failures: 225 dialogues" in out
    assert "(simulated user): 225 dialogues" in out
    assert "the injector is loaded in bounded, llm_led" in out


def test_a_pilot_sends_the_cloud_calls_to_its_standins_with_a_dummy_key(run_experiment):
    env = {"GEMINI_API_KEY": "real", "GOOGLE_API_KEY": "real", "OPENAI_API_KEY": "real"}
    run_experiment.use_standins({"standins": {"gemini_base_url": "http://127.0.0.1:8766",
                                              "openai_base_url": "http://127.0.0.1:8766/v1"}},
                                env)
    assert env["INTERACTION_PILOT_GEMINI_URL"] == "http://127.0.0.1:8766"
    assert env["INTERACTION_PILOT_OPENAI_URL"] == "http://127.0.0.1:8766/v1"
    assert env["GEMINI_API_KEY"] == env["OPENAI_API_KEY"] == run_experiment.STANDIN_KEY
    assert "GOOGLE_API_KEY" not in env
    # An experiment (no stand-ins) refuses stand-in URLs left in the environment
    with pytest.raises(SystemExit, match="not a pilot"):
        run_experiment.use_standins({}, {"INTERACTION_PILOT_GEMINI_URL": "http://127.0.0.1:1"})
    run_experiment.use_standins({}, {})


def test_the_cloud_lane_of_a_pilot_counts_its_standin_model_as_ours(run_experiment,
                                                                    monkeypatch):
    seen = {}
    monkeypatch.setattr(run_experiment, "server_busy",
                        lambda server, ours: seen.setdefault("ours", ours) and [])
    hardware = {"lanes": {"cloud": {"system": "gpu"}}, "servers": {"gpu": {}}}
    run_experiment.busy_servers(hardware, "cloud", ["openai/qwen2.5:32b"], {},
                                {"provider": "gemini", "model": "g"})
    assert "qwen2.5:32b" in seen["ours"]
