# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
A web page, on this computer only, for the hand reviews of the evaluation (doc/EXPERIMENTS.md,
"Hand reviews"), made for people who know nothing about the system: a start page asks who you are
and what you are doing, a confirmation shows the assignment, a short tutorial comes before the
first item, every item must be answered before the next one, and a last screen lists what is left
or doubtful. Spanish by default, English with one click. No LLM call, no network; every answer is
saved to a file as it is given, so a session can be closed at any time and resumed.

    python3 evaluation/panels/review_panel.py                       # the start page, any task
    python3 evaluation/panels/review_panel.py annotate --annotator "Name" --sheet a --lang es
    python3 evaluation/panels/review_panel.py dataset --reviewer "Name" [--all] [--language es]
    python3 evaluation/panels/review_panel.py dataset --adjudicate --reviewer "Name" [--gate gate.json]
    python3 evaluation/panels/review_panel.py adherence <results of an experiment> --auditor "Name"
    python3 evaluation/panels/review_panel.py dataset report [--adjudicate]
    python3 evaluation/panels/review_panel.py dataset apply --reviewer "Name" [--adjudicate] [--dry-run]
    python3 evaluation/panels/review_panel.py adherence report <results> [--auditor "Name"]

With a task on the command line the page is locked to that assignment (send each person one exact
command). The tasks:

  annotate    E0b: the robot utterances of one annotation sheet (analysis/judge_validation.py
              `sheets`: results/e0b/<en|es>/e0/sheet_a.csv or sheet_b.csv), blind (no judge label,
              condition or model), written back into the same CSV after each item. A person holds
              one sheet per language and a sheet belongs to one person (results/reviews/registry.json).
  dataset     E0a phase A: whether the expected output of each case is right (traps only by
              default). Reviews in results/reviews/phase_a/<dataset>.<reviewer>.json, keyed by case
              id and a hash of the case (a later edit of the case invalidates its review).
  --adjudicate  The pre-registered adjudication of phase A: the critical cases that half or more
              of the evaluated models failed (results/e0a_phase_a/gate.json, frozen once in
              results/reviews/adjudication/selection.json), shown blind (never which models failed
              them nor how many). Decisions in results/reviews/adjudication/.
  apply       Writes the accepted corrections into offline/datasets/phase_a/ (a private backup, a
              public CHANGELOG.jsonl without names), then runs core/dataset_check.py.
  adherence   The audit of the simulated user's adherence check (analysis/adherence.py) on a seeded,
              stratified random sample, recorded in <results>/audit/adherence_<auditor>.json;
              `report` gives the checker's accuracy with a 95% Wilson interval.

Every file it writes is under the git-ignored results/ tree except the corrected datasets and their
changelog; each carries the panel version and its first and last save, and the sessions are listed
in results/CATALOG.jsonl (review_start, review_done, dataset_apply). It listens on 127.0.0.1 only,
on the first free port from --port. How to use it: panels/README.md and panels/GUIA_ANOTADORES.md.
"""

from __future__ import annotations

import argparse
import copy
import csv
import datetime
import glob
import hashlib
import html
import io
import json
import math
import os
import random
import re
import secrets
import shutil
import statistics
import sys
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PANEL_VERSION = "2.0"
HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)
OFFLINE = os.path.join(EVAL, "offline")
ANALYSIS = os.path.join(EVAL, "analysis")
SCRIPTS = os.path.join(EVAL, "scripts")
DATASETS = os.path.join(OFFLINE, "datasets", "phase_a")
PUBLIC_CHANGELOG = os.path.join(DATASETS, "CHANGELOG.jsonl")
GUIDE = os.path.join(EVAL, "doc", "JUDGE_VALIDATION.md")
GUIDE_ES = os.path.join(HERE, "annotation_guide_es.md")
# Private, git-ignored: everything that carries a person's name
RESULTS = os.path.join(EVAL, "results")
REVIEWS = os.path.join(RESULTS, "reviews")
# The assistant's pre-review of the trap cases (decided by the author, 2026-11-01): a verdict
# (ok | doubt) and a note per case id, shown next to the proposed answer so that the author
# validates it; never shown in the blind adjudication
PREREVIEW = os.path.join(REVIEWS, "prereview_phase_a.json")


def load_prereview() -> dict:
    try:
        with open(PREREVIEW, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
E0B = os.path.join(RESULTS, "e0b")
GATE = os.path.join(RESULTS, "e0a_phase_a", "gate.json")
for _path in (OFFLINE, ANALYSIS):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import adherence  # noqa: E402  (pure Python: the checker audited here)
from annotate_turns import load_record  # noqa: E402

DEFAULT_PORT = 8098
VERDICTS = ("ok", "wrong", "unsure")
MARKS = ("correct", "incorrect", "unsure")
Z95 = 1.959963984540054
# Seconds per item before the session has its own pace (the estimate of the time left)
DEFAULT_SECONDS = {"dataset": 12, "adjudicate": 40, "annotate": 20, "adherence": 120}


class PanelError(Exception):
    """An error shown to the person: a code the page translates, and its parameters."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code, self.params = code, params


