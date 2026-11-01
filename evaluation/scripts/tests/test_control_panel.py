# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The control panel's campaigns of real sessions, configurations, pre-session check and Tests
tab: its actions (the commands it would start, not run) and its HTTP endpoints, without ROS,
browser or LLM:

    python3 -m pytest scripts/tests/test_control_panel.py
"""

import argparse
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import control_panel as cp  # noqa: E402
import campaigns as cm  # noqa: E402  (real/, on the path through control_panel)

STARTING = cm.load_config("default")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """The panel on empty configs/, campaigns/ and data folders (with the starting
    configuration); start_job records the job instead of starting it."""
    monkeypatch.setattr(cm, "CONFIGS", str(tmp_path / "configs"))
    monkeypatch.setattr(cm, "CAMPAIGNS", str(tmp_path / "campaigns"))
    monkeypatch.setattr(cp, "CHECKS_FILE", str(tmp_path / "checks.json"))
    monkeypatch.setattr(cp, "args", argparse.Namespace(env="jazzy", real_out=str(tmp_path / "out"),
                                                       sim_domain=91))
    cm.write_config("default", STARTING["values"], {})
    jobs = []

    def start_job(kind, title, command, env=None, stdin=False, meta=None, cleanup=""):
        jobs.append({"kind": kind, "title": title, "command": command, "env": env or {},
                     "meta": meta or {}})
        return ""
    monkeypatch.setattr(cp, "start_job", start_job)
    return jobs


def value_after(command, flag):
    return command[command.index(flag) + 1]


def test_a_campaign_is_created_and_switched_from_the_page(panel):
    assert cp.act("/api/real/campaign/new", {"name": "pilot", "configuration": "default",
                                             "sessions_file": "sessions_es.yaml"})["error"] == ""
    state = cp.campaigns_state()
    (row,) = state["campaigns"]
    assert row["configuration"] == "default" and row["can_switch"] and row["integrity"] == ""
    assert state["configs"] == [{"name": "default", "state": "frozen", "used_by": ["pilot"], "error": ""}]
    assert "exists" in cp.act("/api/real/campaign/new", {"name": "pilot", "configuration": "default"})["error"]
    assert cp.act("/api/config/duplicate", {"name": "default"})["name"] == "default-2"
    assert cp.act("/api/real/campaign/switch", {"campaign": "pilot", "configuration": "default-2"})["error"] == ""
    detail = cp.campaign_detail("pilot")
    assert detail["configuration"] == "default-2" and "es_ES-sharvard-medium" in detail["describe"]
    assert list(detail["sessions"]["conditions"]) == ["bounded"] and detail["sessions"]["scenarios"]


def test_the_configurations_view_edits_drafts_only(panel):
    values = {**STARTING["values"], "noise_scale": "0.9", "length_scale": ""}
    saved = cp.act("/api/config/save", {"previous": "default", "name": "default", **values})
    assert saved["error"] == "" and cm.load_config("default")["values"]["noise_scale"] == 0.9
    assert "noise_scale" in cp.act("/api/config/save", {"previous": "default", "name": "default",
                                                        **values, "noise_scale": "7"})["error"]
    cp.act("/api/real/campaign/new", {"name": "pilot", "configuration": "default"})
    refused = cp.act("/api/config/save", {"previous": "default", "name": "default", **values})
    assert "frozen" in refused["error"]
    copy = cp.act("/api/config/duplicate", {"name": "default", "new_name": "woman"})
    assert copy["name"] == "woman"
    renamed = cp.act("/api/config/save", {"previous": "woman", "name": "woman-slow", "note": "E4?",
                                          **values, "voice": "es_ES-sharvard-medium:1"})
    assert renamed["name"] == "woman-slow"
    view = {c["name"]: c for c in cp.configurations_state()["configs"]}
    assert set(view) == {"default", "woman-slow"} and view["woman-slow"]["state"] == "draft"
    assert view["woman-slow"]["note"] == "E4?" and view["default"]["used_by"] == ["pilot"]
    editor = cp.configurations_state()
    assert editor["voices"] and editor["presets"] and "small" in editor["whisper_models"]
    assert set(editor["questions"]) == {"es", "en"}


def test_check_speak_of_the_editor_takes_the_values_being_edited(panel):
    body = {**STARTING["values"], "voice": "es_MX-claude-high", "noise_w_scale": "1.2",
            "phrase": "¿Sí?", "name": "draft"}
    assert cp.act("/api/config/speak", body)["error"] == ""
    command = panel[-1]["command"]
    assert command[command.index("check"):] == ["check", "speak", "--voice", "es_MX-claude-high",
                                                "--noise-w-scale", "1.2", "--phrase", "¿Sí?"]
    assert panel[-1]["meta"] == {"check": "config:speak"}
    assert "voice" in cp.act("/api/config/speak", {**body, "voice": "nobody"})["error"]
    assert len(panel) == 1


def test_the_trials_and_checks_of_a_campaign_run_with_it(panel):
    cp.act("/api/real/campaign/new", {"name": "pilot", "configuration": "default"})
    assert cp.act("/api/real/plan", {"campaign": "pilot", "participant": "p1"})["participant"] == "P01"
    assert os.path.isfile(os.path.join(cp.args.real_out, "pilot", "P01", "plan.yaml"))
    assert [p["participant"] for p in cp.participants_state("pilot")] == ["P01"]
    assert cp.participant_state("P01", "pilot")["plan"]["order"] == ["bounded"]
    trial = {"campaign": "pilot", "participant": "P01", "k": 1, "domain": "7"}
    assert cp.act("/api/real/run", {**trial, "mode": "try"})["error"] == ""
    command = panel[-1]["command"]
    assert value_after(command, "--campaign") == "pilot" and "--try" in command
    assert value_after(command, "--out") == cp.args.real_out and panel[-1]["env"]["ROS_DOMAIN_ID"] == "7"
    # The trial that counts: refused until the pre-session check passed today, or confirmed
    assert "pre-session check" in cp.act("/api/real/run", {**trial, "mode": "record"})["error"]
    assert cp.act("/api/real/run", {**trial, "mode": "record", "unchecked_ok": True})["error"] == ""
    assert "--try" not in panel[-1]["command"] and panel[-1]["meta"]["campaign"] == "pilot"
    ok = {k: {"status": "OK", "detail": ""} for k in ("speak", "listen", "llm")}
    cm.record_presession(cm.load_campaign("pilot"), ok, cp.args.real_out)
    assert cp.act("/api/real/run", {**trial, "mode": "record"})["error"] == ""
    # The checks: the campaign's, with their results kept per campaign
    assert cp.act("/api/check/real", {"campaign": "pilot", "what": "listen"})["error"] == ""
    assert value_after(panel[-1]["command"], "--campaign") == "pilot"
    assert panel[-1]["meta"]["check"] == "real:pilot:listen"
    assert cp.act("/api/real/presession", {"campaign": "pilot"})["error"] == ""
    assert panel[-1]["command"][-2:] == ["check", "presession"]
    assert panel[-1]["meta"]["check"] == "real:pilot:presession"
    assert "No campaign" in cp.act("/api/real/presession", {"campaign": "nobody"})["error"]


def test_a_campaign_whose_configuration_changed_refuses_from_the_page(panel):
    cp.act("/api/real/campaign/new", {"name": "pilot", "configuration": "default"})
    cp.act("/api/real/plan", {"campaign": "pilot", "participant": "P01"})
    with open(cm.config_path("default"), "a") as f:
        f.write("# edited by hand\n")
    for path, body in (("/api/real/run", {"participant": "P01", "k": 1, "mode": "try"}),
                       ("/api/check/real", {"what": "speak"}), ("/api/real/presession", {})):
        assert "refuses its trials and checks" in cp.act(path, {"campaign": "pilot", **body})["error"]
    assert not panel
    assert "changed after it was associated" in cp.campaign_detail("pilot")["integrity"]


def test_the_tests_tab_runs_through_the_panel(panel):
    body = {"file": "e4_es.yaml", "scenario": "pr_tea", "control": "bounded", "model": "qwen3:8b"}
    assert cp.act("/api/test/run", {**body, "config": "default"})["error"] == ""
    assert value_after(panel[-1]["command"], "try-scenario") == "pr_tea"
    assert value_after(panel[-1]["command"], "--config") == "default"
    assert cp.act("/api/test/check", {"what": "speak", "voice": "es_ES-davefx-medium"})["error"] == ""
    assert panel[-1]["command"][-2:] == ["--voice", "es_ES-davefx-medium"]


def test_the_page_and_the_endpoints_over_http(panel):
    server = ThreadingHTTPServer(("127.0.0.1", 0), cp.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.read().decode()

    def post(path, body):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as r:
            return json.load(r)
    try:
        page = get("/")
        for tab in ("campaign", "sim", "real", "configs", "tests"):
            assert f'data-tab="{tab}"' in page, tab
        assert 'id="page-tests"' in page and "function tLoad" in page and "<!--TESTS-->" not in page
        assert 'id="r_camp"' in page and 'id="g_list"' in page and "runPresession" in page
        assert json.loads(get("/api/real/campaigns"))["configs"][0]["name"] == "default"
        assert post("/api/real/campaign/new", {"name": "pilot", "configuration": "default"})["error"] == ""
        assert json.loads(get("/api/real/campaign?name=pilot"))["configuration"] == "default"
        assert json.loads(get("/api/configs"))["configs"][0]["state"] == "frozen"
        assert post("/api/config/duplicate", {"name": "default"})["name"] == "default-2"
        assert json.loads(get("/api/real/participants?campaign=pilot")) == []
        assert any(f["file"] == "e4_es.yaml" for f in json.loads(get("/api/test/catalogue"))["files"])
        assert "error" in json.loads(get("/api/real/campaign?name=nobody"))
        assert post("/api/nothing", {})["error"] == "Unknown action"
    finally:
        server.shutdown()
