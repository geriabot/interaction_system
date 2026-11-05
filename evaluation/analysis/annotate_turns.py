# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The annotator of the simulated user's lines: after the dialogues, a LOCAL model reads each
conversation with its scenario's goal items and marks every line of the simulated user: the goal
items it states ({id, value}) and whether it asks the robot for something (request yes | no).
The adherence check (adherence.py) and the grounded-QA scoring (score_runs.py) read these marks.

Why after the dialogue and not by the simulated user itself (decided by the author, 2026-10-31):
declaring while playing the person loaded the model that plays it; what is annotated after cannot
influence the dialogue, can be redone without running the dialogues again, and costs no paid call
(one local call per dialogue, deterministic, schema-constrained). The annotator is validated by
hand once (light scheme: its disagreements with the simulator's own declarations of the second
debugging round, a random sample where they agree, and every verdict-changing case; per campaign
only the disagreements of two local annotators): doc/human_review/adherence_audit.md.

The annotator chooses, it does not write values. For each goal item a line states, it picks the
item's scripted value, its planned change (`then`, when the item has one) or "other", and quotes
the words of the line that state it; only for "other" does it give the value said. Deterministic
checks follow (recheck_marks): in a questionnaire, an option said is the value of the question
the robot read; a scripted choice whose quote has other numbers is made "other". Every "other"
mark, the only kind that can put a dialogue off script, then gets a second look
(verify_others): a value said that is only a part of a planned value is that value, and any
other gets one call with the whole conversation (the scripted value, the change, something else,
or not about the item). A different value is `confirmed` only with evidence besides the model's
judgement (confirmed(): a different number, or a short answer with no word in common); the
adherence check excludes only for a confirmed one and flags the others for review. Whether each
line is a request comes from a second call with the first annotator's prompt, unchanged. The mark then
holds the scenario's own string for the first two choices ({id, value, choice, quote}), so the
adherence check never compares a paraphrase ("Coloured clothes are washed at 60 degrees" for
"coloured clothes at 60 degrees") with the script, and the quote is kept for the audit. The same
choice recognises the values that describe what the user does ("asks how much the repair will
cost") and a trailing stage direction ("(three times)") is shown to the model as such, not as part
of the value (split_direction). The scenario files are not changed.

The marks go to a sidecar file per dialogue, <results>/annotations/<model>/<condition>/<file>.json
({"annotator": {model, prompt_sha}, "turns": [{turn, revealed, request}]}): the records of the
runs are never changed. load_record() merges them into the record's user_log; a declaration the
simulator made itself (runs before 2026-10-31) is kept as `revealed_sim`. A dialogue whose answer
is still unusable after ATTEMPTS calls (a model error, not JSON, a user line not covered) gets a
failure marker instead ({"failed": true, "reason"}): load_record() does not merge it and sets
`annotation_failed`, the adherence check reads the dialogue as not annotated (never as lines that
state nothing), and the next run of this script tries it again. The run summary counts failures.

    python3 annotate_turns.py <results folder> [--model gemma3:12b] [--host URL] [--redo]
                              [--only <list of run files>] [--out <annotations folder>]
    python3 annotate_turns.py <results folder> --compare      # against the simulator's own
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

DEFAULT_MODEL = "gemma3:12b"
# Calls per dialogue before its annotation is written as failed (a model error, an answer that
# is not JSON or does not cover every user line)
ATTEMPTS = 3
CHOICES = {"scripted": "scripted", "change": "then", "other": "other"}  # the model's -> stored
VERDICTS = {"yes": "scripted", "the change": "then", "something else": "different",
            "not about it": "not stated"}  # the second look's answers -> stored

PROMPT = """You annotate a test conversation between a robot and a simulated user. The user
followed a script: a list of goal items, each with its SCRIPTED value and, for some, a planned
CHANGE of value said later. For EACH line of the USER (numbered below), mark:
- "items": the goal items the line states (an empty list when it states none). An item is stated
  when the line gives its value, or asks about it when the item is a question or a topic. For
  each item stated:
  - "quote": the words of the line that state it, copied exactly from the line (a few words);
  - "choice": "scripted" if the line gives the item's scripted value, "change" if it gives the
    planned change, "other" if it gives a different value. The same value in other words is the
    same value: a paraphrase, a shorter or longer form, a synonym, the number in digits or in
    words ("Coloured clothes are washed at 60 degrees" gives "coloured clothes at 60 degrees").
    A different number, time, name, option, answer or fact for that item is "other". A line
    that neither gives nor asks about an item does not state it: leave the item out rather than
    mark it "other" because the line is about something else;
  - "value": only for "other", the value the line gives, in its words; "" otherwise.
  Some scripted values describe what the user does ("asks the price of the chair", "declines to
  answer"): the line states that item when it does that, in any words. An item that is a
  question or a topic is stated when the line asks it; the answer the user repeats back is not a
  value of it. The user's name, a greeting, thanks, or a question about what the robot meant are
  not values of any goal item unless an item is about them.
A short report or answer states the item the robot had just asked about: "Done" or "I did it"
after the robot gave a step states that step's item; an answer to a numbered question states that
question's item (judge which one by the robot's line before it), even when the answer is the
value of another item: it is then "other" for the item of the question asked.
Judge only what the line says, in the context of the conversation; do not guess intentions.

GOAL ITEMS (id: scripted value; change: the planned change of value):
{items}

THE CONVERSATION (U<n> are the user's lines to annotate):
{conversation}

Answer in JSON: {{"lines": [{{"n": <n>, "items": [{{"id": "<goal item id>", "quote": "<words of
the line>", "choice": "scripted" | "change" | "other", "value": "<only for other>"}}]}}]}}, one
entry per user line."""

# Whether each line asks the robot for something: the first annotator's prompt and schema,
# verbatim (the grounded-QA scoring was validated with its request marks); only its "request" is
# kept, its values are not read
REQUEST = """You annotate a test conversation between a robot and a simulated user. For EACH line
of the USER (numbered below), mark:
- "items": the goal items the line states, from the list below only: an item is stated when the
  line says its value, or asks about it when the item is a question or a topic. Copy the value
  as the list gives it, or the value the user actually said if it said a different one. An
  empty list when the line states none.
- "request": "yes" if the line asks the robot for something (a question or a request, with or
  without a question mark: "I'd like to know the price"), "no" if it does not (a comment,
  thanks, an answer to the robot, a goodbye).
A short report or answer states the item the robot had just asked about: "Done" or "I did it"
after the robot gave a step states that step's item; an answer to a numbered question states that
question's item (judge which one by the robot's line before it).
Judge only what the line says, in the context of the conversation; do not guess intentions.

GOAL ITEMS (id: value; "later" is a planned change of value):
{items}

THE CONVERSATION (U<n> are the user's lines to annotate):
{conversation}

Answer in JSON: {{"lines": [{{"n": <n>, "request": "yes" | "no",
  "items": [{{"id": "<goal item id>", "value": "<the value said>"}}]}}]}}, one entry per user line."""

# The second look at each "other" mark: the only marks that can make a dialogue off script
VERIFY = """A simulated user in a test conversation with a robot followed a script: a list of goal
items, each with the value the user had to say or the thing it had to do.

GOAL ITEMS (id: scripted value; change: the planned change of value):
{items}

THE CONVERSATION (U<n> are the user's lines):
{conversation}

Look at line U{n}: "{text}"
For the goal item "{id}", the script told the user to say or do: "{value}"{change}. Did line U{n}
say or do "{value}"? First "reason": one short sentence on what line U{n} answers or does, and
whether that is about this item. Then "answer":
- "yes": the line says or does it (the same meaning, in other words or shorter){change_option}
- "something else": in its place the line gives another value that contradicts the script for
  the same thing (another number, time, name, option or answer to the same question). Adding
  details, explaining, or saying only part of it does not contradict the script
- "not about it": the line does not deal with this item (it answers another question or item,
  asks a question, asks what the robot meant, gives the user's name, or only thanks or repeats
  the robot)
Answer in JSON: {{"reason": "...", "answer": {answers}}}"""


# A stage direction at the end of a scripted value ("cannot wind the thread (three times)"): how
# the user says it, not what it says
_DIRECTION = re.compile(r"\s*\(([^()]*)\)\s*$")


def split_direction(value) -> tuple[str, str]:
    """(the value without a trailing parenthetical stage direction, the direction or "")."""
    text = str(value)
    m = _DIRECTION.search(text)
    if not m or not text[:m.start()].strip():
        return text.strip(), ""
    return text[:m.start()].strip(), m.group(1).strip()


def prompt_sha() -> str:
    return hashlib.sha256((PROMPT + REQUEST + VERIFY).encode()).hexdigest()[:12]


def sidecar(path: str) -> str:
    """The annotation file of a run record (<results>/runs/... -> <results>/annotations/...)."""
    head, rel = path.split(os.sep + "runs" + os.sep, 1)
    return os.path.join(head, "annotations", rel)


def annotated(path: str) -> bool:
    """Whether a run record has the annotator's marks (a sidecar that is not a failure)."""
    side = sidecar(path)
    if not os.path.exists(side):
        return False
    try:
        with open(side, encoding="utf-8") as f:
            return not json.load(f).get("failed")
    except ValueError:
        return False


def _goal(rec: dict) -> list[dict]:
    return [i for i in rec.get("goal_items") or [] if isinstance(i, dict) and "id" in i]


def _item_line(item: dict) -> str:
    value, direction = split_direction(item["value"])
    line = f'- {item["id"]}: "{value}"'
    if direction:
        line += f" (stage direction for the user: {direction})"
    if item.get("then") is not None:
        line += f'; change: "{split_direction(item["then"])[0]}"'
    return line


def _item_schema(item: dict) -> dict:
    choices = ["scripted", "change", "other"] if item.get("then") is not None \
        else ["scripted", "other"]
    return {"type": "object",
            "properties": {"id": {"type": "string", "enum": [str(item["id"])]},
                           "quote": {"type": "string", "maxLength": 160},
                           "choice": {"type": "string", "enum": choices},
                           "value": {"type": "string", "maxLength": 160}},
            "required": ["id", "quote", "choice", "value"]}


def _context(rec: dict) -> tuple[str, str, list[int]]:
    """The goal items, the numbered conversation and the turns of the user lines."""
    log = [e for e in rec.get("user_log") or [] if e.get("text")]
    conv, turns = [], []
    for n, entry in enumerate(log, 1):
        if entry.get("robot"):
            conv.append(f"Robot: {entry['robot']}")
        conv.append(f"U{n}: {entry['text']}")
        turns.append(entry.get("turn", n))
    return "\n".join(_item_line(i) for i in _goal(rec)), "\n".join(conv), turns


def build(rec: dict) -> tuple[str, dict, list[int]] | None:
    """The prompt of the items, its schema and the turns of the user lines; None without goal
    items or user lines."""
    items = _goal(rec)
    log = [e for e in rec.get("user_log") or [] if e.get("text")]
    if not items or not log:
        return None
    items_text, conversation, turns = _context(rec)
    schema = {"type": "object", "properties": {"lines": {
        "type": "array", "minItems": len(log), "maxItems": len(log), "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                # at most one mark per item, and one more for a value changed within the line:
                # an answer cannot loop until it is cut
                "items": {"type": "array", "maxItems": len(items) + 1,
                          "items": {"anyOf": [_item_schema(i) for i in items]}}},
            "required": ["n", "items"]}}},
        "required": ["lines"]}
    prompt = PROMPT.format(items=items_text, conversation=conversation)
    return prompt, schema, turns


