# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Tests of the campaigns of real sessions and their configurations (campaigns.py), and of how
real_sessions.py runs with them, without ROS nodes, microphone or LLM:

    python3 -m pytest evaluation/real/test_campaigns.py
"""

import argparse
import json
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import campaigns as cm  # noqa: E402
import real_sessions as rs  # noqa: E402

STARTING = cm.load_config("default")     # the repository's starting configuration


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Empty configs/ and campaigns/ folders with the starting configuration, and the folder of
    the sessions' data: tmp_path/out."""
    monkeypatch.setattr(cm, "CONFIGS", str(tmp_path / "configs"))
    monkeypatch.setattr(cm, "CAMPAIGNS", str(tmp_path / "campaigns"))
    cm.write_config("default", STARTING["values"], {"note": "start"})
    return str(tmp_path / "out")


def store_trial(out, campaign, config, pid="P01", k=1):
    """A stored trial record, as real_sessions.run_trial writes it."""
    folder = os.path.join(cm.campaign_out(campaign, out), pid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"trial_{k}_sf_coffee_bounded.json"), "w") as f:
        json.dump({"configuration": config["name"], "configuration_hash": config["hash"]}, f)


def test_the_starting_configuration_is_the_sessions_of_before():
    """Today's defaults: simple_hri's Spanish voice with its own settings, the system's filler
    delay, the system's sound server and whisper small; the same as a test without one."""
    v = STARTING["values"]
    assert v["voice"] == "es_ES-sharvard-medium"
    assert all(v[k] is None for k in ("length_scale", "noise_scale", "noise_w_scale", "filler_after_s"))
    assert {k: v[k] for k in cm.SPEECH_DEFAULTS} == cm.SPEECH_DEFAULTS
    assert list(v) == list(cm.CONFIG_KEYS)


def test_a_configuration_is_validated():
    good = dict(STARTING["values"])
    assert cm.validate_config({**good, "noise_scale": "0.9", "filler_after_s": "0"}) == \
        {**good, "noise_scale": 0.9, "filler_after_s": 0.0}
    assert cm.validate_config({**good, "length_scale": " "})["length_scale"] is None
    for bad in ({"voice": ""}, {"voice": "davefx"}, {"length_scale": 3}, {"noise_scale": "lively"},
                {"noise_w_scale": float("nan")}, {"filler_after_s": -1}, {"input_device": ""},
                {"input_device": "3"}, {"input_device": "a\nb"}, {"whisper_model": "Small Model"},
                {"colour": "blue"}):
        with pytest.raises(ValueError):
            cm.validate_config({**good, **bad})


def test_names(repo):
    for bad in ("", "Pilot", "p07", "a b", "../x", "x" * 41, "-x"):
        with pytest.raises(ValueError):
            cm.check_name(bad, "campaign")
    assert cm.check_name(" pilot-1_b ", "campaign") == "pilot-1_b"


def test_a_draft_is_edited_renamed_and_duplicated(repo):
    assert [(c["name"], c["state"]) for c in cm.configurations()] == [("default", "draft")]
    edited = cm.save_config("default", {**STARTING["values"], "noise_scale": 0.9}, previous="default")
    assert edited["values"]["noise_scale"] == 0.9 and edited["note"] is None   # the note as given
    renamed = cm.save_config("calm", edited["values"], previous="default", note="for E4")
    assert renamed["name"] == "calm" and not os.path.exists(cm.config_path("default"))
    copy = cm.duplicate("calm")
    assert copy["name"] == "calm-2" and copy["values"] == renamed["values"]
    assert copy["note"] == "for E4" and copy["duplicated_from"] == "calm"
    assert cm.duplicate("calm-2")["name"] == "calm-3"          # the next free, not calm-2-2
    assert cm.duplicate("calm", "woman")["name"] == "woman"   # the name is editable
    with pytest.raises(ValueError):
        cm.duplicate("calm", "woman")                          # taken
    with pytest.raises(ValueError):
        cm.save_config("calm", STARTING["values"])              # a new one with a taken name
    with pytest.raises(ValueError):
        cm.save_config("woman", STARTING["values"], previous="calm")   # renamed onto another


