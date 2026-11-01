# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
What each shared mechanism did in a dialogue, read from its record (score_runs.py adds these
columns to every dialogue): the labels the skills returned (`skills`: /interaction/skill_trace),
the events the simulated user applied (`user_log`) and the transcript. No LLM, no keywords.

  cancel_asked, cancel_confirmed, cancel_denied, cancel_withdrawn
               the spoken cancellation: confirmation questions asked; cancellations confirmed
               (with the no_cancel_confirmation ablation, without a question); denied (the robot
               resumes); withdrawn (runs before 2026-11-01: the answer to the confirmation
               went on with the task and the strategy handled it; since then only a yes stops)
  silence_prompts, silence_quiet, silence_grace
               the silent waits: with something said (waiting, the question repeated, "whenever
               you're ready"); quiet; inside the declared duration of the element (no count)
  events_planned, events_applied
               the scenario's events, and those the simulated user applied (an event planned after
               the end of the dialogue never happens: the mechanism was not provoked; records
               written before 2026-10-30 do not keep the events: 0)
  closing_said the dialogue ended with a line of the robot after the user's last line (the closing
               line), unless the requester cancelled it (no closing then, by design)
  error_closing_said
               the closing line of an error: closing_said and the outcome is `error` (the trees say
               the error closing whenever a conversation that started ends in error)
  skill_failures
               skills of the intelligence that failed (success false in the trace: a skill that
               raised; a skill that did not answer in time is not traced as failed)
  faults_planned, faults_hit, faulted_calls
               the model failures the scenario plans (`faults`, E3), those that made at least one
               call of the model fail (the failure was provoked), and the calls that failed
               (`faults_applied`, written by the injector: interaction_sim/llm_faults.py)
  llm_timeouts, llm_skipped
               the calls to the model that timed out, and those not made (the model had not
               answered an earlier call of the same skill, or the time budget of the reply was
               spent): the bounded wait for the model at work (the skill trace; records written
               before 2026-10-30 do not have them: 0). In normal operation both are 0: a count
               above 0 in a condition without injected failures means its model is slower than
               the budget of a reply
  max_reply_wait_s, reply_wait_ok
               the longest time from a line of the user to the next line of the robot (fillers
               aside; the transcript's times, up to the start of the robot's line), and whether
               it is within the bound of the method: REPLY_WAIT_BOUND_S with its default
               parameters, or the reply budget the experiment set (`model_wait` of the record)
               plus the same margin (None when no user line was answered, or when the
               experiment opened the wait with no reply budget, as E1 does: there is no bound
               to check, and the wait is reported only, in max_reply_wait_s and the latency)

