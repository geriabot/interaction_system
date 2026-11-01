# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Tests of the options of a test of real_sessions.py (the voice, its settings and the filler
delay), without ROS nodes, microphone or LLM:

    python3 -m pytest evaluation/real/test_real_sessions.py
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import real_sessions as rs  # noqa: E402

E4 = os.path.join(rs.EVAL, "experiments", "scenarios", "e4_es.yaml")


def test_parse_voice():
    assert rs.parse_voice("es_ES-davefx-medium") == ("es_ES-davefx-medium", None)
    assert rs.parse_voice("es_ES-sharvard-medium:1") == ("es_ES-sharvard-medium", 1)
    assert rs.parse_voice("es_ES-sharvard-medium:0") == ("es_ES-sharvard-medium", 0)
    assert rs.parse_voice("es_ES-carlfm-x_low") == ("es_ES-carlfm-x_low", None)
    assert rs.parse_voice("en_GB-northern_english_male-medium")[0] == "en_GB-northern_english_male-medium"
    for bad in ("", "davefx", "es_ES-davefx", "es_ES-davefx-medium:x", "/tmp/v.onnx",
                "es_ES-davefx-medium; rm"):
        with pytest.raises(ValueError):
            rs.parse_voice(bad)


def sessions(**launch):
    s = rs.load_sessions()
    return {**s, "launch": {**s.get("launch", {}), **launch}}


def test_without_a_voice_the_speech_nodes_are_unchanged():
    # As main.launch.py starts them: the voice of the language file (config/languages)
    stt, tts = rs.speech_commands(sessions())
    assert tts[:4] == ["ros2", "run", "simple_hri", "tts_piper"]
    assert [a for a in tts if a.startswith("voice:=")] == ["voice:=es_ES-sharvard-medium"]
    assert not any(a.startswith("speaker:=") for a in tts)              # the voice's default
    assert "lang_code:=es" in tts and "__node:=tts_service_node" in tts
    assert "language:=es" in stt
    _, tts = rs.speech_commands(sessions(language="en"))
    assert "voice:=en_US-lessac-medium" in tts and "lang_code:=en" in tts
    _, tts = rs.speech_commands(sessions(tts="mms"))
    assert "lang_code:=spa" in tts
    assert stt[3] == "stt_faster_whisper"


def test_a_voice_its_speaker_and_its_settings_reach_the_voice_node():
    _, tts = rs.speech_commands(sessions(), {"voice": "es_MX-claude-high"})
    assert tts[:4] == ["ros2", "run", "simple_hri", "tts_piper"]
    assert tts[-2:] == ["-p", "voice:=es_MX-claude-high"]
    assert [a for a in tts if a.startswith("voice:=")] == ["voice:=es_MX-claude-high"]
    assert not any(a.startswith("speaker:=") for a in tts)              # the voice's default
    _, tts = rs.speech_commands(sessions(), {"voice": "es_ES-sharvard-medium:1"})
    assert tts[:4] == ["ros2", "run", "simple_hri", "tts_piper"]
    assert tts[-4:] == ["-p", "voice:=es_ES-sharvard-medium", "-p", "speaker:=1"]
    _, tts = rs.speech_commands(sessions(), {"voice": "es_ES-sharvard-medium:0"})
    assert tts[-2:] == ["-p", "speaker:=0"]
    _, tts = rs.speech_commands(sessions(), {"length_scale": 1, "noise_scale": 0.9,
                                             "noise_w_scale": 1.2})
    # Always doubles (with a decimal point), the type of the parameters
    assert tts[-6:] == ["-p", "length_scale:=1.0000", "-p", "noise_scale:=0.9000",
                        "-p", "noise_w_scale:=1.2000"]
    # The settings without a voice: the language's voice, once
    assert [a for a in tts if a.startswith("voice:=")] == ["voice:=es_ES-sharvard-medium"]
    with pytest.raises(ValueError):
        rs.speech_commands(sessions(tts="mms"), {"voice": "es_ES-davefx-medium"})
    for key, (low, high) in rs.PIPER_SETTINGS.items():
        assert rs.piper_parameters({key: low}) and rs.piper_parameters({key: high})
        for bad in (low - 0.01, high + 0.01):
            with pytest.raises(ValueError):
                rs.piper_parameters({key: bad})
    assert rs.piper_text({"voice": "es_ES-sharvard-medium:1", "noise_scale": 0.9}) == \
        "es_ES-sharvard-medium:1, noise_scale 0.9"


def test_simple_hri_has_the_parameters_passed():
    """tts_piper declares speaker, length_scale, noise_scale and noise_w_scale (needs the sourced
    workspace)."""
    tts_piper = pytest.importorskip("simple_hri.tts_piper")
    with open(tts_piper.__file__, encoding="utf-8") as f:
        source = f.read()
    for name in ["voice", "speaker"] + list(rs.PIPER_SETTINGS):
        assert f"declare_parameter('{name}'" in source, name


def try_args(**options):
    import argparse
    return argparse.Namespace(**{"scenario": "pr_tea", "scenarios": E4, "control": "bounded",
                                 "model": "qwen3:8b", "allow_cloud": False, "force": False,
                                 "keep": False, "barge_in": None, "voice": "",
                                 "length_scale": None, "noise_scale": None,
                                 "noise_w_scale": None, "filler_after": None, **options})


