# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
LLMDialogClient — thin wrapper around the LLM inference action.

Owns two independent sessions (NLU for understanding, NLG for generation).
All structured-output calls retry on parse failure up to MAX_RETRIES.
NLG calls fall back to a safe phrase in the dialog language if the LLM is unavailable.
The wait for the model is bounded (CallLimits): each call waits at most llm_timeout, a call
that times out is not repeated and the other calls of the same skill fail at once, and once the
time budget of the reply is spent no call is made: the questions fall back.
"""

import inspect  # TRACE
import json
import logging
import re
import time  # TRACE
from difflib import SequenceMatcher
from typing import Callable

from .dialog_context import DialogContext
from .dialog_frame import TURN_ACTS, DialogFrame, _VALID_TYPES
# canonical_language: re-exported for the node and the evaluation
from .languages import canonical_language, language_names, load_language  # noqa: F401
from .prompt_builder import PromptBuilder
from .utils import (cancel_intent_of, local_cancel_check, local_yes_no, normalize_text,
                    parse_loose_json, spoken_ask, to_snake_case)

InferFn = Callable[[str, str, int | None, dict | None], str | None]

_MIN_NLG_CHARS = 3

# The fixed phrases of each language (the fallback of every generated line, the lines that are
# never generated), the acknowledgments and the fillers are in the language files
# (config/languages/*.yaml, languages.py), selected by the `language` parameter.


# Turn intents of the procedure strategy (classify_step_turn).
_STEP_INTENTS = ("done", "question", "problem", "repeat", "back", "skip", "unclear")

# Turn intents of the roleplay strategy (classify_roleplay_turn) and its score range.
_ROLEPLAY_INTENTS = ("continue", "repeat", "coach", "end", "scene_end")
_ROLEPLAY_SCORE_MIN, _ROLEPLAY_SCORE_MAX = 1, 5
# Two character questions at least this similar count as the same question.
_REPEAT_SIMILARITY = 0.75


def _repeats_question(line: str, history: list[dict]) -> str:
    """What the new line asks for when the character already asked it, else ""."""
    question = spoken_ask(line)
    if not question:
        return ""
    for turn in history:
        if turn["role"] != "assistant":
            continue
        earlier = spoken_ask(turn["content"])
        if earlier and SequenceMatcher(None, question, earlier).ratio() >= _REPEAT_SIMILARITY:
            return question
    return ""


def _keep_first_question(text: str) -> str:
    """Cut a spoken line after its first question when it asks more than one.
    Small models often stack two questions; the character must ask one at a time."""
    first = text.find("?")
    if first < 0 or text.find("?", first + 1) < 0:
        return text
    return text[: first + 1].strip()


def default_ack_phrases(language: str) -> tuple[str, ...]:
    """The acknowledgments of a language (its language file), used when no explicit override.
    An unknown language raises UnknownLanguageError, as every helper below."""
    return load_language(language).ack_phrases


def default_filler_phrases(language: str) -> tuple[str, ...]:
    """The conversational fillers of a language, used when no explicit override."""
    return load_language(language).filler_phrases


def default_timeout_prompt(language: str) -> str:
    """The first prompt after a silence ("take your time") of a language, used when no explicit
    override."""
    return load_language(language).phrases["timeout"]


def default_phrases(language: str) -> dict[str, str]:
    """The full fixed-phrase set of a language (the Strategies editor's defaults)."""
    return dict(load_language(language).phrases)


def phrase_languages() -> list[str]:
    """The languages with a language file."""
    return language_names()


class CallLimits:
    """How long the questions of one skill (one request to the intelligence) may wait for the
    model. Shared by the questions of the request, which make their calls one after another.

      call_timeout  the longest wait for one call (s; llm_timeout); <= 0: no limit of its own
      budget_s      what is left of the time budget of the reply being prepared when the request
                    arrives (s); <= 0: no budget
      reasoning_timeout  the longest wait for a call that may reason (s; selective reasoning):
                    only a hung model reaches it; <= 0: no limit

    A call waits at most call_timeout, or what is left of the budget if that is shorter
    (`wait`, set before each call for the function that calls the model). A call that times out
    is not repeated, and the model is taken as unresponsive for the rest of the request: its
    other calls fail at once, so that a hung model costs one wait, not one per question. Once
    the budget is spent, no call is made. A call that is not made returns nothing, as a failed
    call: the question falls back to its safe default (a fixed phrase, "not understood").

    A call that may reason is not cut (author, 2026-11-02: reasoning runs to its end and the
    latencies are measured): it waits only for reasoning_timeout, which a hung model reaches
    (then it is taken as unresponsive, as above), not for call_timeout nor for the budget, and
    the time it takes is not charged to the budget, so that the other calls of the reply keep
    theirs."""

    # A call with less time than this left is not worth making
    MIN_CALL_S = 0.5
    # A call that returned nothing this close to its time limit timed out
    _TIMEOUT_TOLERANCE_S = 0.1

    def __init__(self, call_timeout: float = 0.0, budget_s: float = 0.0, clock=time.monotonic,
                 reasoning_timeout: float = 0.0):
        self._clock = clock
        self.call_timeout = float(call_timeout or 0.0)
        self.reasoning_timeout = float(reasoning_timeout or 0.0)
        self.deadline = clock() + float(budget_s) if budget_s and budget_s > 0 else None
        self.wait: float | None = None
        self.unresponsive = False
        self.timeouts = 0  # calls that timed out
        self.skipped = 0  # calls not made: the model unresponsive or the budget spent

    def now(self) -> float:
        return self._clock()

    def time_left(self) -> float | None:
        """Seconds left of the budget (None: no budget)."""
        return None if self.deadline is None else self.deadline - self._clock()

    def next_wait(self, think: bool = False) -> float | None:
        """How long the next call may wait for the model (None: no limit); think: it may
        reason (only reasoning_timeout bounds it)."""
        if think:
            return self.reasoning_timeout if self.reasoning_timeout > 0 else None
        limits = [t for t in (self.call_timeout if self.call_timeout > 0 else None,
                              self.time_left()) if t is not None]
        return max(min(limits), 0.0) if limits else None

    def refusal(self, think: bool = False) -> str:
        """Why the next call is not made ("": it is); think: it may reason (the budget does
        not stop it)."""
        if self.unresponsive:
            return "the model did not answer an earlier call of this skill"
        if think:
            return ""
        left = self.time_left()
        if left is not None and left < self.MIN_CALL_S:
            return "the time budget of the reply is spent"
        return ""

    def done(self, started: float, wait: float | None, answered: bool,
             think: bool = False) -> None:
        """Record a call: one that returned nothing at its time limit timed out (think: it may
        reason; its time is given back to the budget)."""
        if think and self.deadline is not None:
            self.deadline += self._clock() - started
        if answered or wait is None:
            return
        if self._clock() - started >= wait - self._TIMEOUT_TOLERANCE_S:
            self.unresponsive = True
            self.timeouts += 1


class _ListOp:
    """Sentinel carrying a list operation produced by NLU coercion."""

    __slots__ = ("kind", "items")

    def __init__(self, kind: str, items: list[str]):
        self.kind = kind  # "add" | "remove" | "replace"
        self.items = items

    def __repr__(self) -> str:
        return f"_ListOp({self.kind}, {self.items!r})"


class LLMDialogClient:
    MAX_RETRIES = 2

    def __init__(
        self,
        nlu_session_id: str,
        nlg_session_id: str,
        infer_fn: InferFn,
        prompt_builder: PromptBuilder,
        language: str = "English",
        history_max_turns: int = 200,
        phrase_overrides: dict[str, str] | None = None,
        routes: dict[str, tuple[str, str]] | None = None,
        limits: CallLimits | None = None,
        reasoning: str = "none",
    ):
        # How long the calls of this client may wait for the model (no limit by default: the
        # intelligence passes llm_timeout and the budget of the request). The function that calls
        # the model reads limits.wait, the wait of the call in progress
        self.limits = limits or CallLimits()
        # Whether the last call of this client to the model gave a usable answer: True, False
        # (an error, no answer in time, a call not made because the budget was spent or the
        # model is unresponsive, an understanding answer that is not JSON, a generated line too
        # short to say) or None (no call made). The intelligence keeps it in the state of the
        # dialogue (model_down) between requests: with the model known to be down, an exact
        # stop command ends the conversation without the confirmation question (cancel_check)
        self.model_status: bool | None = None
        self._nlu_sid = nlu_session_id
        self._nlg_sid = nlg_session_id
        self._infer = infer_fn
        self._pb = prompt_builder
        self._log = logging.getLogger("LLMDialogClient")
        # The phrases of the language (its language file; an unknown language raises
        # UnknownLanguageError), with operator overrides (from the Strategies editor) layered on
        # top so only the edited phrases change.
        self.language = load_language(language).name
        self._fb = {**load_language(language).phrases, **(phrase_overrides or {})}
        self._history_max_turns = history_max_turns
        # Questions sent to another model: question (the method that asks it, e.g.
        # classify_intent) -> (nlu session, nlg session) of that model. A route that names no
        # question is a configuration error: it would be ignored silently, so it stops here.
        self._routes = self.resolve_routes(routes or {})
        # Reasoning: "none" (no question lets the model reason) or "critical" (only the
        # REASONING_QUESTIONS)
        if reasoning not in ("none", "critical"):
            raise ValueError(f"reasoning must be 'none' or 'critical', not {reasoning!r}")
        self._reasoning = reasoning

    # The questions whose wrong answer is a critical error (the safety gate of E0a: a correction
    # taken as a confirmation, a wrong answer graded right, an unsupported answer verified, a
    # stop not asked for, an option not chosen, a step not done, an end not asked for). With
    # reasoning="critical" the model may reason before answering them, and only them: the other
    # questions and the generated lines stay fast (selective reasoning, decided by the author,
    # 2026-11-02, after qwen3:14b went from 11 to 7 critical errors in phase A with it)
    REASONING_QUESTIONS = frozenset({
        "classify_intent", "evaluate_quiz_answer", "judge_teach_back", "answer_from_material",
        "verify_answer", "classify_cancel_reply", "map_scale_answer", "classify_step_turn",
        "classify_roleplay_turn",
    })

    # Names by which a question may also be routed: the prompt template it uses, when it differs
    # from the method that asks it
    ROUTE_ALIASES = {"extract_slots": "extract_turn"}

    @classmethod
    def questions(cls) -> set[str]:
        """The questions that can be routed: the methods that ask the LLM themselves."""
        names = set()
        for name, fn in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("__"):
                continue
            try:
                src = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            if "self._call_nlu(" in src or "self._call_nlg(" in src:
                names.add(name)
        names -= {"_call_nlu", "_call_nlg"}
        return names

    @classmethod
    def resolve_routes(cls, routes: dict) -> dict:
        """The routes keyed by the method that asks each question (aliases resolved); an unknown
        question raises ValueError."""
        valid = cls.questions()
        out = {}
        for question, sessions in routes.items():
            method = cls.ROUTE_ALIASES.get(question, question)
            if method not in valid:
                raise ValueError(
                    f"Route for an unknown question '{question}'. Valid questions: "
                    f"{', '.join(sorted(valid | set(cls.ROUTE_ALIASES)))}")
            out[method] = sessions
        return out

    def phrase(self, key: str) -> str:
        """A fixed phrase of the active language (with the operator's overrides): the lines the
        robot says without the LLM (silences, closings, acknowledgments of a skip)."""
        return self._fb[key]

    @property
    def history_max_turns(self) -> int:
        return self._history_max_turns

    def _fb_fmt(self, key: str, **kwargs) -> str:
        """Return a fallback string, formatting any {placeholders} it contains."""
        return self._fb[key].format(**kwargs) if kwargs else self._fb[key]

    # ==== TASK LEVEL ====

    def classify_task_mode(self, task: str, ctx: DialogContext | None = None) -> str:
        prompt, schema = self._pb.classify_task_mode(task, ctx)
        raw = self._call_nlu(prompt, schema, max_tokens=50)
        if not raw:
            return "slot_filling"
        try:
            mode = parse_loose_json(raw).get("mode", "slot_filling")
            return mode if mode in ("slot_filling", "explanation") else "slot_filling"
        except Exception:
            return "slot_filling"

    def choose_dialogue_behavior(
        self, task: str, behaviors: list[dict], ctx: DialogContext | None = None
    ) -> int:
        """The id of the behavior chosen among `behaviors`, or -1 if the LLM gave none."""
        prompt, schema = self._pb.choose_dialogue_behavior(task, behaviors, ctx)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=50)
            if not raw:
                continue
            try:
                return int(parse_loose_json(raw).get("behavior_id", -1))
            except Exception:
                continue
        return -1

    def create_frame(self, task: str, ctx: DialogContext | None = None) -> DialogFrame | None:
        prompt, schema = self._pb.create_frame(task, ctx)
        for attempt in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=500)
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
                slots = data.get("slots", [])
                if not slots:
                    continue

                frame_schema: dict = {}
                types: dict = {}
                conditions: dict = {}
                canon_vals: dict = {}

                for s in slots:
                    if not isinstance(s, dict) or "name" not in s:
                        continue
                    name = to_snake_case(s["name"])
                    frame_schema[name] = None
                    t = s.get("type", "str")
                    types[name] = t if t in _VALID_TYPES else "str"
                    if "condition_slot" in s and "condition_value" in s:
                        conditions[name] = [
                            (to_snake_case(s["condition_slot"]), str(s["condition_value"]))
                        ]
                    if isinstance(s.get("canonical_values"), list):
                        canon_vals[name] = [str(v).lower().strip() for v in s["canonical_values"]]

                if not frame_schema:
                    continue

                return DialogFrame(
                    frame_schema,
                    types,
                    conditions=conditions or None,
                    canonical_values=canon_vals or None,
                    history_max_turns=self._history_max_turns,
                )
            except Exception as exc:
                self._log.warning(f"Frame parse error (attempt {attempt + 1}): {exc}")
        return None

    def audit_frame(self, frame: DialogFrame, task: str, ctx: DialogContext | None = None) -> int:
        """Second pass over the frame's canonical_values against resource context.
        Mutates frame in place. Best-effort — any failure is logged and ignored."""
        if ctx is None or not getattr(ctx, "resources", None):
            return 0

        schema_lines: list[str] = []
        for k in frame.all_keys():
            canon = frame.canonical_values(k)
            slot_type = frame.slot_type(k)
            if canon:
                schema_lines.append(f"  - {k} (type: {slot_type}): canonical_values = {canon}")
            elif slot_type in ("str", "list_str"):
                schema_lines.append(f"  - {k} (type: {slot_type}): canonical_values = []")
        if not schema_lines:
            return 0

        schema = "\n".join(schema_lines)
        prompt, json_schema = self._pb.audit_frame(task, schema, ctx)
        raw = self._call_nlu(prompt, json_schema, max_tokens=300)
        if not raw:
            return 0

        try:
            data = parse_loose_json(raw)
        except Exception as exc:
            self._log.warning(f"Schema-audit parse error: {exc}")
            return 0

        amend = getattr(frame, "amend_canonical_values", None)
        if amend is None:
            self._log.warning("DialogFrame missing amend_canonical_values; schema audit skipped.")
            return 0

        applied = 0
        for action in data.get("actions", []):
            slot = action.get("slot")
            kind = action.get("action")
            values = action.get("values", [])
            if not slot or not values or slot not in frame.all_keys():
                continue
            try:
                if kind == "add" and amend(slot, add=values):
                    applied += 1
                elif kind == "remove" and amend(slot, remove=values):
                    applied += 1
            except Exception as exc:
                self._log.warning(f"Schema audit action ignored ({exc}).")

        if applied:
            self._log.info(f"Schema audit applied {applied} canonical-value action(s).")
        return applied

    # ==== SLOT FILLING ====

    def get_opening(self, task: str, frame: DialogFrame, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.opening(task, frame, ctx)
        res = self._call_nlg(prompt, max_tokens=100)
        return self._clean(res) or self._fb["opening"]

    def get_opening_direct(self, task: str, frame: DialogFrame, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.opening_direct(task, frame, ctx)
        res = self._call_nlg(prompt, max_tokens=100)
        return self._clean(res) or self._fb["opening"]

    def get_resume_opening(
        self, task: str, frame: DialogFrame, next_slot: str | None, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.resume_opening(task, frame, next_slot, ctx)
        res = self._call_nlg(prompt, max_tokens=100)
        return self._clean(res) or self._fb["resume"]

    def extract_slots(
        self, user_text: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> list[dict]:
        """Single-pass NLU during gathering. Detects new values and corrections."""
        return self.extract_turn(user_text, frame, ctx)[1]

    def extract_turn(
        self, user_text: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> tuple[str, list[dict]]:
        """Single-pass NLU during gathering: what the turn does (TURN_ACTS) and the values it
        gives or corrects. An unparseable answer is "other" with no values."""
        prompt, schema = self._pb.extract_slots(user_text, frame, ctx)
        for attempt in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=300)
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
                extractions = self._validate_extractions(data.get("extractions", []), frame)
                act = data.get("act")
                if act not in TURN_ACTS:
                    act = "values" if extractions else "other"
                return act, extractions
            except Exception as exc:
                self._log.warning(f"Extraction parse error (attempt {attempt + 1}): {exc}")
        return "other", []

    def generate_response(
        self,
        task: str,
        frame: DialogFrame,
        user_text: str,
        extracted_slots: list[dict],
        attempts_exceeded: list[str],
        ctx: DialogContext | None = None,
        rejected: dict | None = None,
    ) -> str:
        prompt = self._pb.generate_response(
            task, frame, user_text, extracted_slots, attempts_exceeded, ctx, rejected
        )
        res = self._call_nlg(prompt, max_tokens=120)
        return self._clean(res) or self._fb["unclear"]

    def get_confirmation_question(
        self, task: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.ask_confirmation(task, frame, ctx)
        res = self._call_nlg(prompt, max_tokens=150)
        return self._clean(res) or self._fb["confirm_q"]

    def classify_intent(
        self, user_text: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        """Confirming-phase intent classifier. Returns (intent, corrections)."""
        prompt, schema = self._pb.classify_intent(user_text, frame, ctx)
        for attempt in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=200)
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
                intent = data.get("intent", "unclear")
                if intent not in ("confirms", "corrects", "question", "unclear"):
                    intent = "unclear"

                corrs: dict = {}
                for slot, val in (data.get("corrections") or {}).items():
                    if val is None or slot not in frame.all_keys():
                        continue
                    coerced = self._coerce_value(val, frame.slot_type(slot))
                    if coerced is None:
                        continue
                    if isinstance(coerced, _ListOp):
                        items = self._enforce_canonical_list(slot, coerced.items, frame)
                        if not items and coerced.kind != "remove":
                            continue
                        corrs[slot] = {f"_{coerced.kind}": True, "items": items}
                    elif isinstance(coerced, list):
                        items = self._enforce_canonical_list(slot, coerced, frame)
                        if not items:
                            continue
                        corrs[slot] = items
                    else:
                        if not self._enforce_canonical_scalar(slot, coerced, frame):
                            continue
                        corrs[slot] = coerced

                return intent, corrs
            except Exception as exc:
                self._log.warning(f"Intent parse error (attempt {attempt + 1}): {exc}")
        return "unclear", {}

    def answer_confirmation_question(
        self, user_text: str, task: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.answer_confirmation_question(user_text, task, frame, ctx)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["good_question"]

    def response_to_intent(
        self,
        task: str,
        intent: str,
        corrections: dict,
        frame: DialogFrame,
        ctx: DialogContext | None = None,
    ) -> str:
        prompt = self._pb.response_to_intent(task, intent, corrections, frame, ctx)
        res = self._call_nlg(prompt, max_tokens=100)
        return self._clean(res) or self._fb["intent_response"]

    # ==== EXPLANATION ====

    def generate_explanation(self, task: str, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.explanation_opening(task, ctx)
        res = self._call_nlg(prompt, max_tokens=300)
        return self._clean(res) or self._fb["explain_open"]

    def generate_explanation_direct(self, task: str, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.explanation_opening_direct(task, ctx)
        res = self._call_nlg(prompt, max_tokens=300)
        return self._clean(res) or self._fb["explain_open"]

    def extract_key_points(self, task: str, resources: list[dict]) -> list[dict]:
        """The key points the user must be able to say back ([{point, critical}]), listed from
        the task and the material. On failure, the task itself as the only (critical) point."""
        prompt, schema = self._pb.extract_key_points(task, resources)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=400)
            if not raw:
                continue
            try:
                items = parse_loose_json(raw).get("key_points", [])
            except Exception:
                continue
            points = [
                {"point": str(i["point"]).strip(), "critical": i.get("critical", True) is not False}
                for i in items if isinstance(i, dict) and str(i.get("point", "")).strip()
            ]
            if points:
                return points
        return [{"point": task, "critical": True}]

    def judge_teach_back(
        self, user_text: str, key_points: list[dict], asked: str, ctx: DialogContext | None = None
    ) -> dict:
        """What the reply to a check is and, if it says content back, the status of each key
        point in it (correct, incomplete: part of it right and nothing false, wrong or
        missing). Returns {"kind", "points": [(index, status, said)]}; kind is restates,
        question, declines, not_understood or other ("other" if the LLM fails, so that a
        sustained failure ends as a communication breakdown, not in a loop)."""
        prompt, schema = self._pb.judge_teach_back(user_text, key_points, asked, ctx)
        kinds = ("restates", "question", "declines", "not_understood", "other")
        for _ in range(self.MAX_RETRIES):
            # One entry per key point (with what was said wrong): room for all of them
            raw = self._call_nlu(prompt, schema, max_tokens=120 + 60 * len(key_points))
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
            except Exception:
                continue
            kind = data.get("kind") if data.get("kind") in kinds else "other"
            points = []
            for item in data.get("points", []) if kind == "restates" else []:
                try:
                    i = int(item.get("id")) - 1
                except (TypeError, ValueError, AttributeError):
                    continue
                status = item.get("status")
                # A second entry for the same point is ignored (the first one counts)
                if (0 <= i < len(key_points)
                        and status in ("correct", "incomplete", "wrong", "missing")
                        and all(i != j for j, _, _ in points)):
                    points.append((i, status, str(item.get("said", "")).strip()))
            return {"kind": kind, "points": points}
        return {"kind": "other", "points": []}

    def repair_explanation(
        self, task: str, points: list[dict], correct: list[str], ctx: DialogContext | None = None
    ) -> str:
        """Explain again only the given key points (with what the user said wrong), in other
        words; confirm briefly what was right."""
        prompt = self._pb.repair_explanation(task, points, correct, ctx)
        res = self._call_nlg(prompt, max_tokens=200)
        return self._clean(res) or " ".join(p["point"] for p in points)

    def targeted_question(self, point: str, ctx: DialogContext | None = None) -> str:
        """A direct question whose answer is the key point (the fallback of teach-back)."""
        prompt = self._pb.targeted_question(point, ctx)
        res = self._call_nlg(prompt, max_tokens=60)
        return self._clean(res) or self._fb["teach_back"]

    def ask_missing_points(
        self, task: str, correct: list[str], missing: list[str], ctx: DialogContext | None = None
    ) -> str:
        """Confirm the points said back right and ask only for the ones still missing (a partial
        teach-back that made progress), without giving their content."""
        prompt = self._pb.ask_missing_points(task, correct, missing, ctx)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["teach_back_again"]

    def answer_explanation_question(
        self, user_text: str, task: str, history: list[dict], ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.answer_explanation_question(user_text, task, history, ctx)
        res = self._call_nlg(prompt, max_tokens=150)
        return self._clean(res) or self._fb["good_question"]

    def rephrase_explanation(
        self, task: str, history: list[dict], attempt_number: int, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.rephrase_explanation(task, history, attempt_number, ctx)
        res = self._call_nlg(prompt, max_tokens=300)
        return self._clean(res) or self._fb["explain_open"]

    def understanding_confirmed_response(self, task: str, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.understanding_confirmed(task, ctx)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["understand_ok"]

    # ==== GROUNDED QUESTION ANSWERING ====

    def qa_opening(self, task: str, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.qa_opening(task, ctx)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["qa_invite"]

    def answer_from_material(
        self, user_text: str, task: str, material: list[dict], history: list[dict],
        ctx: DialogContext | None = None,
    ) -> dict:
        """What the turn is and, for an answerable question, the answer from the material only.
        Returns {"act", "kind", "answer", "evidence"}; kind is answerable, not_in_material,
        off_task, comment, done, repeat or other ("other" if the LLM fails).

        The model first says what the person does ("act"): asks for something, has finished
        (wants nothing more) or only reacts. Only "asks" can lead to an answer: "finished" is
        "done" and "reacts" is "comment" whatever the rest of the model's output says, so that
        the robot never volunteers information nobody asked for (decided by the author,
        2026-10-31). An answer without a valid "act" is read by its "kind", as before."""
        prompt, schema = self._pb.answer_from_material(user_text, task, material, history)
        kinds = ("answerable", "not_in_material", "off_task", "comment", "done", "repeat",
                 "other")
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=300)
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
            except Exception:
                continue
            act = data.get("act") if data.get("act") in (
                "asks", "finished", "reacts", "unclear") else ""
            if data.get("sense") == "no":
                # Words that make no sense: not understood, whatever the rest says
                # (provisional, 2026-11-01)
                act = "unclear"
            if act == "unclear":
                # Not understood: a turn without progress (the tree's breakdown after max_stalled)
                return {"act": act, "kind": "other", "answer": "", "evidence": []}
            if act in ("finished", "reacts"):
                return {"act": act, "kind": "done" if act == "finished" else "comment",
                        "answer": "", "evidence": []}
            kind = data.get("kind") if data.get("kind") in kinds else "other"
            return {"act": act, "kind": kind,
                    "answer": self._clean(str(data.get("answer", ""))) or "",
                    "evidence": [str(e) for e in data.get("evidence", []) if str(e).strip()]}
        return {"act": "", "kind": "other", "answer": "", "evidence": []}

    def verify_finished(self, user_text: str, robot: str = "") -> bool:
        """Whether the person said they want nothing more (the closing of grounded QA is
        verified before the dialogue ends: provisional, 2026-11-01). False if the LLM fails: an
        unverified closing does not end the dialogue."""
        prompt, schema = self._pb.verify_finished(user_text, robot)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=20)
            if not raw:
                continue
            try:
                return parse_loose_json(raw).get("finished") is True
            except Exception:
                continue
        return False

    def verify_answer(
        self, answer: str, material: list[dict], ctx: DialogContext | None = None
    ) -> bool:
        """Whether every claim of the answer is supported by the material. False if the LLM
        fails: an answer that cannot be verified is not said."""
        prompt, schema = self._pb.verify_answer(answer, material)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=200)
            if not raw:
                continue
            try:
                return parse_loose_json(raw).get("supported") is True
            except Exception:
                continue
        return False

    # ==== QUESTIONNAIRE ====

    def map_scale_answer(
        self, user_text: str, item: str, options: list[str], ctx: DialogContext | None = None
    ) -> dict:
        """The option of the scale the answer chooses. Returns {"kind", "option"}: kind is
        option (with the option's index), ambiguous, meaning, refuses, repeat or other ("other"
        if the LLM fails: nothing is recorded for the user)."""
        prompt, schema = self._pb.map_scale_answer(user_text, item, options)
        kinds = ("option", "ambiguous", "meaning", "refuses", "repeat", "other")
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=80)
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
            except Exception:
                continue
            kind = data.get("kind") if data.get("kind") in kinds else "other"
            if kind == "option":
                try:
                    index = int(data.get("option")) - 1
                except (TypeError, ValueError):
                    return {"kind": "ambiguous", "option": -1}
                if not 0 <= index < len(options):
                    return {"kind": "ambiguous", "option": -1}
                return {"kind": "option", "option": index}
            return {"kind": kind, "option": -1}
        return {"kind": "other", "option": -1}

    # ==== QUIZ ====

    def quiz_opening(self, task: str, total: int) -> str:
        prompt = self._pb.quiz_opening(task, total)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["quiz_opening"]

    def evaluate_quiz_answer(self, question: str, expected: str, given: str) -> dict:
        """Returns {"kind": "answer" | "repeat", "correct": bool, "feedback": str}: "repeat"
        when the reply is not an attempt to answer (asks to repeat or clarify the question, did
        not hear or understand it). If the LLM fails every retry, the answer is right only when it
        is the expected one exactly (normalised): a substring match would grade "Mercury... no,
        lead" as "Mercury", and a wrong answer graded right is the error to avoid."""
        prompt, json_schema = self._pb.evaluate_quiz_answer(question, expected, given)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, json_schema, max_tokens=120)
            if not raw:
                continue
            try:
                data = parse_loose_json(raw)
                if isinstance(data, dict) and data.get("kind") == "repeat":
                    return {"kind": "repeat", "correct": False, "feedback": ""}
                if isinstance(data, dict) and "correct" in data:
                    correct = bool(data["correct"])
                    feedback = str(data.get("feedback", "")).strip()
                    if not feedback:
                        feedback = (
                            self._fb["quiz_correct"]
                            if correct
                            else self._fb_fmt("quiz_wrong", expected=expected)
                        )
                    return {"kind": "answer", "correct": correct, "feedback": feedback}
            except Exception as exc:
                self._log.warning(f"Quiz evaluation parse error: {exc}")

        correct = normalize_text(given) == normalize_text(expected)
        fb = self._fb["quiz_correct"] if correct else self._fb_fmt("quiz_wrong", expected=expected)
        return {"kind": "answer", "correct": correct, "feedback": fb}

    def quiz_rephrase_question(self, question: str, expected: str) -> str:
        """The question in simpler words, without revealing the answer; the question itself if
        the LLM fails."""
        prompt = self._pb.quiz_rephrase_question(question, expected)
        res = self._call_nlg(prompt, max_tokens=60)
        return self._clean(res) or question

    def quiz_next_question(self, feedback: str, n: int, total: int, question: str) -> str:
        prompt = self._pb.quiz_next_question(feedback, n, total, question)
        res = self._call_nlg(prompt, max_tokens=100)
        return self._clean(res) or f"{feedback} {question}"

    def quiz_summary(self, task: str, correct: int, total: int, wrongs: list[str]) -> str:
        wrongs_str = "; ".join(wrongs) if wrongs else ""
        prompt = self._pb.quiz_summary(task, correct, total, wrongs_str)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["quiz_summary"]

    # ==== PROCEDURE ====

    def procedure_opening(self, task: str, total: int, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.procedure_opening(task, total, ctx)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["procedure_opening"]

    def procedure_step(
        self, step: str, n: int, total: int, situation: str, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.procedure_step(step, n, total, situation, ctx)
        res = self._call_nlg(prompt, max_tokens=150)
        return self._clean(res) or self._fb_fmt("procedure_step", n=n, total=total, step=step)

    def classify_step_turn(
        self,
        user_text: str,
        step: str,
        check: str,
        history: list[dict],
        ctx: DialogContext | None = None,
    ) -> str:
        prompt, schema = self._pb.classify_step_turn(user_text, step, check, history, ctx)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=60)
            if not raw:
                continue
            try:
                intent = parse_loose_json(raw).get("intent", "unclear")
                return intent if intent in _STEP_INTENTS else "unclear"
            except Exception:
                continue
        return "unclear"

    def procedure_answer_question(
        self,
        user_text: str,
        task: str,
        step: str,
        history: list[dict],
        ctx: DialogContext | None = None,
    ) -> str:
        prompt = self._pb.procedure_answer_question(user_text, task, step, history, ctx)
        res = self._call_nlg(prompt, max_tokens=150)
        return self._clean(res) or f"{self._fb['good_question']} {self._fb['procedure_remind']}"

    def procedure_help_step(
        self,
        task: str,
        step: str,
        history: list[dict],
        attempt_number: int,
        ctx: DialogContext | None = None,
    ) -> str:
        prompt = self._pb.procedure_help_step(task, step, history, attempt_number, ctx)
        res = self._call_nlg(prompt, max_tokens=200)
        return self._clean(res) or step

    def procedure_remind(self, step: str, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.procedure_remind(step, ctx)
        res = self._call_nlg(prompt, max_tokens=60)
        return self._clean(res) or self._fb["procedure_remind"]

    def procedure_summary(
        self,
        task: str,
        completed: int,
        total: int,
        not_done: list[str],
        ctx: DialogContext | None = None,
    ) -> str:
        prompt = self._pb.procedure_summary(task, completed, total, "; ".join(not_done), ctx)
        res = self._call_nlg(prompt, max_tokens=80)
        return self._clean(res) or self._fb["procedure_summary"]

    # ==== ROLEPLAY ====

    def roleplay_intro(self, task: str, scene: dict, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.roleplay_intro(task, scene, ctx)
        res = self._call_nlg(prompt, max_tokens=100)
        return self._clean(res) or self._fb["roleplay_intro"]

    def roleplay_reply(
        self, scene: dict, history: list[dict], ctx: DialogContext | None = None
    ) -> str:
        line = self._roleplay_line(self._pb.roleplay_reply(scene, history, ctx))
        repeated = _repeats_question(line, history)
        if repeated:
            # Small models loop on an unanswered question: retry once, told which.
            retry = self._roleplay_line(self._pb.roleplay_reply(scene, history, ctx, avoid=repeated))
            line = retry or line
        return line or self._fb["roleplay_reply"]

    def _roleplay_line(self, prompt: str) -> str:
        res = self._call_nlg(prompt, max_tokens=120)
        return _keep_first_question(self._clean(res) or "")

    def classify_roleplay_turn(
        self, user_text: str, scene: dict, history: list[dict], ctx: DialogContext | None = None
    ) -> str:
        """The kind of the user's turn in the scene (_ROLEPLAY_INTENTS), or "unclear" when the
        model gives no valid reading (no answer, not the JSON asked, an intent outside the list):
        the robot asks to repeat, a turn without progress, as every strategy does with a turn the
        model could not read, so that a sustained failure ends as a breakdown. Reading it as a
        line of the scene would let a model that is down play the whole scene to its limit."""
        prompt, schema = self._pb.classify_roleplay_turn(user_text, scene, history, ctx)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=40)
            if not raw:
                continue
            try:
                intent = parse_loose_json(raw).get("intent")
            except Exception:
                continue
            if intent in _ROLEPLAY_INTENTS:
                return intent
        return "unclear"

    def roleplay_coach(
        self, user_text: str, scene: dict, history: list[dict], ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.roleplay_coach(user_text, scene, history, ctx)
        res = self._call_nlg(prompt, max_tokens=120)
        return self._clean(res) or self._fb["roleplay_coach"]

    def evaluate_roleplay(
        self, scene: dict, history: list[dict], ctx: DialogContext | None = None
    ) -> dict:
        """Scores the user on each criterion, one call per criterion. Returns
        {"scores": [{"criterion","score","evidence","comment"}]}, or {} when no
        criterion could be scored. The evidence is the user's lines cited by
        number, copied by the code, so it is always verbatim."""
        user_lines = [t["content"] for t in history if t["role"] == "user"]
        scores = []
        for criterion in scene["criteria"]:
            prompt, schema = self._pb.evaluate_roleplay_criterion(
                scene, history, user_lines, criterion, ctx
            )
            for attempt in range(self.MAX_RETRIES):
                raw = self._call_nlu(prompt, schema, max_tokens=200)
                if not raw:
                    continue
                try:
                    data = parse_loose_json(raw)
                    score = int(round(float(data.get("score"))))
                    lines = [
                        n for n in data.get("lines") or []
                        if isinstance(n, int) and 1 <= n <= len(user_lines)
                    ]
                    # No line of the user shows the criterion: never shown, whatever the
                    # score said (small models gave 4 to a criterion the user never did)
                    if not lines:
                        score = _ROLEPLAY_SCORE_MIN
                    scores.append({
                        "criterion": criterion,
                        "score": min(max(score, _ROLEPLAY_SCORE_MIN), _ROLEPLAY_SCORE_MAX),
                        "evidence": " / ".join(user_lines[n - 1] for n in sorted(set(lines))[:2]),
                        "comment": str(data.get("comment", "")).strip(),
                    })
                    break
                except Exception as exc:
                    self._log.warning(
                        f"Roleplay evaluation parse error on {criterion!r} (attempt {attempt + 1}): {exc}"
                    )
        return {"scores": scores} if scores else {}

    def roleplay_feedback(
        self, scene: dict, evaluation: dict, ended_by: str, ctx: DialogContext | None = None
    ) -> dict:
        """Spoken closing feedback. Returns {"text", "highlight", "improve"}.
        The scores are the point of it, so they are spoken from a fixed phrase
        between the LLM's closing and its highlight and advice."""
        if not evaluation:
            return {"text": self._fb["roleplay_feedback"], "highlight": "", "improve": ""}
        scores = self._fb_fmt(
            "roleplay_scores",
            scores="; ".join(
                self._fb_fmt("roleplay_score_item", criterion=s["criterion"], score=s["score"])
                for s in evaluation["scores"]
            ),
        )
        parts = {"closing": "", "highlight": "", "improve": ""}
        prompt, schema = self._pb.roleplay_feedback(scene, evaluation, ended_by, ctx)
        raw = self._call_nlu(prompt, schema, max_tokens=200)
        if raw:
            try:
                data = parse_loose_json(raw)
                parts = {k: str(data.get(k, "")).strip() for k in parts}
            except Exception as exc:
                self._log.warning(f"Roleplay feedback parse error: {exc}")
        closing = parts["closing"] or self._fb["roleplay_feedback"]
        text = " ".join(p for p in (closing, scores, parts["highlight"], parts["improve"]) if p)
        return {"text": text, "highlight": parts["highlight"], "improve": parts["improve"]}

    # ==== CANCEL FLOW ====

    def detect_cancel_intent(
        self, user_text: str, ctx: DialogContext | None = None, strict: bool = False,
        task: str = "",
    ) -> bool:
        """Two-stage: an exact stop command is decided locally; anything else by the LLM
        (in strict mode, told to accept only an explicit request to stop). The model chooses
        among cancel, finished (the user has nothing more to ask or add: the normal end of the
        task, not abandoning it) and none; only "cancel" is True: "finished" goes to the
        strategy like any other turn (decided by the author, 2026-10-31). An answer in the
        former form ({"cancel": true}) is still read."""
        local = local_cancel_check(user_text, self.language)
        if local is not None:
            return local
        prompt, schema = self._pb.detect_cancel_intent(user_text, ctx, strict=strict, task=task)
        raw = self._call_nlu(prompt, schema, max_tokens=30)
        if not raw:
            return False
        try:
            return cancel_intent_of(parse_loose_json(raw))
        except Exception:
            return False

    def classify_cancel_reply(self, user_text: str, ctx: DialogContext | None = None) -> str:
        """The answer to "do you want to stop?": "yes", "no", "finished" (the user has nothing
        more to ask or add: not abandoning, the task is done; cancel_check decides what that
        means for the strategy) or "other" (no answer to whether to stop, or it is
        ambiguous), as the model reads it. Only when the model gives no usable answer (an error,
        no answer in time, a call not made, an answer that is not one of the four) a fallback
        reads it: the whole utterance, normalised, exactly one of the language's yes_words is
        "yes", one of its no_words "no" (utils.local_yes_no; decided by the author,
        2026-10-30); anything else "other", so that nothing is cancelled and the turn is not
        lost."""
        prompt, schema = self._pb.classify_cancel_reply(user_text, ctx)
        for _ in range(self.MAX_RETRIES):
            raw = self._call_nlu(prompt, schema, max_tokens=30)
            if not raw:
                continue
            try:
                answer = parse_loose_json(raw).get("answer")
            except Exception:
                continue
            if answer in ("yes", "no", "finished", "other"):
                return answer
            self.model_status = False  # JSON, but not one of the answers asked for
        return local_yes_no(user_text, self.language) or "other"

    def ask_cancel_confirmation(self, task: str, ctx: DialogContext | None = None) -> str:
        prompt = self._pb.ask_cancel_confirmation(task, ctx)
        res = self._call_nlg(prompt, max_tokens=60)
        return self._clean(res) or self._fb["cancel_ask"]

    def cancel_confirmed_response(
        self, task: str, frame: DialogFrame | None = None, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._pb.cancel_confirmed_response(task, frame, ctx)
        res = self._clean(self._call_nlg(prompt, max_tokens=60))
        # The dialogue ends here: a closing that asks something ("do you need anything else?",
        # found in the debugging) is replaced by the fixed one; the prompt is left as it is
        # (decided by the author, 2026-11-02)
        if not res or "?" in res or "¿" in res:
            return self._fb["cancel_ok"]
        return res

    # ==== TRANSPORT ====

    def _infer_limited(self, op: str, sid: str, prompt: str, max_tokens: int,
                       schema: dict | None, think: bool = False) -> str | None:
        """One call to the model within the limits of the request: not made (None) when the
        model is unresponsive or the budget is spent; the time it may wait in limits.wait."""
        refusal = self.limits.refusal(think)
        if refusal:
            self.model_status = False
            self.limits.skipped += 1
            self._log.warning(f"{op}: no call to the model ({refusal}); it falls back")
            return None
        wait = self.limits.next_wait(think)
        self.limits.wait = wait
        started = self.limits.now()
        timeouts = self.limits.timeouts
        result = None
        try:
            # A call that may reason says so (think=True); the others keep the plain signature
            result = (self._infer(sid, prompt, max_tokens, schema, think=True) if think
                      else self._infer(sid, prompt, max_tokens, schema))
            return result
        finally:
            self.model_status = bool(result and str(result).strip())
            self.limits.done(started, wait, result is not None, think)
            self.limits.wait = None
            if self.limits.timeouts > timeouts:
                self._log.warning(
                    f"{op}: the model did not answer in {wait:.1f} s; the other calls of this "
                    "skill fall back without asking it")

    @staticmethod
    def _usable(result: str | None, schema: dict | None) -> bool:
        """Whether an answer can be used: not empty, and JSON when a schema was asked for."""
        if not result or not str(result).strip():
            return False
        if schema is None:
            return True
        try:
            parse_loose_json(result)
            return True
        except Exception:
            return False

    def _call_nlu(self, prompt: str, schema: dict | None, max_tokens: int) -> str | None:
        # TRACE
        _op = inspect.currentframe().f_back.f_code.co_name
        _t = time.monotonic()
        self._log.info(f"[TRACE] nlu_start op={_op} max_tokens={max_tokens} | t={_t:.4f}")
        # /TRACE
        try:
            sid = self._routes.get(_op, (self._nlu_sid, self._nlg_sid))[0]
            think = self._reasoning == "critical" and _op in self.REASONING_QUESTIONS
            result = self._infer_limited(_op, sid, prompt, max_tokens, schema, think)
            if think and not self._usable(result, schema):
                # The model reasoned past its token allowance (an empty answer) or gave no
                # JSON: the same question at once without reasoning, so that reasoning never
                # leaves a critical question unanswered (a hung model is not asked again)
                self._log.warning(f"{_op}: no usable answer with reasoning; asked without it")
                result = self._infer_limited(_op, sid, prompt, max_tokens, schema, False)
            if result and schema is not None:
                try:
                    parse_loose_json(result)
                except Exception:
                    self.model_status = False  # prose instead of the JSON asked for
            # TRACE
            self._log.info(
                f"[TRACE] nlu_end op={_op} elapsed={time.monotonic()-_t:.3f}s | t={time.monotonic():.4f}"
            )
            # /TRACE
            return result
        except Exception as exc:
            self.model_status = False
            self._log.error(f"NLU inference failed: {exc}")
            # TRACE
            self._log.info(f"[TRACE] nlu_error op={_op} | t={time.monotonic():.4f}")
            # /TRACE
            return None

    def _call_nlg(self, prompt: str, max_tokens: int) -> str | None:
        # TRACE
        _op = inspect.currentframe().f_back.f_code.co_name
        _t = time.monotonic()
        self._log.info(f"[TRACE] nlg_start op={_op} max_tokens={max_tokens} | t={_t:.4f}")
        # /TRACE
        for attempt in range(self.MAX_RETRIES):
            try:
                result = self._infer_limited(
                    _op, self._routes.get(_op, (self._nlu_sid, self._nlg_sid))[1], prompt,
                    max_tokens, None
                )
                if result and len(result.strip()) >= _MIN_NLG_CHARS:
                    # TRACE
                    self._log.info(
                        f"[TRACE] nlg_end op={_op} elapsed={time.monotonic()-_t:.3f}s | t={time.monotonic():.4f}"
                    )
                    # /TRACE
                    return result
                if result is not None:
                    self.model_status = False
                    self._log.warning(
                        f"NLG response too short, retrying ({attempt + 1}/{self.MAX_RETRIES}): {result!r}"
                    )
            except Exception as exc:
                self.model_status = False
                self._log.error(f"NLG inference failed ({attempt + 1}): {exc}")
            if self.limits.refusal():
                break  # no time left, or the model did not answer: not asked again
        # TRACE
        self._log.info(
            f"[TRACE] nlg_failed op={_op} elapsed={time.monotonic()-_t:.3f}s | t={time.monotonic():.4f}"
        )
        # /TRACE
        return None

    # ==== COERCION ====

    _CONTENT_KEYS = ("value", "data", "content", "text", "result", "val")
    _METADATA_KEYS = frozenset({"type", "kind", "format", "source_span", "slot", "description"})

    @classmethod
    def _coerce_value(cls, val, slot_type: str):
        """Coerce raw NLU output to a typed value. For list_str, handles plain lists,
        comma-separated strings, and explicit list-operation payloads."""
        if slot_type != "list_str":
            return cls._coerce_primitive(val, slot_type)

        raw_obj = val
        if isinstance(val, str):
            stripped = val.strip()
            if stripped.startswith("{"):
                try:
                    raw_obj = parse_loose_json(stripped)
                except Exception:
                    pass
            elif stripped.startswith("["):
                try:
                    raw_obj = json.loads(stripped)
                except Exception:
                    pass

        if isinstance(raw_obj, dict):
            for kind in ("add", "remove", "replace"):
                if raw_obj.get(f"_{kind}") is True:
                    items = [
                        str(i).strip()
                        for i in raw_obj.get("items", [])
                        if i is not None and str(i).strip()
                    ]
                    return _ListOp(kind, items)

        if isinstance(raw_obj, list):
            items = [str(i).strip() for i in raw_obj if i is not None and str(i).strip()]
            return items if items else None

        if isinstance(val, str):
            parts = [p.strip() for p in re.split(r"[,;]+", val) if p.strip()]
            return parts if parts else None

        return None

    @classmethod
    def _coerce_primitive(cls, val, slot_type: str):
        if isinstance(val, (str, int, float, bool)):
            return val
        if isinstance(val, list):
            return cls._coerce_primitive(val[0], slot_type) if len(val) == 1 else None
        if not isinstance(val, dict):
            return None
        for key in cls._CONTENT_KEYS:
            if key in val:
                return cls._coerce_primitive(val[key], slot_type)
        candidates = {k: v for k, v in val.items() if k not in cls._METADATA_KEYS}
        if len(candidates) == 1:
            return cls._coerce_primitive(next(iter(candidates.values())), slot_type)
        return None

    def _validate_extractions(self, extractions: list, frame: DialogFrame) -> list[dict]:
        """Coerce, validate and enforce canonical values on each extraction."""
        valid: list[dict] = []
        for e in extractions:
            slot = e.get("slot", "")
            val = e.get("value")
            if slot not in frame.all_keys() or val is None:
                continue

            coerced = self._coerce_value(val, frame.slot_type(slot))
            if coerced is None:
                continue

            src = str(e.get("source_span", "")).strip()

            if isinstance(coerced, _ListOp):
                items = self._enforce_canonical_list(slot, coerced.items, frame)
                if not items and coerced.kind != "remove":
                    continue
                valid.append(
                    {"slot": slot, "value": items, "source_span": src, f"_{coerced.kind}": True}
                )
                continue

            if isinstance(coerced, list):
                items = self._enforce_canonical_list(slot, coerced, frame)
                if not items:
                    continue
                valid.append({"slot": slot, "value": items, "source_span": src})
                continue

            if not self._enforce_canonical_scalar(slot, coerced, frame):
                continue

            valid.append({"slot": slot, "value": coerced, "source_span": src})

        self._log.info(f"Validated extractions: {valid}")
        return valid

    @staticmethod
    def _enforce_canonical_list(slot: str, items: list, frame: DialogFrame) -> list[str]:
        canon = frame.canonical_values(slot)
        if not canon:
            return items
        canon_set = {c.lower() for c in canon}
        return [i for i in items if str(i).lower() in canon_set]

    @staticmethod
    def _enforce_canonical_scalar(slot: str, value: object, frame: DialogFrame) -> bool:
        canon = frame.canonical_values(slot)
        if not canon:
            return True
        return str(value).lower().strip() in {c.lower() for c in canon}

    @staticmethod
    def _clean(text) -> str | None:
        if not text:
            return None
        return str(text).strip().strip('"').strip("'") or None
