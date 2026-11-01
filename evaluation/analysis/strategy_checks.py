# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
What the strategies' own mechanisms did in a dialogue, against their specification, read from its
record (score_runs.py adds these columns before it checks expect.mechanisms, so a scenario can
list them there). No LLM. The robot's reply to a planned moment is the first robot line that is not
a filler after the user's line of the event at that turn (`events` of the scenario); what the
robot had recorded when it said it is the feedback of that line (`feedback[i].frame`, one entry
per robot line, in order: the data of the strategy at that moment).

Every dialogue of a strategy:
  rubric_in_intro, rubric_in_intro_words  roleplay: the criteria of the rubric whose text is in the
               robot's first line (the intro must name the role, never give the rubric away): word
               for word (rubric_in_intro), and with every content word of the criterion
               (rubric_in_intro_words, for a person to read: the intro may name the user's goal)
  rp_exchanges, rp_ended_by  roleplay: the exchanges of the scene and who ended it (data)
  key_points, key_points_from_llm  explanation: the key points of the data, and whether the
               request had none (so the LLM listed them) and the data has some

When the scenario names the planted moment (expect):
  out_of_range {turn, slot}  slot filling: range_rejected (the slot is empty in what the robot
               had recorded when it replied to the out-of-range value) and range_said (both bounds
               of the slot, as digits or number words, are in that reply; approximate: reported,
               not planned)
  unanswered [slots]  slot filling: unanswered_ok, the slots the outcome reports as unanswered
               ("Unanswered slots: ...") are exactly these (a slot conditioned on one of them was
               not asked: it is neither in the frame nor unanswered)
  frame_values [values]  slot filling without a schema (the frame designed by the LLM, slot names
               unknown): in score_runs.py, each value must be the value of some slot
  report_contradicts {turn, step}  procedure: contradiction_not_done, the step is still the
               current one and not done when the robot replies to the report that contradicts its
               check (a problem: help, not done)
  repeat {turns: [first, second]}  roleplay: repeat_literal (the reply to the first request to
               repeat is the robot's previous line, word for word) and repeat_rephrased (the
               reply to the second is not that line again)
  teach_back {turn, wrong: [key points, 1-based]}  explanation, a restatement with planted wrong
               points: tb_marked_wrong (each planted point is "wrong" when the robot replies),
               tb_others_correct (every other key point is "correct"), tb_repair_only_wrong (the
               reply restates none of the points said right: no correct point has REPEATED or
               more of its content words in it; a short confirmation such as "the side entrance is
               right" stays below), tb_repair_review (correct points with REVIEW to REPEATED of
               their words in it: for a person to read), and tb_closed_consistent (the
               explanation is never completed while a critical point is not correct, in any line
               nor at the end)
A column whose moment cannot be found in the record (the event never happened, the robot did not
reply) is None: the mechanism was not provoked, which events_applied reports.
"""

import json
import re
import unicodedata

from key_facts import _words

# Shares of a key point's content words in the repair: from REPEATED on, the point is restated;
# from REVIEW on, a person reads it
REPEATED = 0.75
REVIEW = 0.5

NUMBER_WORDS = {
    0: ("zero", "cero"), 1: ("one", "uno", "una", "un"), 2: ("two", "dos"), 3: ("three", "tres"),
    4: ("four", "cuatro"), 5: ("five", "cinco"), 6: ("six", "seis"), 7: ("seven", "siete"),
    8: ("eight", "ocho"), 9: ("nine", "nueve"), 10: ("ten", "diez"), 11: ("eleven", "once"),
    12: ("twelve", "doce"), 15: ("fifteen", "quince"), 20: ("twenty", "veinte"),
    30: ("thirty", "treinta"), 50: ("fifty", "cincuenta"), 100: ("hundred", "cien"),
}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def _lines(rec: dict) -> list[dict]:
    return [t for t in rec.get("transcript") or [] if not t.get("filler")]


def _frame_of(rec: dict, robot_index: int, text: str | None = None) -> dict | None:
    """The data of the strategy when the robot said its robot_index-th line (0-based); with its
    text, the first feedback from that index on that carries it (in case the feedback and the
    lines do not pair one to one)."""
    feedback = rec.get("feedback") or []
    if text is not None:
        robot_index = next((i for i in range(robot_index, len(feedback))
                            if (feedback[i].get("utterance") or "").strip() == text.strip()),
                           robot_index)
    if robot_index >= len(feedback):
        return None
    try:
        frame = json.loads(feedback[robot_index].get("frame") or "{}")
    except (TypeError, ValueError):
        return None
    return frame if isinstance(frame, dict) else None


def reply_to_event(rec: dict, turn: int):
    """(the robot's reply to the line of the event at that turn, its index among the robot
    lines, the robot line before the event's line) or None."""
    event = next((e for e in rec.get("events") or [] if e.get("turn") == turn
                  and e.get("text")), None)
    if event is None:
        return None
    lines = _lines(rec)
    robot_seen, previous = 0, None
    for i, t in enumerate(lines):
        if t["role"] == "robot":
            robot_seen += 1
            previous = t["text"]
            continue
        if t["text"].strip() != event["text"].strip():
            continue
        for later in lines[i + 1:]:
            if later["role"] == "robot":
                return later["text"], robot_seen, previous
            break  # the user spoke again before any reply
        return None
    return None


def _number_said(n: float, text: str) -> bool:
    plain = _plain(text)
    words = set(plain.split())
    if float(n).is_integer():
        n = int(n)
        if str(n) in words or any(w in words for w in NUMBER_WORDS.get(n, ())):
            return True
    return f"{n}" in plain


def _schema_slot(rec: dict, slot: str) -> dict:
    try:
        schema = json.loads((rec.get("goal") or {}).get("frame_schema_json") or "{}")
    except ValueError:
        return {}
    return next((s for s in schema.get("slots", []) if s.get("name") == slot), {})


def out_of_range(rec: dict, plan: dict) -> dict:
    found = reply_to_event(rec, plan.get("turn"))
    if found is None:
        return {"range_rejected": None, "range_said": None}
    reply, index, _ = found
    frame = _frame_of(rec, index, reply)
    slot = plan.get("slot")
    rejected = None if frame is None else frame.get(slot) in (None, "", [])
    spec = _schema_slot(rec, slot)
    bounds = [spec[k] for k in ("minimum", "exclusive_minimum", "maximum", "exclusive_maximum")
              if k in spec]
    said = all(_number_said(b, reply) for b in bounds) if bounds else None
    return {"range_rejected": rejected, "range_said": said}


def unanswered_slots(failure_reason: str) -> list[str] | None:
    """The slots of "Unanswered slots: a, b" (slot filling's reason of a partial outcome)."""
    match = re.search(r"Unanswered slots:\s*(.*)", failure_reason or "")
    if not match:
        return []
    return [s.strip() for s in match.group(1).split(",") if s.strip()]


def contradiction(rec: dict, plan: dict) -> dict:
    found = reply_to_event(rec, plan.get("turn"))
    frame = _frame_of(rec, found[1], found[0]) if found else None
    if frame is None:
        return {"contradiction_not_done": None}
    step = int(plan.get("step", 0))
    done = any(i == step - 1 and r.get("status") == "done"
               for i, r in enumerate(frame.get("results") or []))
    return {"contradiction_not_done": frame.get("current_step") == step and not done}


def repeats(rec: dict, plan: dict) -> dict:
    turns = list(plan.get("turns") or [])
    out = {"repeat_literal": None, "repeat_rephrased": None}
    first = reply_to_event(rec, turns[0]) if turns else None
    if first is None:
        return out
    line = first[2]
    out["repeat_literal"] = line is not None and _plain(first[0]) == _plain(line)
    second = reply_to_event(rec, turns[1]) if len(turns) > 1 else None
    if second is not None and line is not None:
        out["repeat_rephrased"] = bool(second[0].strip()) and _plain(second[0]) != _plain(line)
    return out


def word_share(point: str, text: str) -> float:
    """The share of a key point's content words (by their first five letters) in the text."""
    want, said = _words(point), _words(text)
    if not want:
        return 0.0
    return sum(1 for w in want if any(s.startswith(w[:5]) for s in said)) / len(want)


def _key_points(frame: dict | None) -> list[dict]:
    points = (frame or {}).get("key_points")
    return [p for p in points if isinstance(p, dict)] if isinstance(points, list) else []


def _consistent(frame: dict | None) -> bool:
    """Not completed while a critical key point is not correct."""
    if not frame or not frame.get("completed"):
        return True
    return all(p.get("status") == "correct" for p in _key_points(frame) if p.get("critical", True))


def teach_back(rec: dict, plan: dict) -> dict:
    out = {"tb_marked_wrong": None, "tb_others_correct": None, "tb_repair_only_wrong": None,
           "tb_repair_review": None}
    frames = [_frame_of(rec, i) for i in range(len(rec.get("feedback") or []))]
    final = rec.get("data") if isinstance(rec.get("data"), dict) else None
    out["tb_closed_consistent"] = all(_consistent(f) for f in frames + [final])
    found = reply_to_event(rec, plan.get("turn"))
    if found is None:
        return out
    reply, index, _ = found
    points = _key_points(_frame_of(rec, index, reply))
    if not points:
        return out
    wrong = {int(i) - 1 for i in plan.get("wrong") or []}
    out["tb_marked_wrong"] = all(i < len(points) and points[i].get("status") == "wrong"
                                 for i in wrong)
    others = [p for i, p in enumerate(points) if i not in wrong]
    out["tb_others_correct"] = all(p.get("status") == "correct" for p in others)
    shares = [word_share(p.get("point", ""), reply) for p in others]
    out["tb_repair_only_wrong"] = not any(s >= REPEATED for s in shares)
    out["tb_repair_review"] = sum(1 for s in shares if REVIEW <= s < REPEATED)
    return out


def _resources(rec: dict) -> list:
    try:
        resources = json.loads((rec.get("goal") or {}).get("resources_json") or "[]")
    except ValueError:
        return []
    return resources if isinstance(resources, list) else []


def _content(resource) -> object:
    try:
        return json.loads(resource.get("content", ""))
    except (TypeError, ValueError, AttributeError):
        return None


def _criteria(rec: dict) -> list[str]:
    for r in _resources(rec):
        c = _content(r)
        if isinstance(c, dict) and isinstance(c.get("criteria"), list):
            return [str(x) for x in c["criteria"]]
    return []


def roleplay(rec: dict) -> dict:
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    out = {"rp_exchanges": data.get("exchanges"), "rp_ended_by": data.get("ended_by")}
    robot = [t["text"] for t in _lines(rec) if t["role"] == "robot"]
    criteria = _criteria(rec)
    if robot and criteria:
        intro = _plain(robot[0])
        out["rubric_in_intro"] = sum(1 for c in criteria if _plain(c) and f" {_plain(c)} " in
                                     f" {intro} ")
        out["rubric_in_intro_words"] = sum(1 for c in criteria if word_share(c, robot[0]) == 1.0)
    return out


def explanation(rec: dict) -> dict:
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    points = _key_points(data)
    given = any(isinstance(_content(r), dict) and "key_points" in _content(r)
                for r in _resources(rec))
    return {"key_points": len(points), "key_points_from_llm": bool(points) and not given}


# Columns that describe the dialogue and check nothing: a False is not a failure (the triage does
# not flag it; e.g. key_points_from_llm False only says the request gave the key points)
DESCRIPTIVE = frozenset({"key_points_from_llm"})


def columns(rec: dict) -> dict:
    expect = rec.get("expect") or {}
    strategy = rec.get("strategy")
    out = {}
    if strategy == "roleplay":
        out.update(roleplay(rec))
        if expect.get("repeat"):
            out.update(repeats(rec, expect["repeat"]))
    elif strategy == "explanation":
        out.update(explanation(rec))
        if expect.get("teach_back"):
            out.update(teach_back(rec, expect["teach_back"]))
    elif strategy == "slot_filling":
        if expect.get("out_of_range"):
            out.update(out_of_range(rec, expect["out_of_range"]))
        if "unanswered" in expect:
            got = unanswered_slots(rec.get("failure_reason") or "")
            out["unanswered_ok"] = sorted(got) == sorted(expect["unanswered"] or [])
    elif strategy == "procedure" and expect.get("report_contradicts"):
        out.update(contradiction(rec, expect["report_contradicts"]))
    return out
