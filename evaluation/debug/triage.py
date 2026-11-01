# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Attribution of the dialogues of a debugging run (layer 2 of the debugging before E0a:
doc/DEBUGGING.md). No ROS, no model.

    python3 debug/triage.py flag <results> [--spot 1] [--seed 0]
    python3 debug/triage.py summary <results>
    python3 debug/triage.py compare <results> [--models qwen3:8b,llama3.1:8b]
    python3 debug/triage.py sheet <dialogue record .json>

flag      scores every dialogue of the results folder (analysis/score_runs.py, mechanisms.py,
          strategy_checks.py, adherence.py) and flags those that failed: task_success false, a
          planned ending or mechanism not as planned, a strategy check false, a skill that
          failed, the dialogue timed out, the simulated user excluded by the adherence rule, an
          honoured cancellation the scenario did not plan (read by a person), calls of the model
          that timed out where no failure was planned. Bugs hide in passes too: it also samples
          --spot passing dialogues per strategy and language (default 1; deterministic for a
          --seed). For each one it writes a review sheet, <results>/triage/sheets/
          <model>__<condition>__<dialogue>.md: the planned events and failures, then turn by
          turn what the user did,
          every call of the system's model in that turn (the skill, the question, the latency,
          the prompt, the answer or the error: record_model_calls of the debugging files), the
          tree's decisions (the skills asked and their labels) and the robot's line. And it
          writes (or updates, keeping what the reviewer already filled) the sheet the reviewer
          fills, <results>/triage/classification.yaml: per dialogue, the class (C, P, S or M;
          OK for a spot-checked pass that is right), the first wrong turn and a note.
summary   counts the classes per model and says whether the exit criterion holds: every
          flagged and spot-checked dialogue classified, and no C and no P.
compare   the cross-family check: for every scenario and persona run by two or more models, the
          dialogues side by side turn by turn (<results>/triage/compare/<scenario>__<persona>.md),
          with each model's questions and answers. The same wrong answer from both families to
          the same input points to the prompt (P); for the identical input use
          debug/replay.py same-input.
sheet     prints the review sheet of one dialogue.

The classes (doc/DEBUGGING.md): at the first wrong turn,
  C  program or logic: the tree or the code did the wrong thing with correct model answers
  P  prompt or method: the model's input was wrong, incomplete, ambiguous or unreasonable
  S  simulator: the simulated user broke its goal (excluded, as by the adherence rule)
  M  model: correct and clear input, wrong answer (accepted: what E0a and the experiments measure)
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict

import yaml

EVAL = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(EVAL, "analysis"))
import adherence  # noqa: E402
from annotate_turns import load_record  # noqa: E402
import mechanisms  # noqa: E402
import score_runs  # noqa: E402
import strategy_checks  # noqa: E402

CLASSES = ("C", "P", "S", "M", "OK")
SHEET = "classification.yaml"
HEADER = """\
# The attribution of the flagged dialogues of this debugging run (debug/triage.py,
# doc/DEBUGGING.md).
# Fill, per dialogue: class (C program or logic, P prompt or method, S simulator, M model; OK for
# a spot-checked pass that is right, or a flag that is not a failure), first_wrong_turn (the turn
# of its sheet where it first went wrong) and note (what went wrong and, for C or P, the fix and
# its regression test of layer 1). `triage.py flag` keeps what is filled here when it runs again;
# a dialogue no longer flagged is kept with stale: true. Exit: no C and no P (triage.py summary).
"""


# ==== FLAGGING ====


def records(results: str) -> list[tuple[str, dict]]:
    out = []
    for path in score_runs.run_files(results):
        out.append((path, load_record(path)))
    return out


def dialogue_id(path: str, results: str) -> str:
    """<model dir>/<condition>/<scenario>__<persona>__r<k>, from the record's path."""
    rel = os.path.relpath(path, os.path.join(results, "runs"))
    return rel[:-5] if rel.endswith(".json") else rel


def flags_of(rec: dict, row: dict) -> list[str]:
    """Why a dialogue needs a person: what failed, by the automatic scoring."""
    out = []
    if not row.get("task_success"):
        why = [k for k in ("outcome_ok", "content_ok") if not row.get(k)]
        out.append("task_success false (" + ", ".join(why) + f"; ending {row.get('ending')})")
    if row.get("ending_ok") is False:
        out.append(f"ending {row.get('ending')}, planned {rec.get('expect', {}).get('ending')}")
    if row.get("mechanisms_ok") is False:
        out.append(f"mechanisms not as planned: {row.get('mechanisms_failed')}")
    for key, value in strategy_checks.columns(rec).items():
        if value is False and key not in strategy_checks.DESCRIPTIVE:
            out.append(f"strategy check {key} false")
    if row.get("skill_failures"):
        out.append(f"{row['skill_failures']} skill failure(s)")
    if rec.get("status") == "timeout":
        out.append("the dialogue timed out (dialogue_timeout)")
    if row.get("sim_excluded"):
        out.append(f"simulated user excluded by the adherence rule ({row.get('adh_check')})")
    if row.get("honoured_cancel"):
        out.append("a cancellation the scenario did not plan (honoured: read it)")
    if (row.get("llm_timeouts") or row.get("llm_skipped")) and not rec.get("faults"):
        out.append(f"model calls timed out ({row.get('llm_timeouts')}) or not made "
                   f"({row.get('llm_skipped')}) with no failure planned")
    return out


