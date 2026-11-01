# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Tests of the adherence check of the simulated user, without ROS, LLM or network: the
simulator's side (goal items in the prompt, the `revealed` field and its parsing, its record in
the log, with a fake LLM) and the checker of the scoring (synthetic runs):

    python3 -m pytest evaluation/analysis/test_adherence.py
"""

import csv
import json
import os
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.normpath(os.path.join(HERE, "..", "simulation", "interaction_sim"))
sys.path.insert(0, HERE)
sys.path.insert(0, SIM)
import adherence  # noqa: E402
import score_runs  # noqa: E402
from interaction_sim import cloud_llm, goal_items as gi  # noqa: E402

ITEMS = [{"id": "drink", "value": "latte"}, {"id": "size", "value": "medium"},
         {"id": "water", "value": "8 am", "then": "6 am"}]


# ==== THE SIMULATOR'S SIDE ====

def test_goal_items_are_validated():
    assert gi.validate_goal_items(None) == []
    assert gi.validate_goal_items([{"id": "age", "value": 72}]) == [{"id": "age", "value": "72"}]
    for bad in ([{"id": "a"}], [{"id": "a", "value": "x"}, {"id": "a", "value": "y"}],
                [{"id": "a", "value": "x", "optional": True}], [{"id": "a", "value": ["x"]}],
                {"id": "a", "value": "x"}):
        with pytest.raises(ValueError):
            gi.validate_goal_items(bad)


def test_the_schema_adds_revealed_with_the_ids_only_with_goal_items():
    base = {"type": "object", "properties": {"action": {"type": "string"},
                                             "text": {"type": "string"}},
            "required": ["action", "text"]}
    assert gi.answer_schema(base, []) is base
    s = gi.answer_schema(base, ITEMS)
    assert s["required"] == ["action", "text", "request", "revealed"]
    assert s["properties"]["request"]["enum"] == ["yes", "no"]
    assert s["properties"]["revealed"]["items"]["properties"]["id"]["enum"] == \
        ["drink", "size", "water"]
    assert s["propertyOrdering"] == ["action", "text", "request", "revealed"]
    assert "revealed" not in base["properties"]
    # Each provider's form of it: Gemini keeps the order; Ollama and OpenAI do not get the key
    assert cloud_llm.gemini_schema(s)["propertyOrdering"] == ["action", "text", "request",
                                                              "revealed"]
    assert cloud_llm.gemini_schema(s)["properties"]["revealed"]["type"] == "ARRAY"
    plain = cloud_llm.plain_schema(s)
    assert "propertyOrdering" not in plain
    strict = cloud_llm.strict_schema(plain)
    assert strict["properties"]["revealed"]["items"]["additionalProperties"] is False
    assert strict["properties"]["revealed"]["items"]["required"] == ["id", "value"]
    assert cloud_llm.plain_schema(base) == base


def test_parse_a_good_answer():
    a = gi.parse_answer('{"action": "say", "text": "A medium latte, please.", '
                        '"revealed": [{"id": "drink", "value": "latte"}, '
                        '{"id": "size", "value": "medium"}, {"id": "size", "value": "medium"}]}')
    assert a == {"action": "say", "text": "A medium latte, please.",
                 "revealed": [{"id": "drink", "value": "latte"},
                              {"id": "size", "value": "medium"}]}


def test_parse_reads_whether_the_line_asks_for_something():
    a = gi.parse_answer('{"action": "say", "text": "How much is it?", "request": "yes", '
                        '"revealed": [{"id": "drink", "value": "latte"}]}')
    assert a["request"] == "yes"
    # Not readable: absent (the scoring then treats the turn as unknown), the line is kept
    a = gi.parse_answer('{"action": "say", "text": "Thanks.", "request": "maybe", "revealed": []}')
    assert "request" not in a and a["text"] == "Thanks."
    assert '"request"' in gi.ANSWER_FORMAT_ITEMS and "request" in gi.ITEMS_BLOCK


def test_parse_tolerates_fences_and_text_around():
    a = gi.parse_answer('Sure:\n```json\n{"action": "say", "text": "Hi", "revealed": []}\n```')
    assert a == {"action": "say", "text": "Hi", "revealed": []}


def test_parse_keeps_the_line_when_the_declaration_is_broken():
    a = gi.parse_answer('{"action": "say", "text": "Laura", "revealed": "name"}')
    assert a["text"] == "Laura" and a["revealed"] == [] and a["revealed_error"] == "not a list"
    a = gi.parse_answer('{"action": "say", "text": "Laura"}')
    assert a["revealed_error"] == "missing"
    a = gi.parse_answer('{"action": "say", "text": "Hi", "revealed": [{"id": "drink"}, '
                        '{"id": "size", "value": "large"}]}')
    assert a["revealed"] == [{"id": "size", "value": "large"}]
    assert a["revealed_error"] == "1 malformed entries"
    # Cut by the token limit: the spoken line is recovered, the declaration is lost
    a = gi.parse_answer(
        '{"action": "say", "text": "My name is \\"Laura\\"", "revealed": [{"id": "na')
    assert a["text"] == 'My name is "Laura"' and a["revealed"] == []
    assert a["revealed_error"].startswith("invalid JSON")


def test_parse_raises_when_there_is_no_line():
    with pytest.raises(ValueError):
        gi.parse_answer("I am not JSON")


def _stub_ros():
    """Stand-ins for the ROS modules that harness.py imports, when ROS is not installed."""
    try:
        import rclpy  # noqa: F401
        return
    except ImportError:
        pass

    class Stub(types.ModuleType):

        def __getattr__(self, name):
            return type(name, (), {"__init__": lambda self, *a, **k: None})

    for name in ("rclpy", "rclpy.action", "rclpy.callback_groups", "rclpy.executors",
                 "rclpy.node", "std_msgs", "std_msgs.msg", "ros2_dialog_interfaces",
                 "ros2_dialog_interfaces.action", "ros2_dialog_interfaces.msg",
                 "simple_hri_interfaces", "simple_hri_interfaces.action"):
        sys.modules.setdefault(name, Stub(name))


@pytest.fixture
def llm_user(monkeypatch):
    """LlmUser with a fake LLM: answers[i] is the raw answer of the i-th call."""
    _stub_ros()
    from interaction_sim import llm_user as lu

    def make(answers, **kw):
        calls = []

        def fake_ask_json(provider, model, prompt, schema, **options):
            calls.append({"prompt": prompt, "schema": schema, **options})
            answer = answers[len(calls) - 1]
            if isinstance(answer, Exception):
                raise answer
            return answer, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        monkeypatch.setattr(lu, "ask_json", fake_ask_json)
        user = lu.LlmUser(persona="A calm adult.", goal="You want a medium latte.",
                          model="fake", **kw)
        return user, calls
    return make, lu


# The prompt without goal items, which they must not change (its rules on how to answer as of
# 2026-10-31: say back everything at once, follow the goal in order, always in the language)
LEGACY_PROMPT = ("You play a person talking to a social robot, to test the robot. "
                 "Stay in character." + """

