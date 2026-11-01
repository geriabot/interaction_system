# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""What each shared mechanism did, read from the record of a dialogue (mechanisms.py), and its
columns in score_runs.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mechanisms  # noqa: E402
import score_runs  # noqa: E402


def skill(name, label, dialogue="d2"):
    return {"dialogue_id": dialogue, "skill": name, "label": label}


def rec(skills, transcript=None, events=(), user_log=(), expect=None, status="succeeded"):
    return {"scenario": "s", "strategy": "slot_filling", "persona": "p", "rep": 1,
            "model": "m", "condition": "bounded", "status": status, "success": True,
            "outcome": "completed", "failure_reason": "", "data": {}, "total_turns": 3,
            "duration_s": 1.0, "expect": expect or {}, "events": list(events),
            "skills": skills, "user_log": list(user_log),
            "transcript": transcript or [{"role": "user", "text": "x", "t": 0.0},
                                         {"role": "robot", "text": "Bye.", "t": 1.0}]}


def test_the_cancellation_labels_are_counted_and_a_withdrawn_one_is_told_apart():
    r = rec([skill("finish", "", "d1"), skill("begin", "slot_filling"),
             skill("cancel_check", "ask"), skill("cancel_check", "none"),   # withdrawn
             skill("cancel_check", "ask"), skill("cancel_check", "denied"),
             skill("cancel_check", "none")])
    c = mechanisms.counts(r)
    assert (c["cancel_asked"], c["cancel_withdrawn"], c["cancel_denied"],
            c["cancel_confirmed"]) == (2, 1, 1, 0)


def test_the_trace_of_the_previous_dialogue_is_left_out():
    r = rec([skill("cancel_check", "confirmed", "d1"), skill("begin", "quiz"),
             skill("ablation.cancel_check_no_confirmation", "confirmed")])
    assert mechanisms.counts(r)["cancel_confirmed"] == 1


def test_the_silent_waits_and_the_events():
    r = rec([skill("begin", "procedure"), skill("silence", "wait"), skill("silence", "wait"),
             skill("silence", "prompt"), skill("silence", "quiet")],
            events=[{"turn": 2, "kind": "silence"}, {"turn": 9, "kind": "text", "text": "x"}],
            user_log=[{"turn": 1, "text": "ok"}, {"turn": 2, "event": "silence"},
                      {"turn": 3, "event": "turn_limit"}])
    c = mechanisms.counts(r)
    assert (c["silence_grace"], c["silence_prompts"], c["silence_quiet"]) == (2, 1, 1)
    assert (c["events_planned"], c["events_applied"]) == (2, 1)


def test_the_closing_line_is_the_robots_last_line_but_fillers_do_not_count():
    assert mechanisms.counts(rec([]))["closing_said"] is True
    ends_on_user = [{"role": "robot", "text": "Hi", "t": 0.0},
                    {"role": "user", "text": "x", "t": 1.0},
                    {"role": "robot", "text": "Hmm...", "t": 2.0, "filler": True}]
    assert mechanisms.counts(rec([], ends_on_user))["closing_said"] is False
    # The requester cancelled the goal: no closing by design
    assert mechanisms.counts(rec([], status="canceled"))["closing_said"] is False


def test_the_planned_mechanisms_are_checked_in_the_scores():
    r = rec([skill("begin", "slot_filling"), skill("cancel_check", "ask"),
             skill("cancel_check", "confirmed")],
            expect={"outcome": "success", "mechanisms": {"cancel_asked": 1, "cancel_denied": 1}})
    row = score_runs.score(r)
    assert row["mechanisms_ok"] is False and row["mechanisms_failed"] == "cancel_denied"
    assert "mechanisms_ok" not in score_runs.score(rec([]))
    lines = score_runs.mechanisms_summary([row])
    assert "cancel_denied (1)" in lines[1] and lines[-1].startswith("Dialogues without")


def test_the_injected_model_failures_and_the_error_closing():
    r = rec([skill("begin", "procedure"), skill("cancel_check", "none"),
             {**skill("procedure.turn", ""), "success": False}],
            expect={"outcome": "failed", "ending": "error",
                    "mechanisms": {"error_closing_said": True, "skill_failures": 1,
                                   "faults_hit": 1}})
    r.update(outcome="error", success=False,
             faults=[{"turn": 2, "kind": "crash"}, {"turn": 9, "kind": "error"}],
             faults_applied=[{"fault": 0, "turn": 2, "question": "classify_step_turn",
                              "kind": "crash"}])
    c = mechanisms.counts(r)
    assert (c["faults_planned"], c["faults_hit"], c["faulted_calls"]) == (2, 1, 1)
    assert c["error_closing_said"] is True and c["skill_failures"] == 1
    row = score_runs.score(r)
    assert row["mechanisms_ok"] is True and row["ending_ok"] is True
    # Records without failures (every other experiment): zeros; another outcome: no error closing
    c = mechanisms.counts(rec([]))
    assert (c["faults_planned"], c["faults_hit"], c["faulted_calls"],
            c["skill_failures"]) == (0, 0, 0, 0)
    assert c["error_closing_said"] is False


