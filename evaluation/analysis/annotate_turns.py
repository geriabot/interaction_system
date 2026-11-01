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

The marks go to a sidecar file per dialogue, <results>/annotations/<model>/<condition>/<file>.json
({"annotator": {model, prompt_sha}, "turns": [{turn, revealed, request}]}): the records of the
runs are never changed. load_record() merges them into the record's user_log; a declaration the
simulator made itself (runs before 2026-10-31) is kept as `revealed_sim`.

    python3 annotate_turns.py <results folder> [--model gemma3:12b] [--host URL] [--redo]
    python3 annotate_turns.py <results folder> --compare      # against the simulator's own
"""

import argparse
import glob
import hashlib
import json
import os
import sys

DEFAULT_MODEL = "gemma3:12b"

PROMPT = """You annotate a test conversation between a robot and a simulated user. For EACH line
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


def prompt_sha() -> str:
    return hashlib.sha256(PROMPT.encode()).hexdigest()[:12]


def sidecar(path: str) -> str:
    """The annotation file of a run record (<results>/runs/... -> <results>/annotations/...)."""
    head, rel = path.split(os.sep + "runs" + os.sep, 1)
    return os.path.join(head, "annotations", rel)


def build(rec: dict) -> tuple[str, dict, list[int]] | None:
    """The prompt, the schema and the turns of the user lines; None without goal items or
    user lines."""
    items = [i for i in rec.get("goal_items") or [] if isinstance(i, dict) and "id" in i]
    log = [e for e in rec.get("user_log") or [] if e.get("text")]
    if not items or not log:
        return None
    lines = [f'- {i["id"]}: "{i["value"]}"' + (f', later "{i["then"]}"' if "then" in i else "")
             for i in items]
    conv, turns = [], []
    for n, entry in enumerate(log, 1):
        if entry.get("robot"):
            conv.append(f"Robot: {entry['robot']}")
        conv.append(f"U{n}: {entry['text']}")
        turns.append(entry.get("turn", n))
    schema = {"type": "object", "properties": {"lines": {
        "type": "array", "minItems": len(log), "maxItems": len(log), "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                "request": {"type": "string", "enum": ["yes", "no"]},
                "items": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string",
                                          "enum": [str(i["id"]) for i in items]},
                                   "value": {"type": "string"}},
                    "required": ["id", "value"]}}},
            "required": ["n", "request", "items"]}}},
        "required": ["lines"]}
    prompt = PROMPT.format(items="\n".join(lines), conversation="\n".join(conv))
    return prompt, schema, turns


def parse(raw: str, turns: list[int]) -> list[dict]:
    """The marks per user turn ({turn, revealed, request}); a line the answer does not cover
    gets no marks (request absent: unknown)."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    by_n = {}
    for line in data.get("lines") or []:
        if isinstance(line, dict) and isinstance(line.get("n"), int) and line["n"] not in by_n:
            by_n[line["n"]] = line
    out = []
    for n, turn in enumerate(turns, 1):
        line = by_n.get(n, {})
        entry = {"turn": turn, "revealed": [
            {"id": str(i["id"]), "value": str(i.get("value", ""))}
            for i in line.get("items") or [] if isinstance(i, dict) and i.get("id")]}
        if line.get("request") in ("yes", "no"):
            entry["request"] = line["request"]
        out.append(entry)
    return out


def annotate(rec: dict, ask, model: str) -> dict | None:
    """The annotation of one record, asking ask(prompt, schema, max_tokens) -> raw JSON."""
    built = build(rec)
    if built is None:
        return None
    prompt, schema, turns = built
    raw = ask(prompt, schema, 80 + 60 * len(turns))
    return {"annotator": {"model": model, "prompt_sha": prompt_sha()},
            "turns": parse(raw, turns)}


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
            rec = merge(rec, json.load(f))
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

    def ask(prompt, schema, max_tokens):
        raw, _ = ask_json("ollama", args.model, prompt, schema, temperature=0.0, seed=0,
                          max_tokens=max_tokens, host=args.host)
        return raw

    done = skipped = 0
    for path in sorted(glob.glob(os.path.join(args.results, "runs", "**", "*.json"),
                                 recursive=True)):
        side = sidecar(path)
        if os.path.exists(side) and not args.redo:
            skipped += 1
            continue
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        annotation = annotate(rec, ask, args.model)
        if annotation is None:
            continue
        os.makedirs(os.path.dirname(side), exist_ok=True)
        with open(side, "w", encoding="utf-8") as f:
            json.dump(annotation, f, ensure_ascii=False, indent=1)
        done += 1
    print(f"annotated {done}, already annotated {skipped}")


if __name__ == "__main__":
    main()
