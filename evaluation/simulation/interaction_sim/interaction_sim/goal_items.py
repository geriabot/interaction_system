# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The goal items of a scenario, for the adherence check of the simulated user (analysis/adherence.py)
and the grounded-QA scoring. No ROS: tested with plain pytest.

A scenario may list its goal items, the values the user must state:

    goal_items:
      - {id: drink, value: latte}
      - {id: water_until, value: "8 am", then: "6 am"}   # a planned change: first 8 am, then 6 am

Since 2026-10-31 the simulated user only speaks (decided by the author: declaring while playing
the person loaded the model): it never gets the list, and a local annotator marks its lines after
the dialogue (analysis/annotate_turns.py). This module validates the goal items, reads the
simulated user's answer robustly (parse_line), and keeps the prompt block, schema and reading of
the `revealed` declarations of the runs recorded before (each as {"id", "value"}; a value the
user said that is not the planned one shows up as off script in the check).
"""

import json
import re

ITEMS_BLOCK = """YOUR GOAL ITEMS (a record for the test: never read this list aloud)
{items}
With each line you say, declare in "revealed" the goal items you state in that line: their id
and the value you said, copied exactly from this list (if you said a different value, write the
one you said). Asking about an item states it. Only items of this list; an empty list when the
line states none of them. Declare also in "request" whether the line asks the robot for
something (a question or a request, with or without a question mark: "yes") or not (a comment,
thanks, an answer to the robot, a goodbye: "no").

"""

ANSWER_FORMAT = 'Answer in JSON: {"action": "say" | "end", "text": "<what you say>"}'
ANSWER_FORMAT_ITEMS = ('Answer in JSON: {"action": "say" | "end", "text": "<what you say>", '
                       '"request": "yes" | "no", '
                       '"revealed": [{"id": "<goal item id>", "value": "<its value>"}]}')


def validate_goal_items(items) -> list[dict]:
    """The goal items of a scenario as {id, value[, then]} with string values; [] when it has
    none. Raises ValueError when they are malformed (so that a scenario file is checked before
    running)."""
    if not items:
        return []
    if not isinstance(items, list):
        raise ValueError("goal_items must be a list of {id, value[, then]}")
    out, seen = [], set()
    for i, item in enumerate(items):
        if not isinstance(item, dict) or "id" not in item or "value" not in item:
            raise ValueError(f"goal_items[{i}] needs an id and a value")
        extra = set(item) - {"id", "value", "then"}
        if extra:
            raise ValueError(f"goal_items[{i}]: unknown keys {sorted(extra)}")
        ident = str(item["id"]).strip()
        if not ident or ident in seen:
            raise ValueError(f"goal_items[{i}]: empty or repeated id '{ident}'")
        if isinstance(item["value"], (list, dict)) or isinstance(item.get("then"), (list, dict)):
            raise ValueError(f"goal_items[{i}]: a value is one string or number")
        seen.add(ident)
        entry = {"id": ident, "value": str(item["value"]).strip()}
        if item.get("then") is not None:
            entry["then"] = str(item["then"]).strip()
        out.append(entry)
    return out


SCRIPT_BLOCK = """YOUR SCRIPT (what your goal asks you to say, in its order; a reminder for you: never
read it aloud, and nothing to declare about it)
{items}

"""


def script_block(items: list[dict]) -> str:
    """The goal items as a script for the simulated user to follow ("" when there are none):
    the same list as before, with nothing to declare (provisional, 2026-11-01: without any list
    the simulator followed its goal worse)."""
    if not items:
        return ""
    lines = [f'- {i["id"]}: "{i["value"]}"'
             + (f', later (when your goal says, and from then on only this) "{i["then"]}"'
                if "then" in i else "")
             for i in items]
    return SCRIPT_BLOCK.format(items="\n".join(lines))


def items_block(items: list[dict]) -> str:
    """The goal items in the simulated user's prompt ("" when there are none)."""
    if not items:
        return ""
    lines = [f'- {i["id"]}: "{i["value"]}"'
             + (f', later (when your goal says, and from then on only this) "{i["then"]}"'
                if "then" in i else "")
             for i in items]
    return ITEMS_BLOCK.format(items="\n".join(lines))


