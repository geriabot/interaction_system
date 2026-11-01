# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The whole evaluation (scripts/whole_evaluation.py) and its part of the control panel, without
ROS, LLM, server or paid call: the steps and their commands, the stops of a person, the
permission of the paid steps (never --allow-cloud without it), the method's code hash, the pause
and resume of a detached run, and the run health; every command run here is a fake script.

    python3 -m pytest scripts/tests/test_whole_evaluation.py
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS)
import whole_evaluation as we  # noqa: E402
import control_panel as cp  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A scratch whole-evaluation folder and results folder; a fixed code hash; the signal
    handlers of the test process restored after a run."""
    monkeypatch.setattr(we, "RESULTS", str(tmp_path / "results"))
    monkeypatch.setattr(we, "code_hash", lambda: "hash-1")
    monkeypatch.setattr(we, "PAUSE_WAIT_S", 5)
    we.STOP["flag"] = False
    handlers = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    yield str(tmp_path / "whole")
    signal.signal(signal.SIGINT, handlers[0])
    signal.signal(signal.SIGTERM, handlers[1])
    we.STOP["flag"] = False


def fake(trace, name, code=0, sleep=0.0):
    """A command that appends its name and arguments to `trace` and exits with `code`."""
    script = (f"import sys, time; open({str(trace)!r}, 'a').write({name!r} + ' ' + "
              f"' '.join(sys.argv[1:]) + '\\n'); time.sleep({sleep}); sys.exit({code})")
    return lambda extra=(): we.cmd(sys.executable, "-c", script, *extra)


def mini_steps(trace, paid_key=None, estimate_error=""):
    """Three steps: a free one, a person's stop, a paid one (its command gets --allow-cloud)."""
    key = paid_key if paid_key is not None else {"cloud": {"gemini/x": 10}}
    return [
        we.run_step("free", 1, "free", "a free step", lambda c: [fake(trace, "free")()]),
        we.person("stop", 1, "stop", lambda c: "the labels of two people"),
        we.run_step("paid", 2, "paid", "a paid step",
                    lambda c: [fake(trace, "paid")(["--allow-cloud"])], paid=lambda c: True,
                    estimate=lambda c: {"error": estimate_error} if estimate_error else
                    {"lines": ["gemini/x: 10 dialogues"], "key": key, "output": ""}),
    ]


def ran(trace):
    return open(trace).read().splitlines() if os.path.exists(trace) else []