def spot_sample(candidates: list[dict], n: int, seed: int) -> list[dict]:
    """n passing dialogues per strategy and language, chosen by a hash of their id (the same
    sample for the same seed)."""
    groups = defaultdict(list)
    for c in candidates:
        groups[(c["language"], c["strategy"])].append(c)
    out = []
    for key in sorted(groups):
        ranked = sorted(groups[key], key=lambda c: hashlib.sha256(
            f"{seed}|{c['id']}".encode()).hexdigest())
        out.extend(ranked[:n])
    return out


def flag(results: str, spot: int = 1, seed: int = 0) -> dict:
    """Score, flag, write the sheets and update the classification sheet; returns it."""
    out_dir = os.path.join(results, "triage")
    entries, passing, by_id = [], [], {}
    for path, rec in records(results):
        row = adherence.check(rec)
        row = {**score_runs.score(rec), **row}
        item = {"id": dialogue_id(path, results), "model": rec.get("model"),
                "language": rec.get("language", "English"), "strategy": rec.get("strategy"),
                "scenario": rec.get("scenario"), "persona": rec.get("persona")}
        by_id[item["id"]] = (path, rec, row)
        why = flags_of(rec, row)
        if why:
            entries.append({**item, "why": why})
        else:
            passing.append(item)
    for item in spot_sample(passing, spot, seed):
        entries.append({**item, "why": ["spot-check of a pass"]})
    for item in entries:
        path, rec, row = by_id[item["id"]]
        sheet_path = os.path.join(out_dir, "sheets", item["id"].replace("/", "__") + ".md")
        os.makedirs(os.path.dirname(sheet_path), exist_ok=True)
        with open(sheet_path, "w", encoding="utf-8") as f:
            f.write(sheet(rec, row, item["why"]))
        item["sheet"] = os.path.relpath(sheet_path, out_dir)
    return write_classification(out_dir, entries)


def write_classification(out_dir: str, entries: list[dict]) -> dict:
    path = os.path.join(out_dir, SHEET)
    old = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = {d["id"]: d for d in (yaml.safe_load(f) or {}).get("dialogues") or []}
    dialogues = []
    for item in entries:
        before = old.pop(item["id"], {})
        dialogues.append({**item, "class": before.get("class", ""),
                          "first_wrong_turn": before.get("first_wrong_turn"),
                          "note": before.get("note", "")})
    for item in old.values():  # classified before, no longer flagged: kept, marked stale
        dialogues.append({**item, "stale": True})
    data = {"dialogues": dialogues}
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
    return data


# ==== THE REVIEW SHEET ====


def _block(text, lang="") -> str:
    text = "" if text is None else str(text)
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text}\n{fence}"


def turns_of(rec: dict) -> list[dict]:
    """The dialogue by turns: turn 0 is the opening; turn k (k >= 1) is what the user did after
    the robot's k-th line, what the system did with it (skills, calls of the model) and the
    robot's next line."""
    lines = [t for t in rec.get("transcript") or []]
    robot = [t for t in lines if t["role"] == "robot" and not t.get("filler")]
    log = {e.get("turn"): e for e in rec.get("user_log") or [] if e.get("turn")}
    calls = defaultdict(list)
    for c in rec.get("model_calls") or []:
        calls[c.get("turn") or 0].append(c)
    bounds = [r["t"] for r in robot]
    skills = defaultdict(list)
    for s in mechanisms.dialogue_skills(rec.get("skills") or []):  # not the previous dialogue's
        t = s.get("t", 0.0)
        k = sum(1 for b in bounds if b <= t)  # the robot lines said before it: its turn
        skills[k].append(s)
    fillers = defaultdict(list)
    for t in lines:
        if t.get("filler"):
            fillers[sum(1 for b in bounds if b <= t["t"])].append(t["text"])
    n = max([len(robot)] + list(calls) + list(log))
    out = []
    for k in range(n + 1):
        user = log.get(k)
        out.append({"turn": k, "user": user, "calls": calls.get(k, []),
                    "skills": skills.get(k, []), "fillers": fillers.get(k, []),
                    "robot": robot[k]["text"] if k < len(robot) else None})
    return out