def build_request(rec: dict) -> tuple[str, dict]:
    """The prompt and the schema of the request marks."""
    _, conversation, turns = _context(rec)
    items = "\n".join(f'- {i["id"]}: "{i["value"]}"'
                      + (f', later "{i["then"]}"' if i.get("then") is not None else "")
                      for i in _goal(rec))
    schema = {"type": "object", "properties": {"lines": {
        "type": "array", "minItems": len(turns), "maxItems": len(turns), "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                "request": {"type": "string", "enum": ["yes", "no"]},
                "items": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string",
                                          "enum": [str(i["id"]) for i in _goal(rec)]},
                                   "value": {"type": "string"}},
                    "required": ["id", "value"]}}},
            "required": ["n", "request", "items"]}}},
        "required": ["lines"]}
    return REQUEST.format(items=items, conversation=conversation), schema


def _mark(entry: dict, goal: dict) -> dict | None:
    """One stated item as stored: {id, value, choice, quote}. For the scripted value and the
    planned change the value is the scenario's own string, so a paraphrase never reaches the
    comparison; for "other" it is the value said (the quote if the model gave none)."""
    ident = str(entry.get("id", ""))
    item = goal.get(ident)
    if item is None:
        return None
    quote = str(entry.get("quote", "")).strip()
    choice = CHOICES.get(str(entry.get("choice", "")).strip().lower(), "other")
    if choice == "then" and item.get("then") is None:
        choice = "other"
    if choice == "scripted":
        value = str(item["value"])
    elif choice == "then":
        value = str(item["then"])
    else:
        value = str(entry.get("value", "")).strip() or quote
    return {"id": ident, "value": value, "choice": choice, "quote": quote}


