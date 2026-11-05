# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The questions of the interaction system to the model, as the interpretation server receives
them: the context of each question and the prompt built from it.

Every question of LLMDialogClient is a question of the interpretation server (the questions
file config/interpretation/questions.yaml, answered by the classes of
interpretation_questions.py). The client sends, as the context of the request, the dialogue's
language and the arguments of the PromptBuilder method that writes the question's prompt;
the server rebuilds the arguments and calls the same method with the same templates, so
that the prompt and the schema are those that PromptBuilder writes in the client.

    context = encode_context("extract_turn", "English",
                             {"user_text": text, "frame": frame, "ctx": ctx})
    prompt, schema = build_prompt("extract_turn", templates, decode_context(context))

The arguments are plain JSON, except three kinds of value, written as tagged objects:
a DialogFrame ({"__frame__": its to_state()}), a DialogContext ({"__context__": its
resources, its domain and the state of the user when the context would show it}) and a tuple
({"__tuple__": [...]}). The state of the user is resolved when the context is encoded: its
emotion is sent only when it is notable then (DialogContext.to_prompt_block).

This module has no dependency on the interpretation server: the client encodes with it, and
the server's question module decodes with it.
"""

import json
import time

from .dialog_context import DialogContext, Resource, UserState
from .dialog_frame import DialogFrame
from .prompt_builder import Prompt, PromptBuilder

# The questions: name -> (the PromptBuilder method that writes the prompt, the template it
# fills, whether the answer is JSON (understanding) or a spoken line (generation)). The name is
# the method of LLMDialogClient that asks it (roleplay_reply: the client's _roleplay_line).
# Their token caps are in the questions file (config/interpretation/questions.yaml), except
# judge_teach_back's, which depends on the number of key points (max_tokens_of).
QUESTIONS = {
    # Task level
    "classify_task_mode": ("classify_task_mode", "classify_task_mode", "json"),
    "choose_dialogue_behavior": ("choose_dialogue_behavior", "choose_dialogue_behavior", "json"),
    "create_frame": ("create_frame", "create_frame", "json"),
    "audit_frame": ("audit_frame", "audit_frame", "json"),
    # Slot filling
    "get_opening": ("opening", "opening", "text"),
    "get_opening_direct": ("opening_direct", "opening_direct", "text"),
    "get_resume_opening": ("resume_opening", "resume_opening", "text"),
    "extract_turn": ("extract_slots", "extract_slots", "json"),
    "generate_response": ("generate_response", "generate_response", "text"),
    "get_confirmation_question": ("ask_confirmation", "ask_confirmation", "text"),
    "classify_intent": ("classify_intent", "classify_intent", "json"),
    "answer_confirmation_question": ("answer_confirmation_question",
                                     "answer_confirmation_question", "text"),
    "response_to_intent": ("response_to_intent", "response_to_intent", "text"),
    # Explanation
    "generate_explanation": ("explanation_opening", "explanation_opening", "text"),
    "generate_explanation_direct": ("explanation_opening_direct", "explanation_opening_direct",
                                    "text"),
    "extract_key_points": ("extract_key_points", "extract_key_points", "json"),
    "judge_teach_back": ("judge_teach_back", "judge_teach_back", "json"),
    "repair_explanation": ("repair_explanation", "repair_explanation", "text"),
    "targeted_question": ("targeted_question", "targeted_question", "text"),
    "ask_missing_points": ("ask_missing_points", "ask_missing_points", "text"),
    "answer_explanation_question": ("answer_explanation_question", "answer_explanation_question",
                                    "text"),
    "rephrase_explanation": ("rephrase_explanation", "rephrase_explanation", "text"),
    "understanding_confirmed_response": ("understanding_confirmed", "understanding_confirmed",
                                         "text"),
    # Grounded question answering
    "qa_opening": ("qa_opening", "qa_opening", "text"),
    "answer_from_material": ("answer_from_material", "answer_from_material", "json"),
    "verify_finished": ("verify_finished", "verify_finished", "json"),
    "verify_answer": ("verify_answer", "verify_answer", "json"),
    "detect_wrong_check": ("detect_wrong_check", "detect_wrong_check", "json"),
    # Questionnaire
    "map_scale_answer": ("map_scale_answer", "map_scale_answer", "json"),
    # Quiz
    "quiz_opening": ("quiz_opening", "quiz_opening", "text"),
    "evaluate_quiz_answer": ("evaluate_quiz_answer", "evaluate_quiz_answer", "json"),
    "quiz_rephrase_question": ("quiz_rephrase_question", "quiz_rephrase_question", "text"),
    "quiz_next_question": ("quiz_next_question", "quiz_next_question", "text"),
    "quiz_summary": ("quiz_summary", "quiz_summary", "text"),
    # Procedure
    "procedure_opening": ("procedure_opening", "procedure_opening", "text"),
    "procedure_step": ("procedure_step", "procedure_step", "text"),
    "classify_step_turn": ("classify_step_turn", "classify_step_turn", "json"),
    "procedure_answer_question": ("procedure_answer_question", "procedure_answer_question",
                                  "text"),
    "procedure_help_step": ("procedure_help_step", "procedure_help_step", "text"),
    "procedure_remind": ("procedure_remind", "procedure_remind", "text"),
    "procedure_summary": ("procedure_summary", "procedure_summary", "text"),
    # Roleplay
    "roleplay_intro": ("roleplay_intro", "roleplay_intro", "text"),
    "roleplay_reply": ("roleplay_reply", "roleplay_reply", "text"),
    "classify_roleplay_turn": ("classify_roleplay_turn", "classify_roleplay_turn", "json"),
    "roleplay_coach": ("roleplay_coach", "roleplay_coach", "text"),
    "evaluate_roleplay": ("evaluate_roleplay_criterion", "evaluate_roleplay", "json"),
    "roleplay_feedback": ("roleplay_feedback", "roleplay_feedback", "json"),
    # Cancellation
    "detect_cancel_intent": ("detect_cancel_intent", "detect_cancel_intent", "json"),
    "classify_cancel_reply": ("classify_cancel_reply", "classify_cancel_reply", "json"),
    "ask_cancel_confirmation": ("ask_cancel_confirmation", "ask_cancel_confirmation", "text"),
    "cancel_confirmed_response": ("cancel_confirmed_response", "cancel_confirmed_response",
                                  "text"),
}

# The name of a question in the client's own routing (LLMDialogClient.questions()), where it
# differs from the question's name
CLIENT_NAMES = {"roleplay_reply": "_roleplay_line"}
QUESTION_OF_CLIENT_NAME = {v: k for k, v in CLIENT_NAMES.items()}

# The prefix of the templates in a ROS parameters file (prompts.yaml)
TEMPLATES_PREFIX = "interaction_intelligence.ros__parameters.prompts."


class ContextError(ValueError):
    """A context that does not describe a question's arguments."""