When the scenario lists what must happen (expect.mechanisms: {column: value}), mechanisms_ok says
whether every listed column has that value, and mechanisms_failed lists those that do not. The
LLM-led condition traces none of these skills: its counts are those of the shared parts only.
"""

CANCEL_LABELS = {"ask": "cancel_asked", "confirmed": "cancel_confirmed",
                 "denied": "cancel_denied"}
SILENCE_LABELS = {"prompt": "silence_prompts", "quiet": "silence_quiet",
                  "wait": "silence_grace"}
COLUMNS = (list(CANCEL_LABELS.values()) + ["cancel_withdrawn"] + list(SILENCE_LABELS.values())
           + ["events_planned", "events_applied", "closing_said"]
           + ["error_closing_said", "skill_failures", "faults_planned", "faults_hit",
              "faulted_calls", "llm_timeouts", "llm_skipped", "max_reply_wait_s",
              "reply_wait_ok"])
# The longest wait for a reply the method allows with its default parameters: the time budget of
# a reply (reply_budget, 20 s) and a margin for the rest of the turn (the skills' own work, the
# tree, a filler being finished); the hard limit, intelligence_timeout (30 s), ends the
# conversation with the error closing instead
REPLY_WAIT_BOUND_S = 25.0
DEFAULT_REPLY_BUDGET_S = 20.0


def reply_wait_bound(rec: dict) -> float | None:
    """The longest wait for a reply the method allows in this dialogue: REPLY_WAIT_BOUND_S with
    the default parameters; with the reply budget the experiment set (record's `model_wait`), that
    budget plus the same margin; None when it set no budget (0: the bound is off)."""
    budget = (rec.get("model_wait") or {}).get("reply_budget", DEFAULT_REPLY_BUDGET_S)
    if budget is None or budget <= 0:
        return None
    return REPLY_WAIT_BOUND_S - DEFAULT_REPLY_BUDGET_S + budget


def reply_waits(transcript: list[dict]) -> list[float]:
    """For each line of the user answered by the robot, the seconds until the robot's line."""
    lines = [t for t in transcript or [] if not t.get("filler") and "t" in t]
    return [b["t"] - a["t"] for a, b in zip(lines, lines[1:])
            if a.get("role") == "user" and b.get("role") == "robot"]


def dialogue_skills(skills: list[dict]) -> list[dict]:
    """The trace of this dialogue only: from its `begin` on (the trace may start with the end of
    the previous dialogue)."""
    begins = [i for i, s in enumerate(skills) if s.get("skill") == "begin"]
    if not begins:
        return skills
    dialogue = skills[begins[-1]].get("dialogue_id")
    return [s for s in skills[begins[-1]:] if s.get("dialogue_id", dialogue) == dialogue]


def _is_cancel(skill: str) -> bool:
    # The method's cancel_check and the ablations' (ablation.cancel_check_...)
    return skill == "cancel_check" or skill.startswith("ablation.cancel_check")


def counts(rec: dict) -> dict:
    row = {c: 0 for c in COLUMNS}
    awaiting = False
    for s in dialogue_skills(rec.get("skills") or []):
        skill, label = s.get("skill", ""), s.get("label", "")
        if _is_cancel(skill):
            if awaiting and label == "none":
                row["cancel_withdrawn"] += 1
            if label in CANCEL_LABELS:
                row[CANCEL_LABELS[label]] += 1
            awaiting = label == "ask"
        elif skill == "silence" and label in SILENCE_LABELS:
            row[SILENCE_LABELS[label]] += 1
        if s.get("success") is False:
            row["skill_failures"] += 1
        row["llm_timeouts"] += int(s.get("llm_timeouts") or 0)
        row["llm_skipped"] += int(s.get("llm_skipped") or 0)
    row["events_planned"] = len(rec.get("events") or [])
    row["events_applied"] = sum(1 for e in rec.get("user_log") or [] if e.get("event")
                                and e["event"] != "turn_limit")
    lines = [t for t in rec.get("transcript") or [] if not t.get("filler")]
    row["closing_said"] = bool(lines) and lines[-1].get("role") == "robot" and \
        rec.get("status") != "canceled"
    row["error_closing_said"] = row["closing_said"] and rec.get("outcome") == "error"
    applied = rec.get("faults_applied") or []
    row["faults_planned"] = len(rec.get("faults") or [])
    row["faults_hit"] = len({a.get("fault") for a in applied if a.get("fault") is not None})
    row["faulted_calls"] = len(applied)
    waits = reply_waits(rec.get("transcript"))
    row["max_reply_wait_s"] = round(max(waits), 1) if waits else None
    bound = reply_wait_bound(rec)
    row["reply_wait_ok"] = (row["max_reply_wait_s"] <= bound) if waits and bound else None
    return row


def check(row: dict, expect: dict) -> dict:
    """mechanisms_ok and mechanisms_failed, when the scenario lists what must happen."""
    planned = (expect or {}).get("mechanisms")
    if not planned:
        return {}
    failed = sorted(k for k, v in planned.items() if row.get(k) != v)
    return {"mechanisms_ok": not failed, "mechanisms_failed": ";".join(failed)}
