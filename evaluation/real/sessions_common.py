# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
What the sessions with real participants share, without ROS (real_sessions.py and the control
panel): the sessions file, the counterbalancing, the plan of each participant, the task cards
and where the trials are stored.

The counterbalancing, from the participant's number p (P01 is 1) and the n enabled conditions of
the sessions file, with m scenarios in the pool and t trials per condition:

  - the order of the conditions is a row of a balanced Latin square (Williams design: every
    condition is in every position, and follows every other, equally often): n rows when n is
    even (AB, BA), 2n when it is odd (the six orders of three). Consecutive participants take
    consecutive rows, so the orders are balanced every n (or 2n) participants;
  - the scenarios: condition c (its place in the file, 0...n-1) gets the scenarios
    (p - 1 + (c·t + j)·d) mod m, j = 0...t-1, with d = m // (n·t): over m consecutive
    participants every condition goes through every scenario once, and a participant never
    gets the same scenario twice;
  - which participant takes which row: every group of `rows` consecutive participants takes
    every row once, so the order of the conditions is balanced every 2 participants with two
    conditions (every 6 with three). The groups come in blocks of B = lcm(m, rows) participants
    (or 2B), and in each group the rows go in the order that, over the block, also puts every
    scenario in every position equally often (the first such order, in lexicographic order).
    When there is none (or too many to search), the rows go in their order, moved one more
    every B participants: participant p takes the row (p - 1 + (p - 1) // B) mod rows.

So every group of `rows` participants is balanced in the order of the conditions; every m, in
the scenario of each condition; and every block of B, also in the position of each scenario
(when the search finds an order; `real_sessions.py balance N` shows it).

`real_sessions.py balance N` prints how balanced the plans of N participants are.
"""

import functools
import hashlib
import itertools
import json
import math
import os
import re
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)
SESSIONS = os.path.join(HERE, "sessions_es.yaml")
OUT = os.path.expanduser("~/.ros/interaction_real")
CLOUD_PROVIDERS = ("gemini", "openai")     # as interaction_sim.cloud_llm
API_KEYS = {"openai": ("OPENAI_API_KEY",), "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
CONTROLS = ("bounded", "llm_led")
DETOUR_KINDS = ("off_task", "decline", "not_allowed")


# A voice of the Piper catalog (huggingface.co/rhasspy/piper-voices): NAME (es_ES-davefx-medium)
# or NAME:SPEAKER, the speaker of a voice with several (es_ES-sharvard-medium:1)
PIPER_VOICE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)(:\d+)?$")
# The settings of simple_hri's tts_piper that a test, a check or a configuration may change, with
# their allowed range: length_scale (speed: < 1 faster, > 1 slower), noise_scale (expressiveness:
# the variation of the voice) and noise_w_scale (rhythm: the variation of the phoneme durations).
# Unset: the voice's own (es_ES-sharvard-medium: 0.667 and 0.8; length_scale: simple_hri's 1.0)
PIPER_SETTINGS = {"length_scale": (0.5, 2.0), "noise_scale": (0.0, 1.5),
                  "noise_w_scale": (0.0, 1.5)}


def parse_voice(voice: str) -> tuple[str, int | None]:
    """(name, speaker) of --voice NAME[:SPEAKER] (speaker None: the voice's default);
    ValueError if it is not a catalog name."""
    if not PIPER_VOICE.match(voice or ""):
        raise ValueError(f"--voice {voice!r}: a Piper voice of the catalog, NAME or NAME:SPEAKER "
                         "(e.g. es_ES-davefx-medium, es_ES-sharvard-medium:1)")
    name, _, speaker = voice.partition(":")
    return name, int(speaker) if speaker else None


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def split_model(model: str) -> tuple[str, str]:
    """(provider, model) of a system model: provider/model for a cloud one, else Ollama. A local
    model's "@critical" (selective reasoning) is not part of its name on the server."""
    provider, sep, name = model.partition("/")
    if sep and provider in CLOUD_PROVIDERS:
        return provider, name
    return "ollama", model.partition("@")[0]


def reasoning_of(model: str) -> str:
    """The intelligence's `reasoning` for a system model: "critical" for "<model>@critical" (it
    may reason before the critical questions only; E0a's variants), else "none"."""
    variant = model.partition("@")[2]
    if variant not in ("", "critical"):
        raise ValueError(f"unknown variant '@{variant}' of {model.partition('@')[0]}")
    return "critical" if variant else "none"


def is_cloud(model: str) -> bool:
    return split_model(model)[0] != "ollama"


def file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def load_sessions(path: str = SESSIONS) -> dict:
    """The sessions file, checked, with its enabled conditions (in the file's order) and, in
    each scenario of the pool, the scenario of dialogues_es.yaml (`definition`)."""
    path = os.path.abspath(path)
    sessions = load(path)
    sessions["path"] = path
    sessions["hash"] = file_hash(path)
    defined = {s["id"]: s for s in load(os.path.join(
        os.path.dirname(path), sessions["scenarios_file"]))["scenarios"]}
    for scenario in sessions["scenarios"]:
        if scenario["id"] not in defined:
            raise ValueError(f"Scenario {scenario['id']} is not in {sessions['scenarios_file']}")
        if scenario.get("detour", {}).get("kind") not in DETOUR_KINDS:
            raise ValueError(f"Scenario {scenario['id']}: detour.kind is one of {DETOUR_KINDS}")
        scenario["definition"] = defined[scenario["id"]]
    ids = [s["id"] for s in sessions["scenarios"]]
    if len(set(ids)) != len(ids):
        raise ValueError("A scenario is twice in the pool")
    sessions["enabled"] = {name: c for name, c in sessions["conditions"].items()
                           if c.get("enabled", True)}
    for name, condition in sessions["enabled"].items():
        if condition.get("control") not in CONTROLS:
            raise ValueError(f"Condition {name}: control is one of {CONTROLS}")
    n, t = len(sessions["enabled"]), int(sessions.get("trials_per_condition", 1))
    if n < 1:
        raise ValueError("At least one condition must be enabled")
    if n * t > len(ids):
        raise ValueError(f"{n} conditions x {t} trials need {n * t} scenarios; the pool has {len(ids)}")
    return sessions


def participant(text: str) -> tuple[str, int]:
    """(id, number) of a participant written P07, p7 or 7: the id is P and two digits."""
    match = re.fullmatch(r"[Pp]?0*(\d+)", str(text).strip())
    if not match or int(match.group(1)) < 1:
        raise ValueError(f"A participant is P and a number from 1 (P01, P02...), not '{text}'")
    number = int(match.group(1))
    return f"P{number:02d}", number


def williams(n: int) -> list[list[int]]:
    """The rows of a balanced Latin square of n conditions (Williams design): n rows when n is
    even, 2n when it is odd (each row and its reverse)."""
    base, low, high = [0], 1, n - 1
    while len(base) < n:
        base.append(low)
        low += 1
        if len(base) < n:
            base.append(high)
            high -= 1
    rows = [[(b + r) % n for b in base] for r in range(n)]
    if n % 2 and n > 1:
        rows += [list(reversed(row)) for row in rows]
    return rows


SEARCH_LIMIT = 20000   # orders of a block tried before taking the rows in their order


@functools.lru_cache(maxsize=None)
def block_rows(n: int, m: int, t: int) -> tuple[tuple | None, int]:
    """(the Williams row of each participant of a block, the block size B); None for the rows
    when no order of them also balances the position of each scenario over the block. Every
    group of len(rows) consecutive participants of the block takes every row once."""
    rows = williams(n)
    step = m // (n * t)
    perms = list(itertools.permutations(range(len(rows))))
    for size in (math.lcm(m, len(rows)), 2 * math.lcm(m, len(rows))):
        groups = size // len(rows)
        for tried, choice in enumerate(itertools.product(perms, repeat=groups)):
            if tried >= SEARCH_LIMIT:
                break
            order = [row for group in choice for row in group]
            count = {}
            for i, row in enumerate(order):
                for pos, (c, j) in enumerate((c, j) for c in rows[row] for j in range(t)):
                    key = ((i + (c * t + j) * step) % m, pos)
                    count[key] = count.get(key, 0) + 1
            if len(count) == m * n * t and len(set(count.values())) == 1:
                return tuple(order), size
    return None, math.lcm(m, len(rows))


def make_plan(sessions: dict, number: int) -> dict:
    """The trials of participant `number` (from 1), in the order they are run."""
    conditions = list(sessions["enabled"])
    pool = sessions["scenarios"]
    n, m = len(conditions), len(pool)
    t = int(sessions.get("trials_per_condition", 1))
    rows = williams(n)
    i = number - 1
    order, size = block_rows(n, m, t)
    row = order[i % size] if order else (i + i // size) % len(rows)
    step = m // (n * t)
    trials = []
    for c in rows[row]:
        for j in range(t):
            trials.append(details(sessions, {"k": len(trials) + 1, "condition": conditions[c],
                                             "scenario": pool[(i + (c * t + j) * step) % m]["id"]}))
    return {"latin_square_row": row + 1, "order": [conditions[c] for c in rows[row]],
            "trials": trials}


def details(sessions: dict, trial: dict) -> dict:
    """A trial of a plan (k, scenario, condition) with what the sessions file says now of its
    condition (control, model) and scenario (title, card, detour): a plan keeps the assignment,
    and a trial runs with the current file (e.g. once the cloud model is pinned, or a card
    reworded)."""
    if trial["condition"] not in sessions["enabled"]:
        raise ValueError(f"Condition {trial['condition']} is not enabled in the sessions file")
    scenarios = {s["id"]: s for s in sessions["scenarios"]}
    if trial["scenario"] not in scenarios:
        raise ValueError(f"Scenario {trial['scenario']} is not in the sessions file")
    condition, scenario = sessions["enabled"][trial["condition"]], scenarios[trial["scenario"]]
    return {"k": trial["k"], "scenario": scenario["id"], "title": scenario["title"],
            "strategy": scenario["definition"].get("strategy"),
            "condition": trial["condition"], "control": condition["control"],
            "model": condition["model"], "card": list(scenario["card"]),
            "detour": dict(scenario["detour"])}


def assignment(plan: dict) -> list[tuple]:
    return [(t["k"], t["scenario"], t["condition"]) for t in plan["trials"]]


def plan_path(pid: str, out: str = OUT) -> str:
    return os.path.join(out, pid, "plan.yaml")


def read_plan(pid: str, out: str = OUT) -> dict | None:
    path = plan_path(pid, out)
    return load(path) if os.path.isfile(path) else None


def trial_name(trial: dict) -> str:
    return f"trial_{trial['k']}_{trial['scenario']}_{trial['condition']}"


def record_path(pid: str, trial: dict, out: str = OUT) -> str:
    return os.path.join(out, pid, trial_name(trial) + ".json")


def stored(pid: str, out: str = OUT) -> dict:
    """{k: path of its record} of the trials of a participant stored."""
    plan = read_plan(pid, out) or {"trials": []}
    return {t["k"]: record_path(pid, t, out) for t in plan["trials"]
            if os.path.isfile(record_path(pid, t, out))}


def participants(out: str = OUT) -> list[str]:
    if not os.path.isdir(out):
        return []
    return sorted(d for d in os.listdir(out) if re.fullmatch(r"P\d+", d)
                  and os.path.isfile(plan_path(d, out)))


def write_plan(sessions: dict, text: str, out: str = OUT, replace: bool = False) -> tuple[dict, str]:
    """The plan of a participant: created if it does not exist; an existing one is kept (and a
    note says if the sessions file would now give another order or scenarios), unless replace,
    which is refused when a trial of the participant is stored. Returns (plan, what was done)."""
    pid, number = participant(text)
    new = {"participant": pid, "number": number, "sessions_file": os.path.relpath(
        sessions["path"], EVAL), "sessions_hash": sessions["hash"], **make_plan(sessions, number)}
    old = read_plan(pid, out)
    if old is not None:
        same = assignment(old) == assignment(new)
        if not replace or same:
            return old, ("existing plan" + ("" if same else
                         ": the sessions file now gives another plan (plan --replace to take it, "
                         "only before the first trial)"))
        if stored(pid, out):
            raise ValueError(f"{pid} has trials stored: the plan is not replaced")
    new["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(plan_path(pid, out)), exist_ok=True)
    with open(plan_path(pid, out), "w", encoding="utf-8") as f:
        yaml.safe_dump(new, f, sort_keys=False, allow_unicode=True, width=100)
    return new, "plan replaced" if old is not None else "plan created"


def card_text(trial: dict, sessions: dict | None = None) -> str:
    """The task card of a trial, as the participant reads it (in the language of the sessions
    file)."""
    footer = (sessions or {}).get("card_footer") or []
    return "\n".join([trial["title"].upper(), ""] + trial["card"] + [
        "", (sessions or {}).get("detour_label", "Also:"), trial["detour"]["text"]] + ([""] + footer if footer else []))


def balance(sessions: dict, count: int) -> str:
    """How balanced the plans of participants 1...count are: each scenario under each condition,
    each condition in each position, each scenario in each position, and which condition
    follows which."""
    conditions = list(sessions["enabled"])
    scenarios = [s["id"] for s in sessions["scenarios"]]
    plans = [make_plan(sessions, p)["trials"] for p in range(1, count + 1)]
    positions = range(1, len(plans[0]) + 1)

    def table(title, rows, columns, count_of):
        width = max(len(str(r)) for r in rows) + 2
        lines = [title, " " * width + "".join(f"{str(c):>14s}" for c in columns)]
        for r in rows:
            lines.append(f"{str(r):{width}s}" + "".join(f"{count_of(r, c):14d}" for c in columns))
        return "\n".join(lines)

    flat = [t for plan in plans for t in plan]
    pairs = [(a["condition"], b["condition"]) for plan in plans for a, b in zip(plan, plan[1:])]
    return "\n\n".join([
        f"{count} participants, conditions {conditions}, "
        f"{sessions.get('trials_per_condition', 1)} trial(s) per condition",
        table("Scenario x condition", scenarios, conditions,
              lambda s, c: sum(t["scenario"] == s and t["condition"] == c for t in flat)),
        table("Condition x position", conditions, positions,
              lambda c, k: sum(t["condition"] == c and t["k"] == k for t in flat)),
        table("Scenario x position", scenarios, positions,
              lambda s, k: sum(t["scenario"] == s and t["k"] == k for t in flat)),
        table("Condition (row) followed by condition (column)", conditions, conditions,
              lambda a, b: pairs.count((a, b))),
    ])


# ------------------------------------------------------------------ review of the transcripts
# Before anything of a real session goes to the judge (or to the annotators), a member of the team
# reads it and replaces any real personal data the participant said by mistake (the ethics
# protocol). The replacement runs over the whole record and the logs of its trial, and the record
# is marked as reviewed; analysis/judge_runs.py refuses the records of real sessions without it.
PLACEHOLDER = "[DATO]"


def replace_in_text(text: str, pairs: list) -> tuple:
    """(text with every (old, new) replaced, ignoring case, number of replacements)."""
    count = 0
    for old, new in pairs:
        text, n = re.subn(re.escape(old), lambda m, new=new: new, text, flags=re.IGNORECASE)
        count += n
    return text, count


def review_record(record: dict, pairs: list, reviewer: str, when: str) -> tuple:
    """(the record with the replacements and marked as reviewed, number of replacements). What
    was replaced is not kept."""
    text, count = replace_in_text(json.dumps(record, ensure_ascii=False), pairs)
    out = json.loads(text)
    out["reviewed"] = {"by": reviewer, "time": when, "replacements": count}
    return out, count


def is_reviewed(record: dict) -> bool:
    return bool((record.get("reviewed") or {}).get("by"))