def _user_line(entry: dict | None) -> str:
    if entry is None:
        return "(nothing: the dialogue had ended, or the user stopped answering)"
    if entry.get("event") == "silence":
        return "(silence: planned event)"
    if entry.get("event") == "turn_limit":
        return "(the simulated user's own turn limit)"
    what = f"EVENT {entry['event']}: " if entry.get("event") else ""
    if entry.get("action") == "end":
        return f"(the simulated user ends the conversation) {entry.get('text', '')}".strip()
    text = entry.get("text", "")
    heard = f"  [heard: {entry['heard']}]" if entry.get("heard") else ""
    revealed = f"  [revealed: {entry['revealed']}]" if entry.get("revealed") else ""
    error = f"  [SIMULATOR ERROR: {entry['error']}]" if entry.get("error") else ""
    return f"{what}{text}{heard}{revealed}{error}"


def sheet(rec: dict, row: dict | None = None, why: list[str] | None = None) -> str:
    row = row or {}
    out = [f"# {rec.get('scenario')} / {rec.get('persona')} r{rec.get('rep')} — "
           f"{rec.get('model')} ({rec.get('language', 'English')})", ""]
    out.append(f"- strategy: {rec.get('strategy')}; condition: {rec.get('condition')}")
    out.append(f"- status {rec.get('status')}, outcome {rec.get('outcome')}, turns "
               f"{rec.get('total_turns')}, {rec.get('duration_s')} s; failure_reason: "
               f"{rec.get('failure_reason') or '-'}")
    if why:
        out.append("- flagged: " + "; ".join(why))
    for key in ("task_success", "outcome_ok", "content_ok", "ending_ok", "mechanisms_ok",
                "mechanisms_failed", "adh_check", "sim_excluded", "honoured_cancel"):
        if key in row and row[key] not in (None, ""):
            out.append(f"  - {key}: {row[key]}")
    out += ["", "## Task and plan", "", f"Task: {rec.get('goal', {}).get('task_description')}",
            "", "User's goal:", _block(rec.get("user_goal", "").strip()), ""]
    if rec.get("expect"):
        out += ["Expected:", _block(json.dumps(rec["expect"], ensure_ascii=False, indent=1),
                                    "json"), ""]
    if rec.get("events"):
        out += ["Planned events: " + "; ".join(
            f"t{e['turn']} {e['kind']} {e.get('text', '')}".strip() for e in rec["events"]), ""]
    if rec.get("faults"):
        out += ["Planned model failures: " + json.dumps(rec["faults"], ensure_ascii=False),
                "Failed calls: " + json.dumps(rec.get("faults_applied") or [],
                                              ensure_ascii=False), ""]
    out += ["Result data:", _block(json.dumps(rec.get("data"), ensure_ascii=False, indent=1),
                                   "json"), ""]
    if "model_calls" not in rec:
        out += ["> The model calls were not recorded (record_model_calls is off in this file): "
                "only the skills and their labels are shown.", ""]
    out.append("## Turn by turn")
    for t in turns_of(rec):
        out += ["", f"### Turn {t['turn']}" + (" (opening)" if t["turn"] == 0 else ""), ""]
        if t["turn"] > 0:
            out.append(f"**USER:** {_user_line(t['user'])}")
            out.append("")
        if t["skills"]:
            out.append("Tree: " + ", ".join(
                f"{s.get('skill')}→{s.get('label') or '·'}"
                + ("" if s.get("success", True) else f" FAILED ({s.get('message')})")
                + (f" [timeouts {s['llm_timeouts']}]" if s.get("llm_timeouts") else "")
                + (f" [skipped {s['llm_skipped']}]" if s.get("llm_skipped") else "")
                for s in t["skills"]))
            out.append("")
        for i, c in enumerate(t["calls"], 1):
            answer = c.get("answer") if c.get("error") is None else f"ERROR {c['error']}"
            out.append(f"{i}. `{c.get('question')}` in `{c.get('skill') or '-'}`, "
                       f"{c.get('latency_s')} s")
            out.append(f"   <details><summary>prompt</summary>\n\n{_block(c.get('prompt'))}\n"
                       f"\n</details>\n")
            out.append("   answer: " + ("(nothing)" if answer is None else ""))
            if answer is not None:
                out.append(_block(answer))
        if t["fillers"]:
            out.append(f"(fillers: {' / '.join(t['fillers'])})")
        if t["robot"] is not None:
            out.append(f"**ROBOT:** {t['robot']}")
    out += ["", "## Attribution (classification.yaml)", "",
            "First wrong turn: …  Class: C / P / S / M (OK if right).  Note: …", ""]
    return "\n".join(out)


# ==== SUMMARY ====