def parse(raw: str, turns: list[int], items: list[dict] | None = None,
          request_raw: str | None = None) -> list[dict]:
    """The marks per user turn ({turn, revealed, request}); a line the answer does not cover
    gets no marks (request absent: unknown). With the goal items, each stated item is the
    annotator's choice (_mark); without them, the value as the model wrote it. The request of
    a line is read from request_raw when given, else from the same answer."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    goal = {str(i["id"]): i for i in items or []}
    by_n = _lines(json.dumps(data), len(turns))
    requests = _lines(request_raw, len(turns)) if request_raw is not None else {}
    out = []
    for n, turn in enumerate(turns, 1):
        line = by_n.get(n, {})
        revealed = []
        for i in line.get("items") or []:
            if not isinstance(i, dict) or not i.get("id"):
                continue
            mark = _mark(i, goal) if goal else {"id": str(i["id"]),
                                                "value": str(i.get("value", ""))}
            if mark is not None and (mark["id"], mark["value"]) not in \
                    {(m["id"], m["value"]) for m in revealed}:
                revealed.append(mark)
        entry = {"turn": turn, "revealed": revealed}
        req = (requests.get(n, {}) if request_raw is not None else line).get("request")
        if req in ("yes", "no"):
            entry["request"] = req
        out.append(entry)
    return out


def _lines(raw, n_lines: int | None = None) -> dict:
    """{n: line} of an answer {"lines": [...]} ({} if it is not one). An answer with one entry
    per user line but numbered otherwise (a number repeated or skipped) is read by position."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    lines = [x for x in (data.get("lines") if isinstance(data, dict) else None) or []
             if isinstance(x, dict)]
    if n_lines is not None and len(lines) == n_lines and \
            [x.get("n") for x in lines] != list(range(1, n_lines + 1)):
        return {k: line for k, line in enumerate(lines, 1)}
    out = {}
    for line in lines:
        if isinstance(line.get("n"), int) and line["n"] not in out:
            out[line["n"]] = line
    return out


