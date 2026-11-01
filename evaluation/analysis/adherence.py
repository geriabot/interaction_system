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
               light normalisation: case, spaces, surrounding punctuation and, unless asked to
               keep them, accents), or an id that is not in the goal list, that the simulator
               originated: the value did not appear (normalised words) in any robot utterance up
               to that turn
  robot induced an off-script value the robot had said before (proposed it, read it back,
               misheard it): not a simulator failure; flagged for review, never excluded (the
               author, decision 4 of the methodology review)
  unrevealed   a goal item never declared; with a planned change, its first value or its `then`
               never declared
  review       a declared value that does not appear at all in the words of the line the user
               said in that turn. Only flags the dialogue for a person; it never excludes
  excluded     a simulator failure, left out of the evaluation of the system by the rule fixed
               before running (RULES; default "user_silent"): E1 and the twin of E4. E0a
               excludes nothing (it reports the rate as a check)

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
}
DEFAULT_RULE = "user_silent"


def norm_value(value, keep_accents: bool = False) -> str:
    """Light normalisation: lower case, single spaces, no surrounding punctuation or quotes,
    no accents unless kept."""
    text = str(value).strip().lower()
    if not keep_accents:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
    text = " ".join(text.split())
    return text.strip(" .,;:!?¡¿\"'«»")


def _words(text: str) -> str:
    """The words of a text, for the review flag: lower case, no accents, no punctuation."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " " + " ".join("".join(c if c.isalnum() else " " for c in text).split()) + " "


def goal_items_of(rec: dict) -> list[dict]:
    items = rec.get("goal_items") or []
    return [i for i in items if isinstance(i, dict) and "id" in i and "value" in i]


def declarations(user_log: list[dict]) -> list[dict]:
    """Every declared item, in order: {turn, id, value, text, robot_before} (text: what the user
    said; robot_before: the words of every robot utterance up to that turn, the one answered
    included)."""
    out, robot = [], ""
    for entry in user_log or []:
        robot += _words(entry.get("robot", ""))
        for item in entry.get("revealed") or []:
            out.append({"turn": entry.get("turn"), "id": str(item.get("id", "")),
                        "value": str(item.get("value", "")), "text": entry.get("text", ""),
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

    def n(v):
        return norm_value(v, keep_accents)

    goal = {str(i["id"]): i for i in items}
    decl = declarations(log)
    off_script, robot_induced, review = [], [], []
    said = {k: set() for k in goal}  # the on-script values declared per item
    for d in decl:
        item = goal.get(d["id"])
        planned = set()  # an id out of the goal list has no planned value
        if item is not None:
            planned.add(n(item["value"]))
            if item.get("then") is not None:
                planned.add(n(item["then"]))
        if n(d["value"]) not in planned:
            if robot_said(d["value"], d.get("robot_before", "")):
                robot_induced.append(f"{d['id']}={d['value']}")
            else:
                off_script.append(f"{d['id']}={d['value']}")
        else:
            said[d["id"]].add(n(d["value"]))
        if d["text"] and _words(d["value"]).strip() and _words(d["value"]) not in _words(d["text"]):
            review.append(f"t{d['turn']}:{d['id']}")
    declared_ids = {d["id"] for d in decl}
    unrevealed = []
    for ident, item in goal.items():
        if ident not in declared_ids:
            unrevealed.append(ident)
            continue
        if n(item["value"]) not in said[ident]:
            unrevealed.append(f"{ident} (value)")
        if item.get("then") is not None and n(item["then"]) not in said[ident]:
            unrevealed.append(f"{ident} (then)")

    why = ""
    if off_script:
        why = "off_script"
    elif unrevealed and rule != "off_script" and not planned_early_end(rec):
        if rule == "any_unrevealed" or rec.get("outcome") == "user_silent":
            why = "unrevealed"
    out.update({
        "adh_check": "fail" if why else ("review" if review or robot_induced else "ok"),
        "adh_off_script": "; ".join(off_script),
        "adh_robot_induced": "; ".join(robot_induced),
        "adh_unrevealed": "; ".join(unrevealed),
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