# ------------------------------------------------------------------ common
def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def slug(name: str) -> str:
    """The file-safe form of a person's name: ASCII, lower case, '_' between words."""
    text = unicodedata.normalize("NFKD", str(name or "").strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "_".join(re.findall(r"[a-z0-9]+", text))[:40]


def person(name: str) -> tuple[str, str]:
    name = " ".join(str(name or "").split())
    if len(slug(name)) < 2:
        raise PanelError("name_missing")
    return name, slug(name)


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_atomic(path: str, text: str) -> None:
    """Write a file whole or not at all (a temporary file in the same folder, then renamed)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp_", suffix=os.path.basename(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path: str, data) -> None:
    write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def stamp(data: dict) -> dict:
    """The panel version and the first and last save of a file the panel writes."""
    data["panel_version"] = PANEL_VERSION
    data.setdefault("first_saved", now())
    data["last_saved"] = now()
    return data


def catalog(event: str, folder: str, details: dict | None = None) -> None:
    """A line in results/CATALOG.jsonl (scripts/provenance.py), or the same line written here
    when provenance cannot be imported."""
    details = dict(details or {}, panel_version=PANEL_VERSION)
    try:
        if SCRIPTS not in sys.path:
            sys.path.insert(0, SCRIPTS)
        import provenance
        if os.path.normpath(provenance.RESULTS) == os.path.normpath(RESULTS):
            provenance.catalog(event, folder, details)
            return
    except Exception:  # noqa: B902 - the catalog must never stop a review
        pass
    try:
        os.makedirs(RESULTS, exist_ok=True)
        entry = {"time": now(), "event": event,
                 "folder": os.path.relpath(os.path.abspath(folder), RESULTS),
                 "command": " ".join(sys.argv), **details}
        with open(os.path.join(RESULTS, "CATALOG.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval of a proportion k/n (95% by default); (nan, nan) for n = 0."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def proportion(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"n": n, "correct": k, "accuracy": round(k / n, 4) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)] if n else None}


def fmt_prop(p: dict) -> str:
    if not p["n"]:
        return "n=0"
    return (f"{p['correct']}/{p['n']} = {p['accuracy']:.3f}  "
            f"[{p['ci95'][0]:.3f}, {p['ci95'][1]:.3f}]")


# ------------------------------------------------------------------ registry of people
def registry_path() -> str:
    return os.path.join(REVIEWS, "registry.json")


def registry() -> dict:
    path = registry_path()
    return read_json(path) if os.path.exists(path) else {"people": {}, "sheets": {}}


def save_registry(reg: dict) -> None:
    write_json(registry_path(), stamp(reg))


def claim_sheet(reg: dict, who: str, key: str) -> None:
    """A sheet belongs to one person, and a person holds one sheet per language (A or B):
    raises PanelError otherwise."""
    owner = reg["sheets"].get(key)
    if owner and owner != who:
        raise PanelError("sheet_taken")
    lang = key.split("/")[0]
    for other, holder in reg["sheets"].items():
        if holder == who and other != key and other.split("/")[0] == lang:
            raise PanelError("other_sheet", sheet=other.split("/")[-1].upper())


# ------------------------------------------------------------------ dataset (E0a, phase A)
# file stem -> the fields of the expected output (the label first) and their kind
EXPECTED = {
    "turn_act": (("expected_act", "enum"), ("expected_extractions", "json")),
    "confirmation": (("expected_intent", "enum"), ("expected_corrections", "json"),
                     ("hedged", "bool")),
    "quiz_grading": (("expected_kind", "enum"), ("ground_truth_correct", "bool")),
    "teach_back": (("expected_kind", "enum"), ("expected_points", "json")),
    "qa_answer": (("expected_kind", "enum"), ("key_facts", "json"), ("expected_polarity", "json")),
    "qa_verify": (("expected_supported", "bool"),),
    "scale_answer": (("expected_kind", "enum"), ("expected_option", "int")),
    "step_turn": (("expected_intent", "enum"),),
    "roleplay_turn": (("expected_intent", "enum"),),
    "cancel_detect": (("expected_cancel", "bool"),),
    "cancel_reply": (("expected_answer", "enum"),),
    "wrong_check": (("expected_wrong_check", "bool"),),
}
# Inputs that the question does not send to the LLM (the _instructions of each file say so)
NOT_SENT = {"cancel_detect": {"situation"}, "cancel_reply": {"situation", "robot_question"},
            "teach_back": {"task"}}
META = ("id", "language", "description", "category", "trap", "origin")
USER_FIELDS = ("user_text", "given_answer", "answer")

# Plain words for the reviewer: the situation of each question and its labels (es, en)
PLAIN = {
    "confirmation": {
        "q": ("El robot ha leído en voz alta los datos recogidos y ha preguntado si son correctos. "
              "¿Qué hace la persona?",
              "The robot read back the details it collected and asked whether they are right. "
              "What does the person do?"),
        "confirms": ("Confirma que todo es correcto", "Confirms that everything is right"),
        "corrects": ("Corrige algún dato", "Corrects a detail"),
        "question": ("Hace una pregunta", "Asks a question"),
        "unclear": ("No queda claro (duda, ironía, un sí a medias)",
                    "Unclear (hesitant, ironic, half-hearted)"),
        "expected_corrections": ("Datos corregidos", "Corrected details"),
        "hedged": ("¿Respuesta con reservas o irónica?", "Hedged or ironic reply?")},
    "turn_act": {
        "q": ("El robot ha pedido un dato. ¿Qué hace la persona?",
              "The robot asked for a detail. What does the person do?"),
        "values": ("Da uno o más datos", "Gives one or more details"),
        "question": ("Pregunta algo", "Asks something"),
        "skip": ("No da el dato (no lo sabe, no quiere o lo deja en blanco)",
                 "Does not give it (does not know, does not want to, leaves it blank)"),
        "repeat": ("Pide que se repita", "Asks to repeat"),
        "other": ("Otra cosa", "Something else"),
        "expected_extractions": ("Datos que da", "Details given")},
    "quiz_grading": {
        "q": ("Pregunta de un cuestionario. ¿Qué hace la persona?",
              "A quiz question. What does the person do?"),
        "answer": ("Intenta responder (también «no lo sé» o un intento a ciegas)",
                   "Tries to answer (also 'no idea' or a guess)"),
        "repeat": ("Pide que se repita o aclare la pregunta, o dice algo que no tiene que ver",
                   "Asks to repeat or clarify the question, or says something unrelated"),
        "ground_truth_correct": ("¿La respuesta es correcta? (si no ha intentado responder, No)",
                                 "Is the answer right? (No if they did not try to answer)")},
    "teach_back": {
        "q": ("El robot ha explicado algo y pide a la persona que lo repita con sus palabras. "
              "¿Qué hace la persona?",
              "The robot explained something and asks the person to say it back. "
              "What does the person do?"),
        "restates": ("Lo repite (o responde a la pregunta, aunque no lo sepa)",
                     "Says it back (or answers the question, even without knowing)"),
        "question": ("Hace una pregunta", "Asks a question"),
        "declines": ("Solo dice que lo ha entendido, o se niega, sin decir nada del contenido",
                     "Only says they understood, or refuses, with no content"),
        "not_understood": ("Dice que no lo ha entendido o pide oírlo otra vez",
                           "Did not understand, or asks to hear it again"),
        "other": ("Otra cosa", "Something else"),
        "expected_points": ("Cada punto clave: correct (dicho bien), incomplete (dicho en parte, "
                            "sin nada falso), wrong (dicho mal), missing (no dicho)",
                            "Each key point: correct, incomplete (said in part, nothing false), "
                            "wrong (said differently) or missing")},
    "qa_answer": {
        "q": ("El robot responde preguntas solo con la información que tiene. "
              "¿Qué dice la persona?",
              "The robot answers questions only from the information it has. "
              "What does the person say?"),
        "answerable": ("Una pregunta cuya respuesta está en la información",
                       "A question whose answer is in the information"),
        "not_in_material": ("Una pregunta de la tarea cuya respuesta NO está en la información",
                            "A question about the task whose answer is NOT in the information"),
        "off_task": ("Una pregunta o petición ajena a la tarea",
                     "A question or request outside the task"),
        "comment": ("Un comentario, sin preguntar nada", "A comment, asking nothing"),
        "done": ("Que ya no tiene más preguntas", "That they have no more questions"),
        "repeat": ("Pide que se repita", "Asks to repeat"),
        "other": ("Otra cosa", "Something else"),
        "key_facts": ("Datos que tiene que dar una buena respuesta (solo si la respuesta está en "
                      "la información; varios, separados con punto y coma)",
                      "Facts a right answer must give (only when the information answers it; "
                      "several, separated by semicolons)"),
        "expected_polarity": ("Si es una pregunta de sí o no, ¿la respuesta es sí o no? "
                              "(— si no lo es)",
                              "For a yes/no question, is the answer yes or no? (— if it is not "
                              "one)")},
    "qa_verify": {
        "q": ("Una respuesta del robot. ¿Todo lo que dice está en la información?",
              "An answer of the robot. Is everything it says in the information?"),
        "true": ("Sí: todo lo que dice está en la información",
                 "Yes: everything it says is in the information"),
        "false": ("No: dice algo que la información no dice, o lo cambia",
                  "No: it says something the information does not say, or changes it")},
    "scale_answer": {
        "q": ("Un cuestionario con opciones de respuesta. ¿Qué responde la persona?",
              "A questionnaire with answer options. What does the person answer?"),
        "option": ("Elige claramente una opción", "Clearly chooses one option"),
        "ambiguous": ("Queda entre opciones o no está claro", "Between options, or unclear"),
        "meaning": ("Pregunta qué significa la pregunta o una palabra",
                    "Asks what the item or a word means"),
        "refuses": ("No quiere responder", "Does not want to answer"),
        "repeat": ("Pide que se repita", "Asks to repeat"),
        "other": ("Otra cosa", "Something else"),
        "expected_option": ("Número de la opción elegida", "Number of the option chosen")},
    "step_turn": {
        "q": ("El robot guía un procedimiento paso a paso y acaba de presentar el paso actual. "
              "¿Qué dice la persona?",
              "The robot guides a procedure step by step and has just given the current step. "
              "What does the person say?"),
        "done": ("Que ha terminado el paso (y lo que describe encaja con la comprobación)",
                 "That the step is finished (and what they describe fits the check)"),
        "question": ("Una pregunta sobre el paso o la tarea", "A question about the step or task"),
        "problem": ("Que está atascada, no lo entendió o algo salió mal",
                    "That they are stuck, did not understand, or something went wrong"),
        "repeat": ("Pide oír el paso otra vez", "Asks to hear the step again"),
        "back": ("Quiere volver al paso anterior", "Wants to go back to the previous step"),
        "skip": ("Quiere saltarse el paso sin hacerlo", "Wants to skip the step without doing it"),
        "unclear": ("Otra cosa (fuera de tema, relleno, ambiguo, aún lo está haciendo)",
                    "Anything else (off-topic, filler, ambiguous, still doing it)")},
    "roleplay_turn": {
        "q": ("Práctica de una conversación: el robot hace un papel. ¿Qué dice la persona?",
              "A practice conversation: the robot plays a role. What does the person say?"),
        "continue": ("Sigue con la escena (habla al personaje)",
                     "Goes on with the scene (talks to the character)"),
        "repeat": ("Pide que el personaje repita", "Asks the character to repeat"),
        "coach": ("Pide ayuda con la práctica (qué decir, una pista)",
                  "Asks for help with the practice (what to say, a hint)"),
        "end": ("Pide parar la práctica", "Asks to stop the practice"),
        "scene_end": ("Se despide porque el personaje ya cerró la escena",
                      "Says goodbye because the character already closed the scene")},
    "cancel_detect": {
        "q": ("¿Pide la persona abandonar la conversación a medias, sin terminar la tarea? "
              "(Despedirse cuando ya ha terminado es un final normal: no)",
              "Does the person ask to abandon the conversation halfway, without finishing the "
              "task? (Saying goodbye once it is finished is a normal end: no)"),
        "true": ("Sí, pide dejarlo a medias", "Yes, asks to abandon it halfway"),
        "false": ("No", "No")},
    "cancel_reply": {
        "q": ("El robot ha preguntado si quiere dejar la conversación. ¿Qué responde la persona?",
              "The robot asked whether they want to stop the conversation. What do they answer?"),
        "yes": ("Sí, quiere parar", "Yes, wants to stop"),
        "no": ("No, quiere seguir", "No, wants to go on"),
        "finished": ("Ha terminado: no tiene nada más que preguntar o añadir",
                     "Has finished: nothing more to ask or add"),
        "other": ("Otra cosa (sigue con la tarea, pregunta o no está claro)",
                  "Something else (goes on with the task, asks, or unclear)")},
    "wrong_check": {
        "q": ("El robot ha dado una respuesta y la persona contesta sin preguntar nada nuevo. "
              "¿Repite algún dato de la respuesta (una hora, un precio, un día, un lugar, un "
              "número) con un valor distinto?",
              "The robot gave an answer and the person replies without asking anything new. "
              "Do they repeat back a value of the answer (a time, a price, a day, a place, a "
              "number) with a different value?"),
        "true": ("Sí: repite un dato con otro valor (ha entendido mal)",
                 "Yes: repeats a value with another value (misunderstood)"),
        "false": ("No: el mismo valor, aunque lo diga de otra forma, o no repite ningún dato",
                  "No: the same value, even said another way, or no value repeated")},
}
YES_NO = {"true": ("Sí", "Yes"), "false": ("No", "No")}


def dataset_check():
    from core import dataset_check as module  # noqa: E402  (needs pyyaml; imported when used)
    return module


def case_hash(case: dict) -> str:
    text = json.dumps(case, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def datasets_hash(folder: str | None = None) -> str:
    """One hash of every phase A dataset file (names and contents)."""
    h = hashlib.sha256()
    for path in dataset_paths(folder or DATASETS):
        h.update(os.path.basename(path).encode())
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def dataset_paths(folder: str, files=None) -> list[str]:
    paths = sorted(glob.glob(os.path.join(folder, "*.json")))
    if files:
        wanted = {os.path.splitext(os.path.basename(f))[0] for f in files}
        unknown = wanted - {os.path.splitext(os.path.basename(p))[0] for p in paths}
        if unknown:
            raise SystemExit(f"No dataset {sorted(unknown)} in {folder}")
        paths = [p for p in paths if os.path.splitext(os.path.basename(p))[0] in wanted]
    return [p for p in paths if os.path.splitext(os.path.basename(p))[0] in EXPECTED]


def reviews_dir(adjudicate: bool = False) -> str:
    return os.path.join(REVIEWS, "adjudication" if adjudicate else "phase_a")


def reviews_path(stem: str, reviewer: str, adjudicate: bool = False) -> str:
    return os.path.join(reviews_dir(adjudicate), f"{stem}.{reviewer}.json")


def load_reviews(stem: str, reviewer: str, adjudicate: bool = False) -> dict:
    path = reviews_path(stem, reviewer, adjudicate)
    if not os.path.exists(path):
        return {"dataset": f"{stem}.json", "reviewer": reviewer,
                "kind": "adjudication" if adjudicate else "phase_a_review", "reviews": {}}
    return read_json(path)


def review_status(review: dict | None, chash: str) -> str:
    """pending (no review), stale (the case changed after it), or the verdict."""
    if not review:
        return "pending"
    if review.get("hash") != chash:
        return "stale"
    return review.get("verdict", "pending")


def validate_correction(stem: str, case: dict, correction: dict) -> tuple[dict, list[str]]:
    """The case with the correction, and what is wrong with it (empty: valid)."""
    fields = dict(EXPECTED[stem])
    if not isinstance(correction, dict) or not correction:
        return case, ["choose the right answer"]
    unknown = set(correction) - set(fields)
    if unknown:
        return case, [f"not an expected field: {sorted(unknown)}"]
    problems = []
    for key, value in correction.items():
        kind = fields[key]
        if kind == "bool" and not isinstance(value, bool):
            problems.append(f"'{key}' must be true or false")
        if kind == "int" and value is not None and (not isinstance(value, int)
                                                    or isinstance(value, bool)):
            problems.append(f"'{key}' must be a number or empty")
    if all(case.get(k) == v for k, v in correction.items()):
        problems.append("the correction changes nothing")
    candidate = dict(case)
    candidate.update(correction)
    problems += dataset_check().check_case(stem, candidate)
    return candidate, problems


def adjudication_selection(gate_path: str | None = None) -> dict:
    """The critical cases that half or more of the evaluated models failed (in any repetition),
    from gate.json, or else from the saved runs of its folder. Only the cases are returned:
    never which models failed them nor how many."""
    gate_path = gate_path or GATE
    failed = defaultdict(set)
    models = set()
    if os.path.exists(gate_path):
        gate = read_json(gate_path)
        source_hash = hashlib.sha256(open(gate_path, "rb").read()).hexdigest()[:16]
        for model, g in gate.items():
            # the keys that start with "_" (_finalists, _datasets) are not models
            if model.startswith("_") or not isinstance(g, dict) \
                    or not (g.get("calls") or g.get("critical_cases")):
                continue
            models.add(model)
            for c in g.get("critical_cases", []):
                failed[(c["benchmark"], c["id"])].add(model)
        source = gate_path
    else:
        out = os.path.dirname(gate_path)
        paths = glob.glob(os.path.join(out, "*", "rep*", "*.json"))
        if not paths:
            raise PanelError("no_gate")
        h = hashlib.sha256()
        for path in sorted(paths):
            model = os.path.relpath(path, out).split(os.sep)[0]
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem not in EXPECTED:
                continue
            with open(path, "rb") as f:
                raw = f.read()
            h.update(raw)
            models.add(model)
            for c in json.loads(raw).get("cases", []):
                if c.get("critical"):
                    failed[(stem, c["id"])].add(model)
        source, source_hash = out, h.hexdigest()[:16]
    cases = sorted(k for k, who in failed.items() if 2 * len(who) >= len(models) and models)
    return {"source": os.path.relpath(source, RESULTS) if source.startswith(RESULTS) else source,
            "source_sha256": source_hash, "evaluated_models": len(models),
            "rule": "critical error in half or more of the evaluated models (any repetition)",
            "created": now(), "cases": [list(k) for k in cases]}


def frozen_selection(gate_path: str | None = None, reselect: bool = False) -> dict:
    """The adjudication list, drawn once and kept (pre-registered): selection.json."""
    path = os.path.join(reviews_dir(True), "selection.json")
    if os.path.exists(path) and not reselect:
        return read_json(path)
    selection = stamp(adjudication_selection(gate_path))
    write_json(path, selection)
    catalog("adjudication_selection", reviews_dir(True),
            {"cases": len(selection["cases"]), "source": selection["source"],
             "source_sha256": selection["source_sha256"]})
    return selection


class DatasetReview:
    kind = "dataset"

    def __init__(self, reviewer: str, folder: str | None = None, files=None, language=None,
                 traps: bool = True, adjudicate: bool = False, gate: str | None = None,
                 reselect: bool = False):
        self.name, self.reviewer = person(reviewer)
        self.folder = folder or DATASETS
        self.paths = dataset_paths(self.folder, files)
        self.language, self.traps, self.adjudicate = language or None, traps, adjudicate
        self.selection = None
        if adjudicate:
            self.selection = {tuple(c) for c in frozen_selection(gate, reselect)["cases"]}
            self.kind = "adjudicate"
        self._cache = {}
        self.finished = False
        self.prereview = {} if adjudicate else load_prereview()
        if not self.paths:
            raise SystemExit(f"No phase A dataset in {self.folder}")

    def describe(self) -> dict:
        return {"task": self.kind, "name": self.name, "scope": "traps" if self.traps else "all",
                "case_language": self.language or "", "file": self.where()}

    def where(self) -> str:
        return os.path.relpath(reviews_dir(self.adjudicate), EVAL)

    def data(self, path: str) -> dict:
        mtime = os.path.getmtime(path)
        if path not in self._cache or self._cache[path][0] != mtime:
            self._cache[path] = (mtime, read_json(path))
        return self._cache[path][1]

    def items(self) -> list[tuple[str, dict]]:
        """(stem, case) of every case to review, files in order and cases in file order."""
        out = []
        for path in self.paths:
            stem = os.path.splitext(os.path.basename(path))[0]
            for case in self.data(path).get("cases", []):
                if self.adjudicate:
                    if (stem, case.get("id")) not in self.selection:
                        continue
                elif self.traps and not case.get("trap"):
                    continue
                if self.language and case.get("language") != self.language:
                    continue
                out.append((stem, case))
        return out

    def state(self) -> dict:
        reviews, statuses, labels, doubts = {}, [], [], []
        for i, (stem, case) in enumerate(self.items()):
            if stem not in reviews:
                reviews[stem] = load_reviews(stem, self.reviewer, self.adjudicate)["reviews"]
            review = reviews[stem].get(case["id"])
            status = review_status(review, case_hash(case))
            statuses.append(status)
            labels.append(f"{stem}:{case['id']}")
            if status == "unsure" or (status in VERDICTS and review.get("note")):
                doubts.append(i)
        return {"total": len(statuses), "done": sum(1 for s in statuses if s in VERDICTS),
                "statuses": statuses, "labels": labels, "doubts": doubts,
                "counts": {s: statuses.count(s) for s in ("ok", "wrong", "unsure", "stale",
                                                          "pending")}}

    def item(self, i: int) -> dict:
        stem, case = self.items()[i]
        data = self.data(os.path.join(self.folder, f"{stem}.json"))
        review = load_reviews(stem, self.reviewer, self.adjudicate)["reviews"].get(case["id"])
        chash = case_hash(case)
        spec = dataset_check().SPEC[stem]
        expected_keys = [k for k, _ in EXPECTED[stem]]
        plain = PLAIN.get(stem, {})
        inputs = [{"key": k, "value": v, "sent": k not in NOT_SENT.get(stem, set())}
                  for k, v in case.items() if k not in META and k not in expected_keys]
        fields = []
        for key, kind in EXPECTED[stem]:
            if kind == "enum":
                choices = [{"value": a, "text": list(plain.get(a, (a, a)))}
                           for a in (spec[2] if key == spec[1] else [])]
            elif kind == "bool":
                words = plain if key == spec[1] else YES_NO
                choices = [{"value": v, "text": list(words.get(str(v).lower(), YES_NO[
                    str(v).lower()]))} for v in (True, False)]
            else:
                choices = None
            fields.append({"name": key, "kind": kind, "main": key == spec[1], "choices": choices,
                           "text": list(plain.get(key, ("La respuesta", "The answer")
                                                  if key == spec[1] else (key, key)))})
        instructions = data.get("_instructions", "")
        return {"index": i, "file": f"{stem}.json", "stem": stem, "question": data.get("question"),
                "plain": list(plain.get("q", ("", ""))), "case": {k: case.get(k) for k in META},
                "inputs": inputs, "expected": {k: case.get(k) for k in expected_keys},
                "fields": fields, "hash": chash, "review": review,
                "prereview": self.prereview.get(case.get("id")),
                "status": review_status(review, chash),
                "instructions": "\n".join(instructions) if isinstance(instructions, list)
                else str(instructions)}

    def mark(self, body: dict) -> dict:
        stem, cid = body.get("stem"), body.get("id")
        path = os.path.join(self.folder, f"{stem}.json")
        if stem not in EXPECTED or path not in self.paths:
            return {"error": "Unknown dataset"}
        case = next((c for c in self.data(path).get("cases", []) if c.get("id") == cid), None)
        if case is None:
            return {"error": f"No case {cid} in {stem}.json"}
        chash = case_hash(case)
        if body.get("hash") != chash:
            return {"error": "changed_on_disk", "code": "changed_on_disk"}
        store = load_reviews(stem, self.reviewer, self.adjudicate)
        verdict = body.get("verdict", "")
        if verdict == "":
            store["reviews"].pop(cid, None)
        else:
            if verdict not in VERDICTS:
                return {"error": f"Unknown verdict {verdict!r}"}
            entry = {"hash": chash, "verdict": verdict, "note": str(body.get("note") or ""),
                     "at": now()}
            if "blind_answer" in body:
                # The reviewer's own answer, given before seeing the case's (blind first)
                entry["blind_answer"] = body["blind_answer"]
            old = store["reviews"].get(cid) or {}
            entry["first_at"] = old.get("first_at") or old.get("at") or entry["at"]
            if verdict == "wrong":
                correction = body.get("correction") or {}
                _, problems = validate_correction(stem, case, correction)
                if problems:
                    return {"error": "; ".join(problems), "code": "invalid_correction"}
                entry["correction"] = correction
            store["reviews"][cid] = entry
        store["reviewer_name"] = self.name
        write_json(reviews_path(stem, self.reviewer, self.adjudicate), stamp(store))
        return {"error": ""}

    def prompt(self, i: int) -> dict:
        """The exact prompt(s) the question sends for this case, built by the benchmark's own
        code with the production client and a transport that never calls any model."""
        stem, case = self.items()[i]
        try:
            import importlib

            from core.phase_a import make_clients
            from run_eval import BENCHMARKS
            lang = case["language"]
            prompts = []

            def infer(session_id, prompt, max_tokens, schema, **kwargs):
                if prompt not in prompts:
                    prompts.append(prompt)
                return None   # no answer: the question is built and asked, never answered

            config = {"prompts_yaml": "../../interaction_system_core/interaction_intelligence/"
                                      "config/prompts.yaml", "llm": {"provider": "none"}}
            clients, _ = make_clients(config, infer_fn=infer, languages=(lang,))
            module = importlib.import_module(BENCHMARKS[stem][0])
            try:
                module.ask(clients[lang], case)
            except Exception:  # noqa: B902 - a question without answer may raise: the prompt is in
                pass
            return {"prompts": prompts, "error": "" if prompts else "No prompt was built"}
        except Exception as exc:  # noqa: B902 - shown on the page
            return {"prompts": [], "error": f"{type(exc).__name__}: {exc}"}

    def files(self) -> list[str]:
        return sorted(glob.glob(os.path.join(reviews_dir(self.adjudicate),
                                             f"*.{self.reviewer}.json")))


def dataset_report(reviewer: str | None = None, adjudicate: bool = False,
                   folder: str | None = None) -> list[dict]:
    """Per dataset file and reviewer: cases, reviewed, ok, corrected (applied), unsure, stale,
    pending (in adjudication, the cases of the selection only)."""
    folder = folder or DATASETS
    selection = None
    if adjudicate:
        path = os.path.join(reviews_dir(True), "selection.json")
        selection = {tuple(c) for c in read_json(path)["cases"]} if os.path.exists(path) else set()
    rows = []
    for path in dataset_paths(folder):
        stem = os.path.splitext(os.path.basename(path))[0]
        cases = [c for c in read_json(path).get("cases", [])
                 if selection is None or (stem, c.get("id")) in selection]
        if adjudicate and not cases:
            continue
        if reviewer:
            names = [slug(reviewer)]
        else:
            pattern = os.path.join(reviews_dir(adjudicate), f"{glob.escape(stem)}.*.json")
            names = sorted(os.path.basename(p)[len(stem) + 1:-len(".json")]
                           for p in glob.glob(pattern)) or ["-"]
        for name in names:
            reviews = load_reviews(stem, name, adjudicate)["reviews"] if name != "-" else {}
            st = [review_status(reviews.get(c.get("id")), case_hash(c)) for c in cases]
            applied = sum(1 for c, s in zip(cases, st)
                          if s == "wrong" and reviews[c["id"]].get("applied"))
            traps = [s for c, s in zip(cases, st) if c.get("trap")]
            rows.append({"file": f"{stem}.json", "reviewer": name, "cases": len(cases),
                         "traps": len(traps),
                         "traps_reviewed": sum(1 for s in traps if s in VERDICTS),
                         "reviewed": sum(1 for s in st if s in VERDICTS),
                         "ok": st.count("ok"), "corrected": st.count("wrong"),
                         "applied": applied, "unsure": st.count("unsure"),
                         "stale": st.count("stale"),
                         "pending": sum(1 for s in st if s not in VERDICTS)})
    return rows


def print_dataset_report(rows: list[dict]) -> None:
    cols = ("cases", "traps", "traps_reviewed", "reviewed", "ok", "corrected", "applied",
            "unsure", "stale", "pending")
    print(f"{'file':20s} {'reviewer':14s} " + " ".join(f"{c[:9]:>9s}" for c in cols))
    for r in rows:
        print(f"{r['file']:20s} {r['reviewer']:14s} " + " ".join(f"{r[c]:9d}" for c in cols))
    total = {c: sum(r[c] for r in rows) for c in cols}
    print(f"{'total':20s} {'':14s} " + " ".join(f"{total[c]:9d}" for c in cols))
    print("stale: the case changed after its review (counted as pending); applied: corrections "
          "already written into the dataset")


def apply_reviews(reviewer: str, files=None, dry_run: bool = False, adjudicate: bool = False,
                  run_check: bool = True, folder: str | None = None, log=print) -> list[dict]:
    """Write the reviewer's corrections (verdict WRONG, current, not yet applied) into the
    datasets: a private backup of each changed file first, one public changelog entry per case
    (without names) and one private entry (with the reviewer and the note). Returns the
    changes."""
    folder = folder or DATASETS
    _, who = person(reviewer)
    source = "adjudication, reviewed blind" if adjudicate else "phase A review"
    before = datasets_hash(folder)
    changes = []
    when = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in dataset_paths(folder, files):
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            original = f.read()
        data = json.loads(original)
        store = load_reviews(stem, who, adjudicate)
        mine = []
        for case in data.get("cases", []):
            review = store["reviews"].get(case.get("id"))
            if review_status(review, case_hash(case)) != "wrong" or review.get("applied"):
                if review and review.get("verdict") == "wrong" and not review.get("applied") \
                        and review.get("hash") != case_hash(case):
                    log(f"  SKIPPED {stem}:{case['id']}: the case changed after the review")
                continue
            _, problems = validate_correction(stem, case, review.get("correction") or {})
            if problems:
                log(f"  SKIPPED {stem}:{case['id']}: " + "; ".join(problems))
                continue
            old = {k: copy.deepcopy(case.get(k)) for k in review["correction"]}
            mine.append((case, old, review))
        if not mine:
            continue
        for case, old, review in mine:
            log(f"  {stem}:{case['id']}: {json.dumps(old, ensure_ascii=False)} -> "
                f"{json.dumps(review['correction'], ensure_ascii=False)}")
        if dry_run:
            changes += [{"file": f"{stem}.json", "case_id": c["id"]} for c, _, _ in mine]
            continue
        backup = os.path.join(REVIEWS, "backups", f"{stem}.{when}.json")
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copyfile(path, backup)
        date = now()
        public, private = [], []
        for case, old, review in mine:
            case.update(review["correction"])
            review["applied"] = date
            review["hash"] = case_hash(case)
            base = {"date": date, "file": f"{stem}.json", "case_id": case["id"], "old": old,
                    "new": review["correction"], "source": source}
            public.append(dict(base, reviewed_by="an author"))
            private.append(dict(base, reviewer=who, note=review.get("note", ""),
                                backup=os.path.relpath(backup, RESULTS)))
        write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False)
                     + ("\n" if original.endswith("\n") else ""))
        write_json(reviews_path(stem, who, adjudicate), stamp(store))
        for target, entries in ((os.path.join(folder, os.path.basename(PUBLIC_CHANGELOG)),
                                 public),
                                (os.path.join(REVIEWS, "changelog.jsonl"), private)):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        changes += private
        log(f"  {stem}.json: {len(private)} correction(s); private backup "
            f"{os.path.relpath(backup, RESULTS)}")
    if not changes:
        log("  Nothing to apply")
        return changes
    if dry_run:
        return changes
    after = datasets_hash(folder)
    catalog("dataset_apply", reviews_dir(adjudicate),
            {"reviewer": who, "source": source, "corrections": len(changes),
             "datasets_hash_before": before, "datasets_hash_after": after})
    if run_check:
        problems = dataset_check().check_all()
        for p in problems:
            log(f"  PROBLEM {p}")
        log(f"  dataset check: {len(problems)} problem(s)")
    if adjudicate:
        log("  Now recompute the gate from the saved outputs: (from evaluation/offline) "
            "python3 run_models.py --reps 3 --out ../results/e0a_phase_a --gate-only")
    return changes


# ------------------------------------------------------------------ adherence audit
def run_files(results: str) -> list[str]:
    return sorted(glob.glob(os.path.join(results, "runs", "*", "*", "*.json")))


def stratum(rec: dict) -> str:
    return "|".join(str(rec.get(k) or "?") for k in ("language", "persona", "model"))


def eligible(results: str) -> dict:
    """{relative path: stratum} of the dialogues with goal items (the others are n/a)."""
    out = {}
    for path in run_files(results):
        try:
            rec = read_json(path)
        except (OSError, ValueError):
            continue
        if adherence.goal_items_of(rec):
            out[os.path.relpath(path, results)] = stratum(rec)
    return out


def draw_sample(population: dict, n: int, seed: int) -> tuple[list[str], dict]:
    """A seeded random sample of n dialogues, stratified: each stratum (language, persona,
    model) gets its share of n by largest remainder, then its dialogues are drawn at random.
    Returns the sample (shuffled) and {stratum: [population, taken]}."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for path in sorted(population):
        groups[population[path]].append(path)
    total = len(population)
    n = min(n, total)
    if not n:
        return [], {}
    quota = {k: n * len(v) / total for k, v in groups.items()}
    take = {k: int(q) for k, q in quota.items()}
    for k in sorted(groups, key=lambda k: (-(quota[k] - take[k]), k))[:n - sum(take.values())]:
        take[k] += 1
    sample = []
    for k in sorted(groups):
        sample += rng.sample(groups[k], take[k])
    rng.shuffle(sample)
    return sample, {k: [len(groups[k]), take[k]] for k in sorted(groups)}


def audit_items(rec: dict, rule: str = adherence.DEFAULT_RULE) -> tuple[list[dict], dict]:
    """What the auditor judges in one dialogue: every declared item, every goal item the checker
    found never revealed, and the checker's verdict; and the checker's output."""
    verdict = adherence.check(rec, rule)
    goal = {str(i["id"]): i for i in adherence.goal_items_of(rec)}
    items = []
    for k, d in enumerate(adherence.declarations(rec.get("user_log"))):
        item = goal.get(d["id"])
        planned = [] if item is None else [str(item["value"])] + (
            [str(item["then"])] if item.get("then") is not None else [])
        on_script = adherence.norm_value(d["value"]) in {adherence.norm_value(p) for p in planned}
        in_line = (not d["text"] or not adherence._words(d["value"]).strip()
                   or adherence._words(d["value"]) in adherence._words(d["text"]))
        items.append({"key": f"d{k}", "kind": "declared", "turn": d["turn"], "id": d["id"],
                      "value": d["value"], "text": d["text"], "planned": planned,
                      "off_script": not on_script, "flagged": not in_line})
    for entry in filter(None, (verdict.get("adh_unrevealed") or "").split("; ")):
        ident = entry.split(" (")[0]
        item = goal.get(ident, {})
        items.append({"key": f"u:{entry}", "kind": "unrevealed", "id": ident,
                      "value": str(item.get("then") if entry.endswith("(then)") else
                                   item.get("value", ""))})
    items.append({"key": "verdict", "kind": "verdict", "check": verdict.get("adh_check"),
                  "excluded": verdict.get("sim_excluded"),
                  "why": verdict.get("sim_excluded_why", ""),
                  "off_script": verdict.get("adh_off_script", ""),
                  "unrevealed": verdict.get("adh_unrevealed", "")})
    return items, verdict


def audit_path(results: str, auditor: str) -> str:
    return os.path.join(results, "audit", f"adherence_{auditor}.json")


def experiments() -> list[dict]:
    """The results folders with dialogue records (runs/<model>/<condition>/*.json)."""
    out = []
    for folder in sorted(glob.glob(os.path.join(RESULTS, "*", ""))):
        n = len(run_files(folder))
        if n:
            out.append({"name": os.path.basename(os.path.normpath(folder)), "dialogues": n})
    return out


class AdherenceAudit:
    kind = "adherence"

    def __init__(self, results: str, auditor: str, n=None, seed=None,
                 rule: str = adherence.DEFAULT_RULE):
        self.results = os.path.abspath(results)
        self.name, self.auditor = person(auditor)
        self.path = audit_path(self.results, self.auditor)
        self.finished = False
        if os.path.exists(self.path):
            self.audit = read_json(self.path)
            for key, value in (("n", n), ("seed", seed)):
                if value is not None and self.audit.get(key) != value:
                    raise SystemExit(f"{self.path} was drawn with {key}={self.audit.get(key)}: "
                                     f"the sample is fixed (remove the file to draw another)")
        else:
            n = 40 if n is None else n
            seed = 7 if seed is None else seed
            population = eligible(self.results)
            if not population:
                raise PanelError("no_dialogues")
            sample, strata = draw_sample(population, n, seed)
            self.audit = stamp({"experiment": os.path.basename(self.results),
                                "auditor": self.auditor, "auditor_name": self.name, "seed": seed,
                                "n": n, "population": len(population), "rule": rule,
                                "strata": strata, "sample": sample, "dialogues": {}})
            write_json(self.path, self.audit)

    def describe(self) -> dict:
        return {"task": "adherence", "name": self.name, "experiment": self.audit["experiment"],
                "file": self.where()}

    def where(self) -> str:
        return os.path.relpath(self.path, EVAL)

    def files(self) -> list[str]:
        return [self.path]

    def record(self, rel: str) -> dict:
        # With the annotator's marks of the simulated user's lines, when they exist
        return load_record(os.path.join(self.results, rel))

    def status(self, rel: str) -> str:
        entry = self.audit["dialogues"].get(rel)
        if not entry or not entry.get("marks"):
            return "pending"
        keys = entry.get("keys") or []
        return "done" if keys and all(k in entry["marks"] for k in keys) else "partial"

    def state(self) -> dict:
        sample = self.audit["sample"]
        statuses = [self.status(r) for r in sample]
        doubts = [i for i, r in enumerate(sample)
                  if "unsure" in (self.audit["dialogues"].get(r, {}).get("marks") or {}).values()
                  or self.audit["dialogues"].get(r, {}).get("note")]
        return {"total": len(sample), "done": statuses.count("done"), "statuses": statuses,
                "labels": sample, "doubts": doubts,
                "counts": {s: statuses.count(s) for s in ("done", "partial", "pending")},
                "seed": self.audit["seed"], "n": self.audit["n"],
                "population": self.audit["population"]}

    def item(self, i: int) -> dict:
        rel = self.audit["sample"][i]
        rec = self.record(rel)
        items, verdict = audit_items(rec, self.audit.get("rule", adherence.DEFAULT_RULE))
        log = [{k: e.get(k) for k in ("turn", "robot", "text", "heard", "revealed", "event",
                                      "action", "error", "revealed_error")}
               for e in rec.get("user_log") or []]
        entry = self.audit["dialogues"].get(rel, {})
        return {"index": i, "run": rel, "items": items, "log": log,
                "transcript": rec.get("transcript", []),
                "goal_items": adherence.goal_items_of(rec), "user_goal": rec.get("user_goal", ""),
                "meta": {k: rec.get(k) for k in ("scenario", "strategy", "persona", "model",
                                                 "condition", "language", "outcome", "rep")},
                "marks": entry.get("marks", {}), "note": entry.get("note", ""),
                "status": self.status(rel)}

    def mark(self, body: dict) -> dict:
        rel = body.get("run")
        if rel not in self.audit["sample"]:
            return {"error": "Not a dialogue of the sample"}
        rec = self.record(rel)
        items, verdict = audit_items(rec, self.audit.get("rule", adherence.DEFAULT_RULE))
        by_key = {it["key"]: it for it in items}
        entry = self.audit["dialogues"].setdefault(rel, {"marks": {}, "items": {}, "note": "",
                                                         "first_at": now()})
        key = body.get("key")
        if key is not None:
            if key not in by_key:
                return {"error": f"Unknown item {key!r}"}
            value = body.get("mark", "")
            if value == "":
                entry["marks"].pop(key, None)
            elif value not in MARKS:
                return {"error": f"Unknown mark {value!r}"}
            else:
                entry["marks"][key] = value
                entry["items"][key] = by_key[key]   # what was judged, as shown
        if "note" in body:
            entry["note"] = str(body.get("note") or "")
        entry["keys"] = list(by_key)
        entry["checker"] = {k: verdict.get(k) for k in ("adh_check", "adh_off_script",
                                                        "adh_unrevealed", "adh_review",
                                                        "sim_excluded", "sim_excluded_why")}
        entry["language"] = rec.get("language")
        entry["at"] = now()
        write_json(self.path, stamp(self.audit))
        return {"error": ""}


def adherence_report(audit: dict) -> dict:
    """The checker's accuracy from an audit file: per item (declared, never revealed, all) and
    per dialogue verdict, overall and per language, with 95% Wilson intervals. Unsure marks are
    left out and counted."""
    counts = defaultdict(lambda: [0, 0])
    unsure = 0
    complete = [0, 0]
    for rel in audit.get("sample", []):
        entry = audit.get("dialogues", {}).get(rel)
        if not entry:
            continue
        lang = entry.get("language") or "?"
        marks = entry.get("marks", {})
        for key, mark in marks.items():
            kind = (entry.get("items", {}).get(key) or {}).get("kind", "?")
            if mark == "unsure":
                unsure += 1
                continue
            ok = int(mark == "correct")
            groups = ["verdict"] if kind == "verdict" else ["items", f"items:{kind}"]
            for g in groups:
                for scope in (g, f"{g}@{lang}"):
                    counts[scope][0] += ok
                    counts[scope][1] += 1
        keys = entry.get("keys") or []
        item_keys = [k for k in keys if k != "verdict"]
        if keys and all(k in marks for k in keys) and "unsure" not in [marks[k]
                                                                         for k in item_keys]:
            complete[1] += 1
            complete[0] += int(all(marks[k] == "correct" for k in item_keys))
    out = {"experiment": audit.get("experiment"), "auditor": audit.get("auditor"),
           "seed": audit.get("seed"), "n": audit.get("n"), "population": audit.get("population"),
           "rule": audit.get("rule"), "audited_dialogues": sum(
               1 for r in audit.get("sample", []) if audit.get("dialogues", {}).get(r, {})
               .get("marks")), "unsure_marks": unsure,
           "dialogues_every_item_correct": proportion(*complete)}
    for scope in sorted(counts):
        out[scope] = proportion(*counts[scope])
    return out


def print_adherence_report(rep: dict) -> None:
    print(f"Adherence audit of {rep['experiment']} by {rep['auditor']}: seed {rep['seed']}, "
          f"sample {rep['n']} of {rep['population']} dialogues with goal items, rule "
          f"{rep['rule']}; {rep['audited_dialogues']} audited, {rep['unsure_marks']} unsure "
          f"mark(s) left out")
    names = {"items": "every item", "items:declared": "declared items",
             "items:unrevealed": "never-revealed items", "verdict": "dialogue verdict"}
    for scope in sorted(k for k in rep if k.split("@")[0] in names):
        base, _, lang = scope.partition("@")
        label = names[base] + (f" ({lang})" if lang else "")
        print(f"  {label:32s} {fmt_prop(rep[scope])}")
    print(f"  {'dialogues, every item correct':32s} "
          f"{fmt_prop(rep['dialogues_every_item_correct'])}")
    print("  (accuracy of the checker, 95% Wilson interval)")


# ------------------------------------------------------------------ annotation (E0b)
SHEET_COLUMNS = ["id", "task", "material", "conversation_before", "robot_utterance",
                 "in_frame (1/0)", "unsupported (1/0)", "note"]
LABELS = {"in_frame": "in_frame (1/0)", "unsupported": "unsupported (1/0)"}


def sheet_path(lang: str, sheet: str) -> str:
    return os.path.join(E0B, lang, "e0", f"sheet_{sheet}.csv")


def read_csv(path: str) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in reader if r]
    return header, rows