def summary(results: str) -> tuple[bool, list[str]]:
    path = os.path.join(results, "triage", SHEET)
    if not os.path.exists(path):
        return False, [f"No {path}: run `triage.py flag {results}` first"]
    with open(path, encoding="utf-8") as f:
        dialogues = [d for d in (yaml.safe_load(f) or {}).get("dialogues") or []
                     if not d.get("stale")]
    lines, holds = [], True
    by_model = defaultdict(list)
    for d in dialogues:
        by_model[d.get("model")].append(d)
    for model, ds in sorted(by_model.items()):
        counts = Counter((d.get("class") or "").strip().upper() or "unclassified" for d in ds)
        wrong = [c for c in counts if c not in CLASSES and c != "unclassified"]
        ok = (counts["C"] == 0 and counts["P"] == 0 and counts["unclassified"] == 0
              and not wrong)
        holds = holds and ok
        lines.append(f"{model}: {len(ds)} reviewed — " + ", ".join(
            f"{k} {counts[k]}" for k in (*CLASSES, "unclassified") if counts[k])
            + (f", unknown classes {wrong}" if wrong else ""))
        lines.append(f"   exit criterion (no C, no P, all classified): "
                     f"{'HOLDS' if ok else 'does NOT hold'}")
        for d in ds:
            if (d.get("class") or "").upper() in ("C", "P"):
                lines.append(f"   {d['class']} {d['id']} (turn {d.get('first_wrong_turn')}): "
                             f"{d.get('note', '')}")
    if not by_model:
        lines.append("Nothing flagged or sampled")
    return holds and bool(by_model), lines


# ==== CROSS-FAMILY COMPARISON ====


def _short(text, n=300) -> str:
    text = "" if text is None else " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def compare(results: str, models: list[str] | None = None) -> list[str]:
    groups = defaultdict(dict)
    for path, rec in records(results):
        if models and rec.get("model") not in models:
            continue
        key = (rec.get("scenario"), rec.get("persona"), rec.get("rep"), rec.get("condition"))
        groups[key][rec.get("model")] = rec
    written = []
    out_dir = os.path.join(results, "triage", "compare")
    for (scenario, persona, rep, condition), recs in sorted(groups.items()):
        if len(recs) < 2:
            continue
        names = sorted(recs)
        out = [f"# {scenario} / {persona} r{rep} ({condition}): {' vs '.join(names)}", ""]
        for name in names:
            r = recs[name]
            out.append(f"- {name}: {r.get('outcome')}, {r.get('total_turns')} turns, "
                       f"{r.get('failure_reason') or 'no failure reason'}")
        turns = {name: turns_of(recs[name]) for name in names}
        for k in range(max(len(t) for t in turns.values())):
            out += ["", f"## Turn {k}"]
            for name in names:
                if k >= len(turns[name]):
                    continue
                t = turns[name][k]
                out += ["", f"**{name}**", ""]
                if k > 0:
                    out.append(f"- USER: {_user_line(t['user'])}")
                for c in t["calls"]:
                    answer = c.get("answer") if c.get("error") is None else f"ERROR {c['error']}"
                    out.append(f"- `{c.get('question')}` → {_short(answer)}")
                if t["robot"] is not None:
                    out.append(f"- ROBOT: {t['robot']}")
        path = os.path.join(out_dir, f"{scenario}__{persona}__r{rep}__{condition}.md")
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        written.append(path)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("flag")
    p.add_argument("results")
    p.add_argument("--spot", type=int, default=1,
                   help="Passing dialogues sampled per strategy and language")
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("summary")
    p.add_argument("results")
    p = sub.add_parser("compare")
    p.add_argument("results")
    p.add_argument("--models", default=None, help="Comma separated (default: all)")
    p = sub.add_parser("sheet")
    p.add_argument("record")
    args = parser.parse_args(argv)
    if args.command == "flag":
        data = flag(os.path.abspath(args.results), args.spot, args.seed)
        ds = [d for d in data["dialogues"] if not d.get("stale")]
        spots = sum(1 for d in ds if d["why"] == ["spot-check of a pass"])
        print(f"{len(ds) - spots} flagged, {spots} spot-checks: "
              f"{os.path.join(args.results, 'triage', SHEET)} (sheets in triage/sheets/)")
        return 0
    if args.command == "summary":
        holds, lines = summary(os.path.abspath(args.results))
        print("\n".join(lines))
        return 0 if holds else 1
    if args.command == "compare":
        models = [m.strip() for m in args.models.split(",")] if args.models else None
        written = compare(os.path.abspath(args.results), models)
        print(f"{len(written)} comparisons in {os.path.join(args.results, 'triage', 'compare')}")
        return 0
    with open(args.record, encoding="utf-8") as f:
        print(sheet(json.load(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