def problem(raw: str, n_lines: int, key: str = "items") -> str:
    """Why an answer cannot be used ("" if it can): not JSON, or not one entry per user line
    with its `key` (items: a list; request: yes or no)."""
    try:
        data = json.loads(raw or "")
    except ValueError:
        return "not JSON (truncated?)"
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        return "no lines"
    lines = _lines(raw, n_lines)

    def good(line):
        if key == "request":
            return line.get("request") in ("yes", "no")
        return isinstance(line.get(key), list)
    missing = [n for n in range(1, n_lines + 1) if not good(lines.get(n, {}))]
    return f"lines not covered: {missing}" if missing else ""


def _call(ask, prompt: str, schema: dict, tokens: int, n_lines: int, key: str,
          attempts: int, reasons: list[str]) -> str | None:
    """A usable answer to one prompt, retried (attempt 1, 2...: more room to answer); None and
    the reasons appended after the last attempt."""
    for attempt in range(attempts):
        try:
            raw = ask(prompt, schema, tokens * (attempt + 1), attempt)
        except Exception as e:  # a model or connection error: retried, then recorded
            reasons.append(f"{key}: {type(e).__name__}: {e}"[:200])
            continue
        why = problem(raw, n_lines, key)
        if not why:
            return raw
        reasons.append(f"{key}: {why}")
    return None