def max_tokens_of(question: str, args: dict, declared: int) -> int:
    """The token cap of one call of the question (its arguments, decoded or not; declared: the
    cap of its definition)."""
    if question == "judge_teach_back":
        # One entry per key point (with what was said wrong): room for all of them
        return 120 + 60 * len(args.get("key_points") or [])
    return declared


# ==== ENCODING (the client) ====


def _encode(value):
    if isinstance(value, DialogFrame):
        return {"__frame__": value.to_state()}
    if isinstance(value, DialogContext):
        state = value.current_user_state()
        return {"__context__": {
            "resources": [{"name": r.name, "description": r.description, "content": r.content}
                          for r in value.resources],
            "domain": value.domain,
            # Only when the prompt would show it (notable and recent now)
            "user_state": ({"emotion": state.emotion, "confidence": state.confidence,
                            "source": state.source} if state.is_notable() else None),
        }}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ContextError(f"A mapping with keys that are not strings: {value!r}")
        return {k: _encode(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ContextError(f"Cannot send a value of type {type(value).__name__}: {value!r}")


def encode_args(args: dict) -> dict:
    """The arguments of a PromptBuilder method as JSON."""
    return {name: _encode(value) for name, value in args.items()}


def encode_context(question: str, language: str, args: dict) -> str:
    """The context of a request (a JSON object, as Interpret.srv carries it)."""
    if question not in QUESTIONS:
        raise ContextError(f"Unknown question {question!r}")
    return json.dumps({"language": language, "args": encode_args(args)}, ensure_ascii=False)


# ==== DECODING (the server) ====


def _decode(value):
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"__frame__"}:
        return DialogFrame.from_state(value["__frame__"])
    if set(value) == {"__context__"}:
        data = value["__context__"]
        ctx = DialogContext(
            resources=[Resource(name=r["name"], description=r.get("description", ""),
                                content=r["content"]) for r in data.get("resources") or []],
            domain=data.get("domain", ""))
        state = data.get("user_state")
        if state:
            ctx.update_user_state(UserState(emotion=state["emotion"],
                                            confidence=state["confidence"],
                                            source=state["source"],
                                            timestamp=time.monotonic()))
        return ctx
    if set(value) == {"__tuple__"}:
        return tuple(_decode(v) for v in value["__tuple__"])
    return {k: _decode(v) for k, v in value.items()}


def decode_args(args: dict) -> dict:
    return {name: _decode(value) for name, value in (args or {}).items()}


def decode_context(context: dict) -> dict:
    """{"language", "args"} of a request's context, with the arguments rebuilt."""
    if not isinstance(context, dict) or not isinstance(context.get("args", {}), dict):
        raise ContextError("The context must be {\"language\": ..., \"args\": {...}}")
    return {"language": context.get("language") or "English",
            "args": decode_args(context.get("args") or {})}


# ==== THE PROMPT ====


def prompt_templates(templates: dict) -> dict:
    """The prompt templates of PromptBuilder out of a templates mapping: the entries of a
    prompts.yaml read as a ROS parameters file (by their dotted path), or else the names
    without a dot."""
    nested = {k[len(TEMPLATES_PREFIX):]: v for k, v in templates.items()
              if k.startswith(TEMPLATES_PREFIX) and "." not in k[len(TEMPLATES_PREFIX):]}
    if nested:
        return nested
    return {k: v for k, v in templates.items() if "." not in k}


def build_prompt(question: str, templates: dict, decoded: dict) -> tuple[str, dict | None]:
    """(prompt, schema) of a question, as PromptBuilder writes them: schema None for a spoken
    line. `templates`: PromptBuilder's templates; `decoded`: decode_context()'s result."""
    if question not in QUESTIONS:
        raise ContextError(f"Unknown question {question!r}")
    method, _template, kind = QUESTIONS[question]
    builder = PromptBuilder(templates, language=decoded["language"])
    try:
        out = getattr(builder, method)(**decoded["args"])
    except TypeError as exc:
        raise ContextError(f"{question}: the arguments do not fit {method}(): {exc}") from exc
    if kind == "json":
        prompt, schema = out
        return prompt, schema
    return out, None


# ==== THE CLIENT'S REQUESTS ====

# The variant of the questions on which the model may reason (questions.yaml)
REASONING_VARIANT = "reasoning"
# The field of the server's answer that holds the model's text (parse: text, and the class's
# parse() of the understanding questions)
TEXT_FIELD = "text"


class AskedPrompt(Prompt):
    """A prompt that also says which question of the interpretation server asks it, and with
    which context: what the client's transport sends instead of the prompt."""

    question: str = ""
    context: dict | None = None


def ask_as(prompt: str, question: str, context: dict) -> AskedPrompt:
    """The prompt, marked with its question and context (the cache prefix is kept)."""
    asked = AskedPrompt(prompt)
    asked.cache_prefix_len = getattr(prompt, "cache_prefix_len", 0)
    asked.question = question
    asked.context = context
    return asked


def asked_question(prompt) -> tuple[str, dict] | None:
    """(question, context) of a prompt marked by ask_as(), else None."""
    question = getattr(prompt, "question", "")
    context = getattr(prompt, "context", None)
    if not question or not isinstance(context, dict):
        return None
    return question, context


class InterpretationPromptBuilder(PromptBuilder):
    """PromptBuilder whose prompts are marked with the question that asks them and its context
    (the dialogue's language and the method's arguments, encoded when the prompt is built), so
    that the client's transport sends the question to the interpretation server. The prompt is
    still written: the client's checks, the call records and the tests read it; the server
    writes the same one (evaluation/equivalence)."""


def _marking(method: str, question: str):
    import inspect

    original = getattr(PromptBuilder, method)
    signature = inspect.signature(original)

    def marked(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs).arguments
        bound.pop("self")
        context = {"language": self._language, "args": encode_args(bound)}
        if isinstance(out, tuple):
            return (ask_as(out[0], question, context),) + tuple(out[1:])
        return ask_as(out, question, context)

    marked.__name__ = method
    marked.__doc__ = original.__doc__
    return marked


for _question, (_method, _template, _kind) in QUESTIONS.items():
    setattr(InterpretationPromptBuilder, _method, _marking(_method, _question))


def interpret_request(prompt, request_id: str, max_tokens: int, think: bool,
                      budget_s: float | None) -> dict:
    """The fields of the Interpret request for a call of the client (the prompt marked by
    ask_as(); its token cap; whether the model may reason; the time it may take, None for no
    limit). ContextError for a prompt that is not marked."""
    asked = asked_question(prompt)
    if asked is None:
        raise ContextError("The prompt does not say which question asks it (ask_as)")
    question, context = asked
    return {
        "question": question,
        "request_id": request_id or "",
        "context": json.dumps(context, ensure_ascii=False),
        "variant": REASONING_VARIANT if think else "",
        "budget_s": float(budget_s) if budget_s is not None and budget_s > 0 else 0.0,
        "max_tokens": int(max_tokens or 0),
    }


def answer_text(success: bool, answer: str, source: str) -> str | None:
    """The model's text in the server's response, or None when there is none (a malformed
    request, no answer in time, the model down, a cancelled call)."""
    if not success or source == "invalid" or not answer:
        return None
    try:
        return json.loads(answer)[TEXT_FIELD]
    except (ValueError, KeyError, TypeError):
        return None