def test_a_configuration_used_by_a_campaign_is_frozen(repo):
    campaign = cm.create_campaign("pilot", "default", out=repo)
    assert campaign["configuration_hash"] == cm.load_config("default")["hash"]
    (c,) = cm.configurations()
    assert c["state"] == "frozen" and c["used_by"] == ["pilot"]
    with pytest.raises(ValueError, match="frozen"):
        cm.save_config("default", {**STARTING["values"], "noise_scale": 0.9}, previous="default")
    with pytest.raises(ValueError, match="frozen"):
        cm.save_config("renamed", STARTING["values"], previous="default")
    # Duplicate and edit: a new draft, everything copied
    copy = cm.duplicate("default")
    assert copy["name"] == "default-2" and copy["values"] == c["values"]
    cm.save_config("default-2", {**copy["values"], "length_scale": 1.1}, previous="default-2")
    assert {c["name"]: c["state"] for c in cm.configurations()} == {"default": "frozen",
                                                                   "default-2": "draft"}


def test_a_campaign_switches_only_before_its_first_stored_trial(repo):
    cm.create_campaign("pilot", "default", out=repo)
    other = cm.duplicate("default", "slower")
    cm.save_config("slower", {**other["values"], "length_scale": 1.15}, previous="slower")
    switched = cm.switch("pilot", "slower", out=repo)
    assert switched["configuration"] == "slower"
    assert switched["configuration_hash"] == cm.load_config("slower")["hash"]    # taken again
    # default is a draft again (no campaign uses it), slower is frozen
    assert {c["name"]: c["state"] for c in cm.configurations()} == {"default": "draft",
                                                                   "slower": "frozen"}
    assert cm.integrity(cm.load_campaign("pilot"), repo) == ""
    store_trial(repo, "pilot", cm.load_config("slower"))
    with pytest.raises(ValueError, match="stored trial"):
        cm.switch("pilot", "default", out=repo)
    assert cm.campaigns(repo)[0]["can_switch"] is False
    assert cm.integrity(cm.load_campaign("pilot"), repo) == ""


def test_a_trial_replaced_still_counts_as_stored(repo):
    cm.create_campaign("pilot", "default", out=repo)
    folder = os.path.join(cm.campaign_out("pilot", repo), "P01", "replaced", "20261030_120000")
    os.makedirs(folder)
    with open(os.path.join(folder, "trial_1_sf_coffee_bounded.json"), "w") as f:
        json.dump({"configuration": "default", "configuration_hash": cm.load_config("default")["hash"]}, f)
    with pytest.raises(ValueError):
        cm.switch("pilot", "default", out=repo)


def test_a_configuration_changed_by_hand_makes_its_campaign_refuse(repo):
    cm.create_campaign("pilot", "default", out=repo)
    assert cm.integrity(cm.load_campaign("pilot"), repo) == ""
    with open(cm.config_path("default"), "a") as f:
        f.write("# a comment changes the file too\n")
    why = cm.integrity(cm.load_campaign("pilot"), repo)
    assert "changed after it was associated" in why and "default" in why
    assert cm.campaigns(repo)[0]["integrity"] == why
    os.remove(cm.config_path("default"))
    assert "missing" in cm.integrity(cm.load_campaign("pilot"), repo)


def test_a_stored_trial_of_another_configuration_makes_its_campaign_refuse(repo):
    cm.create_campaign("pilot", "default", out=repo)
    store_trial(repo, "pilot", {"name": "other", "hash": "123456789abc"})
    assert "stored with the configuration other" in cm.integrity(cm.load_campaign("pilot"), repo)


def test_a_campaign_is_checked_when_created(repo):
    with pytest.raises(ValueError, match="No configuration"):
        cm.create_campaign("pilot", "nobody", out=repo)
    with pytest.raises(ValueError, match="sessions file"):
        cm.create_campaign("pilot", "default", sessions_file="cards_en.yaml", out=repo)
    english = cm.save_config("english", {**STARTING["values"], "voice": "en_US-lessac-medium"})
    with pytest.raises(ValueError, match="language"):
        cm.create_campaign("pilot", english["name"], out=repo)
    cm.create_campaign("pilot", "default", out=repo)
    with pytest.raises(ValueError, match="exists"):
        cm.create_campaign("pilot", "default", out=repo)
    with pytest.raises(ValueError, match="language"):
        cm.switch("pilot", "english", out=repo)
    # A folder that already holds participants is never adopted by a new campaign
    os.makedirs(os.path.join(repo, "old", "P01"))
    with open(os.path.join(repo, "old", "P01", "plan.yaml"), "w") as f:
        yaml.safe_dump({"trials": []}, f)
    with pytest.raises(ValueError, match="already holds"):
        cm.create_campaign("old", "default", out=repo)