def annotate(rec: dict, ask, model: str, attempts: int = ATTEMPTS) -> dict | None:
    """The annotation of one record, asking ask(prompt, schema, max_tokens, attempt) -> raw JSON:
    the items (then recheck_marks and verify_others) and the requests. An error or an unusable
    answer is retried; after the last attempt the annotation is a failure marker ({"failed":
    true, "reason"}), which the users of the marks read as a dialogue not annotated."""
    built = build(rec)
    if built is None:
        return None
    prompt, schema, turns = built
    head = {"model": model, "prompt_sha": prompt_sha()}
    reasons = []
    raw = _call(ask, prompt, schema, 200 + 120 * len(turns), len(turns), "items", attempts,
                reasons)
    req_prompt, req_schema = build_request(rec)
    req = _call(ask, req_prompt, req_schema, 80 + 60 * len(turns), len(turns), "request",
                attempts, reasons) if raw is not None else None
    if raw is None or req is None:
        return {"annotator": head, "failed": True, "reason": "; ".join(reasons),
                "attempts": attempts, "turns": []}
    marks = parse(raw, turns, _goal(rec), req)
    recheck_marks(rec, marks)
    verify_others(rec, marks, ask)
    return {"annotator": head, "turns": marks}


_UNITS = {w: k for k, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_UNITS.update({w: k for k, w in enumerate(
    "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince "
    "dieciseis diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres "
    "veinticuatro veinticinco veintiseis veintisiete veintiocho veintinueve".split())})
_UNITS["un"] = _UNITS["una"] = 1
_TENS = {w: 10 * k for k, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety"
                                          .split(), 2)}
_TENS.update({w: 10 * k for k, w in enumerate("treinta cuarenta cincuenta sesenta setenta "
                                               "ochenta noventa".split(), 3)})


def numbers(text) -> set[int]:
    """The numbers of a text, in digits or in words up to 99 (English and Spanish): "forty-eight"
    and "48" are 48."""
    text = unicodedata.normalize("NFKD", str(text))
    words = _words("".join(c for c in text if not unicodedata.combining(c)))
    out, k = set(), 0
    while k < len(words):
        w = words[k]
        if w.isdigit():
            out.add(int(w))
        elif w in _TENS:
            value = _TENS[w]
            nxt = words[k + 2] if k + 2 < len(words) and words[k + 1] == "y" else \
                words[k + 1] if k + 1 < len(words) else ""
            if nxt in _UNITS and 0 < _UNITS[nxt] < 10:
                value += _UNITS[nxt]
                k += 2 if words[k + 1] == "y" else 1
            out.add(value)
        elif w in _UNITS:
            out.add(_UNITS[w])
        k += 1
    return out


def _words(text) -> list[str]:
    return "".join(c if c.isalnum() else " " for c in str(text).lower()).split()


def _as_planned(mark: dict, item: dict, choice: str) -> None:
    mark.update({"value": str(item["value" if choice == "scripted" else "then"]),
                 "choice": choice})


def _instrument(rec: dict) -> tuple[list[str], list[str]]:
    """(options, questions) of a questionnaire run's instrument; ([], []) otherwise."""
    if rec.get("strategy") != "questionnaire":
        return [], []
    try:
        resources = json.loads((rec.get("goal") or {}).get("resources_json") or "[]")
    except (ValueError, TypeError, AttributeError):
        return [], []
    for r in resources if isinstance(resources, list) else []:
        try:
            content = json.loads(r.get("content", ""))
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(content, dict) and isinstance(content.get("options"), list):
            questions = [q if isinstance(q, str) else str(q.get("text", ""))
                         for q in content.get("items") or []]
            return [str(o) for o in content["options"]], questions
    return [], []


def _phrase(text) -> str:
    return " " + " ".join(_words(text)) + " "


