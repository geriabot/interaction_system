# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Checks of the phase A datasets (datasets/phase_a/*.json), run before any model:

  - the fields of every case and the domain of its labels;
  - ids unique, both languages present, at least MIN_CASES cases per language and at least a
    third of them traps;
  - no content of an evaluation scenario (evaluation/experiments/scenarios/*.yaml): no sequence
    of SHINGLE words in common with them;
  - no example of the system's prompts (config/prompts.yaml): no user text equal to a quoted
    example of the prompts, nor containing one of three words or more.

and of the situations of bench_wording (datasets/wording.json, not a phase A dataset): the
fields, both languages, and no content of an evaluation scenario nor of a phase A case (no
sequence of SHINGLE words in common), nor a quoted example of the prompts in what the user says.

    python3 -m core.dataset_check            (from evaluation/offline; exit status 1 on problems)
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import unicodedata

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
OFFLINE = os.path.normpath(os.path.join(_HERE, ".."))
DATASETS = os.path.join(OFFLINE, "datasets", "phase_a")
WORDING = os.path.join(OFFLINE, "datasets", "wording.json")
SCENARIOS = os.path.normpath(os.path.join(OFFLINE, "..", "experiments", "scenarios"))
PROMPTS = os.path.normpath(os.path.join(
    OFFLINE, "..", "..", "interaction_system_core", "interaction_intelligence", "config",
    "prompts.yaml"))

MIN_CASES = 25
SHINGLE = 6

TURN_ACTS = ("values", "question", "skip", "repeat", "other")
COMMON = ("id", "language", "description", "category", "trap")

# file stem -> (required fields, label field, allowed labels or None for a boolean)
SPEC = {
    "turn_act": (("task", "slot_schema", "user_text", "expected_act", "expected_extractions"),
                 "expected_act", TURN_ACTS),
    "confirmation": (("task", "slot_schema", "frame_filled", "user_text", "expected_intent"),
                     "expected_intent", ("confirms", "corrects", "question", "unclear")),
    "quiz_grading": (("question", "expected_answer", "given_answer", "expected_kind",
                      "ground_truth_correct"), "expected_kind", ("answer", "repeat")),
    "teach_back": (("key_points", "user_text", "expected_kind"), "expected_kind",
                   ("restates", "question", "declines", "not_understood", "other")),
    "qa_answer": (("task", "material", "user_text", "expected_kind"), "expected_kind",
                  ("answerable", "not_in_material", "off_task", "comment", "done", "repeat",
                   "other")),
    "qa_verify": (("material", "answer", "expected_supported"), "expected_supported", None),
    "scale_answer": (("item", "options", "user_text", "expected_kind"), "expected_kind",
                     ("option", "ambiguous", "meaning", "refuses", "repeat", "other")),
    "step_turn": (("steps", "step_index", "user_text", "expected_intent"), "expected_intent",
                  ("done", "question", "problem", "repeat", "back", "skip", "unclear")),
    "roleplay_turn": (("scene", "user_text", "expected_intent"), "expected_intent",
                      ("continue", "repeat", "coach", "end", "scene_end")),
    "cancel_detect": (("user_text", "expected_cancel"), "expected_cancel", None),
    "cancel_reply": (("user_text", "expected_answer"), "expected_answer",
                     ("yes", "no", "finished", "other")),
    "wrong_check": (("answer", "user_text", "expected_wrong_check"), "expected_wrong_check", None),
}

# The fields with what the user says (checked against the examples of the prompts)
USER_FIELDS = ("user_text", "given_answer", "answer")

# bench_wording: the lines it asks for (its ASK table) and the fields of every situation
WORDING_PROMPTS = ("generate_response", "opening", "resume_opening", "ask_confirmation",
                   "response_to_intent", "ask_cancel_confirmation", "cancel_confirmed_response",
                   "quiz_feedback", "quiz_summary", "understanding_confirmed",
                   "procedure_summary", "qa_opening")
WORDING_FIELDS = ("id", "language", "description", "category", "prompt", "asks", "max_words")
WORDING_USER_FIELDS = ("user_text", "given")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strings(obj) -> list[str]:
    """Every string leaf of a JSON/YAML value."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings(v)]
    return []


def shingles(text: str, n: int = SHINGLE) -> set[tuple]:
    words = normalize(text).split()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def scenario_shingles() -> set[tuple]:
    out: set[tuple] = set()
    for path in sorted(glob.glob(os.path.join(SCENARIOS, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            for s in strings(yaml.safe_load(f)):
                out |= shingles(s)
    return out


def prompt_examples() -> set[str]:
    """The quoted phrases of the prompt templates, normalized (placeholders and field names
    of one word are dropped by the callers' length rules)."""
    with open(PROMPTS, encoding="utf-8") as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]
    out = set()
    for template in prompts.values():
        for quoted in re.findall(r'"([^"\n{}]{2,120})"', template):
            norm = normalize(quoted)
            if norm and "_" not in quoted and not quoted.startswith("["):
                out.add(norm)
    return out


def check_case(stem: str, case: dict) -> list[str]:
    problems = []
    fields, label, allowed = SPEC[stem]
    for key in COMMON + fields:
        if key not in case:
            problems.append(f"missing '{key}'")
    if problems:
        return problems
    if case["language"] not in ("en", "es"):
        problems.append(f"language '{case['language']}'")
    value = case[label]
    if allowed is None:
        if not isinstance(value, bool):
            problems.append(f"'{label}' must be true or false")
    elif value not in allowed:
        problems.append(f"'{label}' = {value!r} not in {allowed}")
    problems += _check_specific(stem, case)
    return problems


def _check_specific(stem: str, case: dict) -> list[str]:
    problems = []
    if stem in ("turn_act", "confirmation"):
        slots = {s["name"]: s for s in case["slot_schema"].get("slots", [])}
        values = list(case.get("expected_extractions", [])) if stem == "turn_act" else [
            {"slot": k, "value": v} for k, v in (case.get("expected_corrections") or {}).items()]
        for e in values:
            if e.get("slot") not in slots:
                problems.append(f"slot '{e.get('slot')}' not in the schema")
                continue
            canon = [str(c).lower() for c in slots[e["slot"]].get("canonical_values", [])]
            items = e["value"].get("items", []) if isinstance(e["value"], dict) else (
                e["value"] if isinstance(e["value"], list) else [e["value"]])
            for item in items:
                if canon and str(item).lower() not in canon:
                    problems.append(f"value {item!r} of '{e['slot']}' is not canonical")
        if stem == "turn_act" and case["expected_act"] == "values" and not values:
            problems.append("act 'values' without expected_extractions")
        if stem == "turn_act" and case["expected_act"] != "values" and values:
            problems.append(f"act '{case['expected_act']}' with expected_extractions")
        if stem == "confirmation" and case["expected_intent"] == "corrects" and not values:
            problems.append("intent 'corrects' without expected_corrections")
    if stem == "teach_back":
        n = len(case["key_points"])
        if case["expected_kind"] == "restates":
            points = case.get("expected_points") or {}
            if sorted(points) != sorted(str(i) for i in range(1, n + 1)):
                problems.append("expected_points must give every key point (1..n)")
            if any(s not in ("correct", "incomplete", "wrong", "missing")
                   for s in points.values()):
                problems.append("expected_points statuses: correct | incomplete | wrong | missing")
    if stem == "scale_answer":
        opt = case.get("expected_option")
        if case["expected_kind"] == "option":
            if not isinstance(opt, int) or not 1 <= opt <= len(case["options"]):
                problems.append("expected_option must be 1..len(options)")
        elif opt is not None:
            problems.append("expected_option must be null unless the kind is 'option'")
    if stem == "step_turn":
        if not 0 <= case["step_index"] < len(case["steps"]):
            problems.append("step_index out of range")
    if stem == "roleplay_turn":
        for key in ("scenario", "role"):
            if not case["scene"].get(key):
                problems.append(f"scene without '{key}'")
    if stem in ("qa_answer", "qa_verify"):
        if not case["material"] or any("name" not in r or "content" not in r
                                       for r in case["material"]):
            problems.append("material must be a list of {name, content}")
    if stem == "qa_answer":
        facts = case.get("key_facts")
        if case["expected_kind"] == "answerable":
            if not facts or not all(isinstance(f, str) and f.strip() for f in facts):
                problems.append("an answerable case needs key_facts: the fact(s) a right "
                                "answer states")
        elif facts or case.get("expected_polarity"):
            problems.append("key_facts and expected_polarity are for answerable cases only")
        if case.get("expected_polarity") not in (None, "yes", "no"):
            problems.append("expected_polarity must be yes or no")
    if stem == "wrong_check" and case["category"].startswith("wrong_") is not case[
            "expected_wrong_check"]:
        problems.append("the categories wrong_* are the wrong checks (expected_wrong_check true), "
                        "and only they")
    if stem == "quiz_grading" and case["expected_kind"] == "repeat" and case[
            "ground_truth_correct"]:
        problems.append("a reply that is not an answer cannot be correct")
    return problems


def check_file(path: str, scen: set, examples: set) -> list[str]:
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem not in SPEC:
        return [f"{stem}: unknown dataset (no SPEC entry)"]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = []
    if "_instructions" not in data or "question" not in data:
        problems.append(f"{stem}: '_instructions' and 'question' are required")
    cases = data.get("cases", [])
    ids = [c.get("id") for c in cases]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{stem}: duplicate id {dup}")
    long_examples = {e for e in examples if len(e.split()) >= 3}
    for case in cases:
        cid = f"{stem}:{case.get('id')}"
        problems += [f"{cid}: {p}" for p in check_case(stem, case)]
        texts = [s for k, v in case.items() if k not in ("id", "category", "description")
                 for s in strings(v)]
        common = set().union(*(shingles(t) for t in texts)) & scen if texts else set()
        if common:
            problems.append(f"{cid}: shares text with a scenario: "
                            f"{' '.join(sorted(common)[0])!r}")
        for key in USER_FIELDS:
            if key not in case:
                continue
            norm = normalize(case[key])
            if norm in examples:
                problems.append(f"{cid}: {key} is an example of the prompts: {case[key]!r}")
            for ex in long_examples:
                if f" {ex} " in f" {norm} ":
                    problems.append(f"{cid}: {key} contains the prompt example {ex!r}")
    for lang in ("en", "es"):
        mine = [c for c in cases if c.get("language") == lang]
        if len(mine) < MIN_CASES:
            problems.append(f"{stem}: {len(mine)} cases in '{lang}' (at least {MIN_CASES})")
        traps = sum(1 for c in mine if c.get("trap"))
        if mine and traps * 3 < len(mine):
            problems.append(f"{stem}: {traps} traps of {len(mine)} in '{lang}' (a third at least)")
    return problems


def counts() -> dict:
    """{dataset: {language: (cases, traps)}}."""
    out = {}
    for path in sorted(glob.glob(os.path.join(DATASETS, "*.json"))):
        with open(path, encoding="utf-8") as f:
            cases = json.load(f).get("cases", [])
        stem = os.path.splitext(os.path.basename(path))[0]
        out[stem] = {lang: (sum(1 for c in cases if c.get("language") == lang),
                            sum(1 for c in cases if c.get("language") == lang and c.get("trap")))
                     for lang in ("en", "es")}
    return out


def phase_a_shingles() -> set[tuple]:
    """The word sequences of every phase A case (its inputs and expectations)."""
    out: set[tuple] = set()
    for path in sorted(glob.glob(os.path.join(DATASETS, "*.json"))):
        with open(path, encoding="utf-8") as f:
            for case in json.load(f).get("cases", []):
                for k, v in case.items():
                    if k not in ("id", "category", "description"):
                        for s in strings(v):
                            out |= shingles(s)
    return out


def check_wording(path: str = WORDING, scen: set | None = None, phase_a: set | None = None,
                  examples: set | None = None) -> list[str]:
    """The situations of bench_wording: invented, never the content of a scenario, of a phase A
    case or of an example of the prompts."""
    scen = scenario_shingles() if scen is None else scen
    phase_a = phase_a_shingles() if phase_a is None else phase_a
    examples = prompt_examples() if examples is None else examples
    long_examples = {e for e in examples if len(e.split()) >= 3}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = []
    if "_instructions" not in data or "question" not in data:
        problems.append("wording: '_instructions' and 'question' are required")
    cases = data.get("cases", [])
    ids = [c.get("id") for c in cases]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"wording: duplicate id {dup}")
    for case in cases:
        cid = f"wording:{case.get('id')}"
        problems += [f"{cid}: missing '{k}'" for k in WORDING_FIELDS if k not in case]
        if case.get("language") not in ("en", "es"):
            problems.append(f"{cid}: language {case.get('language')!r}")
        if case.get("prompt") not in WORDING_PROMPTS:
            problems.append(f"{cid}: prompt {case.get('prompt')!r} not in {WORDING_PROMPTS}")
        if case.get("asks") not in (True, False, None):
            problems.append(f"{cid}: 'asks' must be true, false or null")
        if case.get("slot_kind") not in (None, "single", "list"):
            problems.append(f"{cid}: 'slot_kind' must be single, list or null")
        texts = [s for k, v in case.items() if k not in ("id", "category", "description")
                 for s in strings(v)]
        mine = set().union(*(shingles(t) for t in texts)) if texts else set()
        if mine & scen:
            problems.append(f"{cid}: shares text with a scenario: "
                            f"{' '.join(sorted(mine & scen)[0])!r}")
        if mine & phase_a:
            problems.append(f"{cid}: shares text with a phase A case: "
                            f"{' '.join(sorted(mine & phase_a)[0])!r}")
        said = [case[k] for k in WORDING_USER_FIELDS if k in case]
        said += [t["content"] for t in case.get("history", []) if t.get("role") == "user"]
        for text in said:
            norm = normalize(text)
            if norm in examples or any(f" {ex} " in f" {norm} " for ex in long_examples):
                problems.append(f"{cid}: {text!r} is or contains an example of the prompts")
    for lang in ("en", "es"):
        if not any(c.get("language") == lang for c in cases):
            problems.append(f"wording: no case in '{lang}'")
    return problems


def check_all(paths: list[str] | None = None) -> list[str]:
    paths = paths or sorted(glob.glob(os.path.join(DATASETS, "*.json")))
    scen, examples = scenario_shingles(), prompt_examples()
    problems = []
    for path in paths:
        problems += check_file(path, scen, examples)
    return problems


def main() -> None:
    paths = sys.argv[1:] or None
    problems = check_all(paths)
    if paths is None:
        problems += check_wording()
    for stem, by_lang in counts().items():
        print(f"  {stem:<15} " + "  ".join(f"{lang}: {n} cases, {t} traps"
                                           for lang, (n, t) in by_lang.items()))
    for p in problems:
        print(f"  PROBLEM {p}")
    print(f"  {len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
