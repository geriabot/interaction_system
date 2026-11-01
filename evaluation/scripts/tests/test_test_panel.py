# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The ready examples of the test panel (test_panel_examples.yaml) and its tester card, without
ROS, browser or LLM:

    python3 -m pytest scripts/tests/test_test_panel.py
"""

import glob
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
EVAL = os.path.dirname(SCRIPTS)
SRC = os.path.dirname(EVAL)
sys.path.insert(0, SCRIPTS)
import test_panel as tp  # noqa: E402

# The simulated user (read, not imported: it needs the ROS interfaces)
LLM_USER = os.path.join(EVAL, "simulation", "interaction_sim", "interaction_sim", "llm_user.py")

EXAMPLES = tp.control_panel.load(tp.EXAMPLES)
# The files whose mechanisms must all have an example: E3 (Spanish) and E4
MECHANISM_FILES = ("e3_es.yaml", "e4_es.yaml")


def scenarios(name):
    return {s["id"]: s for s in tp.control_panel.load(os.path.join(tp.SCENARIOS, name))["scenarios"]}


def all_examples():
    return EXAMPLES["by_strategy"] + EXAMPLES["by_mechanism"]


def test_every_example_is_a_scenario_of_an_existing_file():
    for ex in all_examples():
        assert os.path.exists(os.path.join(tp.SCENARIOS, ex["file"])), ex
        assert ex["scenario"] in scenarios(ex["file"]), ex
        assert ex.get("condition", "bounded") in ("bounded", "llm_led"), ex


def test_every_implemented_strategy_has_one_example_of_that_strategy():
    trees = glob.glob(os.path.join(SRC, "interaction_system_behaviors", "behavior_tree_xml", "*.xml"))
    implemented = {os.path.basename(t)[:-4] for t in trees} - {"common"}
    assert implemented == {"slot_filling", "quiz", "procedure", "explanation", "grounded_qa",
                           "questionnaire", "roleplay"}
    by_strategy = {ex["strategy"]: ex for ex in EXAMPLES["by_strategy"]}
    assert set(by_strategy) == implemented
    assert len(EXAMPLES["by_strategy"]) == len(implemented)
    for strategy, ex in by_strategy.items():
        assert scenarios(ex["file"])[ex["scenario"]]["strategy"] == strategy


def test_every_scenario_of_e3_and_e4_says_what_it_provokes():
    """The `mechanism` of every scenario of e3_es.yaml and e4_es.yaml is mapped to mechanism ids
    with a name (so that none is left without an example by omission)."""
    names = EXAMPLES["mechanisms"]
    for name in MECHANISM_FILES:
        mapped = EXAMPLES["provokes"][name]
        assert set(mapped) == set(scenarios(name)), name
        for scenario, ids in mapped.items():
            assert ids, scenario
            assert set(ids) <= set(names), (scenario, set(ids) - set(names))
    for name, mapped in EXAMPLES["provokes"].items():
        assert set(mapped) <= set(scenarios(name)), name


def test_every_mechanism_has_exactly_one_example_that_provokes_it():
    provoked = {i for mapped in EXAMPLES["provokes"].values() for ids in mapped.values() for i in ids}
    examples = [ex["mechanism"] for ex in EXAMPLES["by_mechanism"]]
    assert sorted(examples) == sorted(set(examples)), "one example per mechanism"
    assert set(examples) == provoked == set(EXAMPLES["mechanisms"])
    for ex in EXAMPLES["by_mechanism"]:
        assert ex["mechanism"] in EXAMPLES["provokes"][ex["file"]][ex["scenario"]], ex


def test_every_expected_mechanism_key_is_shown_by_some_example():
    """Every key of expect.mechanisms in e3_es.yaml and e4_es.yaml appears in the scenario of
    some example, and the card says it in words (not as key: value)."""
    keys = {k for name in MECHANISM_FILES for s in scenarios(name).values()
            for k in (s.get("expect") or {}).get("mechanisms", {})}
    shown = {k for ex in all_examples()
             for k in (scenarios(ex["file"])[ex["scenario"]].get("expect") or {}).get("mechanisms", {})}
    assert keys <= shown, keys - shown
    for key in keys:
        for lang in ("es", "en"):
            (line,) = tp.mechanism_lines({key: 1}, lang)
            assert not line.startswith(f"{key}:"), (key, lang)


def test_every_column_of_the_strategy_checks_has_its_words():
    """The columns of analysis/strategy_checks.py (read, not imported) that a scenario may list in
    expect.mechanisms all have their line on the card."""
    with open(os.path.join(EVAL, "analysis", "strategy_checks.py"), encoding="utf-8") as f:
        source = f.read()
    columns = set(re.findall(r'out\["(\w+)"\]', source)) | set(
        re.findall(r'"(\w+)": (?:None|said|rejected|data|bool|len|frame)', source))
    assert {"range_rejected", "tb_marked_wrong", "repeat_literal", "rp_exchanges"} <= columns
    assert columns <= set(tp.STRATEGY_CHECKS), columns - set(tp.STRATEGY_CHECKS)
    assert tp.mechanism_lines({"rp_exchanges": 3}, "es") == [
        "Intercambios de la escena: 3 (las peticiones de repetir no cuentan)"]
    assert tp.mechanism_lines({"rubric_in_intro": []}, "en")[0].endswith(": none")


def test_an_example_without_events_says_that_its_role_provokes_the_mechanism():
    for ex in EXAMPLES["by_mechanism"]:
        scenario = scenarios(ex["file"])[ex["scenario"]]
        if not scenario.get("events") and ex["file"] == "dialogues_es.yaml":
            assert "user_goal" in ex.get("note", ""), ex


def test_the_card_has_one_line_per_event_of_every_kind():
    events = [{"turn": 3, "kind": "cancel", "text": "Para, déjalo."},
              {"turn": 1, "kind": "text", "text": "Un café."},
              {"turn": 4, "kind": "silence"},
              {"turn": 5, "kind": "detour", "detour": "off_task", "text": "¿Va a llover?"},
              {"turn": 6, "kind": "barge_in", "barge_in_after": 1.0},
              {"turn": 7, "kind": "whistle", "text": "fiu"}]
    # Every kind the simulated user accepts (llm_user.UserEvent) is in the list above
    with open(LLM_USER, encoding="utf-8") as f:
        source = f.read()
    accepted = set(re.findall(r'"(\w+)"', re.search(r"self\.kind not in \(([^)]*)\)", source).group(1)))
    assert accepted == {e["kind"] for e in events} - {"whistle"}
    limits = {"wait_timeout": 15.0, "max_silence": 90.0, "max_turns": 25}
    for lang, spanish in (("es", True), ("en", False)):
        card = tp.tester_card({"user_goal": "x", "events": events}, spanish, limits)
        turns = next(s for s in card["sections"] if s["title"] == tp.CARD[lang]["turns"])["lines"]
        lines = turns[1:-1]   # after how turns are counted, before "the other turns"
        assert len(lines) == len(events)
        # In the order of the turns, each with its ordinal and its line
        assert all(tp.ordinal(n, lang) in line for n, line in zip((1, 3, 4, 5, 6, 7), lines))
        assert "Un café." in lines[0] and "Para, déjalo." in lines[1] and "¿Va a llover?" in lines[3]
        assert "1" in lines[4] and "whistle" in lines[5] and "fiu" in lines[5]
    assert tp.ordinal(3, "es") == "3.er" and tp.ordinal(4, "es") == "4.º" and tp.ordinal(2, "en") == "2nd"


def test_every_scenario_of_every_file_gets_a_card_with_all_its_events():
    catalogue = tp.catalogue()
    assert catalogue["examples"]["by_strategy"] and catalogue["examples"]["by_mechanism"]
    for f in catalogue["files"]:
        defined = scenarios(f["file"])
        lang = "es" if f["file"].endswith("_es.yaml") else "en"
        for listed in f["strategies"].values():
            for s in listed:
                events = defined[s["id"]].get("events") or []
                turns = next(x for x in s["card"]["sections"] if x["title"] == tp.CARD[lang]["turns"])
                planned = [line for line in turns["lines"] if tp.CARD[lang]["text"].split("{")[0] in line]
                assert len(planned) == len(events), (f["file"], s["id"])
                role = s["card"]["sections"][0]["lines"][0]
                assert role == str(defined[s["id"]].get("user_goal", "")).strip()


@pytest.mark.parametrize("experiment", ["e3_mechanisms.yaml", "e4_sim_es.yaml"])
def test_the_panel_runs_the_examples_with_the_limits_their_events_are_planned_on(experiment):
    """try-scenario takes the limits of real/sessions_es.yaml; the silence events of E3 and E4 are
    planned on those of their experiments: they must be the same (otherwise the card is wrong)."""
    sessions = tp.control_panel.load(tp.control_panel.sessions_common.SESSIONS)
    exp = tp.control_panel.load(os.path.join(tp.control_panel.EXPERIMENTS, experiment))
    assert exp["limits"] == sessions["limits"]
    assert exp["max_turns"] == sessions["max_turns"]
    assert exp["dialogue_timeout"] == sessions["dialogue_timeout"]


# ==== The voice and the filler delay of a test (passed to real_sessions.py) ====

sys.path.insert(0, os.path.join(EVAL, "real"))
import real_sessions as rs  # noqa: E402


def test_every_voice_is_a_piper_catalog_name_of_its_language():
    values = [v for v, _, _, _ in tp.VOICES]
    assert len(values) == len(set(values))
    for value, lang, gender, label in tp.VOICES:
        name, speaker = rs.parse_voice(value)
        assert name.startswith(lang + "_") and lang in ("es", "en"), value
        assert label.startswith(name), value
        assert gender in ("man", "woman"), value
        # Only es_ES-sharvard-medium has several speakers (man: 0, woman: 1) among those listed
        assert speaker is None or (name == "es_ES-sharvard-medium" and speaker == 1), value
    # The voices asked for, and simple_hri's defaults of each language
    assert {"es_ES-sharvard-medium", "es_ES-sharvard-medium:1", "es_ES-davefx-medium",
            "es_ES-carlfm-x_low", "es_MX-claude-high", "es_MX-ald-medium", "es_AR-daniela-high",
            "en_US-lessac-medium"} <= set(values)
    assert tp.voice_language("es_AR-daniela-high") == "es" and tp.voice_language("x") is None
    assert set(tp.QUESTIONS) == {"es", "en"} and all(q.endswith("?") for q in tp.QUESTIONS.values())
    # Both a man's and a woman's voice in each language, for the panel's filter
    assert {(lang, gender) for _, lang, gender, _ in tp.VOICES} == {
        (lang, gender) for lang in ("es", "en") for gender in ("man", "woman")}
    assert tp.catalogue()["voices"][0] == {"value": "es_ES-sharvard-medium", "language": "es",
                                          "gender": "man", "label": tp.VOICES[0][3]}


@pytest.fixture
def started(monkeypatch):
    """The command the panel would start (control_panel.start_job, not run)."""
    calls = []
    monkeypatch.setattr(tp.control_panel, "start_job",
                        lambda kind, title, command, env=None, **kw: calls.append(command) or "")
    return calls


def test_the_voice_and_the_filler_delay_reach_try_scenario(started):
    body = {"file": "e4_es.yaml", "scenario": "pr_tea", "control": "bounded", "model": "qwen3:8b"}
    assert tp.run(body) == ""
    assert "--voice" not in started[-1] and "--filler-after" not in started[-1]   # empty: default
    assert tp.run({**body, "voice": "", "filler_after": "  "}) == ""
    assert "--voice" not in started[-1] and "--filler-after" not in started[-1]
    assert tp.run({**body, "voice": "es_ES-sharvard-medium:1", "filler_after": "0.5"}) == ""
    command = started[-1]
    assert command[command.index("--voice") + 1] == "es_ES-sharvard-medium:1"
    assert command[command.index("--filler-after") + 1] == "0.5"
    assert tp.run({**body, "filler_after": 0}) == "" and started[-1][-2:] == ["--filler-after", "0"]
    count = len(started)
    assert "Unknown voice" in tp.run({**body, "voice": "es_ES-nobody-medium"})
    assert "language" in tp.run({**body, "voice": "en_US-lessac-medium"})   # a Spanish file
    assert "Filler" in tp.run({**body, "filler_after": "-1"})
    assert "Filler" in tp.run({**body, "filler_after": "soon"})
    assert len(started) == count


def test_the_voice_reaches_the_speak_check_only(started):
    assert tp.check({"what": "speak", "phrase": tp.QUESTIONS["es"], "voice": "es_MX-claude-high"}) == ""
    assert started[-1][-4:] == ["--phrase", tp.QUESTIONS["es"], "--voice", "es_MX-claude-high"]
    assert tp.check({"what": "speak", "voice": ""}) == "" and "--voice" not in started[-1]
    assert tp.check({"what": "listen", "voice": "es_MX-claude-high"}) == ""
    assert "--voice" not in started[-1]
    assert "Unknown voice" in tp.check({"what": "speak", "voice": "es_ES-nobody-medium"})


def test_the_tab_has_the_voice_selector_the_filler_field_and_the_configurations():
    """The Tests tab of the control panel (test_panel.TAB_HTML and TAB_JS in its page)."""
    tab = tp.TAB_HTML + tp.TAB_JS
    for element in ('id="t_voice"', 'id="t_filler"', 'id="t_phrasepick"', "voice:val('t_voice')",
                    "filler_after:val('t_filler')", 'id="t_preset"', 'id="t_settings"',
                    "...settingsOf('t_')", 'id="t_config"', "config:val('t_config')",
                    "'/api/test/run'", "'/api/test/check'", 'id="page-tests"'):
        assert element in tab, element
    page = tp.control_panel.PAGE
    assert "<!--TESTS-->" in page and "/*TESTS_JS*/" in page


def test_the_standalone_page_is_retired(capsys):
    assert tp.main() == 2
    assert "control_panel.py" in capsys.readouterr().err


def test_a_configuration_reaches_try_scenario_and_the_checks(started):
    body = {"file": "e4_es.yaml", "scenario": "pr_tea", "control": "bounded", "model": "qwen3:8b",
            "config": "default"}
    assert tp.run(body) == ""
    command = started[-1]
    assert command[command.index("--config") + 1] == "default" and "--voice" not in command
    assert tp.check({"what": "speak", "config": "default"}) == ""
    assert started[-1][-2:] == ["--config", "default"]
    assert tp.check({"what": "listen", "config": "default"}) == ""   # its microphone and model
    assert started[-1][-2:] == ["--config", "default"]
    assert tp.check({"what": "llm", "config": "default"}) == "" and "--config" not in started[-1]
    count = len(started)
    assert "No configuration" in tp.run({**body, "config": "nobody"})
    english = {**body, "file": "dialogues.yaml",
               "scenario": tp.control_panel.load(os.path.join(tp.SCENARIOS, "dialogues.yaml"))["scenarios"][0]["id"]}
    assert "language" in tp.run(english)      # a Spanish voice for an English file
    assert len(started) == count
    names = [c["name"] for c in tp.catalogue()["configs"]]
    assert "default" in names


def test_the_voice_settings_and_their_presets_are_in_range():
    assert set(tp.SETTINGS) == set(rs.PIPER_SETTINGS)
    assert tp.PRESETS[0] == ("voice default", {})
    for label, values in tp.PRESETS:
        assert set(values) <= set(tp.SETTINGS), label
        assert tp.voice_settings(values) == values, label     # every preset is accepted
    settings = tp.catalogue()["settings"]
    assert [s["key"] for s in settings] == list(tp.SETTINGS)
    assert all(s["low"] < s["high"] for s in settings)
    assert tp.voice_settings({"length_scale": "", "noise_scale": " ", "noise_w_scale": None}) == {}
    for key, (low, high) in rs.PIPER_SETTINGS.items():
        for bad in (low - 0.1, high + 0.1, "lively"):
            with pytest.raises(ValueError):
                tp.voice_settings({key: bad})


def test_the_voice_settings_reach_the_speak_check_and_try_scenario(started):
    body = {"file": "e4_es.yaml", "scenario": "pr_tea", "control": "bounded", "model": "qwen3:8b",
            "voice": "es_ES-sharvard-medium", "length_scale": "1.15", "noise_scale": "0.9",
            "noise_w_scale": ""}
    assert tp.run(body) == ""
    command = started[-1]
    assert command[command.index("--length-scale") + 1] == "1.15"
    assert command[command.index("--noise-scale") + 1] == "0.9"
    assert "--noise-w-scale" not in command                       # empty: the voice's own
    assert tp.check({"what": "speak", "noise_w_scale": "1.2"}) == ""
    assert started[-1][-2:] == ["--noise-w-scale", "1.2"] and "--voice" not in started[-1]
    assert tp.check({"what": "listen", "noise_w_scale": "1.2"}) == ""
    assert "--noise-w-scale" not in started[-1]
    count = len(started)
    assert "Expressiveness" in tp.run({**body, "noise_scale": "3"})
    assert "Speed" in tp.check({"what": "speak", "length_scale": "0.1"})
    assert len(started) == count