# ---------------------------------------------------------------- the steps
def test_the_steps_follow_the_order_of_reproducing_and_its_commands():
    steps = we.steps()
    assert [s["n"] for s in steps] == sorted(s["n"] for s in steps)
    assert sorted({s["n"] for s in steps}) == list(range(1, 9))
    ids = [s["id"] for s in steps]
    # The order of the work (REPRODUCING.md, The order): the debugging's exit, the pilot of every
    # experiment and its review, the freeze commit, the trap review, E0a, E0b, the go/no-go gate
    # (after E0b), E1 and the role swap, E2, E3, the twin of E4
    assert ids[:5] == ["debugging", "pilot", "pilot_review", "freeze", "e0a_traps"]
    assert ids.index("e0a_apply") < ids.index("e0b_preflight") < ids.index("e0b_report") \
        < ids.index("e0b_label_cloud") < ids.index("e1_gate") < ids.index("e1_campaign") \
        < ids.index("e1_swap") < ids.index("e2") < ids.index("e3_mechanisms") \
        < ids.index("e4_twin")
    assert "e1_pilot" not in ids and "e1_pilot_review" not in ids
    # A cloud smoke check right before every paid campaign with a cloud model
    for smoke, campaign in (("e1_smoke", "e1_campaign"), ("e1_swap_smoke", "e1_swap"),
                            ("e3_smoke", "e3_mechanisms"), ("e4_smoke", "e4_twin")):
        assert ids.index(smoke) + 1 == ids.index(campaign)
    assert ids.index("e0a_phase_a") < ids.index("e0a_adjudication") < ids.index("e0a_phase_b") \
        < ids.index("e0a_decide") < ids.index("e0a_decision") < ids.index("e0a_apply")
    assert ids.index("e0b_label_dev") < ids.index("e0b_tune") < ids.index("e0b_label") \
        < ids.index("e0b_report")
    assert ids.index("e1_gate") < ids.index("e1_gate_decision") < ids.index("e1_smoke") \
        < ids.index("e1_campaign") < ids.index("e1_swap")
    assert ids[-1] == "tables"
    ctx = we.Ctx("/nonexistent")
    by = we.by_id()

    def argv(sid):
        return [" ".join(c["argv"]) for c in by[sid]["commands"](ctx)]
    assert argv("e0a_preflight") == ["python3 scripts/run_e0a.py preflight --allow-cloud"]
    assert argv("e0a_phase_a") == ["python3 scripts/run_e0a.py phase-a"]
    assert argv("e0a_phase_b") == ["python3 scripts/run_e0a.py phase-b --allow-cloud"]
    assert argv("e0b_label_dev") == ["python3 scripts/run_e0b.py label --development-only"]
    assert argv("e0b_label") == ["python3 scripts/run_e0b.py label"]       # free: local only
    assert argv("pilot") == ["python3 scripts/run_pilot.py run"]
    assert by["pilot"]["paid"](ctx) is False                                # the stand-ins
    assert argv("e3_smoke") == [
        "python3 scripts/cloud_smoke.py experiments/e3_mechanisms.yaml "
        "experiments/e3_noise.yaml experiments/e3_failures.yaml --allow-cloud"]
    assert argv("e1_campaign") == [
        f"python3 scripts/run_parallel.py {we.E1} {we.HARDWARE} --check-servers",
        f"python3 scripts/run_parallel.py {we.E1} {we.HARDWARE} --allow-cloud",
        "python3 analysis/score_runs.py results/e1_bounded_vs_llm_led"]
    # Only a paid step's commands carry --allow-cloud
    for step in we.steps():
        if step["kind"] == "run" and step["paid"](ctx) is False:
            assert not any("--allow-cloud" in c["argv"] for c in step["commands"](ctx)), step["id"]


def test_an_experiment_with_the_provisional_model_or_a_pin_waits(root, monkeypatch):
    ctx = we.Ctx(root)
    why = we.requires_experiment("experiments/e3_noise.yaml", ctx)
    assert "provisional local model" in why                    # E0a has not decided
    os.makedirs(os.path.join(we.RESULTS, "e0a"))
    with open(os.path.join(we.RESULTS, "e0a", "decision.json"), "w") as f:
        json.dump({"languages": {"en": {"chosen": "qwen3:8b"}}}, f)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert we.requires_experiment("experiments/e3_noise.yaml", ctx) == ""
    assert "dated version" in we.requires_experiment(we.SWAP, ctx)   # gemini/<pin>
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert "GEMINI_API_KEY" in we.requires_experiment("experiments/e3_noise.yaml", ctx)


def test_the_pilot_needs_its_files_and_the_report_the_sheets(root, monkeypatch):
    step = we.by_id()["pilot"]
    assert step["requires"](we.Ctx(root)) == ""          # the pilot files of the repository
    assert len(step["results"](we.Ctx(root))) == 10
    monkeypatch.setattr(we, "pilot_files", lambda: {})
    assert "No pilot file" in step["requires"](we.Ctx(root))
    review = we.by_id()["pilot_review"]["awaits"](we.Ctx(root))
    assert "has not run yet" in review and "attributed" in review
    report = we.by_id()["e0b_report"]
    assert "still to finish" in report["requires"](we.Ctx(root))
    for lang in ("en", "es"):
        os.makedirs(os.path.join(we.RESULTS, "e0b", lang, "e0"))
        for sheet in ("a", "b"):
            with open(os.path.join(we.RESULTS, "e0b", lang, "e0",
                                   f"sheet_{sheet}.progress.json"), "w") as f:
                json.dump({"done": 150, "total": 150, "finished": "now"}, f)
    assert report["requires"](we.Ctx(root)) == ""