def test_the_bounded_wait_for_the_model():
    # The calls cut by the limits, from the trace; the longest wait for a reply, from the
    # transcript (fillers aside, up to the start of the robot's line)
    transcript = [{"role": "robot", "text": "Hi", "t": 0.0},
                  {"role": "user", "text": "a", "t": 2.0},
                  {"role": "robot", "text": "Hmm...", "t": 4.0, "filler": True},
                  {"role": "robot", "text": "Could you repeat that?", "t": 22.1},
                  {"role": "user", "text": "b", "t": 25.0},
                  {"role": "robot", "text": "Bye.", "t": 26.0}]
    r = rec([skill("begin", "slot_filling"),
             {**skill("cancel_check", "none"), "llm_timeouts": 1},
             {**skill("slot_filling.update", "not_understood"), "llm_timeouts": 1,
              "llm_skipped": 1},
             {**skill("slot_filling.next_question", ""), "llm_skipped": 1}], transcript)
    c = mechanisms.counts(r)
    assert (c["llm_timeouts"], c["llm_skipped"]) == (2, 2)
    assert c["max_reply_wait_s"] == 20.1 and c["reply_wait_ok"] is True
    late = transcript[:3] + [{"role": "robot", "text": "Sorry...", "t": 32.0}]
    c = mechanisms.counts(rec([], late))
    assert c["max_reply_wait_s"] == 30.0 and c["reply_wait_ok"] is False
    # Records written before the bound: zeros; no user line answered: no wait
    c = mechanisms.counts(rec([], [{"role": "robot", "text": "Hi", "t": 0.0}]))
    assert (c["llm_timeouts"], c["llm_skipped"], c["max_reply_wait_s"],
            c["reply_wait_ok"]) == (0, 0, None, None)


def test_the_reply_wait_is_not_checked_when_the_experiment_opens_the_wait():
    """E1 opens the wait for the model (model_wait, reply_budget 0: no budget): the longest wait
    is reported, reply_wait_ok is n/a (None), never a failure; a budget set by the experiment
    moves the bound with it."""
    transcript = [{"role": "robot", "text": "Hi", "t": 0.0},
                  {"role": "user", "text": "a", "t": 2.0},
                  {"role": "robot", "text": "Ok.", "t": 47.0}]
    r = rec([], transcript)
    assert mechanisms.counts(r)["reply_wait_ok"] is False
    r["model_wait"] = {"reply_budget": 0.0, "llm_timeout": 90.0}
    c = mechanisms.counts(r)
    assert c["max_reply_wait_s"] == 45.0 and c["reply_wait_ok"] is None
    r["model_wait"] = {"reply_budget": 40.0}
    assert mechanisms.reply_wait_bound(r) == 45.0
    assert mechanisms.counts(r)["reply_wait_ok"] is True
    assert mechanisms.reply_wait_bound({"model_wait": {}}) == mechanisms.REPLY_WAIT_BOUND_S


def test_the_latency_per_model_and_condition_is_summarised():
    """score_runs.latency_summary: per model and condition, the turns, median, p95, worst turn,
    the share over two seconds, fillers per dialogue and the calls cut; descriptive only."""
    rows = [{"model": "m", "condition": "bounded", "_latencies": [1.0, 3.0, 5.0],
             "fillers": 2, "llm_timeouts": 0, "llm_skipped": 0},
            {"model": "m", "condition": "bounded", "_latencies": [1.0],
             "fillers": 0, "llm_timeouts": 1, "llm_skipped": 2},
            {"model": "m", "condition": "llm_led", "_latencies": [],
             "fillers": 0, "llm_timeouts": 0, "llm_skipped": 0}]
    lines = score_runs.latency_summary(rows)
    assert lines[0].split()[:7] == ["model", "condition", "n", "turns", "median", "p95", "worst"]
    bounded = lines[1].split()
    assert bounded[:4] == ["m", "bounded", "2", "4"]
    assert [float(x) for x in bounded[4:8]] == [2.0, round(score_runs.pct([1, 1, 3, 5], .95), 2),
                                                5.0, 0.5]
    assert bounded[8:] == ["1.00", "1", "2"]
    assert lines[2].split()[:7] == ["m", "llm_led", "1", "0", "-", "-", "-"]