def test_the_participants_of_before_campaigns_are_listed_as_legacy(repo):
    os.makedirs(os.path.join(repo, "P03"))
    with open(os.path.join(repo, "P03", "plan.yaml"), "w") as f:
        yaml.safe_dump({"trials": []}, f)
    assert cm.legacy_participants(repo) == ["P03"]
    cm.create_campaign("pilot", "default", out=repo)
    assert cm.legacy_participants(repo) == ["P03"]       # never moved
    assert cm.campaigns(repo)[0]["participants"] == 0


def test_the_pre_session_check_passes_for_a_day_and_a_configuration(repo):
    campaign = cm.load_campaign(cm.create_campaign("pilot", "default", out=repo)["name"])
    assert cm.presession_state(campaign, repo)["passed_today"] is False
    ok = {k: {"status": "OK", "detail": ""} for k in ("speak", "listen", "llm")}
    state = cm.record_presession(campaign, {**ok, "listen": {"status": "FAILED", "detail": "x"}}, repo)
    assert state["last"]["passed"] is False and "last_pass" not in state
    assert cm.presession_state(campaign, repo)["passed_today"] is False
    cm.record_presession(campaign, ok, repo)
    today = cm.presession_state(campaign, repo)
    assert today["passed_today"] and today["last_pass"]["configuration"] == "default"
    assert cm.presession_state(campaign, repo, today="2099-01-01")["passed_today"] is False
    # A failed check later keeps the date of the last pass
    cm.record_presession(campaign, {**ok, "speak": {"status": "FAILED", "detail": ""}}, repo)
    assert cm.presession_state(campaign, repo)["passed_today"]
    # Another configuration: the pass was not with it
    cm.duplicate("default", "other")
    switched = cm.switch("pilot", "other", out=repo)
    assert cm.presession_state(switched, repo)["passed_today"] is False


def test_apply_puts_the_configuration_where_the_trial_reads_it():
    sessions = rs.load_sessions()
    assert set(sessions["launch"]) == {"speech", "language", "stt", "tts"}   # one source of truth
    config = {"name": "c", "hash": "h", "values": {**STARTING["values"], "voice": "es_MX-claude-high",
                                                    "noise_scale": 0.9, "filler_after_s": 0.5,
                                                    "input_device": "USB mic", "whisper_model": "medium"}}
    applied = cm.apply(sessions, config, "pilot")
    assert applied["piper"] == {"voice": "es_MX-claude-high", "noise_scale": 0.9}
    assert applied["launch"]["speech"] == "none"
    assert applied["filler_after_s"] == 0.5 and applied["campaign"] == "pilot"
    assert applied["configuration"] == {"name": "c", "hash": "h", "values": config["values"]}
    assert "piper" not in sessions and sessions["launch"]["speech"] == "simple_hri"   # unchanged
    assert "speech:=none" in rs.stack_command(applied, "bounded", "p.yaml")
    stt, tts = rs.speech_commands(applied, applied["piper"])
    assert "model:=medium" in stt and "input_device:=USB mic" in stt
    assert "voice:=es_MX-claude-high" in tts and "noise_scale:=0.9000" in tts
    # Without a configuration: the defaults (those of the starting configuration)
    stt, _ = rs.speech_commands(sessions)
    assert "model:=small" in stt and "input_device:=default" in stt
    command = rs.stack_command(sessions, "bounded", "p.yaml")
    assert "speech:=simple_hri" in command and "input_device:=default" in command


def args_for(command, **options):
    return argparse.Namespace(**{"command": command, "campaign": "", "out": "", "config": "",
                                 "sessions": rs.SESSIONS, "just_try": False, **options})


def test_real_sessions_runs_a_campaign_with_its_configuration(repo, capsys):
    cm.create_campaign("pilot", "default", out=repo)
    args = args_for("trial", campaign="pilot", out=repo)
    sessions, campaign, root = rs.configured(args)
    assert root == repo and args.out == os.path.join(repo, "pilot") and campaign["name"] == "pilot"
    assert sessions["configuration"]["name"] == "default" and sessions["campaign"] == "pilot"
    assert sessions["piper"] == {"voice": "es_ES-sharvard-medium"}
    # The trial that counts warns: the pre-session check has not passed today
    assert "has not passed today" in capsys.readouterr().out
    rs.configured(args_for("trial", campaign="pilot", out=repo, just_try=True))
    assert "has not passed today" not in capsys.readouterr().out