def test_a_judge_of_the_family_of_a_cloud_system_model_stops(root, tmp_path, monkeypatch):
    os.makedirs(os.path.join(we.RESULTS, "e0b"))
    with open(os.path.join(we.RESULTS, "e0b", "report.json"), "w") as f:
        json.dump({"chosen": "gpt_4o_mini", "tentative": False}, f)
    monkeypatch.setattr(we.glob, "glob", lambda pattern: ["x.json"])   # dialogues to judge
    ctx = we.Ctx(root)
    assert ctx.judge()["provider"] == "openai"
    why = we.by_id()["e1_judge"]["requires"](ctx)
    assert "family" in why and "openai/" in why
    assert we.by_id()["e1_judge"]["paid"](ctx) is True


# ---------------------------------------------------------------- the runner
def test_it_runs_in_order_stops_for_a_person_and_never_pays_without_permission(root, tmp_path,
                                                                              monkeypatch):
    trace = str(tmp_path / "trace")
    monkeypatch.setattr(we, "steps", lambda: mini_steps(trace))
    assert we.run(root) == 2                                 # the person's stop
    state = we.read_state(root)
    assert state["halt"]["kind"] == "person" and state["next"] == "stop"
    assert ran(trace) == ["free "]
    assert state["method"]["code_hash"] == "hash-1"
    assert state["steps"]["free"]["health"]["verdict"] == "OK"
    # Done: it goes on, and stops at the paid step without having run it
    assert we.acknowledge(root, "stop") == ""
    assert we.run(root) == 2
    state = we.read_state(root)
    assert state["halt"]["kind"] == "permission" and "gemini/x: 10 dialogues" in state["halt"]["text"]
    assert ran(trace) == ["free "]
    # Allowed: it runs, with --allow-cloud, and finishes
    est = we.estimate(we.by_id()["paid"], we.Ctx(root))
    assert we.allow(root, "paid", est) == ""
    assert we.run(root) == 0
    assert ran(trace) == ["free ", "paid --allow-cloud"]
    assert we.read_state(root)["status"] == "done"


def test_a_permission_holds_only_for_what_was_shown(root, tmp_path, monkeypatch):
    trace = str(tmp_path / "trace")
    monkeypatch.setattr(we, "steps", lambda: mini_steps(trace))
    we.allow(root, "paid", {"lines": ["old"], "key": {"cloud": {"gemini/x": 5}}})
    assert we.run(root, "paid") == 2
    halt = we.read_state(root)["halt"]
    assert halt["kind"] == "permission" and "changed since they were allowed" in halt["text"]
    assert ran(trace) == []
    # A step whose estimate cannot be made does not run either
    monkeypatch.setattr(we, "steps", lambda: mini_steps(trace, estimate_error="no dry run"))
    assert we.run(root, "paid") == 2 and ran(trace) == []


def test_a_start_elsewhere_runs_again_and_asks_the_stops_again(root, tmp_path, monkeypatch):
    trace = str(tmp_path / "trace")
    monkeypatch.setattr(we, "steps", lambda: mini_steps(trace))
    we.run(root)
    we.acknowledge(root, "stop")
    we.allow(root, "paid", we.estimate(we.by_id()["paid"], we.Ctx(root)))
    assert we.run(root) == 0
    assert we.run(root, "free") == 2                    # from the start: the stop again
    assert we.read_state(root)["halt"]["step"] == "stop"
    assert "paid" in we.read_state(root)["permissions"]   # the permission stays


def test_a_changed_method_stops_until_accepted_and_tells_the_steps_it_affects(root, tmp_path,
                                                                              monkeypatch):
    trace = str(tmp_path / "trace")
    monkeypatch.setattr(we, "steps", lambda: mini_steps(trace))
    we.run(root)                                        # free runs with hash-1
    monkeypatch.setattr(we, "code_hash", lambda: "hash-2")
    check = we.method_check(root)
    assert check["changed"] and [a["id"] for a in check["affected"]] == ["free"]
    assert we.run(root) == 2
    assert we.read_state(root)["halt"]["kind"] == "method"
    we.acknowledge(root, "stop")
    assert we.run(root, accept_method=True) == 2         # accepted: on to the permission
    state = we.read_state(root)
    assert state["method"]["code_hash"] == "hash-2"
    assert state["method_history"][0]["code_hash"] == "hash-1"
    assert state["halt"]["kind"] == "permission"
    assert not we.method_check(root)["changed"]