def recheck_marks(rec: dict, marks: list[dict]) -> None:
    """Deterministic checks of the model's choices, in place.

    In a questionnaire whose goal items are its questions in order, a line that says exactly one
    of the options answers the question the robot's line reads (the last question read, when the
    robot only reads the options again): that question's item gets the option as its value (the
    scripted one, the planned change or "other"), whatever item the model marked, and the
    model's other marks of an option in that line are dropped. Elsewhere a scripted value or
    change whose quote has numbers and none of the chosen value's is made "other" (the quote its
    value), for verify_others."""
    import adherence
    norm = adherence.norm_value
    items = _goal(rec)
    goal = {str(i["id"]): i for i in items}
    options, questions = _instrument(rec)
    by_question = len(questions) == len(items) and bool(options)
    log = {e.get("turn"): e for e in rec.get("user_log") or [] if e.get("text")}
    asked = None  # the index of the question the robot read last
    for entry in marks:
        line = log.get(entry["turn"], {})
        if by_question:
            read = [k for k, q in enumerate(questions)
                    if _words(q) and _phrase(q) in _phrase(line.get("robot", ""))]
            asked = read[0] if len(read) == 1 else (asked if not read else None)
            said = [o for o in options if _phrase(o) in _phrase(line.get("text", ""))]
            said = [o for o in said if not any(o != p and _phrase(o) in _phrase(p) for p in said)]
            if asked is not None and len(said) == 1:
                item, option = items[asked], said[0]
                mark = {"id": str(item["id"]), "value": option, "choice": "other",
                        "quote": option, "verified": "option said", "confirmed": True}
                if norm(option) == norm(item["value"]):
                    _as_planned(mark, item, "scripted")
                elif item.get("then") is not None and norm(option) == norm(item["then"]):
                    _as_planned(mark, item, "then")
                entry["revealed"] = [m for m in entry["revealed"]
                                     if m["id"] != mark["id"]
                                     and not any(_phrase(o) in _phrase(m.get("quote", ""))
                                                 for o in options)] + [mark]
                continue
        for mark in entry["revealed"]:
            item = goal.get(mark["id"])
            if item is None or mark.get("choice") not in ("scripted", "then"):
                continue
            got, want = numbers(mark.get("quote", "")), numbers(mark["value"])
            if got and want and not got & want:
                mark.update({"value": mark["quote"], "choice": "other"})


# Words that carry no value of their own, left out when two values are compared word by word
_FUNCTION = set("""a an the of to in on at by for from with and or is are it its this that my your
i you el la los las lo un una unos unas de del al en con por para y o u es son mi tu yo""".split())
# The strategies whose scripted values are short answers (a slot value, a quiz answer, a step
# report): another value shares no content word with the scripted one
SHORT_ANSWERS = {"slot_filling", "quiz", "procedure"}


def _plain(text) -> list[str]:
    text = unicodedata.normalize("NFKD", str(text))
    return _words("".join(c for c in text if not unicodedata.combining(c)))


def confirmed(rec: dict, item: dict, value: str, text: str) -> bool:
    """Whether an "other" value has evidence besides the model's judgement, so that it may put
    the dialogue off script: its words are in the line, and it has numbers none of which is a
    planned value's (both with numbers), or, where the scripted values are short answers, it
    shares no content word with the scripted value nor the planned change. Without it the mark
    stays "other" but unconfirmed: the adherence check flags it for review, it does not exclude."""
    words = set(_plain(value))
    if not words or not words <= set(_plain(text)):
        return False
    planned = [split_direction(item[k])[0] for k in ("value", "then") if item.get(k) is not None]
    got = numbers(value)
    if got and all(numbers(p) and not got & numbers(p) for p in planned):
        return True
    if rec.get("strategy") in SHORT_ANSWERS:
        content = words - _FUNCTION
        return bool(content) and not any(content & set(_plain(p)) for p in planned)
    return False


