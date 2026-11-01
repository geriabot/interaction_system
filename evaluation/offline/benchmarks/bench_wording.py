# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The form of the spoken lines: invented situations rendered through the real prompt builder and
prompts.yaml, asked with the production client methods that write a line the user hears (the
next question of slot filling with a single-value slot and with a list slot, a recovery line,
the openings, the read-back, the closings, the cancellation question and its closing, the quiz
feedback and summary...), in English and Spanish, and checked automatically.

A REPORTED measure of E0a, never a selection criterion: the form of a line is not a safety
error (the Behavior Trees decide what is asked and when; the LLM only words it), so no check is
critical and the benchmark is not part of the safety gate (core/gate.py). Each check gives a
pass rate over the lines it applies to:

  question_form    a line that asks ends in a question mark; a closing asks nothing (no "?")
  single_value     a question for a single-value slot does not invite several values
  list_invitation  a question for a list slot says that several values may be given (the rule
                   the code adds only for list slots)
  no_slot_name     no internal slot name is spoken: the names with an underscore (raw, or with
                   spaces) and the literal renderings of the case (slot_phrases, e.g. "nombre
                   del cliente"); one-word names are ordinary words and are not checked
  second_person    the user is not referred to in the third person (the case's third_person
                   phrases, e.g. "the customer", "el cliente" (also "del cliente", "al
                   cliente"), and "the user" / "el usuario")
  informal         Spanish only: no "usted" register (heuristic below)
  max_length       the line has at most the words the prompt allows (cases with max_words)

The "usted" heuristic (FORMAL_ES) is lexical and conservative: it flags forms that, in a line
addressed to the user, are the formal register (or a third-person reference, also a defect):
"usted"; "desea(n)"; a question opening with a third-person verb ("¿Quiere...?", "¿Puede...?",
"¿Confirma...?"); "me puede decir", "puede indicarme" (puede/podría with "me"/"nos"); "quiere" /
"desea" / "prefiere" + infinitive ("¿Seguro que quiere cancelar?"; not "quiere decir"); "le
gustaría", "le parece"...; the usted imperatives "dígame", "indíqueme", "disculpe", "elija",
"confirme"...; and "su"/"sus" before a word of the user's own data ("su nombre", "su pedido").
"puede" alone ("puede tardar un poco") and "su" before other words are not flagged, to avoid
false positives; a formal line built only from other forms passes (a false negative). A line the
model did not write (no call answered: the client says its fixed phrase) is counted as
no_answer, not checked.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import context, main, run_cases  # noqa: E402

from interaction_intelligence.dialog_frame import DialogFrame  # noqa: E402

DATASET = "datasets/wording.json"

CHECKS = ("question_form", "single_value", "list_invitation", "no_slot_name", "second_person",
          "informal", "max_length")

# An invitation to give several values or more items
_SEVERAL = {
    "en": re.compile(r"\b(several|multiple|more than one|as many|any others?|anything else|"
                     r"other items?|more items?|add (more|another|anything)|or more)\b", re.I),
    "es": re.compile(r"\b(varias|varios|más de un[ao]?|tant[ao]s como|algo más|alguna más|"
                     r"alguno más|añadir (más|algo|otr[ao]s?)|otr[ao]s? más|o más)\b", re.I),
}

_THIRD_DEFAULT = {"en": ("the user",), "es": ("el usuario", "la usuaria", "del usuario")}

_DATA_NOUNS = ("nombre|apellidos?|pedido|reserva|respuesta|elección|opción|teléfono|correo|"
               "dirección|datos|cita|edad|preferencias?|alquiler|encargo|prenda|arreglos?")
FORMAL_ES = [
    re.compile(p, re.I) for p in (
        r"\busted(es)?\b",
        r"\bdesean?\b",
        r"¿\s*(quiere|prefiere|necesita|tiene|puede|podría|sabe|recuerda|confirma|desea)\b",
        r"\b(me|nos)\s+(puede|podría)\s+\w+(ar|er|ir)\b",
        r"\b(puede|podría)\s+\w+(ar|er|ir)(me|nos)\b",
        r"\b(quiere|desea|prefiere)\s+(?!decir\b)\w+(ar|er|ir)\b",
        r"\ble\s+(gustaría|apetece|parece|interesa|importa|viene)\b",
        r"\b(dígame|indíqueme|cuénteme|explíqueme|avíseme|repítame|perdone|disculpe|elija|"
        r"escoja|confirme)\b",
        rf"\bsus?\s+({_DATA_NOUNS})\b",
    )
]

# The client method asked for each kind of line, as its skill asks it
ASK = {
    "generate_response": lambda llm, c, f: llm.generate_response(
        c["task"], f, c["user_text"], c.get("extracted", []), [], context(c), None),
    "opening": lambda llm, c, f: llm.get_opening(c["task"], f, context(c)),
    "resume_opening": lambda llm, c, f: llm.get_resume_opening(
        c["task"], f, f.next_slot(), context(c)),
    "ask_confirmation": lambda llm, c, f: llm.get_confirmation_question(c["task"], f, context(c)),
    "response_to_intent": lambda llm, c, f: llm.response_to_intent(
        c["task"], "confirms", {}, f, context(c)),
    "ask_cancel_confirmation": lambda llm, c, f: llm.ask_cancel_confirmation(
        c["task"], context(c)),
    "cancel_confirmed_response": lambda llm, c, f: llm.cancel_confirmed_response(
        c["task"], f, context(c)),
    "quiz_feedback": lambda llm, c, f: llm.evaluate_quiz_answer(
        c["question"], c["expected"], c["given"])["feedback"],
    "quiz_summary": lambda llm, c, f: llm.quiz_summary(
        c["task"], c["correct"], c["total"], c.get("wrongs", [])),
    "understanding_confirmed": lambda llm, c, f: llm.understanding_confirmed_response(
        c["task"], context(c)),
    "procedure_summary": lambda llm, c, f: llm.procedure_summary(
        c["task"], c["completed"], c["total"], c.get("not_done", []), context(c)),
    "qa_opening": lambda llm, c, f: llm.qa_opening(c["task"], context(c)),
}


def frame_of(case: dict) -> DialogFrame | None:
    """The form of a slot filling case: its schema, the values already given and the
    conversation so far (with the user's turn, as the skill adds it before asking)."""
    if "slot_schema" not in case:
        return None
    import json

    f = DialogFrame.from_slots_json(json.dumps(case["slot_schema"]))
    if case.get("frame_filled"):
        f.try_update(case["frame_filled"])
    for turn in case.get("history", []):
        f.add_turn(turn["role"], turn["content"])
    if case.get("user_text"):
        f.add_turn("user", case["user_text"])
    return f


def _answered(llm) -> int:
    """The calls of the counting transport that the model answered (CountingInfer)."""
    counter = getattr(llm, "_infer", None)
    return getattr(counter, "calls", 0) - getattr(counter, "no_answer", 0)


def ask(llm, case: dict) -> dict:
    before = _answered(llm)
    line = ASK[case["prompt"]](llm, case, frame_of(case))
    # No call answered: the line is the client's fixed fallback phrase, not the model's
    return {"line": line, "no_answer": _answered(llm) == before}


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(_norm(phrase))}(?!\w)", _norm(text)) is not None


def slot_names(case: dict) -> list[str]:
    """The internal names that must not be spoken: the slot names with an underscore (raw and
    with spaces) and the case's literal renderings."""
    names = []
    for s in case.get("slot_schema", {}).get("slots", []):
        if "_" in s["name"]:
            names += [s["name"], s["name"].replace("_", " ")]
    return names + list(case.get("slot_phrases", []))


def third_person(case: dict) -> list[str]:
    """The phrases that refer to the user in the third person: the case's and the defaults; in
    Spanish "el X" also as "del X" and "al X" (the contractions)."""
    lang = case["language"]
    phrases = list(case.get("third_person", [])) + list(_THIRD_DEFAULT[lang])
    if lang == "es":
        phrases += [f"{c} {p[3:]}" for p in phrases if p.startswith("el ") for c in ("del", "al")]
    return list(dict.fromkeys(phrases))


def formal_es(line: str) -> list[str]:
    """The usted forms of a Spanish line (the matched text of each pattern)."""
    return [m.group(0) for p in FORMAL_ES for m in [p.search(line)] if m]


def checks(case: dict, line: str) -> dict:
    """{check: True (passed) | False (failed) | None (does not apply)} and the evidence."""
    lang = case["language"]
    text = line.strip().rstrip("\"'»”)").strip()
    out: dict = {k: None for k in CHECKS}
    evidence: dict = {}
    if case.get("asks") is True:
        out["question_form"] = text.endswith("?")
    elif case.get("asks") is False:
        out["question_form"] = "?" not in text
    kind = case.get("slot_kind")
    several = _SEVERAL[lang].search(line)
    if kind == "single":
        out["single_value"] = several is None
        if several:
            evidence["single_value"] = several.group(0)
    elif kind == "list":
        out["list_invitation"] = several is not None
    echoed = [n for n in slot_names(case) if _contains(line, n)]
    out["no_slot_name"] = not echoed
    if echoed:
        evidence["no_slot_name"] = echoed
    third = [p for p in third_person(case) if _contains(line, p)]
    out["second_person"] = not third
    if third:
        evidence["second_person"] = third
    if lang == "es":
        formal = formal_es(line)
        out["informal"] = not formal
        if formal:
            evidence["informal"] = formal
    if case.get("max_words"):
        out["max_length"] = len(line.split()) <= case["max_words"]
    return {"checks": out, "evidence": evidence}


def score(case: dict, got: dict) -> dict:
    if got.get("no_answer") or not str(got.get("line", "")).strip():
        return {"correct": False, "critical": None, "flags": ["no_answer"],
                "checks": {k: None for k in CHECKS}, "evidence": {}}
    result = checks(case, got["line"])
    failed = [k for k, v in result["checks"].items() if v is False]
    return {"correct": not failed, "critical": None, "flags": failed, **result}


def check_rates(records: list[dict]) -> dict:
    """Per check: the lines it applies to, those that passed and the pass rate; also flat as
    rate_<check> (summary.csv of run_models.py)."""
    out: dict = {"checks": {}}
    for name in CHECKS:
        applied = [r["checks"][name] for r in records
                   if r.get("checks", {}).get(name) is not None]
        passed = sum(1 for v in applied if v)
        rate = round(passed / len(applied), 4) if applied else None
        out["checks"][name] = {"lines": len(applied), "passed": passed, "rate": rate}
        out[f"rate_{name}"] = rate
    return out


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    results = run_cases("wording", cfg, dataset_path, ask, score, verbose, infer_fn,
                        extra=check_rates)
    for lang in results["languages"]:
        results["by_language"][lang].update(
            check_rates([r for r in results["cases"] if r["language"] == lang]))
    print("  pass rate per check: " + "  ".join(
        f"{k} {v['rate']}" for k, v in results["global"]["checks"].items()))
    if verbose:
        for r in results["cases"]:
            if r["flags"]:
                print(f"    {r['id']}: {', '.join(r['flags'])}  {r.get('evidence', {})}  "
                      f"{(r['got'] or {}).get('line', '')!r}")
    return results


if __name__ == "__main__":
    main("wording", run, DATASET)
