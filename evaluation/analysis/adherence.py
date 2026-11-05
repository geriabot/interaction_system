# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The adherence check of the simulated user: whether it said what its scenario planned, from the
marks of each of its lines (`revealed`: the goal items it stated, {id, value}; by the local
annotator after the dialogue, annotate_turns.py, or, in runs before 2026-10-31, declared by the
simulator itself), compared with the
scenario's goal items ({id, value[, then]}; recorded with the run). No keyword matching, no LLM.
Used by score_runs.py (one set of columns per dialogue); run alone, it checks the goal items of
scenario files before running:

    python3 adherence.py ../experiments/scenarios/<file>.yaml [...]

Per dialogue:
  off script   a declared value that is neither the item's value nor its planned `then` (after
               light normalisation: case, spaces, punctuation anywhere and, unless asked to keep
               them, accents; a trailing stage direction in parentheses of a scripted value is
               ignored), or an id that is not in the goal list, that the simulator originated:
               the value did not appear (normalised words) in any robot utterance up to that
               turn. The annotator marks the scripted value and the planned change by choice
               and writes the scenario's own string for them (annotate_turns.py), so a
               paraphrase is never off script; only a value it marked "other" can be
  robot induced an off-script value the robot had said before (proposed it, read it back,
               misheard it): not a simulator failure; flagged for review, never excluded (the
               author, decision 4 of the methodology review). A robot line that reads two or
               more of a questionnaire's options proposes none of them: those options do not
               count, so a user who picks another option than its script is off script
  unconfirmed  an off-script value the annotator marked "other" without evidence besides its own
               judgement (`confirmed` false: annotate_turns.confirmed): flagged for review, never
               excluded
  unrevealed   a goal item never declared; with a planned change, its first value or its `then`
               never declared
  review       a declaration whose words (the annotator's quote, else the value) do not appear
               at all in the words of the line the user said in that turn. Only flags the
               dialogue for a person; it never excludes
  skipped change a planned change (`then`) never said although the simulated user said the
               item's first value and had at least one more line of its own after it: it had the
               chance to follow its script
  excluded     a simulator failure, left out of the evaluation of the system by the rule fixed
               before running (RULES; default "user_silent"): E1 and the twin of E4. E0a phase 2
               uses its own rule ("e0a": off script, or a skipped change)
  unannotated  a dialogue with goal items whose lines have no marks (not annotated yet, or the
               annotator failed on it: `annotation_failed`): adh_check "unannotated", never
               excluded, never read as a user who stated nothing

Scenarios without goal items: n/a, never excluded.
"""

import argparse
import sys
import unicodedata

# The exclusion rules. A goal item never revealed may be the system's failure (it never asked,
# or closed early) as well as the simulator's, so the default excludes for it only when the
# simulated user ended the dialogue itself by falling silent (or running out of turns), which is
# not the system's doing, and the scenario planned no such end.
RULES = {
    "user_silent": "off script, or a goal item never revealed in a dialogue that ended because "
                   "the simulated user fell silent when the scenario did not plan it (default)",
    "off_script": "off script only",
    "any_unrevealed": "off script, or a goal item never revealed in a dialogue whose scenario "
                      "plans no early end (no cancel or silence event, outcome success)",
    "e0a": "off script, or a planned change of value never said although the simulated user "
           "had at least one more line after the first value (E0a phase 2)",
}
DEFAULT_RULE = "user_silent"


def norm_value(value, keep_accents: bool = False) -> str:
    """Light normalisation: lower case, words and digits only (any punctuation, inside or
    around, is a space), single spaces, no accents unless kept."""
    text = str(value).strip().lower()
    if not keep_accents:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def bare(value) -> str:
    """A scripted value without a trailing stage direction in parentheses ("cannot wind the
    thread (three times)" -> "cannot wind the thread"): how the user says it, not what."""
    text = str(value).strip()
    if text.endswith(")") and "(" in text:
        head = text[:text.rindex("(")].strip()
        if head:
            return head
    return text


def planned_values(item: dict, keep_accents: bool = False) -> set[str]:
    """The normalised values an item's marks may hold on script: its value and its `then`,
    each also without a trailing stage direction."""
    out = set()
    for v in (item.get("value"), item.get("then")):
        if v is not None:
            out |= {norm_value(v, keep_accents), norm_value(bare(v), keep_accents)}
    return out - {""}


def _words(text: str) -> str:
    """The words of a text, for the review flag: lower case, no accents, no punctuation."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " " + " ".join("".join(c if c.isalnum() else " " for c in text).split()) + " "


def goal_items_of(rec: dict) -> list[dict]:
    items = rec.get("goal_items") or []
    return [i for i in items if isinstance(i, dict) and "id" in i and "value" in i]


def options_of(rec: dict) -> list[str]:
    """The answer options of a questionnaire run (its instrument, recorded with the goal)."""
    import json
    try:
        resources = json.loads((rec.get("goal") or {}).get("resources_json") or "[]")
    except (ValueError, TypeError, AttributeError):
        return []
    for r in resources if isinstance(resources, list) else []:
        try:
            content = json.loads(r.get("content", ""))
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(content, dict) and isinstance(content.get("options"), list):
            return [str(o) for o in content["options"]]
    return []


def _robot_words(text: str, options: list[str]) -> str:
    """The words of a robot utterance for the robot-induced check. An utterance that reads two
    or more of the questionnaire's options lists them, it proposes none: those options are left
    out of it (a read-back of one answer still counts)."""
    words = _words(text)
    listed = [o for o in options if _words(o).strip() and _words(o) in words]
    if len(listed) >= 2:
        for o in sorted(listed, key=len, reverse=True):
            words = words.replace(_words(o), " | ")
    return words


def declarations(user_log: list[dict], options: list[str] | None = None) -> list[dict]:
    """Every declared item, in order: {turn, id, value, text, quote, choice, robot_before}
    (text: what the user said; quote and choice: the annotator's, when given; robot_before: the
    words of every robot utterance up to that turn, the one answered included, without the
    option lists of a questionnaire)."""
    out, robot = [], ""
    for entry in user_log or []:
        robot += _robot_words(entry.get("robot", ""), options or [])
        for item in entry.get("revealed") or []:
            out.append({"turn": entry.get("turn"), "id": str(item.get("id", "")),
                        "value": str(item.get("value", "")), "text": entry.get("text", ""),
                        "quote": item.get("quote"), "choice": item.get("choice"),
                        "confirmed": item.get("confirmed"),
                        "robot_before": robot})
    return out


def robot_said(value, robot_before: str) -> bool:
    """Whether the robot had said the value before (its normalised words, as a phrase)."""
    words = _words(value)
    return bool(words.strip()) and words in robot_before


def planned_early_end(rec: dict) -> bool:
    """The scenario plans an end other than a completed task: an expected outcome other than
    success, a planned ending, or a cancellation or silence that the harness injected."""
    expect = rec.get("expect") or {}
    events = {e.get("event") for e in rec.get("user_log") or []}
    return (expect.get("outcome", "success") != "success" or bool(expect.get("ending"))
            or bool(events & {"cancel", "silence"}))


def check(rec: dict, rule: str = DEFAULT_RULE, keep_accents: bool = False) -> dict:
    """The adherence columns of one dialogue record."""
    if rule not in RULES:
        raise ValueError(f"Unknown exclusion rule '{rule}': {list(RULES)}")
    log = rec.get("user_log") or []
    out = {"sim_errors": sum(1 for e in log if "error" in e)}
    items = goal_items_of(rec)
    if not items:
        out.update({"adh_check": "n/a", "sim_excluded": False})
        return out

    if rec.get("annotation_failed") or not any("revealed" in e for e in log):
        # Not annotated (no marks, or the annotator failed): unknown, never "said nothing"
        out.update({"adh_check": "unannotated", "adh_off_script": "", "adh_robot_induced": "",
                    "adh_unconfirmed": "",
                    "adh_unrevealed": "", "adh_skipped_change": "", "adh_review": "",
                    "adh_revealed_errors": 0, "sim_excluded": False, "sim_excluded_why": ""})
        return out

    def n(v):
        return norm_value(v, keep_accents)

    def forms(v):  # a planned value as the marks hold it, and without its stage direction
        return {n(v), n(bare(v))} - {""} if v is not None else set()

    goal = {str(i["id"]): i for i in items}
    decl = declarations(log, options_of(rec) if rec.get("strategy") == "questionnaire" else [])
    off_script, robot_induced, review, unconfirmed = [], [], [], []
    said = {k: set() for k in goal}  # the on-script values declared per item ("value", "then")
    for d in decl:
        item = goal.get(d["id"])
        # an id out of the goal list has no planned value
        planned = planned_values(item, keep_accents) if item is not None else set()
        if n(d["value"]) not in planned:
            if robot_said(d["value"], d.get("robot_before", "")):
                robot_induced.append(f"{d['id']}={d['value']}")
            elif d.get("confirmed") is False:
                unconfirmed.append(f"{d['id']}={d['value']}")
            else:
                off_script.append(f"{d['id']}={d['value']}")
        else:
            said[d["id"]].add("then" if item.get("then") is not None
                              and n(d["value"]) in forms(item["then"])
                              and n(d["value"]) not in forms(item["value"]) else "value")
        # the annotator's quote when it gave one (its value is the scripted string, not the
        # words said), else the value itself
        shown = d["quote"] if d.get("quote") else d["value"]
        if d["text"] and _words(shown).strip() and _words(shown) not in _words(d["text"]):
            review.append(f"t{d['turn']}:{d['id']}")
    declared_ids = {d["id"] for d in decl}
    # The turn of each simulated user's own line (an injected event is not its line)
    own = [e.get("turn") for e in log if "event" not in e and e.get("text")]
    first = {}  # the turn the item's first value was said
    for d in decl:
        item = goal.get(d["id"])
        if item is not None and n(d["value"]) in forms(item["value"]):
            first.setdefault(d["id"], d["turn"])
    skipped = [ident for ident, item in goal.items()
               if item.get("then") is not None and "then" not in said[ident]
               and ident in first and any(t is not None and t > first[ident] for t in own)]
    unrevealed = []
    for ident, item in goal.items():
        if ident not in declared_ids:
            unrevealed.append(ident)
            continue
        if "value" not in said[ident]:
            unrevealed.append(f"{ident} (value)")
        if item.get("then") is not None and "then" not in said[ident]:
            unrevealed.append(f"{ident} (then)")

    why = ""
    if off_script:
        why = "off_script"
    elif rule == "e0a":
        why = "skipped_change" if skipped else ""
    elif unrevealed and rule != "off_script" and not planned_early_end(rec):
        if rule == "any_unrevealed" or rec.get("outcome") == "user_silent":
            why = "unrevealed"
    out.update({
        "adh_check": "fail" if why else ("review" if review or robot_induced or unconfirmed
                                         else "ok"),
        "adh_off_script": "; ".join(off_script),
        "adh_robot_induced": "; ".join(robot_induced),
        "adh_unconfirmed": "; ".join(unconfirmed),
        "adh_unrevealed": "; ".join(unrevealed),
        "adh_skipped_change": "; ".join(skipped),
        "adh_review": "; ".join(review),
        "adh_revealed_errors": sum(1 for e in log if "revealed_error" in e),
        "sim_excluded": bool(why),
        "sim_excluded_why": why,
    })
    return out


def check_scenarios(path: str) -> list[str]:
    """The goal items of every scenario of a file: how many each has, or what is wrong."""
    import yaml
    with open(path, encoding="utf-8") as f:
        scenarios = (yaml.safe_load(f) or {}).get("scenarios", [])
    lines = []
    for s in scenarios:
        items = s.get("goal_items")
        if not items:
            lines.append(f"{s.get('id')}: no goal_items (adherence n/a)")
            continue
        problems = []
        if not isinstance(items, list):
            problems.append("not a list")
        else:
            ids = []
            for k, i in enumerate(items):
                if not isinstance(i, dict) or "id" not in i or "value" not in i:
                    problems.append(f"item {k}: needs id and value")
                    continue
                extra = set(i) - {"id", "value", "then"}
                if extra:
                    problems.append(f"item {k}: unknown keys {sorted(extra)}")
                if isinstance(i["value"], (list, dict)) or isinstance(i.get("then"), (list, dict)):
                    problems.append(f"item {k}: a value is one string or number")
                ids.append(str(i["id"]))
            if len(ids) != len(set(ids)):
                problems.append("repeated ids")
        verdict = "ERROR " + "; ".join(problems) if problems else f"{len(items)} goal items"
        lines.append(f"{s.get('id')}: {verdict}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario_files", nargs="+")
    args = parser.parse_args()
    bad = False
    for path in args.scenario_files:
        print(f"== {path}")
        for line in check_scenarios(path):
            print(f"  {line}")
            bad = bad or "ERROR" in line
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