@pytest.fixture
def trial(monkeypatch):
    """What try-scenario gives run_trial (the conversation is not run)."""
    box = {}

    def run_trial(args, test, pid, plan, trial):
        box.update(test=test, trial=trial)
        return {}
    monkeypatch.setattr(rs, "run_trial", run_trial)
    return box


def test_try_scenario_without_options_keeps_the_sessions_file(trial):
    s = rs.load_sessions()
    assert rs.try_scenario(try_args(), s) == 0
    test = trial["test"]
    assert "piper" not in test and test["launch"].get("speech", "simple_hri") == "simple_hri"
    goal = test["scenarios"][0]["definition"]["goal"]
    assert "filler_after_s" not in goal
    assert "speech:=simple_hri" in rs.stack_command(test, "bounded", "p.yaml")


def test_try_scenario_with_a_voice_runs_the_stack_without_its_speech(trial):
    s = rs.load_sessions()
    before = dict(s["launch"])
    assert rs.try_scenario(try_args(voice="es_ES-davefx-medium", filler_after=0.5), s) == 0
    test = trial["test"]
    assert test["piper"] == {"voice": "es_ES-davefx-medium"}
    assert "speech:=none" in rs.stack_command(test, "bounded", "p.yaml")
    assert "speech:=none" in rs.stack_command(test, "llm_led", "p.yaml")
    assert rs.speech_commands(test, test["piper"])[1][-1] == "voice:=es_ES-davefx-medium"
    assert test["scenarios"][0]["definition"]["goal"]["filler_after_s"] == 0.5
    # The sessions file and the scenario file are not changed
    assert s["launch"] == before and "piper" not in s
    assert "filler_after_s" not in next(x for x in rs.load(E4)["scenarios"] if x["id"] == "pr_tea")["goal"]


def test_try_scenario_with_only_settings_keeps_the_default_voice(trial):
    assert rs.try_scenario(try_args(noise_scale=0.9), rs.load_sessions()) == 0
    test = trial["test"]
    assert test["piper"] == {"noise_scale": 0.9} and test["launch"]["speech"] == "none"
    assert rs.speech_commands(test, test["piper"])[1][-2:] == ["-p", "noise_scale:=0.9000"]


def test_try_scenario_refuses_a_wrong_voice_or_delay(trial):
    with pytest.raises(SystemExit):
        rs.try_scenario(try_args(voice="davefx"), rs.load_sessions())
    with pytest.raises(SystemExit):
        rs.try_scenario(try_args(filler_after=-1.0), rs.load_sessions())
    with pytest.raises(SystemExit):
        rs.try_scenario(try_args(noise_w_scale=5.0), rs.load_sessions())
    assert not trial


def test_the_options_reach_the_command_line(monkeypatch):
    seen = {}
    monkeypatch.setattr(rs, "try_scenario", lambda args, s: seen.setdefault("try", args) and 0)
    monkeypatch.setattr(rs, "run_check", lambda args, s: seen.setdefault("check", args) and 0)
    for argv in (["try-scenario", "pr_tea", "--voice", "es_ES-sharvard-medium:1", "--filler-after", "0.5",
                  "--length-scale", "1.1", "--noise-scale", "0.9", "--noise-w-scale", "1"],
                 ["check", "speak", "--voice", "es_AR-daniela-high", "--phrase", "¿Sí?",
                  "--noise-scale", "0.4"]):
        monkeypatch.setattr(sys, "argv", ["real_sessions.py"] + argv)
        with pytest.raises(SystemExit):
            rs.main()
    assert seen["try"].voice == "es_ES-sharvard-medium:1" and seen["try"].filler_after == 0.5
    assert seen["check"].voice == "es_AR-daniela-high" and seen["check"].phrase == "¿Sí?"
    assert rs.piper_of(seen["try"]) == {"voice": "es_ES-sharvard-medium:1", "length_scale": 1.1,
                                        "noise_scale": 0.9, "noise_w_scale": 1.0}
    assert rs.piper_of(seen["check"]) == {"voice": "es_AR-daniela-high", "noise_scale": 0.4}
    seen.clear()
    for argv in (["try-scenario", "pr_tea"], ["check", "speak"]):
        monkeypatch.setattr(sys, "argv", ["real_sessions.py"] + argv)
        with pytest.raises(SystemExit):
            rs.main()
    assert seen["try"].voice == "" and seen["try"].filler_after is None and seen["check"].voice == ""
    assert rs.piper_of(seen["try"]) == {} and rs.piper_of(seen["check"]) == {}


def test_check_refuses_a_voice_but_for_speak(monkeypatch):
    import argparse
    args = argparse.Namespace(what="listen", voice="es_ES-davefx-medium")
    with pytest.raises(SystemExit):
        rs.run_check(args, rs.load_sessions())
    with pytest.raises(SystemExit):
        rs.run_check(argparse.Namespace(what="listen", voice="", noise_scale=0.9), rs.load_sessions())
    with pytest.raises(SystemExit):
        rs.run_check(argparse.Namespace(what="speak", voice="", noise_scale=9.0), rs.load_sessions())
    with pytest.raises(SystemExit):
        rs.run_check(argparse.Namespace(what="speak", voice="nobody"), rs.load_sessions())
