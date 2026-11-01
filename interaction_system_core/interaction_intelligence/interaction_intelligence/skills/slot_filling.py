# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Slot filling: collect structured data in a DialogFrame, then confirm it.

The tree (slot_filling.xml) owns the phases (gathering, confirming) and the count of the
turns not understood. These skills understand the user (extract slots, classify the answer
to the confirmation), apply what was understood to the frame (deterministic) and phrase the
replies. A slot the user cannot or will not give ("I don't know", "skip it"), or that is not
obtained after the attempts allowed, is left unanswered: the frame does not ask it again, the
read-back says it is blank and the outcome is "partial". State keys: "frame"
(DialogFrame.to_state()), "ack_idx", "confirmed", "last" (what the last understanding step
found, for the reply that follows it).

Resume: if the request has initial_frame_json, the frame is seeded with those values.
"""

import json
import logging

from ..dialog_frame import DialogFrame
from ..frame_operations import (
    OpKind,
    apply_operations,
    corrections_to_extractions,
    extractions_to_operations,
)
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill

_log = logging.getLogger("SlotFillingSkills")


def _frame(state: dict) -> DialogFrame:
    return DialogFrame.from_state(state["frame"])


def _save(state: dict, frame: DialogFrame) -> None:
    state["frame"] = frame.to_state()


def _last(state: dict) -> dict:
    return state.get("last") or {"extractions": [], "rejected": {}, "ack": "", "corrections": {}}


def _pop_ack(env: SkillEnv, state: dict) -> str:
    """Acknowledgment phrases are injected by code (not the LLM) to guarantee variety and
    prevent the LLM from saying "Noted." when nothing was extracted."""
    phrases = env.ack_phrases
    wanted = state.get("acknowledgments", -1)
    if not phrases or wanted == 0 or (wanted != 1 and not env.ack_enabled):
        return ""
    idx = state.get("ack_idx", 0)
    state["ack_idx"] = idx + 1
    return phrases[idx % len(phrases)]


def _slots_over_attempts(frame: DialogFrame, max_attempts: int) -> list[str]:
    return [s for s in frame.available_slots() if frame.attempts(s) >= max_attempts]


def _seed_from_initial_json(state: dict, frame: DialogFrame) -> int:
    """Seed the frame with the values of initial_frame_json. Returns the slots seeded."""
    initial = state.get("initial_frame_json", "")
    if not initial:
        return 0
    try:
        seed = json.loads(initial)
    except Exception as exc:
        _log.warning(f"initial_frame_json is not valid JSON ({exc}); ignored.")
        return 0
    if not isinstance(seed, dict):
        _log.warning("initial_frame_json must be a JSON object; ignored.")
        return 0

    known_slots = set(frame.all_keys())
    applicable = {k: v for k, v in seed.items() if k in known_slots}
    unknown = [k for k in seed if k not in known_slots]
    if unknown:
        _log.info(f"initial_frame_json: {len(unknown)} unknown slot(s) ignored ({', '.join(unknown)}).")
    if not applicable:
        return 0

    frame.try_update(applicable)
    rejected = frame.pop_rejected()
    if rejected:
        _log.warning(f"initial_frame_json: out-of-range value(s) ignored: {rejected}")
    seeded = sum(1 for k in applicable if not frame.is_empty(k))
    if seeded:
        _log.info(f"Resumed with {seeded}/{len(applicable)} slot(s) pre-filled.")
    return seeded


# ==== MODE ====


def _init(env: SkillEnv, state: dict) -> None:
    state.update({"frame": None, "ack_idx": 0, "confirmed": False, "last": None})


def _outcome(state: dict) -> tuple[str, str]:
    if not state.get("confirmed"):
        return "failed", "The data were not confirmed"
    unanswered = _frame(state).unanswered_slots()
    if unanswered:
        return "partial", "Unanswered slots: " + ", ".join(unanswered)
    return "completed", ""


def _strict_cancel(state: dict) -> bool:
    """While answering a closed-choice question, cancel-like words are routine: only an
    explicit command cancels."""
    if not state.get("frame"):
        return False
    frame = _frame(state)
    next_slot = frame.next_slot()
    return bool(next_slot and frame.canonical_values(next_slot))


register_mode(
    "slot_filling",
    DialogueMode(
        init=_init,
        phase=lambda state: state.get("phase") or "gathering",
        data=lambda state: _frame(state).to_dict() if state.get("frame") else {},
        succeeded=lambda state: bool(state.get("confirmed")) and not state.get("user_cancelled"),
        strict_cancel=_strict_cancel,
        outcome=_outcome,
    ),
)


# ==== OPENING ====


@skill("slot_filling.prepare")
def prepare(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Build the frame: from the request's schema if any (no LLM), otherwise designed by the
    LLM and audited against the resources; then seed it with initial_frame_json.

    label: "complete" (every slot already filled: confirm), "resumed" (some slots seeded) or
    "new"."""
    task, ctx = state["task"], env.ctx
    schema = state.get("frame_schema_json", "")
    if schema:
        frame = DialogFrame.from_slots_json(schema, history_max_turns=env.history_max_turns)
    else:
        frame = env.llm.create_frame(task, ctx)
        if not frame or not frame.all_keys():
            raise ValueError("Failed to create DialogFrame from LLM")
        try:
            env.llm.audit_frame(frame, task, ctx)
        except Exception as exc:
            _log.warning(f"Schema audit failed ({exc}); proceeding with original schema.")

    seeded = _seed_from_initial_json(state, frame)
    _save(state, frame)
    state["phase"] = "gathering"
    if frame.is_complete():
        return SkillResult(label="complete")
    return SkillResult(label="resumed" if seeded > 0 else "new")