def test_real_sessions_refuses_a_campaign_whose_configuration_changed(repo):
    cm.create_campaign("pilot", "default", out=repo)
    with open(cm.config_path("default"), "a") as f:
        f.write("noise_scale: 0.9\n")
    for command in ("trial", "check", "try-scenario"):
        with pytest.raises(SystemExit, match="refuses its trials and checks"):
            rs.configured(args_for(command, campaign="pilot", out=repo))
    # Reading its plans and cards is still possible
    sessions, _, _ = rs.configured(args_for("card", campaign="pilot", out=repo))
    assert "configuration" not in sessions


def test_real_sessions_needs_a_campaign_for_a_participant(repo):
    for command in ("plan", "trial"):
        with pytest.raises(SystemExit, match="campaign"):
            rs.configured(args_for(command, out=repo))
    with pytest.raises(SystemExit, match="own configuration"):
        cm.create_campaign("pilot", "default", out=repo)
        rs.configured(args_for("check", campaign="pilot", out=repo, config="default"))
    # A check or a test with any configuration, drafts included, and none: the defaults
    sessions, campaign, _ = rs.configured(args_for("check", out=repo, config="default"))
    assert campaign is None and sessions["configuration"]["name"] == "default"
    sessions, _, _ = rs.configured(args_for("check", out=repo))
    assert "configuration" not in sessions and "piper" not in sessions
    with pytest.raises(SystemExit, match="No configuration"):
        rs.configured(args_for("check", out=repo, config="nobody"))


def test_try_scenario_takes_the_configuration_and_the_options_over_it(monkeypatch, repo):
    box = {}
    monkeypatch.setattr(rs, "run_trial", lambda args, test, pid, plan, trial: box.update(test=test) or {})
    cm.save_config("woman", {**STARTING["values"], "voice": "es_ES-sharvard-medium:1",
                             "filler_after_s": 1.0})
    sessions = cm.apply(rs.load_sessions(), cm.load_config("woman"))
    targs = argparse.Namespace(scenario="pr_tea", scenarios=os.path.join(rs.EVAL, "experiments",
                               "scenarios", "e4_es.yaml"), control="bounded", model="qwen3:8b",
                               allow_cloud=False, force=False, keep=False, barge_in=None, voice="",
                               length_scale=None, noise_scale=0.4, noise_w_scale=None,
                               filler_after=None)
    assert rs.try_scenario(targs, sessions) == 0
    test = box["test"]
    assert test["piper"] == {"voice": "es_ES-sharvard-medium:1", "noise_scale": 0.4}
    assert test["filler_after_s"] == 1.0 and test["configuration"]["name"] == "woman"
    targs.filler_after = 0.0
    rs.try_scenario(targs, sessions)
    assert box["test"]["filler_after_s"] == 0.0              # --filler-after over the configuration
    # A Spanish voice for an English scenario file is refused
    targs.scenarios = os.path.join(rs.EVAL, "experiments", "scenarios", "dialogues.yaml")
    targs.scenario = next(s["id"] for s in rs.load(targs.scenarios)["scenarios"])
    with pytest.raises(SystemExit, match="language"):
        rs.try_scenario(targs, sessions)


def test_the_records_and_the_trials_csv_keep_the_configuration():
    source = open(rs.__file__, encoding="utf-8").read()
    for key in ('"campaign":', '"configuration":', '"configuration_hash":', '"configuration_values":'):
        assert key in source, key
    assert "configuration" in rs.TRIALS_CSV and "presession" in rs.CHECKS


def test_the_command_line(repo, capsys, monkeypatch):
    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["campaigns.py", "--out", repo] + list(argv))
        cm.main()
        return capsys.readouterr().out
    assert "default: draft" in run("list")
    assert "frozen" in run("new-campaign", "pilot", "--config", "default")
    with pytest.raises(SystemExit, match="frozen"):
        run("set", "default", "noise_scale=0.9")
    assert "default-2" in run("duplicate", "default")
    assert "noise_scale 0.9" in run("set", "default-2", "noise_scale=0.9")
    assert "now uses default-2" in run("switch", "pilot", "default-2")
    out = run("list")
    assert "default: draft" in out and "default-2: frozen (used by pilot)" in out
