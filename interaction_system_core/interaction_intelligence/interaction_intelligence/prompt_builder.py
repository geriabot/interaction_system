# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
PromptBuilder — assembles prompts and JSON schemas from YAML templates.

Templates use Python str.format placeholders. Public methods return either
a str (NLG prompt) or a (str, dict) tuple (NLU prompt + output schema).

The `language` parameter (e.g. "Spanish", "English") is injected into every
template automatically, so templates can use {language} wherever needed.
{address} is injected the same way: the shared rule on how every spoken line
addresses the user (the `address_rule` entry of prompts.yaml: directly, in the
informal second person). Rules that depend on the turn are inserted by the code,
never left to the model: generate_response gets `list_slot_rule` only when the
slot it asks for is a list (list_str).

Prompt caching: templates put their fixed instructions first and the per-turn
data last. Rendered prompts are `Prompt` strings whose `cache_prefix_len` marks
where the stable part ends (instructions + [CONTEXT] block), so providers with
prompt caching reuse it across turns. The split is the last paragraph break
before the first placeholder other than {language} and {address}.
"""

import logging  # TRACE
import time  # TRACE
from string import Formatter

from .dialog_context import DialogContext
from .dialog_frame import DialogFrame
from .utils import spoken_ask

_log = logging.getLogger("PromptBuilder")  # TRACE


# How a roleplay ended, as told to the feedback prompt.
_ROLEPLAY_ENDINGS = {
    "user": "the user asked to stop",
    "scene": "the scene reached its natural end",
    "limit": "the time for the practice ran out",
}


class Prompt(str):
    """Prompt text plus the length of its stable leading part."""

    cache_prefix_len: int = 0


def _prompt(text: str, cache_prefix_len: int) -> Prompt:
    p = Prompt(text)
    p.cache_prefix_len = cache_prefix_len
    return p


# Entries of prompts.yaml that are shared rules, inserted into the prompts, not prompts.
ADDRESS_RULE = "address_rule"
LIST_SLOT_RULE = "list_slot_rule"


class PromptBuilder:

    def __init__(self, templates: dict[str, str], language: str = "English"):
        self._tpl = templates
        self._language = language
        # The same for every template and turn: part of the cached prefix, like {language}
        self._static = {"language": language, "address": self._rule(ADDRESS_RULE)}

    @property
    def address(self) -> str:
        """The shared rule on how a spoken line addresses the user, in one line (the LLM-led
        baseline of the ablations gives its prompt the same rule)."""
        return self._static["address"]

    def _rule(self, key: str) -> str:
        """A shared rule of prompts.yaml as one line ("" if the templates do not define it)."""
        return " ".join(self._tpl.get(key, "").split())

    def _t(self, key: str, **kwargs) -> Prompt:
        """Render template `key`, always making {language} and {address} available."""
        tpl = self._tpl[key]
        text = tpl.format(**self._static, **kwargs)
        return _prompt(text, self._static_prefix_len(tpl))

    def _static_prefix_len(self, tpl: str) -> int:
        """Length of the rendered text up to the last paragraph break before
        the first per-call placeholder (0 if the template opens with data)."""
        static = []
        for literal, field, _spec, _conv in Formatter().parse(tpl):
            static.append(literal)
            if field is None:
                continue
            if field not in self._static:
                break
            static.append(self._static[field])
        head = "".join(static)
        cut = head.rfind("\n\n")
        return cut + 2 if cut >= 0 else 0

    # ==== CONTEXT INJECTION ====

    @staticmethod
    def _inject(prompt: str, ctx: DialogContext | None) -> str:
        if not ctx:
            return prompt
        block = ctx.to_prompt_block()
        # TRACE
        if block:
            _emotion = ctx.current_user_state().emotion
            _log.info(
                f"[TRACE] context_injected resources={len(ctx.resources)} has_domain={bool(ctx.domain)} emotion={_emotion} | t={time.monotonic():.4f}"
            )
        # /TRACE
        if not block:
            return prompt
        # The context block is stable within a dialog: place it right after the
        # instructions so it joins the cached prefix. Templates that open with
        # data (no prefix) keep it in front.
        n = getattr(prompt, "cache_prefix_len", 0)
        head = prompt[:n] + block + "\n"
        return _prompt(head + prompt[n:], len(head) if n else 0)

    # ==== TASK LEVEL ====

    def classify_task_mode(self, task: str, ctx: DialogContext | None = None) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["slot_filling", "explanation"]}},
            "required": ["mode"],
        }
        prompt = self._inject(self._t("classify_task_mode", task=task), ctx)
        return prompt, schema

    def choose_dialogue_behavior(
        self, task: str, behaviors: list[dict], ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        """behaviors: the offered ones, {behavior_id, name, explanation}."""
        ids = [b["behavior_id"] for b in behaviors]
        schema = {
            "type": "object",
            "properties": {"behavior_id": {"type": "integer", "enum": ids}},
            "required": ["behavior_id"],
        }
        listing = "\n".join(
            f"- {b['behavior_id']}: {b['name']}. {b['explanation'].strip()}" for b in behaviors
        )
        prompt = self._inject(
            self._t("choose_dialogue_behavior", behaviors=listing, task=task), ctx
        )
        return prompt, schema

    def create_frame(self, task: str, ctx: DialogContext | None = None) -> tuple[str, dict]:
        # No context injection: slot schema must derive from the task alone.
        schema = {
            "type": "object",
            "properties": {
                "slots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": ["str", "int", "float", "bool", "list_str"],
                            },
                            "condition_slot": {"type": "string"},
                            "condition_value": {"type": "string"},
                            "canonical_values": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "type"],
                    },
                }
            },
            "required": ["slots"],
        }
        prompt = self._t("create_frame", task=task)
        return prompt, schema

    def audit_frame(
        self, task: str, schema: str, ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        json_schema = {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slot": {"type": "string"},
                            "action": {"type": "string", "enum": ["add", "remove"]},
                            "values": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["slot", "action", "values"],
                    },
                }
            },
            "required": ["actions"],
        }
        prompt = self._t("audit_frame", task=task, schema=schema)
        return self._inject(prompt, ctx), json_schema

    # ==== SLOT FILLING ====

    def opening(self, task: str, frame: DialogFrame, ctx: DialogContext | None = None) -> str:
        # No context injection: avoids the opening reciting menu options unprompted.
        # The slot asked is the code's (the next one of the frame), as in every later turn: the
        # extraction of the reply counts a skip or a failed attempt against that slot
        return self._t("opening", task=task, slots=frame.describe_slots(),
                       next_slot=frame.next_slot() or "NONE")

    def opening_direct(self, task: str, frame: DialogFrame, ctx: DialogContext | None = None) -> str:
        # Context IS injected: opening_direct is for chained dialogs where the
        # patient/domain context is already established and the LLM needs it
        # (e.g. to use the patient's name without resorting to placeholders).
        return self._inject(self._t("opening_direct", task=task, slots=frame.describe_slots(),
                                    next_slot=frame.next_slot() or "NONE"), ctx)

    def resume_opening(
        self, task: str, frame: DialogFrame, next_slot: str | None, ctx: DialogContext | None = None
    ) -> str:
        filled = frame.filled_slots()
        known = "\n".join(f"  - {k}: {v!r}" for k, v in filled.items()) or "  (none)"
        prompt = self._t(
            "resume_opening", task=task, known_slots=known, next_slot=next_slot or "NONE"
        )
        return self._inject(prompt, ctx)

    def extract_slots(
        self, user_text: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        """NLU pass for gathering: detects new values and corrections in a single call."""
        modifiable = frame.modifiable_slots()
        # The conditional slots that do not apply yet are listed too, with their condition: the
        # user may give one together with the value that makes it apply ("a latte with oat milk")
        pending = [k for k in frame.all_keys() if k not in modifiable]
        slot_defs = "\n".join([self._slot_def_line(k, frame) for k in modifiable]
                              + [self._pending_slot_line(k, frame) for k in pending])
        filled = frame.filled_slots()
        filled_ctx = ", ".join(f"{k}={v!r}" for k, v in filled.items()) if filled else "None"
        recent_ctx = self._fmt_history(frame.last_turns(2))
        prompt = self._t(
            "extract_slots",
            user_text=user_text,
            slot_defs=slot_defs,
            filled_ctx=filled_ctx,
            recent_context=recent_ctx,
            # The slot the robot asked (the code's choice: the reply is read against it). Without
            # it small models took "Ana Gil, that's right" for a confirmation, not the name asked
            asked_slot=frame.next_slot() or "NONE",
        )
        return self._inject(prompt, ctx), frame.schema_for_extraction()

    def _pending_slot_line(self, slot: str, frame: DialogFrame) -> str:
        line = (f"  - {slot} (type: {frame.slot_type(slot)}) [ONLY WHEN "
                f"{frame.describe_condition(slot)} — extract it only if the user gives it]")
        return line + self._canonical_hint(slot, frame)

    def _slot_def_line(self, slot: str, frame: DialogFrame) -> str:
        slot_type = frame.slot_type(slot)
        line = f"  - {slot} (type: {slot_type})"
        if slot_type == "list_str":
            line += " [LIST — accumulates across turns; user may add, remove with _remove, or replace with _replace]"
        elif not frame.is_empty(slot):
            line += f" [ALREADY FILLED with {frame.current_value(slot)!r} — extract only if the user is explicitly correcting it]"
        else:
            line += " [empty — needs a value]"
        line += self._canonical_hint(slot, frame)
        rng = frame.describe_bounds(slot)
        if rng:
            # Out-of-range numbers must still be extracted so the frame can
            # reject them and the reply can explain the allowed range.
            line += f" [allowed range: {rng}; extract the number as said even if outside it]"
        return line

    @staticmethod
    def _canonical_hint(slot: str, frame: DialogFrame) -> str:
        canon = frame.canonical_values(slot)
        if not canon:
            return ""
        return f" [must be one of: {' | '.join(repr(v) for v in canon)}]"

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
        history_text = self._fmt_history(frame.last_turns(10))
        asked = frame.next_slot()
        next_slot = asked or "NONE"
        # Inviting several values is right only for a list slot: decided here, not by the model
        # (small models applied an always-present list rule to single-value slots too)
        is_list = bool(asked) and frame.slot_type(asked) == "list_str"
        list_rule = self._rule(LIST_SLOT_RULE) if is_list else ""
        extracted_summary = (
            ", ".join(f"{e['slot']}={e['value']!r}" for e in extracted_slots) or "None"
        )
        rejected_summary = (
            ", ".join(
                f"{k}={v!r} (allowed range: {frame.describe_bounds(k)})"
                for k, v in (rejected or {}).items()
            )
            or "None"
        )
        prompt = self._t(
            "generate_response",
            task=task,
            slots=frame.describe_slots(),
            history=history_text,
            user_text=user_text,
            extracted_summary=extracted_summary,
            rejected_summary=rejected_summary,
            next_slot=next_slot,
            attempts_exceeded=str(attempts_exceeded),
            list_slot_rule=list_rule,
        )
        return self._inject(prompt, ctx)

    def ask_confirmation(
        self, task: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._t("ask_confirmation", task=task, slots=frame.describe_slots())
        return self._inject(prompt, ctx)

    def classify_intent(
        self, user_text: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        prompt = self._t(
            "classify_intent", slots_summary=frame.describe_slots(), user_text=user_text
        )
        return self._inject(prompt, ctx), frame.schema_for_intent()

    def response_to_intent(
        self,
        task: str,
        intent: str,
        corrections: dict,
        frame: DialogFrame,
        ctx: DialogContext | None = None,
    ) -> str:
        history_text = self._fmt_history(frame.last_turns(6))
        corr_str = ", ".join(f"{k}={v!r}" for k, v in corrections.items()) or "None"
        prompt = self._t(
            "response_to_intent",
            task=task,
            intent=intent,
            corrections=corr_str,
            slots=frame.describe_slots(),
            history=history_text,
        )
        return self._inject(prompt, ctx)

    def answer_confirmation_question(
        self, user_text: str, task: str, frame: DialogFrame, ctx: DialogContext | None = None
    ) -> str:
        slots_summary = ", ".join(f"{k}={v!r}" for k, v in frame.filled_slots().items())
        prompt = self._t(
            "answer_confirmation_question",
            task=task,
            slots_summary=slots_summary,
            user_text=user_text,
        )
        return self._inject(prompt, ctx)

    # ==== QUIZ ====

    def quiz_opening(self, task: str, total: int) -> str:
        return self._t("quiz_opening", task=task, total=total)

    def evaluate_quiz_answer(self, question: str, expected: str, given: str) -> tuple[str, dict]:
        json_schema = {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["answer", "repeat"]},
                "correct": {"type": "boolean"},
                "feedback": {"type": "string"},
            },
            "required": ["kind", "correct", "feedback"],
        }
        prompt = self._t("evaluate_quiz_answer", question=question, expected=expected, given=given)
        return prompt, json_schema

    def quiz_rephrase_question(self, question: str, expected: str) -> str:
        return self._t("quiz_rephrase_question", question=question, expected=expected)

    def quiz_next_question(self, feedback: str, n: int, total: int, question: str) -> str:
        return self._t("quiz_next_question", feedback=feedback, n=n, total=total, question=question)

    def quiz_summary(self, task: str, correct: int, total: int, wrongs: str) -> str:
        return self._t(
            "quiz_summary", task=task, correct=correct, total=total, wrongs=wrongs or "(none)"
        )

    # ==== EXPLANATION ====

    def explanation_opening(self, task: str, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("explanation_opening", task=task), ctx)

    def explanation_opening_direct(self, task: str, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("explanation_opening_direct", task=task), ctx)

    def extract_key_points(self, task: str, resources: list[dict]) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {"key_points": {"type": "array", "items": {
                "type": "object",
                "properties": {"point": {"type": "string"}, "critical": {"type": "boolean"}},
                "required": ["point", "critical"],
            }}},
            "required": ["key_points"],
        }
        material = "\n".join(f"[{r['name']}] {r['content']}" for r in resources) or "(none)"
        return self._t("extract_key_points", task=task, material=material), schema

    def judge_teach_back(
        self, user_text: str, key_points: list[dict], asked: str,
        ctx: DialogContext | None = None,
    ) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": [
                    "restates", "question", "declines", "not_understood", "other"]},
                # One entry per key point (a shorter list was renumbered from 1 by small
                # models, so the status of one point landed on another)
                "points": {"type": "array", "minItems": len(key_points),
                           "maxItems": len(key_points), "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "status": {"type": "string",
                                   "enum": ["correct", "incomplete", "wrong", "missing"]},
                        "said": {"type": "string"},
                    },
                    "required": ["id", "status"],
                }},
            },
            "required": ["kind", "points"],
        }
        points = "\n".join(f"{i + 1}. {p['point']}" for i, p in enumerate(key_points))
        prompt = self._t("judge_teach_back", key_points=points, asked=asked, user_text=user_text)
        return prompt, schema

    def repair_explanation(
        self, task: str, points: list[dict], correct: list[str],
        ctx: DialogContext | None = None,
    ) -> str:
        lines = "\n".join(f"- {p['point']}{_said_note(p)}" for p in points)
        prompt = self._t("repair_explanation", task=task, points=lines,
                         correct="; ".join(correct) or "(nothing yet)")
        return self._inject(prompt, ctx)

    def targeted_question(self, point: str, ctx: DialogContext | None = None) -> str:
        return self._t("targeted_question", point=point)

    def ask_missing_points(
        self, task: str, correct: list[str], missing: list[str],
        ctx: DialogContext | None = None,
    ) -> str:
        return self._t("ask_missing_points", task=task, correct="; ".join(correct),
                       missing="\n".join(f"- {m}" for m in missing))

    def answer_explanation_question(
        self, user_text: str, task: str, history: list[dict], ctx: DialogContext | None = None
    ) -> str:
        prompt = self._t(
            "answer_explanation_question",
            task=task,
            history=self._fmt_history(history[-6:]),
            user_text=user_text,
        )
        return self._inject(prompt, ctx)

    def rephrase_explanation(
        self, task: str, history: list[dict], attempt_number: int, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._t(
            "rephrase_explanation",
            task=task,
            history=self._fmt_history(history[-8:]),
            attempt_number=attempt_number,
        )
        return self._inject(prompt, ctx)

    def understanding_confirmed(self, task: str, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("understanding_confirmed", task=task), ctx)

    # ==== GROUNDED QUESTION ANSWERING ====

    @staticmethod
    def _fmt_material(material: list[dict]) -> str:
        return "\n".join(f"[{r['name']}] {r['content']}" for r in material) or "(none)"

    def qa_opening(self, task: str, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("qa_opening", task=task), ctx)

    def answer_from_material(
        self, user_text: str, task: str, material: list[dict], history: list[dict]
    ) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {
                "sense": {"type": "string", "enum": ["yes", "no"]},
                "act": {"type": "string", "enum": ["asks", "finished", "reacts", "unclear"]},
                "kind": {"type": "string", "enum": [
                    "answerable", "not_in_material", "off_task", "repeat", "other"]},
                "answer": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["sense", "act", "kind", "answer", "evidence"],
        }
        prompt = self._t("answer_from_material", task=task,
                         material=self._fmt_material(material),
                         history=self._fmt_history(history), user_text=user_text)
        return prompt, schema

    def verify_finished(self, user_text: str, robot: str) -> tuple[str, dict]:
        schema = {"type": "object", "properties": {"finished": {"type": "boolean"}},
                  "required": ["finished"]}
        return self._t("verify_finished", user_text=user_text, robot=robot), schema

    def verify_answer(self, answer: str, material: list[dict]) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {"supported": {"type": "boolean"},
                           "unsupported": {"type": "array", "items": {"type": "string"}}},
            "required": ["supported", "unsupported"],
        }
        return self._t("verify_answer", material=self._fmt_material(material),
                       answer=answer), schema

    # ==== QUESTIONNAIRE ====

    def map_scale_answer(self, user_text: str, item: str, options: list[str]) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": [
                    "option", "ambiguous", "meaning", "refuses", "repeat", "other"]},
                "option": {"type": "integer"},
            },
            "required": ["kind", "option"],
        }
        opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
        return self._t("map_scale_answer", item=item, options=opts, user_text=user_text), schema

    # ==== PROCEDURE ====

    def procedure_opening(self, task: str, total: int, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("procedure_opening", task=task, total=total), ctx)

    def procedure_step(
        self, step: str, n: int, total: int, situation: str, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._t("procedure_step", step=step, n=n, total=total, situation=situation)
        return self._inject(prompt, ctx)

    def classify_step_turn(
        self,
        user_text: str,
        step: str,
        check: str,
        history: list[dict],
        ctx: DialogContext | None = None,
    ) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["done", "question", "problem", "repeat", "back", "skip", "unclear"],
                }
            },
            "required": ["intent"],
        }
        prompt = self._t(
            "classify_step_turn",
            step=step,
            check=check or "(none)",
            history=self._fmt_history(history[-6:]),
            user_text=user_text,
        )
        return self._inject(prompt, ctx), schema

    def procedure_answer_question(
        self,
        user_text: str,
        task: str,
        step: str,
        history: list[dict],
        ctx: DialogContext | None = None,
    ) -> str:
        prompt = self._t(
            "procedure_answer_question",
            task=task,
            step=step,
            history=self._fmt_history(history[-6:]),
            user_text=user_text,
        )
        return self._inject(prompt, ctx)

    def procedure_help_step(
        self,
        task: str,
        step: str,
        history: list[dict],
        attempt_number: int,
        ctx: DialogContext | None = None,
    ) -> str:
        prompt = self._t(
            "procedure_help_step",
            task=task,
            step=step,
            history=self._fmt_history(history[-8:]),
            attempt_number=attempt_number,
        )
        return self._inject(prompt, ctx)

    def procedure_remind(self, step: str, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("procedure_remind", step=step), ctx)

    def procedure_summary(
        self, task: str, completed: int, total: int, not_done: str, ctx: DialogContext | None = None
    ) -> str:
        prompt = self._t(
            "procedure_summary",
            task=task,
            completed=completed,
            total=total,
            not_done=not_done or "(none)",
        )
        return self._inject(prompt, ctx)

    # ==== ROLEPLAY ====

    def roleplay_intro(self, task: str, scene: dict, ctx: DialogContext | None = None) -> str:
        prompt = self._t("roleplay_intro", task=task, scene=self._fmt_scene(scene))
        return self._inject(prompt, ctx)

    def roleplay_reply(
        self,
        scene: dict,
        history: list[dict],
        ctx: DialogContext | None = None,
        avoid: str = "",
    ) -> str:
        # The whole scene, not a sliding window: it only grows by appending, so
        # each turn's prompt starts with the previous one (prefix cache reuse).
        asked = [spoken_ask(t["content"]) for t in history if t["role"] == "assistant"]
        prompt = self._t(
            "roleplay_reply",
            scene=self._fmt_scene(scene),
            history=self._fmt_transcript(history),
            asked="\n".join(f"  - {a}" for a in asked if a) or "  (none yet)",
        )
        if avoid:
            # Retry after a repeated question: a note at the very end, so the
            # cached prefix stays the same.
            prompt = _prompt(
                f"{prompt}\nYour draft repeated a question you already asked: \"{avoid}\". "
                "Ask about something different this time.\n",
                prompt.cache_prefix_len,
            )
        return self._inject(prompt, ctx)

    def classify_roleplay_turn(
        self, user_text: str, scene: dict, history: list[dict], ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        # No context injection: a quick routing decision over the scene only.
        schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["continue", "repeat", "coach", "end", "scene_end"],
                }
            },
            "required": ["intent"],
        }
        # history ends with the reply being classified: show the exchange before it.
        prompt = self._t(
            "classify_roleplay_turn",
            scene=self._fmt_scene(scene),
            history=self._fmt_transcript(history[-3:-1]),
            user_text=user_text,
        )
        return prompt, schema

    def roleplay_coach(
        self, user_text: str, scene: dict, history: list[dict], ctx: DialogContext | None = None
    ) -> str:
        prompt = self._t(
            "roleplay_coach",
            scene=self._fmt_scene(scene),
            history=self._fmt_transcript(history[-7:-1]),
            user_text=user_text,
        )
        return self._inject(prompt, ctx)

    def evaluate_roleplay_criterion(
        self,
        scene: dict,
        transcript: list[dict],
        user_lines: list[str],
        criterion: str,
        ctx: DialogContext | None = None,
    ) -> tuple[str, dict]:
        """One criterion per call. Everything but the criterion is the same for
        all of them, and the criterion goes last, so the calls share the cache."""
        schema = {
            "type": "object",
            "properties": {
                # Evidence, then reasoning, then the score: decoding follows
                # property order, so the score is grounded on what came before.
                "lines": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                "comment": {"type": "string"},
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["lines", "comment", "score"],
        }
        prompt = self._t(
            "evaluate_roleplay",
            scene=self._fmt_scene(scene),
            transcript=self._fmt_transcript(transcript),
            user_lines="\n".join(f"  {i}. {line}" for i, line in enumerate(user_lines, 1))
            or "  (none)",
            criterion=criterion,
        )
        return self._inject(prompt, ctx), schema

    def roleplay_feedback(
        self, scene: dict, evaluation: dict, ended_by: str, ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {
                "closing": {"type": "string"},
                "highlight": {"type": "string"},
                "improve": {"type": "string"},
            },
            "required": ["closing", "highlight", "improve"],
        }
        results = "\n".join(
            f"  - {s['criterion']}: {s['score']}/5. {s['comment']}"
            + (f' Evidence: "{s["evidence"]}"' if s.get("evidence") else "")
            for s in evaluation["scores"]
        )
        prompt = self._t(
            "roleplay_feedback",
            scene=self._fmt_scene(scene),
            results=results,
            ended_by=_ROLEPLAY_ENDINGS.get(ended_by, ended_by),
        )
        return self._inject(prompt, ctx), schema

    # ==== CANCEL FLOW ====

    def detect_cancel_intent(
        self, user_text: str, ctx: DialogContext | None = None, strict: bool = False,
        task: str = "",
    ) -> tuple[str, dict]:
        # No context injection (fast and unambiguous): only the task description, so that
        # "cancel" as content of the task is not taken for a request to stop (provisional,
        # 2026-11-01)
        schema = {
            "type": "object",
            "properties": {"about": {"type": "string", "enum": ["conversation", "task"]},
                           "request": {"type": "string",
                                       "enum": ["cancel", "finished", "none"]}},
            "required": ["about", "request"],
        }
        prompt = self._t(
            "detect_cancel_intent", user_text=user_text, strict="true" if strict else "false",
            task=task or "(not given)"
        )
        return prompt, schema

    def classify_cancel_reply(
        self, user_text: str, ctx: DialogContext | None = None
    ) -> tuple[str, dict]:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string",
                                      "enum": ["yes", "no", "finished", "other"]}},
            "required": ["answer"],
        }
        return self._t("classify_cancel_reply", user_text=user_text), schema

    def ask_cancel_confirmation(self, task: str, ctx: DialogContext | None = None) -> str:
        return self._inject(self._t("ask_cancel_confirmation", task=task), ctx)

    def cancel_confirmed_response(
        self, task: str, frame: DialogFrame | None = None, ctx: DialogContext | None = None
    ) -> str:
        return self._inject(self._t("cancel_confirmed_response", task=task), ctx)

    # ==== HELPERS ====

    @staticmethod
    def _fmt_scene(scene: dict) -> str:
        lines = [f"  Scenario: {scene['scenario']}", f"  Your character: {scene['role']}"]
        if scene.get("user_goal"):
            lines.append(f"  The user's goal: {scene['user_goal']}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_transcript(turns: list[dict]) -> str:
        """Roleplay history: the assistant's turns are the character's lines."""
        if not turns:
            return "  (empty)"
        labels = {"assistant": "Character", "user": "User"}
        return "\n".join(f"  {labels.get(t['role'], t['role'])}: {t['content']}" for t in turns)

    @staticmethod
    def _fmt_history(turns: list[dict]) -> str:
        if not turns:
            return "  (empty)"
        return "\n".join(f"  {t['role'].capitalize()}: {t['content']}" for t in turns)


def _said_note(point: dict) -> str:
    """What the person said of a key point to explain again: a wrong content (to correct) or
    a right but incomplete part (to confirm and complete, never to correct)."""
    said = point.get("said")
    if not said:
        return ""
    if point.get("status") == "wrong":
        return f" (the person said instead: \"{said}\")"
    if point.get("status") == "incomplete":
        return f" (the person said only this part, which is right: \"{said}\")"
    return ""