def _opened(state: dict, frame: DialogFrame, reply: str) -> SkillResult:
    """The opening line, recorded in the frame's history like every later reply: the
    extraction of the first answer sees the question it answers."""
    frame.add_turn("assistant", reply)
    _save(state, frame)
    return SkillResult(reply=reply)


@skill("slot_filling.opening")
def opening(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    frame = _frame(state)
    return _opened(state, frame, env.llm.get_opening(state["task"], frame, env.ctx))


@skill("slot_filling.opening_direct")
def opening_direct(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The opening without a greeting (skip_intro, when dialogues are chained)."""
    frame = _frame(state)
    return _opened(state, frame, env.llm.get_opening_direct(state["task"], frame, env.ctx))


@skill("slot_filling.resume_opening")
def resume_opening(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    frame = _frame(state)
    return _opened(state, frame, env.llm.get_resume_opening(
        state["task"], frame, frame.next_slot(), env.ctx))


# ==== GATHERING ====


def _plain(text) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def _words_of(text) -> set[str]:
    return {w for w in _plain(text).split() if len(w) >= 3 or any(ch.isdigit() for ch in w)}


def _said(value, user_text: str) -> bool:
    """Whether the user's words contain the value: the whole value as a phrase (also a short
    one, e.g. "b"), or one of its words of three or more letters, or a number."""
    return (f" {_plain(value)} " in f" {_plain(user_text)} ") or bool(
        _words_of(value) & _words_of(user_text))


def _grounded(frame: DialogFrame, extractions: list[dict], user_text: str) -> list[dict]:
    """The extractions without the values of a closed slot (canonical values) that the user's
    words do not contain: a value picked from the list for words that say nothing of it
    (provisional, 2026-11-01). Lists keep their grounded items."""
    out = []
    for e in extractions:
        slot, value = e.get("slot"), e.get("value")
        if not slot or value is None or not frame.canonical_values(slot):
            out.append(e)
            continue
        items = value if isinstance(value, list) else [value]
        kept = [v for v in items if _said(v, user_text)]
        if not kept:
            _log.info(f"Value not in the user's words, not taken: {slot}={value!r}")
            continue
        out.append({**e, "value": kept if isinstance(value, list) else kept[0]})
    return out


@skill("slot_filling.update")
def update(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Understand a gathering turn (number: max_attempts): what the turn does and the slots it
    gives or corrects (NLU), applied to the frame. Out-of-range values are rejected. If the
    user cannot or will not give the slot asked, it is left unanswered. A turn that neither
    fills the slot asked nor asks a question counts as a failed attempt on it; after
    max_attempts failed attempts the slot is asked differently, and after one more it is left
    unanswered.

    label: "complete" (go to the confirmation), "incomplete" (understood: ask for what is
    missing) or "not_understood" (no value, no question, no skip: a turn without progress)."""
    frame = _frame(state)
    frame.add_turn("user", inp.user_text)
    slot_asked = frame.next_slot()
    act, extractions = env.llm.extract_turn(inp.user_text, frame, env.ctx)
    extractions = _grounded(frame, extractions, inp.user_text)

    operations = extractions_to_operations(frame, extractions, default_list_op=OpKind.ADD)
    applied = apply_operations(frame, operations)
    rejected = frame.pop_rejected()
    if rejected:
        _log.info(f"Out-of-range value(s) rejected: {rejected}")
        extractions = [e for e in extractions if e.get("slot") not in rejected]

    extracted_keys = {op.slot for op in operations} - rejected.keys()
    ack = _pop_ack(env, state) if applied > 0 else ""
    skipped = ""
    if slot_asked is not None and slot_asked not in extracted_keys:
        if act == "skip":
            skipped = slot_asked
        elif act != "question" and frame.increment_attempts(slot_asked) > inp.number > 0:
            skipped = slot_asked
        if skipped:
            frame.mark_unanswered(skipped)
            ack = f"{ack} {env.llm.phrase('slot_skipped')}".strip()
            _log.info(f"Slot left unanswered: {skipped} ({act})")

    state["last"] = {
        "extractions": extractions,
        "rejected": rejected,
        "ack": ack,
        "corrections": {},
        "skipped": skipped,
    }
    _save(state, frame)
    if frame.is_complete():
        return SkillResult(label="complete")
    if extractions or rejected or skipped or act == "question":
        return SkillResult(label="incomplete")
    return SkillResult(label="not_understood")


@skill("slot_filling.next_question")
def next_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Acknowledge what the last turn gave and ask for what is missing (number: the attempts
    after which a slot is asked differently, max_attempts)."""
    frame = _frame(state)
    last = _last(state)
    next_q = env.llm.generate_response(
        task=state["task"],
        user_text=inp.user_text,
        frame=frame,
        extracted_slots=last["extractions"],
        attempts_exceeded=_slots_over_attempts(frame, inp.number),
        ctx=env.ctx,
        rejected=last["rejected"],
    )
    reply = f"{last['ack']} {next_q}".strip() if last["ack"] else next_q
    frame.add_turn("assistant", reply)
    _save(state, frame)
    state["phase"] = "gathering"
    return SkillResult(reply=reply)


# ==== CONFIRMING ====


@skill("slot_filling.confirmation_question")
def confirmation_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Read back the frame and ask whether it is right (after the acknowledgment of the last
    turn, if any). option "opening": the frame was complete from the start (not recorded in
    the history, as the other openings)."""
    frame = _frame(state)
    question = env.llm.get_confirmation_question(state["task"], frame, env.ctx)
    ack = _last(state)["ack"]
    reply = f"{ack} {question}".strip() if ack else question
    if inp.option != "opening":
        frame.add_turn("assistant", reply)
    if state.get("last"):
        state["last"]["ack"] = ""
    _save(state, frame)
    state["phase"] = "confirming"
    return SkillResult(reply=reply)


@skill("slot_filling.classify_confirmation")
def classify_confirmation(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Understand the answer to the confirmation question and apply its corrections.

    label: "confirms", "question", "corrects_reopened" (a correction emptied a slot: gather
    again), "corrects_applied" (corrected and still complete: confirm again) or "unclear"
    (also a "correction" that changed nothing: the user is almost certainly affirming)."""
    frame = _frame(state)
    frame.add_turn("user", inp.user_text)
    intent, corrections = env.llm.classify_intent(inp.user_text, frame, env.ctx)
    corrections = corrections or {}
    state["last"] = {"extractions": [], "rejected": {}, "ack": "", "corrections": corrections}
    label = intent if intent in ("confirms", "question") else "unclear"

    if intent == "corrects":
        operations = extractions_to_operations(
            frame, corrections_to_extractions(corrections), default_list_op=OpKind.ADD
        )
        applied = apply_operations(frame, operations)
        if applied == 0 and not frame.has_rejected():
            # The correction could not be applied (unreadable): read the turn again as in the
            # gathering phase, so that "No, three tickets, not two" is not lost
            _, extractions = env.llm.extract_turn(inp.user_text, frame, env.ctx)
            extractions = [e for e in _grounded(frame, extractions, inp.user_text)
                           if frame.filled_slots().get(e.get("slot")) != e.get("value")]
            operations = extractions_to_operations(frame, extractions,
                                                   default_list_op=OpKind.ADD)
            applied = apply_operations(frame, operations)
        # An out-of-range correction means the old value is wrong and the new one is
        # invalid: empty the slot so gathering asks for it again.
        rejected = frame.pop_rejected()
        for slot in rejected:
            frame.clear_slot(slot)
        if rejected:
            _log.info(f"Out-of-range correction(s) rejected: {rejected}")
            operations = [op for op in operations if op.slot not in rejected]

        if not frame.is_complete():
            state["last"]["extractions"] = [{"slot": op.slot, "value": op.value} for op in operations]
            state["last"]["rejected"] = rejected
            label = "corrects_reopened"
        elif applied > 0:
            label = "corrects_applied"

    _save(state, frame)
    return SkillResult(label=label)


@skill("slot_filling.confirmed")
def confirmed(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    state["confirmed"] = True
    state["completed"] = True
    reply = env.llm.response_to_intent(
        task=state["task"], intent="confirms", corrections={}, frame=_frame(state), ctx=env.ctx
    )
    # The dialogue ends here: a closing that asks again (small models restated the summary and
    # asked "is that correct?" once more) is replaced by the fixed one
    if "?" in reply or "¿" in reply:
        reply = env.llm.phrase("confirmed_closing")
    return SkillResult(reply=reply)


@skill("slot_filling.answer_question")
def answer_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    reply = env.llm.answer_confirmation_question(
        inp.user_text, state["task"], _frame(state), env.ctx
    )
    return SkillResult(reply=reply)


@skill("slot_filling.correction_reply")
def correction_reply(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """After a correction: an acknowledgment and the frame read back as it is recorded, with
    the confirmation question (provisional, 2026-11-01: a free sentence about the correction
    said that a value had been changed when it had not)."""
    frame = _frame(state)
    question = env.llm.get_confirmation_question(state["task"], frame, env.ctx)
    reply = f"{_pop_ack(env, state)} {question}".strip()
    frame.add_turn("assistant", reply)
    _save(state, frame)
    return SkillResult(reply=reply)