def verify_others(rec: dict, marks: list[dict], ask) -> None:
    """A second look at every "other" mark, in place. A value said whose words all lie in one
    planned value of the item (a part of it: "60 degrees" for "coloured clothes at 60 degrees")
    is that planned value. Otherwise one short call asks whether the line gives this item the
    scripted value, the planned change, a different value, or does not state it; the mark is
    changed or dropped accordingly ("verified" records the answer). A call that fails leaves the
    mark as it was ("verified": "error"). A different value keeps "confirmed" (confirmed())."""
    goal = {str(i["id"]): i for i in _goal(rec)}
    log = {e.get("turn"): e for e in rec.get("user_log") or [] if e.get("text")}
    items_text, conversation, turns = _context(rec)
    number = {t: n for n, t in enumerate(turns, 1)}
    for entry in marks:
        kept = []
        for mark in entry["revealed"]:
            item = goal.get(mark["id"])
            if mark.get("choice") != "other" or item is None or \
                    mark.get("verified") == "option said":
                kept.append(mark)
                continue
            said = set(_words(mark["value"]))
            inside = [c for c, k in (("scripted", "value"), ("then", "then"))
                      if item.get(k) is not None and said
                      and said <= set(_words(split_direction(item[k])[0]))]
            if len(inside) == 1:
                _as_planned(mark, item, inside[0])
                mark["verified"] = "part of the planned value"
                kept.append(mark)
                continue
            line = log.get(entry["turn"], {})
            has_then = item.get("then") is not None
            answers = ["yes"] + (["the change"] if has_then else []) + ["something else",
                                                                       "not about it"]
            prompt = VERIFY.format(
                items=items_text, conversation=conversation, n=number.get(entry["turn"], "?"),
                id=item["id"], value=split_direction(item["value"])[0],
                change=(f' (and later, as a planned change, "{split_direction(item["then"])[0]}")'
                        if has_then else ""),
                change_option=(f'\n- "the change": the line says "{split_direction(item["then"])[0]}" instead'
                               if has_then else ""),
                text=line.get("text", ""),
                answers=" | ".join(f'"{a}"' for a in answers))
            schema = {"type": "object",
                      "properties": {"reason": {"type": "string", "maxLength": 240},
                                     "answer": {"type": "string", "enum": answers}},
                      "required": ["reason", "answer"]}
            try:
                answer = json.loads(ask(prompt, schema, 120, 0) or "{}").get("answer")
            except Exception:  # the mark stays "other", unverified
                answer = None
            if answer not in answers:
                mark.update({"verified": "error", "confirmed": False})
                kept.append(mark)
                continue
            verdict = VERDICTS[answer]
            if verdict in ("scripted", "then"):
                # a value said with numbers, none of them the planned value's, is not it
                got, want = numbers(mark["value"]), numbers(item["value" if verdict == "scripted"
                                                                   else "then"])
                if got and want and not got & want:
                    verdict = "different (another number)"
            mark["verified"] = verdict
            if verdict == "not stated":
                continue
            if verdict in ("scripted", "then"):
                _as_planned(mark, item, verdict)
            else:
                mark["confirmed"] = confirmed(rec, item, mark["value"], line.get("text", ""))
            kept.append(mark)
        entry["revealed"] = [m for k, m in enumerate(kept)
                             if (m["id"], m["value"]) not in {(x["id"], x["value"])
                                                              for x in kept[:k]}]


def merge(rec: dict, annotation: dict) -> dict:
    """The record with the annotator's marks in its user_log (a declaration of the simulator
    itself kept as revealed_sim, request_sim)."""
    marks = {t["turn"]: t for t in annotation.get("turns") or []}
    log = []
    for entry in rec.get("user_log") or []:
        entry = dict(entry)
        if "revealed" in entry:
            entry["revealed_sim"] = entry.pop("revealed")
        if "request" in entry:
            entry["request_sim"] = entry.pop("request")
        entry.pop("revealed_error", None)
        mark = marks.get(entry.get("turn"))
        if mark is not None:
            entry["revealed"] = mark["revealed"]
            if "request" in mark:
                entry["request"] = mark["request"]
        log.append(entry)
    return {**rec, "user_log": log, "annotator": annotation.get("annotator")}


def load_record(path: str) -> dict:
    """A run record, with the annotator's marks merged when its sidecar exists (as is
    otherwise: earlier runs keep the simulator's own declarations)."""
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    side = sidecar(path) if os.sep + "runs" + os.sep in path else ""
    if side and os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            annotation = json.load(f)
        if annotation.get("failed"):
            # not annotated: never read as lines that state nothing
            rec["annotation_failed"] = annotation.get("reason") or "failed"
        else:
            rec = merge(rec, annotation)
    return rec