def test_a_failed_step_stops_with_its_health(root, tmp_path, monkeypatch):
    trace = str(tmp_path / "trace")
    steps = [we.run_step("bad", 1, "bad", "fails", lambda c: [fake(trace, "bad", code=4)()])]
    monkeypatch.setattr(we, "steps", lambda: steps)
    assert we.run(root) == 1
    state = we.read_state(root)
    assert state["status"] == "failed" and state["next"] == "bad"
    h = state["steps"]["bad"]["health"]
    assert h["verdict"] == "CHECK" and "exit status 4" in h["sentence"]
    assert "Run health:" in open(os.path.join(root, "logs", "bad.log")).read()


def test_a_busy_server_stops_before_the_step(root, tmp_path, monkeypatch):
    trace = str(tmp_path / "trace")
    steps = [we.run_step("x", 1, "x", "x", lambda c: [
        we.cmd(sys.executable, "-c", "import sys; sys.exit(3)", busy=True), fake(trace, "x")()])]
    monkeypatch.setattr(we, "steps", lambda: steps)
    assert we.run(root) == 2
    assert we.read_state(root)["halt"]["kind"] == "busy" and ran(trace) == []


def test_pause_stops_the_step_as_ctrl_c_and_start_resumes_it(root, tmp_path):
    """A detached run (its own process group), paused with SIGINT to the group: the step under
    way stops and is recorded as paused; a new run resumes at it."""
    trace = str(tmp_path / "trace")
    harness = tmp_path / "harness.py"
    harness.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {SCRIPTS!r})
        sys.path.insert(0, {HERE!r})
        import whole_evaluation as we
        import test_whole_evaluation as t
        we.RESULTS = {str(tmp_path / 'results')!r}
        we.code_hash = lambda: "hash-1"
        sleep = float(sys.argv[1])
        we.steps = lambda: [we.run_step("long", 1, "long", "long", lambda c: [
            t.fake({trace!r}, "long", sleep=sleep)()]), we.run_step(
            "next", 1, "next", "next", lambda c: [t.fake({trace!r}, "next")()])]
        sys.exit(we.run({root!r}))
    """))
    process = subprocess.Popen([sys.executable, str(harness), "30"], start_new_session=True)
    for _ in range(100):
        if ran(trace):
            break
        time.sleep(0.1)
    assert we.alive(we.read_state(root)["runner"])
    os.killpg(process.pid, signal.SIGINT)
    assert process.wait(timeout=20) == 0
    state = we.read_state(root)
    assert state["status"] == "paused" and state["next"] == "long"
    assert state["steps"]["long"]["status"] == "paused" and "next" not in state["steps"]
    assert not we.alive(state["runner"])
    assert subprocess.run([sys.executable, str(harness), "0"], timeout=30).returncode == 0
    assert ran(trace) == ["long ", "long ", "next "]
    assert we.read_state(root)["status"] == "done"


# ---------------------------------------------------------------- run health
def record(timeouts=0, skipped=0, status="succeeded"):
    return {"scenario": "s", "strategy": "slot_filling", "persona": "p", "rep": 1, "model": "m",
            "condition": "bounded", "status": status, "success": True, "outcome": "completed",
            "failure_reason": "", "data": {}, "total_turns": 1, "duration_s": 1.0, "expect": {},
            "events": [], "user_log": [],
            "skills": [{"dialogue_id": "d", "skill": "begin", "label": "slot_filling",
                        "llm_timeouts": timeouts, "llm_skipped": skipped}],
            "transcript": [{"role": "user", "text": "x", "t": 0.0},
                           {"role": "robot", "text": "Bye.", "t": 1.5}]}


def write_runs(folder, records):
    runs = os.path.join(folder, "runs", "m", "bounded")
    os.makedirs(runs, exist_ok=True)
    for i, rec in enumerate(records):
        with open(os.path.join(runs, f"s__p__r{i}.json"), "w") as f:
            json.dump(rec, f)


def by_what(measures, start):
    return next(m for m in measures if m["what"].startswith(start))


def test_health_reports_latency_without_verdict_and_calls_cut_with_one(tmp_path):
    folder = str(tmp_path / "e")
    write_runs(folder, [record(), record(timeouts=1, skipped=2)])
    measures, details = we.dialogue_measures([folder], 0)
    assert by_what(measures, "Dialogues saved")["value"].startswith("2")
    latency = by_what(measures, "Model latency")
    assert latency["verdict"] == "" and "median 1.50 s" in latency["value"]
    cut = by_what(measures, "LLM calls cut")
    assert cut["verdict"] == "CHECK" and cut["value"] == "llm_timeouts 1, llm_skipped 2"
    assert by_what(measures, "Dialogues excluded")["verdict"] == ""
    assert details[0].split()[:3] == ["model", "condition", "n"]   # score_runs.latency_summary
    # Injected failures: the calls cut are reported, not judged
    assert by_what(we.dialogue_measures([folder], 0, faults=True)[0], "LLM calls cut")["verdict"] == ""
    # Only the dialogues of this step
    assert we.dialogue_measures([folder], time.time() + 60)[0][0]["value"].startswith("0")


def test_health_sentence_and_the_server_check(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "capture", lambda argv, timeout: (3, "lane local: NOT FREE"))
    folder = str(tmp_path / "e")
    write_runs(folder, [record()])
    step = we.run_step("x", 1, "x", "x", lambda c: [], results=lambda c: [folder],
                       server=lambda c: we.E1)
    h = we.health(step, we.Ctx(str(tmp_path)), 0, 0, False)
    assert by_what(h["measures"], "GPU server free")["verdict"] == "CHECK"
    assert h["verdict"] == "CHECK" and h["sentence"].startswith("Check before going on")
    monkeypatch.setattr(we, "capture", lambda argv, timeout: (0, ""))
    h = we.health(step, we.Ctx(str(tmp_path)), 0, 0, False)
    assert h["verdict"] == "OK" and h["sentence"] == "All as usual."
    assert we.health(step, we.Ctx(str(tmp_path)), 0, -2, True)["sentence"].startswith("Paused")


# ---------------------------------------------------------------- the panel
@pytest.fixture
def panel(root, tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "args", argparse.Namespace(env="jazzy", real_out=str(tmp_path / "out"),
                                                       sim_domain=91, whole_root=root))
    monkeypatch.setattr(cp, "_hash", {"value": "", "time": 0.0})
    monkeypatch.setattr(cp, "experiment_campaign", lambda: "")
    monkeypatch.setattr(cp, "job_running", lambda: False)
    started = []

    class Popen:
        def __init__(self, command, **kw):
            started.append(command)
            self.pid = 999999

        def wait(self):
            return 0
    monkeypatch.setattr(cp.subprocess, "Popen", Popen)
    return started


def test_the_panel_starts_it_detached_at_the_chosen_step(panel, root):
    assert cp.whole_start({"from": "e0a_phase_a", "openai_judges": True})["error"] == ""
    command = " ".join(panel[0])
    assert "whole_evaluation.py --root " + root + " run --from e0a_phase_a" in command
    assert "--openai-judges" in command and "--accept-method-change" not in command
    assert we.read_state(root)["runner"]["pgid"] == 999999
    assert "Unknown step" in cp.whole_start({"from": "nope"})["error"]


def test_the_panel_asks_to_confirm_a_changed_method(panel, root, monkeypatch):
    with we.locked(root) as state:
        state["method"] = {"code_hash": "hash-0", "time": "t"}
    out = cp.whole_start({})
    assert "method changed" in out["error"] and not panel
    assert cp.whole_start({"accept_method": True})["error"] == ""
    assert "--accept-method-change" in panel[0][-1] or "--accept-method-change" in " ".join(panel[0])


def test_the_panel_refuses_while_something_else_runs(panel, monkeypatch):
    monkeypatch.setattr(cp, "job_running", lambda: True)
    assert "job" in cp.whole_start({})["error"] and not panel
    monkeypatch.setattr(cp, "job_running", lambda: False)
    monkeypatch.setattr(cp, "experiment_campaign", lambda: "e3_noise")
    assert "e3_noise" in cp.whole_start({})["error"] and not panel


def test_the_panel_allows_only_the_estimate_it_showed(panel, root, tmp_path, monkeypatch):
    trace = str(tmp_path / "trace")
    monkeypatch.setattr(we, "steps", lambda: mini_steps(trace))
    est = cp.whole_estimate({"step": "paid"})
    assert est["error"] == "" and est["lines"] == ["gemini/x: 10 dialogues"]
    assert "changed" in cp.whole_allow({"step": "paid", "key": {"other": 1}})["error"]
    assert "paid" not in we.read_state(root)["permissions"]
    assert cp.whole_allow({"step": "paid", "key": est["key"]})["error"] == ""
    assert we.read_state(root)["permissions"]["paid"]["key"] == est["key"]
    assert "no paid call" in cp.whole_estimate({"step": "free"})["error"]
    assert cp.whole_done({"step": "stop"})["error"] == ""
    state = cp.whole_state()
    assert [s["id"] for s in state["steps"]] == ["free", "stop", "paid"]
    assert state["steps"][1]["status"] == "done" and state["steps"][2]["permission"]


def test_the_running_whole_evaluation_blocks_the_other_campaigns_and_jobs(panel, root,
                                                                         monkeypatch):
    with we.locked(root) as state:
        state["runner"] = {"pid": os.getpid(), "pgid": os.getpgrp()}
    monkeypatch.setattr(cp, "experiment_files", lambda: {})
    assert cp.running_campaign() == cp.WHOLE
    assert "running" in cp.whole_start({})["error"]
    assert "campaign is running" in cp.start_job("sim", "t", ["true"])


def test_e0b_asks_to_pay_for_the_cloud_judges_only_if_no_local_judge_passed(root, monkeypatch):
    """The cloud judges' stage runs nothing and asks for nothing when a local judge passed; it is
    paid, with its estimate, only when the report chose none and they have not labelled yet."""
    import run_e0b
    step = we.by_id()["e0b_label_cloud"]
    ctx = we.Ctx(root)
    monkeypatch.setattr(run_e0b, "RESULTS", os.path.join(we.RESULTS, "e0b"))
    assert "has not run" in step["requires"](ctx)
    os.makedirs(os.path.join(we.RESULTS, "e0b"))
    path = os.path.join(we.RESULTS, "e0b", "report.json")
    json.dump({"chosen": "llama3_1_8b", "cloud_labelled": False}, open(path, "w"))
    assert step["requires"](ctx) == "" and step["paid"](ctx) is False
    assert step["commands"](ctx) == []
    json.dump({"chosen": None, "cloud_labelled": False}, open(path, "w"))
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert step["paid"](ctx) is True
    argv = [" ".join(c["argv"]) for c in step["commands"](ctx)]
    assert argv[0] == "python3 scripts/run_e0b.py label-cloud --allow-cloud"
    assert argv[1].startswith("python3 scripts/run_e0b.py report")
    json.dump({"chosen": None, "cloud_labelled": ["gemini_2_5_flash"]}, open(path, "w"))
    assert step["paid"](ctx) is False and step["commands"](ctx) == []


def test_the_smoke_check_lists_every_cloud_model_and_the_cloud_judge(root, monkeypatch):
    os.makedirs(os.path.join(we.RESULTS, "e0b"))
    with open(os.path.join(we.RESULTS, "e0b", "report.json"), "w") as f:
        json.dump({"chosen": "gemini_2_5_flash_lite", "tentative": False}, f)
    ctx = we.Ctx(root)
    est = we.estimate(we.by_id()["e1_smoke"], ctx)
    lines = " ".join(est["lines"])
    assert "openai/<pin> (system (schema))" in lines and "(simulated user)" in lines
    assert "gemini/gemini-2.5-flash-lite (judge)" in lines
    argv = " ".join(we.by_id()["e1_smoke"]["commands"](ctx)[0]["argv"])
    assert "--judge gemini/gemini-2.5-flash-lite" in argv
    # It waits for what the campaign waits for: E0a's model, then the dated versions
    assert "provisional" in we.by_id()["e1_smoke"]["requires"](ctx)
    os.makedirs(os.path.join(we.RESULTS, "e0a"))
    with open(os.path.join(we.RESULTS, "e0a", "decision.json"), "w") as f:
        json.dump({"languages": {"en": {"chosen": "qwen3:8b"}}}, f)
    assert "dated version" in we.by_id()["e1_smoke"]["requires"](ctx)
