# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Score the dialogues of an experiment against the ground truth of their scenarios.

    python3 score_runs.py <results dir of the experiment> [--csv scores.csv]
    python3 score_runs.py ~/.ros/interaction_real/<campaign>   # the sessions with real participants (one campaign)

Per dialogue:
  outcome_ok   the dialogue ended as expected (success / cancelled / failed). The system's
               outcome (DialogTask result) "completed" and "partial" are both a normal end
               ("success" here): whether every element was done is checked by content_ok
               against the plan, the same way for every condition (the LLM-led one only
               reports success or failure). The outcome itself is kept as "ending". A
               cancellation the scenario did not plan, asked for by the simulated user and done by
               the robot after confirming, is right too (honoured_cancel: see its rule below;
               reported apart per condition)
  honoured_cancel None when the scenario plans the cancellation or the dialogue was not
               cancelled by what the user said; else whether the user asked and the robot
               confirmed (honoured_cancel())
  content_ok   what it produced is right: the final frame (slot filling), the grading of each
               answer (quiz), the outcome of each step (procedure), a score for every criterion
               and, when the scenario plans one, the lowest score for the criterion the simulated
               user fails on purpose (roleplay), the understanding verified by teach-back
               (explanation)
  rubric_contrast roleplay: the planned weak criterion has a lower score than every other one
               (reported apart, not in content_ok: author, 2026-11-01)
               (not for real participants, whose performance is not planned)
  qa_agreement grounded_qa: the resolution of each planned question (answered, abstained,
               declined) as planned; whether the answers are faithful is the judge's. With the
               simulator's marks (request and the question's goal item per turn), each planned
               question is scored by the first time it was asked, and reported apart (not in
               content_ok): qa_repeated, qa_false_questions (a turn that asked nothing recorded
               as a question; qa_false_with_info: answered with information), qa_never_asked (a
               simulator failure), qa_unlinked (entries matched to no turn); without the marks,
               position by position. When the
               scenario lists the key facts of its answered questions (expect.qa_facts, parallel
               to qa_resolutions: a list of facts, or null), each answer must state them
               (key_facts.py; content_ok needs them; qa_facts_missing, qa_facts_review)
  answer_agreement, items_literal questionnaire: the option recorded for each item as planned
               (none for a declined item), and the share of items read word for word (each item
               of the instrument must appear in the robot's utterances; content_ok needs both)
  score_ok     questionnaire, when the scenario gives expect.score: the score the code computed
               (data.score) is that one (not in content_ok)
  slot filling without a schema (expect.frame_values): each planned value is the value of some
               slot of the frame the LLM designed (slot_accuracy, content_ok)
  task_success outcome_ok and content_ok
  ending_ok    when the scenario plans an ending that is not the strategy's own (expect.ending:
               breakdown, partial; the scenarios of E4), the system's outcome is that one
  slot_accuracy, grading_agreement, step_agreement: the partial figures
  turn_latency_s the system's time per turn: from the user's line to the robot's first reply that
               is not a conversational filler (a robot line with `filler: true`, said when the
               reply takes long); fillers are left out of the task scoring and counted
               (fillers)
  cancel_*, silence_*, events_*, closing_said, error_closing_said, skill_failures, faults_*,
  faulted_calls, mechanisms_ok what each shared mechanism did (mechanisms.py: from the skill
               trace, the simulated user's log, the transcript and the injected model failures),
               and, when the scenario lists it (expect.mechanisms), whether it is what was planned
  range_*, unanswered_ok, contradiction_not_done, repeat_*, rp_*, rubric_in_intro*, key_points*,
  tb_*         what the strategies' own mechanisms did (strategy_checks.py: the robot's reply to
               the planted moment and what it had recorded then), listed in expect.mechanisms
               like the shared ones
  adh_*, sim_excluded the simulated user's adherence to its goal items (adherence.py): adh_check
               (ok, review, fail or n/a), adh_off_script, adh_robot_induced (an off-script value the robot
               had said before: review, never excluded), adh_unrevealed, adh_review, and whether
               the dialogue is a simulator failure by the exclusion rule (--adherence-rule)