WHO YOU ARE
{persona}

WHAT YOU WANT FROM THIS CONVERSATION
{goal}

HOW TO ANSWER
- Say only what this person would say aloud next: one short spoken line (usually 1 sentence,
  at most 25 words), ALWAYS in {language} (whatever the persona or the robot), with no stage
  directions, quotes or emojis.
- When the robot asks you to say back or sum up what it explained, say back in that one line
  everything your goal tells you to say (up to 45 words), not one point at a time.
- Answer what the robot just said. Give information only when it is asked or natural.
- Do what your goal says, when it says it and in its order (e.g. "first say X, and in your next
  answer change it to Y": the next answer changes it). If your goal is to do something in a
  conversation (ask, propose, negotiate), do it yourself: do not only answer the other side's
  questions.
- A change your goal plans ("change it to Y", "when it corrects you, say it correctly") is for
  good: once it has happened, say only the new version from then on, never the first one again.
- Do not ask again what the robot has already answered, declined or said it does not know,
  unless your goal says so: go on with the rest of your goal, or end when it is done.
- Do not invent facts beyond your goal; if asked something your goal does not cover, answer
  like the persona would (e.g. "I don't mind", "no idea").
- When your goal is reached and the robot has closed the conversation, or the robot says
  goodbye, answer with action "end".

THE CONVERSATION SO FAR
{transcript}

THE ROBOT JUST SAID
"{robot}"

Answer in JSON: {{"action": "say" | "end", "text": "<what you say>"}}""")


def test_without_goal_items_the_prompt_and_the_log_are_as_before(llm_user):
    make, lu = llm_user
    user, calls = make(['{"action": "say", "text": "A latte."}'])
    turn = user("Hello, what would you like?", [{"role": "robot", "text": "Hello"}])
    assert turn.text == "A latte."
    assert calls[0]["prompt"] == LEGACY_PROMPT.format(
        persona="A calm adult.", goal="You want a medium latte.", language="English",
        transcript="(the robot speaks first)", robot="Hello, what would you like?")
    assert calls[0]["schema"] is lu.SCHEMA and calls[0]["max_tokens"] == 120
    assert "revealed" not in user.log[0]


def test_with_goal_items_the_simulated_user_only_speaks(llm_user):
    # Decided by the author, 2026-10-31: nothing to declare (a local annotator marks its lines
    # after the dialogue: annotate_turns.py); its goal items come back as a script to follow,
    # with nothing to declare (provisional, 2026-11-01)
    make, lu = llm_user
    user, calls = make([
        '{"action": "say", "text": "A medium latte.", "revealed": []}',
        '```json\n{"action": "say", "text": "Yes."}\n```',
        '{"action": "say", "text": "Hmm, let me think", "pla',
        RuntimeError("HTTP 500"),
    ], goal_items=ITEMS)
    for robot in ("What would you like?", "A medium latte?", "Anything else?", "Sorry?"):
        user(robot, [{"role": "robot", "text": robot}])
    assert "YOUR SCRIPT" in calls[0]["prompt"] and 'drink: "latte"' in calls[0]["prompt"]
    assert "nothing to declare" in calls[0]["prompt"] and '"revealed"' not in calls[0]["prompt"]
    assert calls[0]["schema"] is lu.SCHEMA and calls[0]["max_tokens"] == 120
    assert all("revealed" not in e and "request" not in e for e in user.log)
    assert [e["text"] for e in user.log] == ["A medium latte.", "Yes.", "Hmm, let me think",
                                             "Sorry, could you repeat that?"]
    assert "error" in user.log[3]


def test_the_turn_limit_is_logged_once(llm_user):
    make, _ = llm_user
    user, _ = make(['{"action": "say", "text": "Hi", "revealed": []}'], goal_items=ITEMS,
                   max_turns=1)
    user("Hello", [])
    assert user("Hello?", []) is None and user("Hello??", []) is None
    assert [e.get("event") for e in user.log] == [None, "turn_limit"]


def test_a_detour_is_said_at_its_turn_and_logged_with_its_kind(llm_user):
    make, lu = llm_user
    user, calls = make(['{"action": "say", "text": "A latte."}'] * 2, events=[
        lu.UserEvent(turn=2, kind="detour", detour="off_task", text="Will it rain tomorrow?")])
    texts = [user(robot, [{"role": "robot", "text": robot}]).text
             for robot in ("What would you like?", "What size?", "What size, then?")]
    assert texts == ["A latte.", "Will it rain tomorrow?", "A latte."]
    assert len(calls) == 2   # the detour is not asked to the simulator
    assert user.log[1] == {"turn": 2, "event": "detour", "detour": "off_task",
                           "text": "Will it rain tomorrow?"}


def test_a_detour_needs_its_text_and_a_known_kind(llm_user):
    _, lu = llm_user
    with pytest.raises(ValueError):
        lu.UserEvent(turn=1, kind="detour", detour="off_task")
    with pytest.raises(ValueError):
        lu.UserEvent(turn=1, kind="detour", detour="rude", text="Hey")
    with pytest.raises(ValueError):
        lu.UserEvent(turn=1, kind="shout", text="Hey")


# ==== THE CHECKER ====

def run(log, items=ITEMS, outcome="completed", expect=None, **kw):
    """A synthetic dialogue record."""
    rec = {"model": "m", "condition": "c", "strategy": "slot_filling", "scenario": "s",
           "persona": "p", "rep": 0, "status": "succeeded", "success": True,
           "outcome": outcome, "failure_reason": "", "data": {}, "total_turns": len(log),
           "duration_s": 1.0, "expect": expect or {"outcome": "success"}, "transcript": [],
           "skills": [], "user_log": log, "goal_items": items}
    rec.update(kw)
    return rec


def say(turn, text, *revealed):
    return {"turn": turn, "action": "say", "text": text,
            "revealed": [{"id": i, "value": v} for i, v in revealed]}


GOOD = [say(1, "A medium LATTE please", ("drink", "Latte"), ("size", " medium ")),
        say(2, "Until 8 am, right?", ("water", "8 am")),
        say(3, "Oh, until 6 am then.", ("water", "6 am"))]


def test_a_faithful_user_is_ok():
    c = adherence.check(run(GOOD))
    assert c["adh_check"] == "ok" and not c["sim_excluded"]
    assert c["adh_off_script"] == c["adh_unrevealed"] == c["adh_review"] == ""


def test_without_goal_items_the_check_is_not_applicable():
    c = adherence.check(run(GOOD, items=[]))
    assert c["adh_check"] == "n/a" and c["sim_excluded"] is False
    assert adherence.check(run([], items=None))["adh_check"] == "n/a"


def test_normalisation_is_light():
    assert adherence.norm_value("  Café  con   Leche. ") == "cafe con leche"
    assert adherence.norm_value("Café", keep_accents=True) == "café"
    log = [say(1, "Un café", ("drink", "café"))]
    items = [{"id": "drink", "value": "cafe"}]
    assert adherence.check(run(log, items=items))["adh_off_script"] == ""
    assert adherence.check(run(log, items=items), keep_accents=True)["adh_off_script"] == \
        "drink=café"
    # Exact after normalisation: no synonyms, no numbers in words
    log = [say(1, "A big one", ("drink", "big latte"))]
    assert adherence.check(run(log, items=[{"id": "drink", "value": "latte"}]))["sim_excluded"]


def test_an_off_script_value_excludes():
    log = GOOD + [say(4, "Actually a large one", ("size", "large"))]
    c = adherence.check(run(log))
    assert c["adh_off_script"] == "size=large"
    assert c["sim_excluded"] and c["sim_excluded_why"] == "off_script" and c["adh_check"] == "fail"


def test_an_id_out_of_the_goal_list_is_off_script():
    c = adherence.check(run(GOOD + [say(4, "Laura", ("name", "Laura"))]))
    assert c["adh_off_script"] == "name=Laura" and c["sim_excluded"]


def test_unrevealed_items_and_planned_changes():
    log = [say(1, "A latte", ("drink", "latte")), say(2, "Until 6 am", ("water", "6 am"))]
    c = adherence.check(run(log))
    assert c["adh_unrevealed"] == "size; water (value)"
    c = adherence.check(run([say(1, "Until 8 am", ("water", "8 am"))]))
    assert c["adh_unrevealed"] == "drink; size; water (then)"


def test_unrevealed_excludes_only_when_the_user_fell_silent_unplanned():
    log = GOOD[1:]  # drink and size never given
    # The system closed (or failed) without them: maybe the system's failure, kept
    for ending in ("completed", "partial", "failed", "breakdown", "turn_limit"):
        assert not adherence.check(run(log, outcome=ending))["sim_excluded"]
    # The simulated user fell silent before giving them: the simulator's failure
    c = adherence.check(run(log, outcome="user_silent"))
    assert c["sim_excluded"] and c["sim_excluded_why"] == "unrevealed"
    # ... unless the scenario planned it (an injected silence or cancel, or another outcome)
    planned = log + [{"turn": 4, "event": "silence"}]
    assert not adherence.check(run(planned, outcome="user_silent"))["sim_excluded"]
    assert not adherence.check(run(log, outcome="user_silent",
                                   expect={"outcome": "failed"}))["sim_excluded"]


def test_the_rules():
    log = GOOD[1:]
    assert not adherence.check(run(log, outcome="user_silent"), rule="off_script")["sim_excluded"]
    assert adherence.check(run(log, outcome="completed"), rule="any_unrevealed")["sim_excluded"]
    cancel = log + [{"turn": 4, "event": "cancel", "text": "Stop"}]
    assert not adherence.check(run(cancel, outcome="cancelled"),
                               rule="any_unrevealed")["sim_excluded"]
    with pytest.raises(ValueError):
        adherence.check(run(log), rule="nope")


def test_a_value_not_in_the_line_only_flags_for_review():
    log = [say(1, "That one, please", ("drink", "latte"), ("size", "medium")),
           say(2, "Eight", ("water", "8 am")), say(3, "6 am", ("water", "6 am"))]
    c = adherence.check(run(log))
    assert c["adh_review"] == "t1:drink; t1:size; t2:water"
    assert c["adh_check"] == "review" and not c["sim_excluded"]


def test_parse_failures_and_simulator_errors_are_counted():
    log = GOOD + [{"turn": 4, "action": "say", "text": "Sorry, could you repeat that?",
                   "error": "RuntimeError: x", "revealed": [], "revealed_error": "no answer"}]
    c = adherence.check(run(log))
    assert c["adh_revealed_errors"] == 1 and c["sim_errors"] == 1 and not c["sim_excluded"]


def test_scenario_files_are_checked(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("scenarios:\n"
                    "  - id: a\n    goal_items:\n      - {id: drink, value: latte}\n"
                    "      - {id: water, value: 8 am, then: 6 am}\n"
                    "  - id: b\n"
                    "  - id: c\n    goal_items:\n      - {id: x, value: 1}\n"
                    "      - {id: x, value: 2, extra: 3}\n", encoding="utf-8")
    lines = adherence.check_scenarios(str(path))
    assert lines[0] == "a: 2 goal items"
    assert lines[1] == "b: no goal_items (adherence n/a)"
    assert lines[2].startswith("c: ERROR") and "repeated ids" in lines[2]
    assert "unknown keys" in lines[2]


# ==== THE SCORING ====

def test_score_runs_adds_the_columns_and_the_summary(tmp_path):
    runs = tmp_path / "runs" / "m" / "c"
    runs.mkdir(parents=True)
    recs = [run(GOOD), run(GOOD + [say(4, "large", ("size", "large"))]), run(GOOD, items=[])]
    for k, rec in enumerate(recs):
        rec["rep"] = k
        (runs / f"s__p__r{k}.json").write_text(json.dumps(rec), encoding="utf-8")
    rows = [score_runs.add_adherence(score_runs.score(r), r) for r in recs]
    assert [r["adh_check"] for r in rows] == ["ok", "fail", "n/a"]
    assert [r["sim_excluded"] for r in rows] == [False, True, False]
    summary = score_runs.adherence_summary(rows)
    assert summary[1].split()[2:5] == ["3", "2", "0.33"]

    out = subprocess.run([sys.executable, os.path.join(HERE, "score_runs.py"), str(tmp_path),
                          "--exclude-sim-failures"], capture_output=True, text=True, check=True)
    assert "Simulated user's adherence (rule: user_silent; excluded" in out.stdout
    with open(tmp_path / "scores.csv", encoding="utf-8") as f:
        table = list(csv.DictReader(f))
    assert len(table) == 3  # the excluded dialogue stays in the CSV
    assert [t["sim_excluded"] for t in table] == ["False", "True", "False"]
    assert table[1]["adh_off_script"] == "size=large"
