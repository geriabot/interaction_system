# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""What the strategies' own mechanisms did (strategy_checks.py), the questionnaire score, the
frame designed by the LLM and the honoured cancellations in score_runs.py: on hand-made records,
no ROS, no LLM.

    python3 -m pytest analysis/test_strategy_checks.py
"""

import json
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(__file__))
import score_runs  # noqa: E402
import strategy_checks as sc  # noqa: E402

SCENARIOS = os.path.join(os.path.dirname(__file__), "..", "experiments", "scenarios")


def rec(strategy, lines, frames=None, events=(), expect=None, goal=None, data=None,
        skills=(), outcome="completed", status="succeeded", failure_reason=""):
    """A record: lines are (role, text); frames the data of the strategy at each robot line."""
    transcript = [{"role": r, "text": x, "t": float(i)} for i, (r, x) in enumerate(lines)]
    robot = [x for r, x in lines if r == "robot"]
    frames = frames or [{}] * len(robot)
    return {"scenario": "s", "strategy": strategy, "persona": "p", "rep": 1, "model": "m",
            "condition": "bounded", "status": status, "success": status == "succeeded",
            "outcome": outcome, "failure_reason": failure_reason, "data": data or {},
            "total_turns": 3, "duration_s": 1.0, "expect": expect or {},
            "events": list(events), "goal": goal or {}, "skills": list(skills),
            "user_log": [], "transcript": transcript,
            "feedback": [{"utterance": x, "frame": json.dumps(f)} for x, f in zip(robot, frames)]}


def ev(turn, text):
    return {"turn": turn, "kind": "text", "text": text}


# ==== SLOT FILLING ====

TICKETS = {"frame_schema_json": json.dumps({"slots": [
    {"name": "trip", "type": "str"}, {"name": "tickets", "type": "int", "minimum": 1, "maximum": 8}]})}


def test_an_out_of_range_value_is_not_recorded_and_the_range_is_said():
    lines = [("robot", "Which trip?"), ("user", "40 tickets for the sunset trip."),
             ("robot", "Noted. You can buy between one and 8 tickets: how many?"),
             ("user", "4."), ("robot", "Sunset trip, 4 tickets: right?")]
    frames = [{}, {"trip": "sunset trip"}, {"trip": "sunset trip", "tickets": 4}]
    plan = {"out_of_range": {"turn": 1, "slot": "tickets"}}
    r = rec("slot_filling", lines, frames, [ev(1, "40 tickets for the sunset trip.")], plan,
            TICKETS)
    assert sc.columns(r) == {"range_rejected": True, "range_said": True}
    # Recorded anyway, and the range not said
    frames[1] = {"trip": "sunset trip", "tickets": 40}
    lines[2] = ("robot", "Noted. How many tickets?")
    r = rec("slot_filling", lines, frames, [ev(1, "40 tickets for the sunset trip.")], plan,
            TICKETS)
    assert sc.columns(r) == {"range_rejected": False, "range_said": False}
    # The event never happened: not provoked
    r = rec("slot_filling", lines, frames, [ev(5, "40 tickets")], plan, TICKETS)
    assert sc.columns(r) == {"range_rejected": None, "range_said": None}


def test_the_unanswered_slots_are_those_planned():
    plan = {"unanswered": ["delivery"]}
    r = rec("slot_filling", [("robot", "x")], expect=plan, outcome="partial",
            failure_reason="Unanswered slots: delivery")
    assert sc.columns(r) == {"unanswered_ok": True}
    r["failure_reason"] = "Unanswered slots: delivery, address"   # the conditioned slot asked
    assert sc.columns(r) == {"unanswered_ok": False}


def test_a_frame_designed_by_the_llm_is_scored_on_its_values():
    expect = {"frame_values": ["gardens", "French", "Nuria Campos"]}
    good = {"tour_name": "the gardens tour", "guide_language": "french",
            "name": "Nuria Campos", "time": "11:00"}
    assert score_runs.score_slot_filling(good, expect) == {"slot_accuracy": 1.0, "content_ok": True}
    bad = dict(good, guide_language="English")
    out = score_runs.score_slot_filling(bad, expect)
    assert out["content_ok"] is False and abs(out["slot_accuracy"] - 2 / 3) < 1e-9


def test_a_value_is_compared_by_its_letters_and_digits():
    # Case, accents, spaces, punctuation and symbols do not make a value wrong (round 9: the
    # name recorded with a final full stop; decided by the author, 2026-11-02)
    expect = {"frame": {"customer_name": "Andrés Gil", "service": "lavar y secar", "hour": 11}}
    got = {"customer_name": "andres gil.", "service": " Lavar y  secar ", "hour": "11"}
    assert score_runs.score_slot_filling(got, expect) == {"slot_accuracy": 1.0,
                                                          "content_ok": True}
    assert score_runs.norm("#Andrés-Gil!") == score_runs.norm("Andres Gil")
    assert score_runs.norm("Andrés Gil") != score_runs.norm("Andrés Gómez")
    assert score_runs.norm(["Té", "café."]) == score_runs.norm(["cafe", "te"])


# ==== QUESTIONNAIRE ====

def test_the_score_computed_by_code_is_compared():
    expect = {"answers": ["Quite"], "score": 11}
    assert score_runs.score_questionnaire({"answers": [{"answer": "Quite"}], "score": 11.0},
                                          expect)["score_ok"] is True
    assert score_runs.score_questionnaire({"answers": [{"answer": "Quite"}], "score": 10},
                                          expect)["score_ok"] is False
    assert score_runs.score_questionnaire({"answers": [{"answer": "Quite"}]},
                                          expect)["score_ok"] is False
    assert "score_ok" not in score_runs.score_questionnaire({"answers": [{"answer": "Quite"}]},
                                                            {"answers": ["Quite"]})


def _instrument_score(instrument, answers):
    """The rule of the questionnaire skill (questionnaire.py: _value, _score), on labels."""
    def options(raw):
        return [{"label": o, "value": i + 1} if isinstance(o, str) else
                {"label": o["label"], "value": o.get("value", i + 1)} for i, o in enumerate(raw)]
    total = 0
    for item, answer in zip(instrument["items"], answers):
        if answer is None:
            continue
        item = {"text": item} if isinstance(item, str) else item
        opts = options(item.get("options") or instrument["options"])
        value = next(o["value"] for o in opts if o["label"] == answer)
        if item.get("reverse"):
            values = [o["value"] for o in opts]
            value = min(values) + max(values) - value
        total += value
    return total


def test_the_planned_scores_follow_the_rule_of_the_instrument():
    for name in ("dialogues.yaml", "dialogues_es.yaml", "e3.yaml", "e3_es.yaml"):
        with open(os.path.join(SCENARIOS, name), encoding="utf-8") as f:
            scenarios = yaml.safe_load(f)["scenarios"]
        for s in scenarios:
            if "score" not in s.get("expect", {}):
                continue
            content = json.loads(json.loads(s["goal"]["resources_json"])[0]["content"])
            assert _instrument_score(content, s["expect"]["answers"]) == s["expect"]["score"], \
                (name, s["id"])
    # Every questionnaire of E1 that ends with answers has its score
    with open(os.path.join(SCENARIOS, "dialogues.yaml"), encoding="utf-8") as f:
        qn = [s for s in yaml.safe_load(f)["scenarios"] if s["strategy"] == "questionnaire"]
    assert all("score" in s["expect"] for s in qn)


# ==== PROCEDURE ====

def test_a_report_against_the_check_keeps_the_step_open():
    report = "Done, but it didn't click."
    lines = [("robot", "Step 1"), ("user", "Done."), ("robot", "Step 2"), ("user", report),
             ("robot", "Push it harder."), ("user", "Now it clicked."), ("robot", "Step 3")]
    open_ = {"current_step": 2, "results": [{"step": "a", "status": "done"}]}
    frames = [{"current_step": 1}, {"current_step": 2}, open_, {"current_step": 3}]
    plan = {"report_contradicts": {"turn": 2, "step": 2}}
    r = rec("procedure", lines, frames, [ev(1, "Done."), ev(2, report)], plan)
    assert sc.columns(r) == {"contradiction_not_done": True}
    frames[2] = {"current_step": 3, "results": [{"status": "done"}, {"status": "done"}]}
    r = rec("procedure", lines, frames, [ev(1, "Done."), ev(2, report)], plan)
    assert sc.columns(r) == {"contradiction_not_done": False}


# ==== ROLEPLAY ====

SCENE = {"resources_json": json.dumps([{"name": "scene", "content": json.dumps(
    {"scenario": "A hotel", "role": "Receptionist",
     "criteria": ["Gives the name of the booking", "Thanks the receptionist"]})}])}


def test_a_repeat_is_literal_then_in_other_words_and_the_intro_hides_the_rubric():
    lines = [("robot", "You are at a hotel. Good evening!"), ("user", "Hi, a room for two nights."),
             ("robot", "Under what name?"), ("user", "Pardon?"), ("robot", "Under what name?"),
             ("user", "Another way?"), ("robot", "Whose name is the booking under?")]
    plan = {"repeat": {"turns": [2, 3]}}
    events = [ev(1, "Hi, a room for two nights."), ev(2, "Pardon?"), ev(3, "Another way?")]
    r = rec("roleplay", lines, events=events, expect=plan, goal=SCENE,
            data={"exchanges": 3, "ended_by": "limit"})
    assert sc.columns(r) == {"rp_exchanges": 3, "rp_ended_by": "limit", "rubric_in_intro": 0,
                             "rubric_in_intro_words": 0, "repeat_literal": True,
                             "repeat_rephrased": True}
    lines[4] = ("robot", "I said: under what name?")
    lines[6] = ("robot", "Under what name?")
    lines[0] = ("robot", "Practise: you will be scored on whether it gives the name of the "
                         "booking.")
    r = rec("roleplay", lines, events=events, expect=plan, goal=SCENE)
    out = sc.columns(r)
    assert (out["repeat_literal"], out["repeat_rephrased"]) == (False, False)
    assert out["rubric_in_intro"] == 1 and out["rubric_in_intro_words"] == 1


# ==== EXPLANATION ====

POINTS = ["The shuttle leaves from the side entrance of the hotel", "It runs every thirty minutes",
          "Show your room key to the driver"]


def kp(*statuses, completed=False):
    return {"completed": completed, "key_points": [
        {"point": p, "critical": True, "status": s} for p, s in zip(POINTS, statuses)]}


def test_teach_back_with_a_planted_wrong_point():
    said = "From the side entrance, once an hour, and I show my room key."
    repair = "Yes, the side entrance is right. It comes every thirty minutes, not every hour."
    lines = [("robot", "Explanation. Say it back."), ("user", said),
             ("robot", repair + " Say it again."), ("user", "All of it right."),
             ("robot", "Well done.")]
    frames = [kp("pending", "pending", "pending"), kp("correct", "wrong", "correct"),
              kp("correct", "correct", "correct", completed=True)]
    plan = {"teach_back": {"turn": 1, "wrong": [2]}}
    r = rec("explanation", lines, frames, [ev(1, said)], plan, data=frames[-1])
    out = sc.columns(r)
    assert out["key_points"] == 3 and out["key_points_from_llm"] is True  # no resource given
    assert (out["tb_marked_wrong"], out["tb_others_correct"], out["tb_repair_only_wrong"],
            out["tb_repair_review"], out["tb_closed_consistent"]) == (True, True, True, 0, True)
    # The repair repeats a point said right, and the dialogue closed with a point still wrong
    lines[2] = ("robot", "The shuttle leaves from the side entrance of the hotel, every thirty "
                         "minutes. Say it again.")
    frames[2] = kp("correct", "wrong", "correct", completed=True)
    r = rec("explanation", lines, frames, [ev(1, said)], plan, data=frames[-1])
    out = sc.columns(r)
    assert (out["tb_repair_only_wrong"], out["tb_closed_consistent"]) == (False, False)
    # The planted point not marked wrong (taken as right)
    frames[1] = kp("correct", "correct", "correct")
    r = rec("explanation", lines, frames, [ev(1, said)], plan, data=frames[-1])
    assert sc.columns(r)["tb_marked_wrong"] is False


def test_key_points_given_in_the_request_are_not_from_the_llm():
    goal = {"resources_json": json.dumps([{"name": "key points",
                                           "content": json.dumps({"key_points": POINTS})}])}
    r = rec("explanation", [("robot", "x")], goal=goal, data=kp("correct", "correct", "correct"))
    assert sc.columns(r) == {"key_points": 3, "key_points_from_llm": False}


def test_the_new_columns_reach_mechanisms_ok():
    lines = [("robot", "Which trip?"), ("user", "40"), ("robot", "Between 1 and 8, please.")]
    r = rec("slot_filling", lines, [{}, {}], [ev(1, "40")],
            {"outcome": "success", "out_of_range": {"turn": 1, "slot": "tickets"},
             "mechanisms": {"range_rejected": True}}, TICKETS)
    row = score_runs.score(r)
    assert row["range_rejected"] is True and row["mechanisms_ok"] is True


# ==== HONOURED CANCELLATIONS (I-13) ====

def skill(name, label):
    return {"dialogue_id": "d", "skill": name, "label": label}


def cancelled(skills, lines=None, **kw):
    lines = lines or [("robot", "Your drink?"), ("user", "I'd rather stop this."),
                      ("robot", "Do you want to stop?"), ("user", "Yes."),
                      ("robot", "All right, goodbye.")]
    return rec("slot_filling", lines, skills=skills, outcome="cancelled", status="aborted",
               expect={"outcome": "success", "frame": {"drink": "tea"}}, **kw)


def test_a_cancellation_the_user_asked_for_and_confirmed_is_right():
    r = cancelled([skill("begin", "slot_filling"), skill("cancel_check", "ask"),
                   skill("cancel_check", "confirmed")])
    row = score_runs.score(r)
    assert row["honoured_cancel"] is True
    assert row["outcome_ok"] is True and row["content_ok"] is True and row["task_success"] is True


def test_a_cancellation_without_confirmation_or_from_an_event_is_not_honoured():
    # no_cancel_confirmation: confirmed without asking
    r = cancelled([skill("begin", "slot_filling"),
                   skill("ablation.cancel_check_no_confirmation", "confirmed")])
    assert score_runs.score(r)["honoured_cancel"] is False
    assert score_runs.score(r)["outcome_ok"] is False
    # The request was a line of the scenario (a planned text event), not the user's own
    r = cancelled([skill("cancel_check", "ask"), skill("cancel_check", "confirmed")],
                  events=[ev(1, "I'd rather stop this.")])
    assert score_runs.score(r)["honoured_cancel"] is False


def test_a_planned_or_requester_cancellation_is_not_counted():
    r = cancelled([skill("cancel_check", "ask"), skill("cancel_check", "confirmed")])
    r["expect"] = {"outcome": "cancelled"}
    assert score_runs.score(r)["honoured_cancel"] is None
    r = cancelled([skill("cancel_check", "ask"), skill("cancel_check", "confirmed")])
    r["status"] = "canceled"   # the requester cancelled the action
    assert score_runs.score(r)["honoured_cancel"] is None
    done = rec("slot_filling", [("robot", "x")], expect={"outcome": "success"})
    assert score_runs.score(done)["honoured_cancel"] is None


def test_the_llm_led_cancellation_needs_its_confirmation_question():
    mono = [skill("monolithic.open", ""), skill("monolithic.turn", "continue"),
            skill("monolithic.turn", "done")]
    assert score_runs.score(cancelled(mono))["honoured_cancel"] is True
    lines = [("robot", "Your drink?"), ("user", "I'd rather stop this."),
             ("robot", "Fine."), ("user", "Yes."), ("robot", "Goodbye.")]
    assert score_runs.score(cancelled(mono, lines))["honoured_cancel"] is False


def test_the_cancel_summary_reports_them_apart():
    rows = [score_runs.score(cancelled([skill("cancel_check", "ask"),
                                        skill("cancel_check", "confirmed")])),
            score_runs.score(rec("slot_filling", [("robot", "x")],
                                 expect={"outcome": "success"}))]
    lines = score_runs.cancel_summary(rows)
    assert lines[1].split()[2:5] == ["2", "1", "1"]
    assert lines[1].split()[5:] == ["1.00", "0.50"]
    assert lines[-1].startswith("Honoured cancellations to read: bounded/s/p/1")