def compare(results: str) -> dict:
    """Line by line, the annotator against the simulator's own declarations (runs that have
    both): agreement on the items and on request, and the disagreeing lines for a person."""
    lines = items_agree = req_n = req_agree = 0
    disagree = []
    for path in sorted(glob.glob(os.path.join(results, "runs", "**", "*.json"), recursive=True)):
        rec = load_record(path)
        for e in rec.get("user_log") or []:
            if "revealed_sim" not in e or "revealed" not in e:
                continue
            lines += 1
            ids = sorted(i["id"] for i in e["revealed"])
            sim = sorted(i.get("id", "") for i in e["revealed_sim"])
            same = ids == sim
            items_agree += same
            if "request_sim" in e and "request" in e:
                req_n += 1
                req_agree += e["request_sim"] == e["request"]
            if not same or ("request_sim" in e and e.get("request") != e["request_sim"]):
                disagree.append({"run": os.path.relpath(path, results), "turn": e.get("turn"),
                                 "text": e.get("text"), "annotator": ids, "simulator": sim})
    return {"lines": lines, "items_agree": items_agree, "request_lines": req_n,
            "request_agree": req_agree, "disagree": disagree}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--redo", action="store_true", help="annotate again what has a sidecar")
    ap.add_argument("--only", default=None,
                    help="a file listing the run records to annotate (one path per line)")
    ap.add_argument("--out", default=None,
                    help="write the annotations under this folder instead of <results>/annotations")
    ap.add_argument("--compare", action="store_true",
                    help="compare with the simulator's own declarations; no model call")
    args = ap.parse_args()
    if args.compare:
        out = compare(args.results)
        print(json.dumps({k: v for k, v in out.items() if k != "disagree"}))
        for d in out["disagree"]:
            print(json.dumps(d, ensure_ascii=False))
        return
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                    "simulation", "interaction_sim"))
    from interaction_sim.cloud_llm import ask_json  # noqa: E402

    def ask(prompt, schema, max_tokens, attempt=0):
        # a longer context only when the prompt and the answer would not fit in the default one
        # (characters / 3 overestimates the tokens of English and Spanish)
        ctx = 8192 if len(prompt) / 3 + max_tokens < 8000 else 16384
        raw, _ = ask_json("ollama", args.model, prompt, schema, temperature=0.0, seed=attempt,
                          max_tokens=max_tokens, host=args.host, num_ctx=ctx)
        return raw

    paths = sorted(glob.glob(os.path.join(args.results, "runs", "**", "*.json"), recursive=True))
    if args.only:
        with open(args.only, encoding="utf-8") as f:
            wanted = {os.path.abspath(x.strip()) for x in f if x.strip()}
        paths = [p for p in paths if os.path.abspath(p) in wanted]
    done = skipped = nothing = 0
    failed = []
    for k, path in enumerate(paths, 1):
        side = sidecar(path)
        if args.out:
            side = os.path.join(args.out, os.path.relpath(side, os.path.join(args.results,
                                                                              "annotations")))
        if os.path.exists(side) and not args.redo:
            with open(side, encoding="utf-8") as f:
                if not json.load(f).get("failed"):  # a failure is tried again
                    skipped += 1
                    continue
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        annotation = annotate(rec, ask, args.model)
        if annotation is None:
            nothing += 1
            continue
        os.makedirs(os.path.dirname(side), exist_ok=True)
        with open(side, "w", encoding="utf-8") as f:
            json.dump(annotation, f, ensure_ascii=False, indent=1)
        if annotation.get("failed"):
            failed.append(path)
            print(f"FAILED {os.path.relpath(path, args.results)}: {annotation['reason']}",
                  flush=True)
        else:
            done += 1
        if k % 25 == 0:
            print(f"{k}/{len(paths)} annotated {done} failed {len(failed)}", flush=True)
    print(f"annotated {done}, failed {len(failed)} (not annotated: retried on the next run), "
          f"already annotated {skipped}, without goal items or user lines {nothing}")


if __name__ == "__main__":
    main()