Then the summary per model, condition and strategy (success rate, turns, latency p50/p95), the
latency per model and condition (latency_summary: turns, median, p95, worst turn, the share of
turns over SLOW_TURN_S, the fillers and the calls cut by the wait for the model; reported and
compared descriptively, never a pass/fail criterion: in E1, whose experiment opens the wait for
the model, it tells what each model and condition would be fit for in a conversation with a
person), the mechanisms, the cancellations the scenarios did not plan (cancel_summary), and the adherence per model and condition: the excluded rate and the success rate with and without
the excluded dialogues. --exclude-sim-failures leaves them out of the first summary too; the
CSV always keeps every dialogue.
"""

import argparse
import csv
import glob
import json
import os
import statistics
import unicodedata
from collections import defaultdict

import adherence
from annotate_turns import load_record
import mechanisms
import strategy_checks
from key_facts import fact_status
from percentiles import percentile


# Leading articles ignored in the comparison (English and Spanish scenarios)
ARTICLES = ("the ", "a ", "an ", "el ", "la ", "los ", "las ", "un ", "una ")


def norm(value) -> str:
    """The value as compared: its letters and digits only, in one string (lower case, without
    accents, spaces, punctuation or symbols: a dropped accent or a final full stop is not a wrong
    value, "Andrés Gil." is "Andrés Gil"), without a leading article; a number as a number. For
    the general case of social robotics, accepting that it may fail in a rare value whose
    symbols matter (decided by the author, 2026-11-02)."""
    if isinstance(value, (list, tuple)):
        return "|".join(sorted(norm(v) for v in value))
    text = unicodedata.normalize("NFKD", str(value).strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for article in ARTICLES:
        if text.startswith(article):
            text = text[len(article):]
    try:
        return str(float(text))
    except ValueError:
        return "".join(c for c in text if c.isalnum())


def _slot_values(data: dict) -> list[str]:
    """Every value of a frame, normalised (the items of a list one by one)."""
    out = []
    for v in data.values():
        out += [norm(x) for x in v] if isinstance(v, (list, tuple)) else [norm(v)]
    return [v for v in out if v]


def score_slot_filling(data: dict, expect: dict) -> dict:
    frame = expect.get("frame", {})
    values = expect.get("frame_values")
    if not frame and values:
        # A frame designed by the LLM (no schema): its slot names are not known, so each planned
        # value must be the value of some slot (one contains the other, at least 3 letters)
        got = _slot_values(data)
        found = sum(1 for v in values if any(
            min(len(norm(v)), len(g)) >= 3 and (norm(v) in g or g in norm(v)) for g in got))
        return {"slot_accuracy": found / len(values), "content_ok": found == len(values)}
    if not frame:
        return {}
    right = sum(1 for k, v in frame.items() if norm(data.get(k, "")) == norm(v))
    # A slot the user does not give (expect.unanswered) must be left empty, not invented
    extra = [k for k in data if k not in frame]
    return {"slot_accuracy": right / len(frame),
            "content_ok": right == len(frame) and not extra}


def score_quiz(data: dict, expect: dict) -> dict:
    planned = expect.get("quiz_correct")
    if planned is None:
        return {}
    got = [bool(r.get("correct")) for r in data.get("results", [])]
    agree = sum(1 for a, b in zip(planned, got) if a == b)
    return {"grading_agreement": agree / len(planned),
            "content_ok": len(got) == len(planned) and agree == len(planned)}


def score_procedure(data: dict, expect: dict) -> dict:
    planned = expect.get("step_status")
    if planned is None:
        return {}
    got = [r.get("status") for r in data.get("results", [])]
    agree = sum(1 for a, b in zip(planned, got) if a == b)
    return {"step_agreement": agree / len(planned),
            "content_ok": len(got) == len(planned) and agree == len(planned)}


def score_roleplay(data: dict, expect: dict) -> dict:
    """Every criterion of the rubric is scored (content_ok: the practice was completed; what
    the scores mean is for the behavior that requested it, decided by the author, 2026-11-01).
    When the scenario plans a weak criterion (the simulated user fails it on purpose),
    rubric_contrast says whether its score is lower than every other one: reported apart, not
    in content_ok (it depends on the model that scores and on the simulated user)."""
    criteria = expect.get("criteria")
    if criteria is None:
        return {}
    scores = {norm(s.get("criterion", "")): s["score"] for s in data.get("scores", [])
              if isinstance(s.get("score"), (int, float))}
    complete = all(norm(c) in scores for c in criteria)
    weak = expect.get("weak_criterion")
    if not weak:
        return {"content_ok": complete}
    others = [scores[norm(c)] for c in criteria if norm(c) != norm(weak) and norm(c) in scores]
    contrast = (complete and norm(weak) in scores and bool(others)
                and all(scores[norm(weak)] < o for o in others))
    return {"rubric_contrast": contrast, "content_ok": complete}


def score_explanation(data: dict, expect: dict) -> dict:
    if "understood" not in expect:
        return {}
    understood = bool(data.get("completed", data.get("understood", False)))
    return {"content_ok": understood == expect["understood"]}


def _qa_turns(rec: dict | None, qids: list[str]) -> list[dict] | None:
    """The simulated user's turns as {text, request, asks: the planned question ids it asks}, or
    None when the simulator did not mark them (a scenario without goal items, older runs)."""
    log = (rec or {}).get("user_log") or []
    if not qids or not any("request" in e for e in log):
        return None
    return [{"text": e.get("text", ""), "request": e.get("request"),
             "asks": [g.get("id") for g in e.get("revealed") or [] if g.get("id") in qids]}
            for e in log]


def _link(question: str, turns: list[dict], used: set) -> int | None:
    """The user turn an entry of the robot's list records: the same words (the bounded system
    records the user's line), else the turn sharing most words, at least half (the LLM-led
    condition writes its own list); None if none."""
    words = set(_words(question).split())
    best, share = None, 0.5
    for i, t in enumerate(turns):
        if i in used:
            continue
        if _words(t["text"]) == _words(question):
            return i
        other = set(_words(t["text"]).split())
        if words and other:
            s = len(words & other) / len(words | other)
            if s >= share:
                best, share = i, s
    return best


def score_grounded_qa(data: dict, expect: dict, rec: dict | None = None) -> dict:
    """Each planned question scored by what the robot did the FIRST time the simulated user
    asked it (its turn marked request "yes" and the question's goal item, q1, q2, ... in the
    planned order); the robot's entries linked to their turns. Apart (not in content_ok): the
    questions asked again, the FALSE QUESTIONS (an entry whose turn asked nothing: thanks, a
    comment, a goodbye, recorded as a question; with information or not) and the planned
    questions never asked (a simulator failure). Without the simulator's marks, the entries are
    compared position by position, as before (decided by the author, 2026-10-31)."""
    planned = expect.get("qa_resolutions")
    if planned is None:
        return {}
    questions = [q for q in data.get("questions", []) if isinstance(q, dict)]
    qids = [g["id"] for g in (rec or {}).get("goal_items") or []
            if isinstance(g, dict) and str(g.get("id", "")).startswith("q")
            and str(g["id"])[1:].isdigit()]
    turns = _qa_turns(rec, qids) if len(qids) == len(planned) else None
    out = {}
    if turns is None:
        got = [q.get("resolution") for q in questions]
        agree = sum(1 for a, b in zip(planned, got) if a == b)
        out["content_ok"] = len(got) == len(planned) and agree == len(planned)
        picked = questions[:len(planned)]
    else:
        linked, used = {}, set()
        for k, q in enumerate(questions):
            i = _link(q.get("question") or "", turns, used)
            if i is not None:
                used.add(i)
                linked[i] = k
        picked, never, repeated = [], 0, 0
        for qid in qids:
            asks = [i for i, t in enumerate(turns) if t["request"] == "yes" and qid in t["asks"]]
            if not asks:
                never += 1
                picked.append(None)
                continue
            repeated += len(asks) - 1
            k = linked.get(asks[0])
            picked.append(questions[k] if k is not None else {"resolution": None})
        false = [questions[k] for i, k in linked.items() if turns[i]["request"] == "no"]
        got = [q.get("resolution") if q else None for q in picked]
        agree = sum(1 for a, b in zip(planned, got) if a == b)
        asked = [(a, b) for a, b, q in zip(planned, got, picked) if q is not None]
        out.update({
            "content_ok": all(a == b for a, b in asked),
            "qa_never_asked": never, "qa_repeated": repeated,
            "qa_false_questions": len(false),
            "qa_false_with_info": sum(1 for q in false if q.get("resolution") == "answered"),
            "qa_unlinked": len(questions) - len(linked)})
    out["qa_agreement"] = agree / len(planned)
    facts = expect.get("qa_facts")
    if facts:
        missing = review = 0
        for i, wanted in enumerate(facts):
            q = picked[i] if i < len(picked) else None
            if not wanted or not q or q.get("resolution") != "answered":
                continue  # nothing to check, or the resolution is already wrong
            status = [fact_status(f, q.get("answer") or "") for f in wanted]
            missing += status.count("missing")
            review += status.count("unsure")
        out.update({"qa_facts_missing": missing, "qa_facts_review": review})
        out["content_ok"] = out["content_ok"] and missing == 0
    return out


def _instrument_items(rec: dict | None) -> list[str]:
    """The items of the questionnaire of the request (the goal recorded with the run)."""
    try:
        resources = json.loads((rec or {}).get("goal", {}).get("resources_json") or "[]")
    except ValueError:
        return []
    for r in resources:
        try:
            content = json.loads(r.get("content", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(content, dict) and isinstance(content.get("items"), list):
            return [i if isinstance(i, str) else i.get("text", "") for i in content["items"]]
    return []


def _words(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def score_questionnaire(data: dict, expect: dict, rec: dict | None = None) -> dict:
    planned = expect.get("answers")
    if planned is None:
        return {}
    got = [a.get("answer") for a in data.get("answers", [])]
    agree = sum(1 for a, b in zip(planned, got)
                if (a is None and b is None) or (a is not None and b is not None and norm(a) == norm(b)))
    out = {"answer_agreement": agree / len(planned)}
    items = _instrument_items(rec)
    said = _words(" ".join(t["text"] for t in (rec or {}).get("transcript", [])
                           if t["role"] == "robot" and not t.get("filler")))
    literal = sum(1 for i in items if _words(i) in said) / len(items) if items else None
    out["items_literal"] = literal
    out["content_ok"] = (len(got) == len(planned) and agree == len(planned)
                         and (literal is None or literal == 1.0))
    if "score" in expect:
        # The score computed by code from the answers (reversed items, the values of the
        # options); reported apart, not in content_ok: the LLM-led condition computes none
        try:
            out["score_ok"] = abs(float(data.get("score")) - float(expect["score"])) < 1e-6
        except (TypeError, ValueError):
            out["score_ok"] = False
    return out


SCORERS = {
    "slot_filling": score_slot_filling, "quiz": score_quiz, "procedure": score_procedure,
    "roleplay": score_roleplay, "explanation": score_explanation,
    "grounded_qa": score_grounded_qa, "questionnaire": score_questionnaire,
}
# The scorers that also read the record (the transcript, the goal)
WITH_RECORD = {"grounded_qa", "questionnaire"}


def turn_latencies(transcript: list[dict]) -> list[float]:
    """The system's time per turn: from each user line to the robot's first reply after it that
    is not a filler (a fixed line said while the reply takes long)."""
    out, asked = [], None
    for turn in transcript:
        if turn["role"] == "user":
            asked = turn["t"]  # from the user's latest line, as before the fillers
        elif turn["role"] == "robot" and not turn.get("filler") and asked is not None:
            out.append(turn["t"] - asked)
            asked = None
    return out


def fillers(transcript: list[dict]) -> int:
    """The conversational fillers the robot said in the dialogue."""
    return sum(1 for t in transcript if t["role"] == "robot" and t.get("filler"))


def run_files(results: str) -> list[str]:
    """The records of the dialogues: runs/<model>/<condition>/*.json of an experiment, or
    <participant>/trial_*.json of the sessions with real participants (real/real_sessions.py)."""
    return sorted(glob.glob(os.path.join(results, "runs", "*", "*", "*.json"))
                  or glob.glob(os.path.join(results, "P*", "trial_*.json")))


def honoured_cancel(rec: dict) -> bool | None:
    """Whether the dialogue ended because the simulated user asked to stop and the robot did it
    after confirming (I-13 of the coverage audit, decided 2026-10-30): a right behaviour, not a
    failure, although the scenario plans success (E1: the `uncooperative` persona "says they want
    to stop"). None when it does not apply: the scenario plans the cancellation (expect.outcome
    cancelled, or a cancel event), or the dialogue did not end as cancelled by what the user said
    (an ending other than `cancelled`, or the requester cancelled the action: status canceled).
    Otherwise True when:
      - the two last user lines were the simulated user's own (no line of an event): the request
        and the answer to the confirmation;
      - the robot confirmed before cancelling: with the method's cancel check (and the ablations
        that keep the confirmation) the last two cancel labels of the skill trace are "ask" then
        "confirmed"; in the LLM-led condition, which traces no cancel check, its last turn
        closed the dialogue ("monolithic.turn" done) right after a robot line that is a question
        (the confirmation its rules ask for). A cancellation without a confirmation
        (no_cancel_confirmation) is never honoured.
    Neither the trace nor the question is evidence independent of the system that the user
    wanted to stop: the summary lists these dialogues for a person to read."""
    expect = rec.get("expect") or {}
    events = rec.get("events") or []
    if expect.get("outcome") == "cancelled" or any(e.get("kind") == "cancel" for e in events):
        return None
    if rec.get("outcome") != "cancelled" or rec.get("status") == "canceled":
        return None
    lines = [t for t in rec.get("transcript") or [] if not t.get("filler")]
    users = [i for i, t in enumerate(lines) if t["role"] == "user"]
    planned = {(e.get("text") or "").strip() for e in events if e.get("text")}
    if len(users) < 2 or any(lines[i]["text"].strip() in planned for i in users[-2:]):
        return False
    skills = mechanisms.dialogue_skills(rec.get("skills") or [])
    cancel = [s.get("label") for s in skills if mechanisms._is_cancel(s.get("skill", ""))]
    if cancel:
        return cancel[-2:] == ["ask", "confirmed"]
    mono = [s.get("label") for s in skills if s.get("skill") == "monolithic.turn"]
    if mono:
        before = [t for t in lines[:users[-1]] if t["role"] == "robot"]
        return mono[-1] == "done" and bool(before) and before[-1]["text"].rstrip().endswith("?")
    return False


def score(rec: dict) -> dict:
    expect = rec.get("expect", {})
    expected_outcome = expect.get("outcome", "success")
    # Runs recorded before the outcome existed only have success
    ending = rec.get("outcome") or ("completed" if rec["success"] else "")
    if ending in ("completed", "partial"):
        outcome = "success"
    elif (ending == "cancelled" or "Cancelled" in (rec.get("failure_reason") or "")
          or rec["status"] == "canceled"):
        outcome = "cancelled"
    else:
        outcome = "failed"
    row = {k: rec[k] for k in ("model", "condition", "strategy", "scenario", "persona", "rep",
                              "status", "total_turns", "duration_s", "failure_reason")}
    row["outcome"] = outcome
    row["ending"] = ending
    row["outcome_ok"] = outcome == expected_outcome
    # A cancellation the user asked for and the robot honoured after confirming is right
    # (honoured_cancel); reported apart per condition (cancel_summary)
    row["honoured_cancel"] = honoured_cancel(rec)
    if row["honoured_cancel"]:
        row["outcome_ok"] = True
    if expect.get("ending"):
        # A planned ending that is not the strategy's own (E4: breakdown, partial). Kept apart
        # from outcome_ok: the LLM-led condition only reports success or failure
        row["ending_ok"] = ending == expect["ending"]
    if rec.get("persona") == "real":
        # The performance of a real participant is not planned: no expected weak criterion
        expect = {k: v for k, v in expect.items() if k != "weak_criterion"}
    scorer = SCORERS.get(rec["strategy"], lambda d, e: {})
    data = rec.get("data", {})
    if not isinstance(data, dict):
        # The LLM-led condition may return its list without the key the rules ask for: read it
        # the same way, so that both conditions are scored alike
        key = {"grounded_qa": "questions", "questionnaire": "answers"}.get(rec["strategy"])
        data = {key: data} if key and isinstance(data, list) else {}
    content = (scorer(data, expect, rec) if rec["strategy"] in WITH_RECORD
               else scorer(data, expect))
    row.update(content)
    # Nothing to check in the content of an honoured cancellation (the task was stopped)
    row["content_ok"] = (content.get("content_ok", True)
                         if expected_outcome == "success" and not row["honoured_cancel"] else True)
    row["task_success"] = row["outcome_ok"] and row["content_ok"]
    lat = turn_latencies(rec.get("transcript", []))
    row["turn_latency_mean_s"] = round(statistics.mean(lat), 2) if lat else None
    row["turn_latency_max_s"] = round(max(lat), 2) if lat else None
    row["llm_calls"] = len(rec.get("skills", []))
    row["fillers"] = fillers(rec.get("transcript", []))
    # What each shared mechanism did (mechanisms.py), and whether it is what the scenario plans
    row.update(mechanisms.counts(rec))
    # What the strategy's own mechanisms did (strategy_checks.py), checked the same way
    row.update(strategy_checks.columns(rec))
    row.update(mechanisms.check(row, rec.get("expect", {})))
    row["_latencies"] = lat
    return row


# The one percentile of the evaluation (linear interpolation: percentiles.py)
pct = percentile
# The response time of reference, as in E0a (run_e0a.py): the share of turns over two seconds is
# reported next to the latency (a robot should answer within about two seconds; the reference is
# cited in the paper), never as a gate
SLOW_TURN_S = 2.0


def latency_summary(rows: list[dict]) -> list[str]:
    """Per model and condition: the dialogues, the turns measured, the system's time per turn
    (median, p95, the worst turn, the share over SLOW_TURN_S), the fillers per dialogue, and the
    calls to the model that timed out or were not made (llm_timeouts, llm_skipped: 0 in normal
    operation). Descriptive: compared across models and conditions, never a pass/fail."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["condition"])].append(r)
    lines = [f"{'model':16s} {'condition':14s} {'n':>4s} {'turns':>6s} {'median':>7s} "
             f"{'p95':>7s} {'worst':>7s} {f'>{SLOW_TURN_S:g}s':>6s} {'fill/d':>6s} "
             f"{'timeouts':>8s} {'skipped':>7s}"]

    def sec(x):
        return f"{x:7.2f}" if x is not None else f"{'-':>7s}"
    for (model, cond), rs in sorted(groups.items()):
        lat = [x for r in rs for x in r.get("_latencies") or []]
        over = (f"{sum(x > SLOW_TURN_S for x in lat) / len(lat):6.2f}" if lat else f"{'-':>6s}")
        lines.append(
            f"{model:16s} {cond:14s} {len(rs):4d} {len(lat):6d} {sec(pct(lat, .5))} "
            f"{sec(pct(lat, .95))} {sec(max(lat) if lat else None)} {over} "
            f"{sum(r.get('fillers') or 0 for r in rs) / len(rs):6.2f} "
            f"{sum(r.get('llm_timeouts') or 0 for r in rs):8d} "
            f"{sum(r.get('llm_skipped') or 0 for r in rs):7d}")
    return lines


def add_adherence(row: dict, rec: dict, rule: str = adherence.DEFAULT_RULE,
                  keep_accents: bool = False) -> dict:
    """The row with the adherence columns of the simulated user (adherence.py)."""
    row.update(adherence.check(rec, rule, keep_accents))
    return row


def mechanisms_summary(rows: list[dict]) -> list[str]:
    """Per model, condition and scenario that plans its mechanisms (expect.mechanisms): the
    dialogues, the share where every planned mechanism did what was planned, the share with every
    event applied, and the columns that failed most; then the dialogues without a closing line."""
    groups = defaultdict(list)
    for r in rows:
        if "mechanisms_ok" in r:
            groups[(r["model"], r["condition"], r["scenario"])].append(r)
    lines = []
    if groups:
        lines.append(f"{'model':16s} {'condition':22s} {'scenario':26s} {'n':>3s} "
                     f"{'mech ok':>8s} {'events':>7s}  failed")
    for (model, cond, scen), rs in sorted(groups.items()):
        failed = defaultdict(int)
        for r in rs:
            for k in filter(None, r["mechanisms_failed"].split(";")):
                failed[k] += 1
        events = sum(r["events_applied"] >= r["events_planned"] for r in rs) / len(rs)
        lines.append(f"{model:16s} {cond:22s} {scen:26s} {len(rs):3d} "
                     f"{sum(r['mechanisms_ok'] for r in rs) / len(rs):8.2f} {events:7.2f}  "
                     + ", ".join(f"{k} ({v})" for k, v in sorted(failed.items())))
    silent = [r for r in rows if not r["closing_said"]]
    lines.append(f"Dialogues without a closing line: {len(silent)} of {len(rows)}"
                 + (": " + ", ".join(sorted({f"{r['condition']}/{r['scenario']}" for r in silent}))
                    if silent else ""))
    return lines


def cancel_summary(rows: list[dict]) -> list[str]:
    """Per model and condition: the dialogues, those cancelled although the scenario did not plan
    it, those of them honoured (the user asked and the robot confirmed: honoured_cancel, counted
    as right), the success rate, and the success rate with the honoured cancellations counted as
    failures; then the honoured ones, for a person to read."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["condition"])].append(r)
    lines = [f"{'model':16s} {'condition':14s} {'n':>4s} {'unplanned':>9s} {'honoured':>8s} "
             f"{'success':>8s} {'succ w/o':>8s}"]
    for (model, cond), rs in sorted(groups.items()):
        unplanned = [r for r in rs if r["honoured_cancel"] is not None]
        honoured = [r for r in unplanned if r["honoured_cancel"]]
        lines.append(f"{model:16s} {cond:14s} {len(rs):4d} {len(unplanned):9d} {len(honoured):8d} "
                     f"{sum(r['task_success'] for r in rs) / len(rs):8.2f} "
                     f"{sum(r['task_success'] and not r['honoured_cancel'] for r in rs) / len(rs):8.2f}")
    honoured = sorted(f"{r['condition']}/{r['scenario']}/{r['persona']}/{r['rep']}"
                      for r in rows if r["honoured_cancel"])
    if honoured:
        lines.append("Honoured cancellations to read: " + ", ".join(honoured))
    return lines


def qa_summary(rows: list[dict]) -> list[str]:
    """Grounded QA, per model and condition, next to the success rate: the questions asked again,
    the false questions (with information), the planned questions never asked and the entries
    linked to no turn (only the dialogues with the simulator's marks; [] when there are none)."""
    groups = defaultdict(list)
    for r in rows:
        if r["strategy"] == "grounded_qa" and "qa_false_questions" in r:
            groups[(r["model"], r["condition"])].append(r)
    if not groups:
        return []
    lines = [f"{'model':16s} {'condition':14s} {'n':>4s} {'success':>8s} {'repeated':>8s} "
             f"{'false q':>7s} {'w/ info':>7s} {'never':>5s} {'unlinked':>8s}"]
    for (model, cond), rs in sorted(groups.items()):
        lines.append(f"{model:16s} {cond:14s} {len(rs):4d} "
                     f"{sum(r['task_success'] for r in rs) / len(rs):8.2f} "
                     + " ".join(f"{sum(r[k] for r in rs):{w}d}" for k, w in (
                         ("qa_repeated", 8), ("qa_false_questions", 7),
                         ("qa_false_with_info", 7), ("qa_never_asked", 5), ("qa_unlinked", 8))))
    return lines


def adherence_summary(rows: list[dict]) -> list[str]:
    """Per model and condition: dialogues, those checked (with goal items), the excluded rate
    (over all dialogues), those flagged for review, and the success rate with and without the
    excluded dialogues."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["condition"])].append(r)
    lines = [f"{'model':16s} {'condition':14s} {'n':>3s} {'checked':>7s} {'excluded':>8s} "
             f"{'review':>6s} {'success':>8s} {'succ excl':>9s}"]
    for (model, cond), rs in sorted(groups.items()):
        kept = [r for r in rs if not r.get("sim_excluded")]
        lines.append(
            f"{model:16s} {cond:14s} {len(rs):3d} "
            f"{sum(r.get('adh_check') != 'n/a' for r in rs):7d} "
            f"{sum(bool(r.get('sim_excluded')) for r in rs) / len(rs):8.2f} "
            f"{sum(r.get('adh_check') == 'review' for r in rs):6d} "
            f"{sum(r['task_success'] for r in rs) / len(rs):8.2f} "
            + (f"{sum(r['task_success'] for r in kept) / len(kept):9.2f}" if kept
               else f"{'-':>9s}"))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results")
    parser.add_argument("--csv", default=None)
    parser.add_argument("--adherence-rule", default=adherence.DEFAULT_RULE,
                        choices=list(adherence.RULES),
                        help="When a dialogue is a simulator failure: "
                             + "; ".join(f"{k}: {v}" for k, v in adherence.RULES.items()))
    parser.add_argument("--keep-accents", action="store_true",
                        help="Compare the declared values with their accents (adherence)")
    parser.add_argument("--exclude-sim-failures", action="store_true",
                        help="Leave the simulator failures out of the summary per strategy")
    args = parser.parse_args()

    rows = []
    for path in run_files(args.results):
        rec = load_record(path)
        rows.append(add_adherence(score(rec), rec, args.adherence_rule, args.keep_accents))
    if not rows:
        print("No runs")
        return

    csv_path = args.csv or os.path.join(args.results, "scores.csv")
    fields = [k for k in rows[0] if not k.startswith("_")]
    for r in rows:
        fields += [k for k in r if k not in fields and not k.startswith("_")]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    groups = defaultdict(list)
    for r in rows:
        if args.exclude_sim_failures and r.get("sim_excluded"):
            continue
        groups[(r["model"], r["condition"], r["strategy"])].append(r)
        groups[(r["model"], r["condition"], "ALL")].append(r)
    print(f"{'model':16s} {'condition':14s} {'strategy':13s} {'n':>3s} {'success':>8s} "
          f"{'outcome':>8s} {'turns':>6s} {'lat p50':>8s} {'lat p95':>8s}")
    for (model, cond, strat), rs in sorted(groups.items()):
        lat = [x for r in rs for x in r["_latencies"]]
        n = len(rs)
        print(f"{model:16s} {cond:14s} {strat:13s} {n:3d} "
              f"{sum(r['task_success'] for r in rs) / n:8.2f} "
              f"{sum(r['outcome_ok'] for r in rs) / n:8.2f} "
              f"{statistics.mean(r['total_turns'] for r in rs):6.1f} "
              f"{(pct(lat, .5) or 0):8.2f} {(pct(lat, .95) or 0):8.2f}")
    print(f"\nLatency per turn (s; descriptive, not a criterion; share of turns over "
          f"{SLOW_TURN_S:g} s)")
    print("\n".join(latency_summary([r for r in rows if not (
        args.exclude_sim_failures and r.get("sim_excluded"))])))
    print("\nShared mechanisms (mechanisms.py; expect.mechanisms of the scenarios)")
    print("\n".join(mechanisms_summary(rows)))
    print("\nCancellations the scenario did not plan (honoured_cancel)")
    print("\n".join(cancel_summary(rows)))
    qa = qa_summary(rows)
    if qa:
        print("\nGrounded QA apart from success (false questions: a turn that asked nothing, "
              "recorded as a question)")
        print("\n".join(qa))
    print(f"\nSimulated user's adherence (rule: {args.adherence_rule}"
          + ("; excluded dialogues left out above" if args.exclude_sim_failures else "") + ")")
    print("\n".join(adherence_summary(rows)))
    print(f"\nScores: {csv_path}")


if __name__ == "__main__":
    main()