def answer_format(items: list[dict]) -> str:
    return ANSWER_FORMAT_ITEMS if items else ANSWER_FORMAT


def answer_schema(base: dict, items: list[dict]) -> dict:
    """The JSON schema of the answer: the base one plus `revealed` when there are goal items.
    propertyOrdering (Gemini only; ask_json removes it for the other providers) keeps the spoken
    line before the declaration, as Ollama and OpenAI follow the order of the properties."""
    if not items:
        return base
    schema = json.loads(json.dumps(base))
    schema["properties"]["request"] = {"type": "string", "enum": ["yes", "no"]}
    schema["properties"]["revealed"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "enum": [i["id"] for i in items]},
                "value": {"type": "string"},
            },
            "required": ["id", "value"],
        },
    }
    schema["required"] = list(schema.get("required", [])) + ["request", "revealed"]
    schema["propertyOrdering"] = list(schema["properties"])
    return schema


def _load_object(raw: str):
    """The JSON object of an answer, tolerating code fences and text around it; None if none."""
    text = (raw or "").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _salvage(raw: str) -> dict | None:
    """The spoken line and the action of a broken answer (e.g. cut by the token limit)."""
    text = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw or "")
    if not text:
        return None
    try:
        line = json.loads(f'"{text.group(1)}"')
    except ValueError:
        line = text.group(1)
    action = re.search(r'"action"\s*:\s*"(say|end)"', raw or "")
    return {"action": action.group(1) if action else "say", "text": line}


def parse_revealed(value) -> tuple[list[dict], str]:
    """The declared items, as {id, value} strings, and an error ("" if none). Entries are kept
    even when their id is not in the goal list (the check reports them as off script)."""
    if value is None:
        return [], "missing"
    if not isinstance(value, list):
        return [], "not a list"
    out, bad = [], 0
    for entry in value:
        value = entry.get("value") if isinstance(entry, dict) else None
        if (isinstance(entry, dict) and entry.get("id") not in (None, "")
                and value is not None and not isinstance(value, (list, dict))):
            item = {"id": str(entry["id"]).strip(), "value": str(value).strip()}
            if item not in out:
                out.append(item)
        else:
            bad += 1
    return out, f"{bad} malformed entries" if bad else ""


def parse_line(raw: str) -> dict:
    """The simulated user's answer {action, text}, tolerating code fences, text around the JSON
    and a cut answer (the spoken line is recovered). Raises ValueError when not even the spoken
    line can be read (the caller falls back to asking for a repetition)."""
    obj = _load_object(raw) or _salvage(raw)
    if obj is None:
        raise ValueError(f"no JSON answer: {(raw or '')[:120]!r}")
    return {"action": obj.get("action", "say"), "text": str(obj.get("text", "")).strip()}


def parse_answer(raw: str) -> dict:
    """The simulated user's answer when it has goal items: {action, text, revealed}, request
    ("yes" | "no": whether the line asks the robot for something; absent when not readable) and
    revealed_error when the declaration could not be read (then revealed is []). The spoken line
    is kept whenever it can be read, so a broken declaration never stops the dialogue. Raises
    ValueError when not even the spoken line can be read (the caller falls back as before)."""
    obj = _load_object(raw)
    error = ""
    if obj is None:
        obj = _salvage(raw)
        if obj is None:
            raise ValueError(f"no JSON answer: {(raw or '')[:120]!r}")
        error = "invalid JSON (spoken line recovered)"
        revealed = []
    else:
        revealed, error = parse_revealed(obj.get("revealed"))
    out = {"action": obj.get("action", "say"), "text": str(obj.get("text", "")).strip(),
           "revealed": revealed}
    if obj.get("request") in ("yes", "no"):
        out["request"] = obj["request"]
    if error:
        out["revealed_error"] = error
    return out