def write_csv(path: str, header: list[str], rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(c, "") for c in header])
    write_atomic(path, buf.getvalue())


def guide_section(path: str | None = None, heading: str = "The annotation guide") -> str:
    """The text of one '## ' section of a Markdown file."""
    try:
        with open(path or GUIDE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def guide_es() -> tuple[str, bool]:
    """The Spanish guide (annotation_guide_es.md) and whether it translates the current English
    section (the hash in its first line)."""
    try:
        with open(GUIDE_ES, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "", False
    m = re.match(r"<!--\s*source-sha256:\s*([0-9a-f]+)\s*-->\s*", text)
    current = hashlib.sha256(guide_section().encode("utf-8")).hexdigest()[:16]
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
    return body, bool(m and m.group(1) == current)


def md_inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<i>\1</i>", text)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)


def md_to_html(text: str) -> str:
    """Enough Markdown for the guide: headings, paragraphs, lists, tables (the first one gets
    id="examples"), bold, italics, code."""
    out, para, rows, items = [], [], [], []
    tables = [0]

    def flush():
        if para:
            out.append("<p>" + md_inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{md_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
        if rows:
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows
                     if not re.match(r"^\|[\s:|-]+\|$", r.strip())]
            head, body = cells[0], cells[1:]
            ident = ' id="examples"' if tables[0] == 0 else ""
            tables[0] += 1
            out.append(f"<table{ident}><tr>" + "".join(f"<th>{md_inline(c)}</th>" for c in head)
                       + "</tr>" + "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in r)
                                           + "</tr>" for r in body) + "</table>")
            rows.clear()

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("<!--"):
            continue
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            out.append(f"<h4>{md_inline(s.lstrip('#').strip())}</h4>")
        elif s.startswith("|"):
            if para or items:
                flush()
            rows.append(s)
        elif s.startswith("- "):
            if para or rows:
                flush()
            items.append(s[2:])
        elif items and line.startswith("  "):
            items[-1] += " " + s
        else:
            if rows or items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


class Annotation:
    kind = "annotate"

    def __init__(self, sheet: str, annotator: str = "", key: str = ""):
        self.path = os.path.abspath(sheet)
        if not os.path.exists(self.path):
            raise PanelError("sheet_missing")
        self.name = " ".join(str(annotator or "").split())
        self.key = key or os.path.basename(self.path)
        self.finished = False
        self._stamp = None
        self.reload()
        missing = [c for c in SHEET_COLUMNS if c not in self.header]
        if missing:
            raise SystemExit(f"{sheet} is not an annotation sheet: no column {missing}")

    def describe(self) -> dict:
        lang, _, sheet = self.key.partition("/")
        if lang not in ("en", "es"):
            lang, sheet = "", os.path.basename(self.path)
        return {"task": "annotate", "name": self.name, "sheet": sheet.upper(), "lang": lang,
                "file": self.where()}

    def where(self) -> str:
        return os.path.relpath(self.path, EVAL) if self.path.startswith(EVAL) else self.path

    def progress_path(self) -> str:
        return self.path[:-len(".csv")] + ".progress.json" if self.path.endswith(".csv") \
            else self.path + ".progress.json"

    def files(self) -> list[str]:
        return [self.path, self.progress_path()]

    def reload(self) -> None:
        mtime = os.path.getmtime(self.path)
        if mtime != self._stamp:
            self.header, self.rows = read_csv(self.path)
            self._stamp = mtime

    @staticmethod
    def filled(row: dict) -> bool:
        return all(row.get(c, "").strip() in ("0", "1") for c in LABELS.values())

    def state(self) -> dict:
        self.reload()
        statuses = ["done" if self.filled(r) else ("partial" if any(
            r.get(c, "").strip() for c in LABELS.values()) else "pending") for r in self.rows]
        return {"total": len(self.rows), "done": statuses.count("done"), "statuses": statuses,
                "labels": [f"#{r.get('id', '')}" for r in self.rows],
                "doubts": [i for i, r in enumerate(self.rows) if r.get("note", "").strip()],
                "counts": {s: statuses.count(s) for s in ("done", "partial", "pending")}}

    def item(self, i: int) -> dict:
        self.reload()
        r = self.rows[i]
        return {"index": i, "id": r.get("id", ""), "task": r.get("task", ""),
                "material": r.get("material", ""),
                "conversation_before": r.get("conversation_before", ""),
                "robot_utterance": r.get("robot_utterance", ""),
                "in_frame": r.get(LABELS["in_frame"], "").strip(),
                "unsupported": r.get(LABELS["unsupported"], "").strip(),
                "note": r.get("note", "")}

    def mark(self, body: dict) -> dict:
        self.reload()
        i = body.get("index")
        if not isinstance(i, int) or not 0 <= i < len(self.rows):
            return {"error": "Unknown row"}
        row = self.rows[i]
        if str(body.get("id")) != row.get("id"):
            return {"error": "changed_on_disk", "code": "changed_on_disk"}
        for name, column in LABELS.items():
            if name in body:
                value = str(body[name])
                if value not in ("0", "1", ""):
                    return {"error": f"{name} is 1 or 0"}
                row[column] = value
        if "note" in body:
            row["note"] = str(body.get("note") or "")
        write_csv(self.path, self.header, self.rows)
        self._stamp = os.path.getmtime(self.path)
        self.save_progress()
        return {"error": ""}

    def save_progress(self, finished: bool = False) -> None:
        """The sheet's own record beside it (the CSV keeps judge_validation's columns only)."""
        path = self.progress_path()
        data = read_json(path) if os.path.exists(path) else {}
        st = self.state()
        data.update({"sheet": os.path.basename(self.path), "annotator": self.name,
                     "done": st["done"], "total": st["total"]})
        if finished:
            data["finished"] = now()
        write_json(path, stamp(data))


# ------------------------------------------------------------------ sessions
class Session:
    """One person doing one task: the task's object, and the pace of the answers (for the time
    left)."""

    def __init__(self, app, task: str, name: str, who: str, resumed: bool):
        self.app, self.task, self.name, self.who = app, task, name, who
        self.resumed = resumed
        self.times: list[float] = []

    def tick(self) -> None:
        self.times.append(time.time())

    def seconds_per_item(self) -> float:
        recent = self.times[-31:]
        gaps = [min(b - a, 300.0) for a, b in zip(recent, recent[1:])]
        return statistics.median(gaps) if len(gaps) >= 3 else DEFAULT_SECONDS[self.task]

    def state(self) -> dict:
        st = self.app.state()
        left = st["total"] - st["done"]
        st["eta_s"] = round(left * self.seconds_per_item())
        st["task"] = self.task
        return st


def task_key(task: str, app) -> str:
    d = app.describe()
    return {"annotate": f"annotate:{d.get('lang')}/{d.get('sheet', '').lower()}",
            "adherence": f"adherence:{d.get('experiment')}",
            "dataset": f"dataset:{d.get('scope')}:{d.get('case_language') or 'both'}",
            "adjudicate": f"adjudicate:{d.get('case_language') or 'both'}"}[task]


def open_session(body: dict, preset: dict | None, dry: bool = False) -> Session | dict:
    """Validate a start request (and the locked assignment, if any) and open the task. With
    dry=True only the summary for the confirmation screen is returned: nothing is written."""
    preset = preset or {}
    if preset.get("locked"):
        for k, v in preset.items():
            if k not in ("locked", "csv", "results", "n", "seed", "gate", "files",
                         "reselect") and str(body.get(k, "")) != str(v):
                raise PanelError("locked")
    name, who = person(body.get("name"))
    task = body.get("task")
    reg = registry()
    if task == "annotate":
        if preset.get("csv"):
            path, key = preset["csv"], body.get("lang", "") + "/" + body.get("sheet", "")
            if key == "/":
                key = "file/" + os.path.basename(path)
        else:
            lang, sheet = body.get("lang"), body.get("sheet")
            if lang not in ("en", "es") or sheet not in ("a", "b"):
                raise PanelError("bad_choice")
            path, key = sheet_path(lang, sheet), f"{lang}/{sheet}"
        if not os.path.exists(path):
            raise PanelError("sheet_missing")
        claim_sheet(reg, who, key)
        app = Annotation(path, name, key)
    elif task in ("dataset", "adjudicate"):
        lang = body.get("case_language") or None
        if lang not in (None, "en", "es"):
            raise PanelError("bad_choice")
        if dry and task == "adjudicate" and not os.path.exists(
                os.path.join(reviews_dir(True), "selection.json")):
            sel = adjudication_selection(preset.get("gate"))
            return {"task": task, "name": name, "total": len(sel["cases"]), "done": 0,
                    "case_language": lang or "", "file": os.path.relpath(reviews_dir(True), EVAL),
                    "eta_s": len(sel["cases"]) * DEFAULT_SECONDS[task], "tutorial_done": bool(
                        reg["people"].get(who, {}).get("tutorials", {}).get(task))}
        app = DatasetReview(name, files=preset.get("files"), language=lang,
                            traps=body.get("scope", "traps") != "all",
                            adjudicate=task == "adjudicate", gate=preset.get("gate"),
                            reselect=bool(preset.get("reselect")) and not dry)
    elif task == "adherence":
        results = preset.get("results") or os.path.join(RESULTS, os.path.basename(
            str(body.get("experiment") or "")))
        if not body.get("experiment") and not preset.get("results"):
            raise PanelError("bad_choice")
        if not os.path.isdir(os.path.join(results, "runs")):
            raise PanelError("no_dialogues")
        if dry and not os.path.exists(audit_path(results, who)):
            n = preset.get("n") or 40
            return {"task": task, "name": name, "experiment": os.path.basename(results),
                    "total": n, "done": 0, "eta_s": n * DEFAULT_SECONDS[task],
                    "file": os.path.relpath(audit_path(results, who), EVAL),
                    "tutorial_done": bool(reg["people"].get(who, {}).get("tutorials", {})
                                          .get(task))}
        app = AdherenceAudit(results, name, preset.get("n"), preset.get("seed"))
    else:
        raise PanelError("bad_choice")
    st = app.state()
    people = reg["people"].setdefault(who, {"name": name, "tasks": {}, "tutorials": {}})
    summary = dict(app.describe(), total=st["total"], done=st["done"],
                   eta_s=(st["total"] - st["done"]) * DEFAULT_SECONDS[task],
                   tutorial_done=bool(people.get("tutorials", {}).get(task)))
    if dry:
        return summary
    if task == "annotate":
        reg["sheets"][app.key] = who
        app.save_progress()
    tkey = task_key(task, app)
    resumed = tkey in people["tasks"]
    people["tasks"].setdefault(tkey, {"started": now()})["last"] = now()
    save_registry(reg)
    catalog("review_start", os.path.dirname(app.files()[0]) if app.files() else REVIEWS,
            {"task": task, "reviewer": who, "assignment": tkey, "resumed": resumed,
             "done": st["done"], "total": st["total"]})
    session = Session(app, task, name, who, resumed)
    session.summary = summary
    return session


def finish_session(session: Session) -> dict:
    st = session.app.state()
    counts = dict(st["counts"], done=st["done"], total=st["total"], doubtful=len(st["doubts"]))
    reg = registry()
    entry = reg["people"].setdefault(session.who, {"name": session.name, "tasks": {},
                                                   "tutorials": {}})
    entry["tasks"].setdefault(task_key(session.task, session.app), {})["finished"] = now()
    save_registry(reg)
    if session.task == "annotate":
        session.app.save_progress(finished=st["done"] == st["total"])
    catalog("review_done", os.path.dirname(session.app.files()[0]) if session.app.files()
            else REVIEWS, {"task": session.task, "reviewer": session.who,
                           "assignment": task_key(session.task, session.app), **counts})
    return {"files": [os.path.relpath(p, EVAL) for p in session.app.files() if os.path.exists(p)],
            "counts": counts}


def tutorial_done(session: Session) -> None:
    reg = registry()
    entry = reg["people"].setdefault(session.who, {"name": session.name, "tasks": {},
                                                   "tutorials": {}})
    entry.setdefault("tutorials", {})[session.task] = now()
    save_registry(reg)


# ------------------------------------------------------------------ server
SESSIONS: dict[str, Session] = {}
PRESET: dict = {}


def config() -> dict:
    sheets = {lang: {s: os.path.exists(sheet_path(lang, s)) for s in ("a", "b")}
              for lang in ("es", "en")}
    traps = {"traps": {"en": 0, "es": 0}, "all": {"en": 0, "es": 0}}
    for path in dataset_paths(DATASETS):
        for c in read_json(path).get("cases", []):
            lang = c.get("language")
            if lang in ("en", "es"):
                traps["all"][lang] += 1
                traps["traps"][lang] += int(bool(c.get("trap")))
    es_text, current = guide_es()
    return {"version": PANEL_VERSION, "preset": {k: v for k, v in PRESET.items()
                                                 if k in ("task", "name", "lang", "sheet",
                                                          "experiment", "scope",
                                                          "case_language", "locked")},
            "sheets": sheets, "experiments": experiments(), "cases": traps,
            "adjudication": os.path.exists(GATE) or bool(glob.glob(os.path.join(
                os.path.dirname(GATE), "*", "rep*", "*.json"))) or os.path.exists(
                os.path.join(reviews_dir(True), "selection.json")),
            "seconds": DEFAULT_SECONDS,
            "guide": {"en": md_to_html(guide_section()), "es": md_to_html(es_text),
                      "es_current": current}}


class Handler(BaseHTTPRequestHandler):
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json", cookie=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"rp={cookie}; Path=/; SameSite=Strict; HttpOnly")
        self.end_headers()
        self.wfile.write(data)

    def session(self) -> Session:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "rp" and v in SESSIONS:
                return SESSIONS[v]
        raise PanelError("no_session")

    def reply(self, fn, cookie=None):
        try:
            with self.lock:
                result = fn()
        except PanelError as exc:
            result = {"error": exc.code, "code": exc.code, "params": exc.params}
        except SystemExit as exc:
            result = {"error": str(exc), "code": "other"}
        except Exception as exc:  # noqa: B902 - shown on the page
            result = {"error": f"{type(exc).__name__}: {exc}", "code": "other"}
        if isinstance(result, tuple):
            result, cookie = result
        self.send(200, json.dumps(result), cookie=cookie)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            return self.send(200, PAGE, "text/html")
        if url.path == "/api/config":
            return self.reply(config)
        if url.path == "/api/state":
            return self.reply(lambda: self.session().state())
        if url.path == "/api/item":
            return self.reply(lambda: self.session().app.item(int(query["i"][0])))
        if url.path == "/api/prompt":
            def prompt():
                app = self.session().app
                return app.prompt(int(query["i"][0])) if isinstance(app, DatasetReview) else {}
            return self.reply(prompt)
        self.send(404, "{}")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        if path == "/api/check":
            return self.reply(lambda: {"summary": open_session(body, PRESET, dry=True)})
        if path == "/api/start":
            def start():
                session = open_session(body, PRESET)
                token = secrets.token_urlsafe(16)
                SESSIONS[token] = session
                return {"summary": session.summary, "state": session.state(),
                        "error": ""}, token
            return self.reply(start)
        if path == "/api/mark":
            def mark():
                s = self.session()
                result = s.app.mark(body)
                if os.environ.get("REVIEW_PANEL_DEBUG"):
                    print("mark", json.dumps(body, ensure_ascii=False), "->", result.get("error"),
                          file=sys.stderr, flush=True)
                if not result.get("error"):
                    s.tick()
                    result["state"] = s.state()
                return result
            return self.reply(mark)
        if path == "/api/tutorial_done":
            return self.reply(lambda: (tutorial_done(self.session()), {"error": ""})[1])
        if path == "/api/finish":
            return self.reply(lambda: dict(finish_session(self.session()), error=""))
        self.send(404, "{}")


def bind(port: int) -> ThreadingHTTPServer:
    """The first free port from `port` (20 tries), or any free one."""
    for p in list(range(port, port + 20)) + [0]:
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError:
            continue
    raise SystemExit("No free port")


def serve(port: int) -> None:
    text, current = guide_es()
    if not current:
        print("NOTE: panels/annotation_guide_es.md does not translate the current annotation guide "
              "of doc/JUDGE_VALIDATION.md (its source-sha256 differs): update the translation",
              flush=True)
    server = bind(port)
    print(f"Open http://localhost:{server.server_address[1]}  (Ctrl+C to stop; every answer is "
          f"already saved)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# ------------------------------------------------------------------ page
PAGE_FILE = os.path.join(HERE, "review_panel_page.html")


def load_page() -> str:
    with open(PAGE_FILE, encoding="utf-8") as f:
        return f.read()


PAGE = ""


# ------------------------------------------------------------------ command line
ACTIONS = ("serve", "report", "apply")


def main(argv=None) -> None:
    global PAGE
    argv = list(sys.argv[1:] if argv is None else argv)
    action = "serve"
    if len(argv) >= 2 and argv[0] in ("dataset", "adherence", "annotate") and argv[1] in ACTIONS:
        action = argv.pop(1)
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="mode")
    p = sub.add_parser("dataset", help="[report | apply] the phase A cases of E0a")
    p.add_argument("--reviewer", default=None, help="Your name")
    p.add_argument("--all", action="store_true", help="Every case, not only the traps")
    p.add_argument("--language", choices=("en", "es"), default=None)
    p.add_argument("--file", action="append", default=None,
                   help="Only this dataset (its name, e.g. confirmation); repeatable")
    p.add_argument("--adjudicate", action="store_true",
                   help="The blind adjudication of the cases half the models failed")
    p.add_argument("--gate", default=None, help="gate.json of phase A (adjudication)")
    p.add_argument("--reselect", action="store_true",
                   help="Draw the adjudication list again from the current gate")
    p.add_argument("--dry-run", action="store_true", help="apply: show the changes only")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p = sub.add_parser("adherence", help="[report] the audit of the adherence check")
    p.add_argument("results", help="The results folder of an experiment (with runs/)")
    p.add_argument("--auditor", default=None, help="Your name")
    p.add_argument("--n", type=int, default=None, help="Dialogues in the sample (40 by default)")
    p.add_argument("--seed", type=int, default=None, help="The seed of the sample (7 by default)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p = sub.add_parser("annotate", help="the E0b labels of one annotation sheet")
    p.add_argument("--annotator", default=None, help="Your name")
    p.add_argument("--sheet", choices=("a", "b"), default=None)
    p.add_argument("--lang", choices=("es", "en"), default=None)
    p.add_argument("--csv", default=None, help="Another sheet file (instead of --sheet/--lang)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    PRESET.clear()
    if args.mode == "dataset":
        if action == "report":
            return print_dataset_report(dataset_report(args.reviewer, args.adjudicate))
        if action == "apply":
            if not args.reviewer:
                raise SystemExit("apply needs --reviewer")
            apply_reviews(args.reviewer, args.file, dry_run=args.dry_run,
                          adjudicate=args.adjudicate)
            return
        task = "adjudicate" if args.adjudicate else "dataset"
        PRESET.update({"task": task, "scope": "all" if args.all else "traps",
                       "case_language": args.language or "", "files": args.file,
                       "gate": args.gate, "reselect": args.reselect})
        if args.reviewer:
            PRESET.update(name=args.reviewer, locked=True)
    elif args.mode == "adherence":
        if action == "report":
            paths = ([audit_path(args.results, slug(args.auditor))]
                     if args.auditor else sorted(glob.glob(os.path.join(
                         args.results, "audit", "adherence_*.json"))))
            paths = [p for p in paths if not p.endswith("_report.json") and os.path.exists(p)]
            if not paths:
                raise SystemExit(f"No audit in {os.path.join(args.results, 'audit')}")
            for path in paths:
                rep = adherence_report(read_json(path))
                print_adherence_report(rep)
                write_json(path[:-len(".json")] + "_report.json", stamp(rep))
            return
        if action == "apply":
            raise SystemExit("adherence has no apply")
        PRESET.update({"task": "adherence", "results": os.path.abspath(args.results),
                       "experiment": os.path.basename(os.path.normpath(args.results)),
                       "n": args.n, "seed": args.seed})
        if args.auditor:
            PRESET.update(name=args.auditor, locked=True)
    elif args.mode == "annotate":
        if action != "serve":
            raise SystemExit("annotate has no report or apply: judge_validation.py report "
                             "reads the sheets")
        PRESET.update({"task": "annotate"})
        if args.csv:
            PRESET.update(csv=os.path.abspath(args.csv))
        if args.sheet:
            PRESET["sheet"] = args.sheet
        if args.lang:
            PRESET["lang"] = args.lang
        if args.annotator and (args.csv or (args.sheet and args.lang)):
            PRESET.update(name=args.annotator, locked=True)
        elif args.annotator:
            PRESET["name"] = args.annotator
    PAGE = load_page()
    serve(args.port)


if __name__ == "__main__":
    main()
