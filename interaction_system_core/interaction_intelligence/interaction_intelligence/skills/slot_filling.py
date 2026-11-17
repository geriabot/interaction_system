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
import re

from ..dialog_frame import DialogFrame
from ..frame_operations import (
    OpKind,
    apply_operations,
    corrections_to_extractions,
    extractions_to_operations,
)
from ..languages import bool_words, load_language
from ..spoken_numbers import spoken_numbers
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
    """The slots on their last asking: max_attempts - 1 failed attempts (the next failure
    leaves the slot unanswered), asked differently."""
    last = max(max_attempts - 1, 1)
    return [s for s in frame.available_slots() if frame.attempts(s) >= last]


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


def _cancel_stage(state: dict) -> str:
    """Where the conversation is, for the cancel detector: the robot carries nothing out until
    the conversation ends, so "cancel the booking" drops the request being prepared."""
    if state.get("phase") == "confirming":
        return ("The robot has read the details back and asks the user to confirm them; nothing "
                "has been booked, ordered or sent yet.")
    return ("The robot is collecting the details of the user's request; nothing has been "
            "booked, ordered or sent yet.")


def _pending(env: SkillEnv, state: dict) -> str:
    """The question being asked for the next slot (the last question of the robot's line, without
    its acknowledgment); "" while confirming (the whole read-back is what is pending)."""
    if state.get("phase") == "confirming" or not state.get("frame"):
        return ""
    return _question_of(_robot_line(_frame(state)))


register_mode(
    "slot_filling",
    DialogueMode(
        finishing_is_an_answer=True,  # "that's all" goes to the reading of the frame
        init=_init,
        phase=lambda state: state.get("phase") or "gathering",
        data=lambda state: _frame(state).to_dict() if state.get("frame") else {},
        succeeded=lambda state: bool(state.get("confirmed")) and not state.get("user_cancelled"),
        strict_cancel=_strict_cancel,
        cancel_stage=_cancel_stage,
        expected_answers=lambda state: (
            _frame(state).canonical_values(_frame(state).next_slot() or "")
            if state.get("frame") and _frame(state).next_slot() else []),
        yes_is_an_answer=lambda state: state.get("phase") == "confirming",
        outcome=_outcome,
        pending=_pending,
    ),
)


# ==== OPENING ====


@skill("slot_filling.prepare")
def prepare(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Build the frame: from the request's schema if any (no LLM), otherwise designed by the
    LLM and audited against the resources; then seed it with initial_frame_json.

    label: "complete" (every slot already filled: confirm), "resumed" (some slots seeded),
    "new", or "design_failed": the LLM could not design the frame (no schema), and the reply is
    the closing line of an error, said before the dialogue ends with the outcome error (the
    user in front of the robot is not left in silence). An invalid schema is the requester's
    error and fails at once, before the robot speaks."""
    task, ctx = state["task"], env.ctx
    schema = state.get("frame_schema_json", "")
    if schema:
        frame = DialogFrame.from_slots_json(schema, history_max_turns=env.history_max_turns)
    else:
        try:
            frame = env.llm.create_frame(task, ctx)
        except Exception as exc:
            _log.warning(f"The LLM failed to design the frame ({exc}).")
            frame = None
        if not frame or not frame.all_keys():
            _log.error("Failed to create DialogFrame from LLM")
            return SkillResult(reply=env.llm.phrase("close_error"), label="design_failed")
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


# A canonical value written as a time of day ("18:00", "9.30", "7h15")
_TIME = re.compile(r"^(\d{1,2})\s*[:.h]\s*(\d{2})$")


def _language(env: SkillEnv) -> str:
    return getattr(env.llm, "language", "English") or "English"


def _numbers(text: str, language: str) -> set[float]:
    """The numbers of a line, in digits or in words (the weak "a"/"one" only when there is no
    other)."""
    strong, weak = spoken_numbers(str(text), language)
    return set(strong) or set(weak)


def _number_match(value, said: set[float]) -> int:
    """How the numbers the user said match a canonical value: 2 every number of the value (a
    time: its hour, also on a 12-hour clock, and its minutes; ":00" when no other number was
    said), 1 a time whose hour only matches, 0 otherwise or a value without numbers."""
    time = _TIME.match(str(value).strip())
    if time:
        hour, minute = int(time.group(1)), int(time.group(2))
        hours = {hour, hour % 12 or 12}
        if not hours & said:
            return 0
        if minute in said or (minute == 0 and not said - hours):
            return 2
        return 1
    numbers = set(spoken_numbers(str(value), "English")[0]) | set(
        spoken_numbers(str(value), "Spanish")[0])
    return 2 if numbers and numbers <= said else 0


def _match_scores(canon: list[str], user_text: str, language: str) -> dict[str, int]:
    """How strongly the user's words point to each canonical value of a slot: 4 the whole value
    said as a phrase, 3 its numbers said (also in words: "six in the evening" for "18:00"), 2 a
    word of it that no other value has (or the hour of a time), 1 only words other values share
    too ("a car" for "car rental" and "car wash"), 0 nothing. A value said only inside a longer
    value that was said ("satisfied" in "very satisfied") is 1."""
    said = f" {_plain(user_text)} "
    user_words = _words_of(user_text)
    numbers = _numbers(user_text, language)
    words = {c: _words_of(c) for c in canon}
    scores = {}
    for c in canon:
        if _plain(c) and f" {_plain(c)} " in said:
            scores[c] = 4
            continue
        others = set().union(*(words[o] for o in canon if o != c)) if len(canon) > 1 else set()
        by_number = _number_match(c, numbers)
        distinct = (words[c] - others) & user_words
        scores[c] = (3 if by_number == 2 else 2 if (distinct or by_number == 1)
                     else 1 if words[c] & user_words else 0)
    phrases = [c for c, s in scores.items() if s == 4]
    for c in phrases:
        if any(o != c and f" {_plain(c)} " in f" {_plain(o)} " for o in phrases):
            scores[c] = 1
    return scores


def _robot_line(frame: DialogFrame) -> str:
    """What the robot said last, before the user's turn being read."""
    turns = frame.last_turns(6)
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]
    return next((t["content"] for t in reversed(turns) if t["role"] == "assistant"), "")


def _named(canon: list[str], line: str) -> list[str]:
    """The canonical values a robot line names, in the order it says them (a value said only
    inside a longer one it names is not counted)."""
    text = f" {_plain(line)} "
    found = sorted((text.find(f" {_plain(c)} "), c) for c in canon
                   if _plain(c) and f" {_plain(c)} " in text)
    names = [c for _, c in found]
    return [c for c in names
            if not any(o != c and f" {_plain(c)} " in f" {_plain(o)} " for o in names)]


def _affirms(user_text: str) -> bool:
    """Whether the line starts with a yes word of a language file ("yes", "okay", "sí", "vale")."""
    words = _plain(user_text).split()
    yes = {_plain(w) for w in bool_words()[0]}
    return bool(words) and (words[0] in yes or " ".join(words[:2]) in yes)


def _ordinal_pick(named: list[str], user_text: str, language: str) -> str | None:
    """The value an ordinal points to ("the second one", "la última") among the values the robot
    has just read in order; None when there is no single ordinal or no list was read."""
    if len(named) < 2:
        return None
    try:
        lang = load_language(language)
    except Exception:
        return None
    words = set(_plain(user_text).split())
    picks = {named[i] for i, alternatives in enumerate(lang.ordinal_words[:len(named)])
             if words & {_plain(a) for a in alternatives.split("|")}}
    if words & {_plain(w) for w in lang.last_words}:
        picks.add(named[-1])
    return picks.pop() if len(picks) == 1 else None


def _canonical(value, canon: list[str]) -> str | None:
    return next((c for c in canon if c.lower().strip() == str(value).lower().strip()), None)


def _grounded(frame: DialogFrame, extractions: list[dict], user_text: str,
              robot_line: str = "", language: str = "English") -> tuple[list[dict], list[str]]:
    """The extractions without the values of a closed slot (canonical values) that the user's
    words do not point to: a value picked from the list for words that say nothing of it.
    Returns them and the closed slots whose answer was ambiguous (several values matched
    equally: the options are read again).

    A value is taken when the user says it (also in other words for numbers and times: "six in
    the evening" for "18:00", "two" for "2"), or a word only it has, unless another value
    matches as well or better; or when the user says yes to a robot question that named only
    that value ("Is it 18:00?" "Yes."); or when the user picks it by its position just after
    the robot read the options ("the second one"). Lists keep their grounded items."""
    out, ambiguous = [], []
    for e in extractions:
        slot, value = e.get("slot"), e.get("value")
        canon = frame.canonical_values(slot) if slot else []
        if not slot or value is None or not canon:
            out.append(e)
            continue
        scores = _match_scores(canon, user_text, language)
        named = _named(canon, robot_line)
        affirmed = _affirms(user_text) and len(named) == 1
        picked = _ordinal_pick(named, user_text, language)
        best = max(scores.values())
        top = [c for c, s in scores.items() if s == best]
        kept = []
        for v in value if isinstance(value, list) else [value]:
            c = _canonical(v, canon)
            s = scores.get(c, 0)
            if (s == 4 or (s >= 2 and s == best and len(top) == 1)
                    or (affirmed and named == [c]) or (picked is not None and picked == c)):
                kept.append(v)
            elif s >= 1 and s == best and len(top) > 1 and slot not in ambiguous:
                ambiguous.append(slot)
        if not kept:
            _log.info(f"Value not in the user's words, not taken: {slot}={value!r}")
            continue
        out.append({**e, "value": kept if isinstance(value, list) else kept[0]})
    return out, ambiguous


def _hedged(frame: DialogFrame, slot: str | None, act: str, user_text: str,
            language: str) -> bool:
    """Whether the answer to the closed slot asked does not settle on one of its values: it
    hedges between them or falls between them ("about 3, or 4" for 2, 4 or 6; "between
    morning and afternoon"). The model says so (act "between"); by code as well, when the
    user says two or more of the values in full with a hedge word ("or", "between", "maybe":
    hedge_words of the language file) and no "no" (a correction: "morning, no, afternoon").
    Not for list slots, which take several values."""
    canon = frame.canonical_values(slot) if slot else []
    if not canon or frame.slot_type(slot) == "list_str":
        return False
    if act == "between":
        return True
    try:
        lang = load_language(language)
    except Exception:
        return False
    said = [c for c, s in _match_scores(canon, user_text, language).items() if s >= 3]
    text = f" {_plain(user_text)} "

    def has(words):
        return any(_plain(w) and f" {_plain(w)} " in text for w in words)
    return len(said) >= 2 and has(lang.hedge_words) and not has(lang.no_words)


def _picked_by_position(frame: DialogFrame, slot: str | None, extractions: list[dict],
                        user_text: str, robot_line: str, language: str) -> list[dict]:
    """An ordinal answer to the options just read ("the second one") for the closed slot asked,
    when the model extracted nothing for it: the value at that position."""
    if not slot or not frame.canonical_values(slot) or any(
            e.get("slot") == slot for e in extractions):
        return extractions
    picked = _ordinal_pick(_named(frame.canonical_values(slot), robot_line), user_text, language)
    if picked is None:
        return extractions
    value = [picked] if frame.slot_type(slot) == "list_str" else picked
    return extractions + [{"slot": slot, "value": value, "source_span": user_text}]


def _trimmed(frame: DialogFrame, extractions: list[dict], language: str) -> list[dict]:
    """Free-text values without the short words that never end a value said on its own
    ("Marta the" is "Marta"; the trailing_words of the language file) nor recognizer noise at
    either end ("uh"); a value made only of them is dropped."""
    try:
        lang = load_language(language)
    except Exception:
        return extractions
    trailing = set(lang.trailing_words)
    noise = {p for p in lang.asr_noise_phrases if " " not in p}
    out = []
    for e in extractions:
        slot, value = e.get("slot"), e.get("value")
        if (not slot or not isinstance(value, str) or frame.canonical_values(slot)
                or frame.slot_type(slot) != "str"):
            out.append(e)
            continue
        words = value.split()
        while words and _plain(words[0]) in noise:
            words.pop(0)
        while words and (_plain(words[-1]) in trailing or _plain(words[-1]) in noise):
            words.pop()
        cleaned = " ".join(words).strip(" ,.;:")
        if cleaned != value:
            _log.info(f"Free-text value trimmed: {slot}={value!r} -> {cleaned!r}")
        if cleaned:
            out.append({**e, "value": cleaned})
    return out


def _options_question(env: SkillEnv, frame: DialogFrame, slot: str,
                      key: str = "slot_options") -> str:
    """The closed question of a slot's last asking, composed by code: "Is it A, B or C?"; with
    key "slot_between", after an answer between its values: "I need one of these: A, B or C."."""
    values = frame.canonical_values(slot)
    joined = values[0] if len(values) == 1 else (
        f"{', '.join(values[:-1])} {env.llm.phrase('questionnaire_or')} {values[-1]}")
    return env.llm.phrase(key).format(options=joined)


@skill("slot_filling.update")
def update(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Understand a gathering turn (number: max_attempts): what the turn does and the slots it
    gives or corrects (NLU), applied to the frame. Out-of-range values are rejected. An answer
    between values of the closed slot asked ("about 3, or 4" for 2, 4 or 6: _hedged) takes no
    value for it and counts as a failed attempt; the options are read next. If the
    user cannot or will not give the slot asked, it is left unanswered. A turn that gives no
    value at all (none for another slot either) and asks no question counts as a failed
    attempt on the slot asked; the last asking (after max_attempts - 1 failed attempts) is
    different, and at max_attempts failed attempts the slot is left unanswered: the same
    ladder as the other behaviors (give up at the max_attempts-th failure).

    label: "complete" (go to the confirmation), "incomplete" (understood: ask for what is
    missing; also a slot the user would not or could not give, which is an answer), "repeat"
    (the user asked to hear the question again: a turn without progress that is not a failed
    attempt), "unanswered" (the slot asked was left unanswered at its max_attempts-th failed
    attempt, and the turn gave nothing else: a turn without progress, as when the other
    behaviors give up an element) or "not_understood" (no value, no question, no skip: a turn
    without progress)."""
    frame = _frame(state)
    frame.add_turn("user", inp.user_text)
    slot_asked = frame.next_slot()
    act, extractions = env.llm.extract_turn(inp.user_text, frame, env.ctx)
    robot_line, language = _robot_line(frame), _language(env)
    extractions, ambiguous = _grounded(frame, extractions, inp.user_text, robot_line, language)
    extractions = _picked_by_position(frame, slot_asked, extractions, inp.user_text, robot_line,
                                      language)
    extractions = _trimmed(frame, extractions, language)
    between = []
    if _hedged(frame, slot_asked, act, inp.user_text, language):
        # An answer between values of the closed slot asked: nothing is taken for it, it counts
        # as a failed attempt (below) and the options are read ("I need one of these: ...")
        extractions = [e for e in extractions if e.get("slot") != slot_asked]
        between = [slot_asked]
        _log.info(f"Answer between values, not taken: {slot_asked}")

    operations = extractions_to_operations(frame, extractions, default_list_op=OpKind.ADD)
    applied = apply_operations(frame, operations)
    rejected = frame.pop_rejected()
    if rejected:
        _log.info(f"Out-of-range value(s) rejected: {rejected}")
        extractions = [e for e in extractions if e.get("slot") not in rejected]

    extracted_keys = {op.slot for op in operations} - rejected.keys()
    if act == "repeat" and not extractions and not rejected:
        # A request to hear the question again: a turn without progress (the tree counts it),
        # never a failed attempt on the slot; its own ladder per slot (repeat_question)
        repeats = state.setdefault("repeats", {})
        repeats[slot_asked or ""] = repeats.get(slot_asked or "", 0) + 1
        state["last"] = {"extractions": [], "rejected": {}, "ack": "", "corrections": {},
                         "skipped": "", "act": act, "ambiguous": []}
        _save(state, frame)
        return SkillResult(label="repeat")
    ack = _pop_ack(env, state) if applied > 0 else ""
    skipped = ""
    if slot_asked is not None and slot_asked not in extracted_keys:
        if act == "skip":
            skipped = slot_asked
        elif (act != "question" and not extracted_keys
              and frame.increment_attempts(slot_asked) >= inp.number > 0):
            # An answer about another slot is not a failed attempt on the slot asked
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
        "act": act,
        "ambiguous": ambiguous,
        "between": between,
    }
    if frame.is_complete() or extractions or rejected or skipped or act == "question":
        state.pop("repeats", None)  # progress: the ladder of repetitions starts again
    _save(state, frame)
    if frame.is_complete():
        return SkillResult(label="complete")
    if skipped and act != "skip" and not (extractions or rejected or act == "question"):
        return SkillResult(label="unanswered")
    if extractions or rejected or skipped or act == "question":
        return SkillResult(label="incomplete")
    return SkillResult(label="not_understood")


def _number_words(value: float, slot_type: str) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _range_words(env: SkillEnv, frame: DialogFrame, slot: str) -> str:
    """The allowed range of a numeric slot in the dialogue's words ("between 1 and 8", "at least
    18"), built by code from the schema so that no bound is left out. An exclusive bound of an
    integer slot is said as the inclusive one next to it ("less than 120" is "at most 119")."""
    bounds = frame.bounds(slot)
    integer = frame.slot_type(slot) == "int"
    low = high = None
    low_open = high_open = False
    if "minimum" in bounds:
        low = bounds["minimum"]
    elif "exclusive_minimum" in bounds:
        low, low_open = bounds["exclusive_minimum"], True
        if integer and float(low).is_integer():
            low, low_open = low + 1, False
    if "maximum" in bounds:
        high = bounds["maximum"]
    elif "exclusive_maximum" in bounds:
        high, high_open = bounds["exclusive_maximum"], True
        if integer and float(high).is_integer():
            high, high_open = high - 1, False
    kind = frame.slot_type(slot)
    lo = _number_words(low, kind) if low is not None else ""
    hi = _number_words(high, kind) if high is not None else ""
    if low is not None and high is not None and not low_open and not high_open:
        return env.llm.phrase("range_between").format(low=lo, high=hi)
    parts = []
    if low is not None:
        parts.append(env.llm.phrase("range_more_than" if low_open else "range_at_least")
                     .format(low=lo))
    if high is not None:
        parts.append(env.llm.phrase("range_less_than" if high_open else "range_at_most")
                     .format(high=hi))
    return ", ".join(parts)


def _rejected_line(env: SkillEnv, frame: DialogFrame, rejected: dict) -> str:
    """What the robot says about values out of range: each value and its allowed range."""
    lines = []
    for slot, value in (rejected or {}).items():
        words = _range_words(env, frame, slot)
        if not words:
            continue
        shown = _number_words(value, frame.slot_type(slot)) if isinstance(
            value, (int, float)) and not isinstance(value, bool) else str(value)
        lines.append(env.llm.phrase("slot_out_of_range").format(value=shown, range=words))
    return " ".join(lines)


@skill("slot_filling.next_question")
def next_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Acknowledge what the last turn gave and ask for what is missing (number: max_attempts;
    a slot on its last asking, max_attempts - 1 failed attempts, is asked differently). A
    value out of range is answered
    with the value and its allowed range, said by code (the model asks for the slot again)."""
    frame = _frame(state)
    last = _last(state)
    asked = frame.next_slot()
    over = _slots_over_attempts(frame, inp.number)
    if (asked and frame.canonical_values(asked) and last.get("act") != "question"
            and not last["rejected"] and asked in last.get("between", [])):
        # An answer between its values ("about 3, or 4" for 2, 4 or 6): the options, by code
        next_q = _options_question(env, frame, asked, "slot_between")
    elif (asked and frame.canonical_values(asked) and last.get("act") != "question"
            and not last["rejected"] and (asked in over or asked in last.get("ambiguous", []))):
        # The last asking of a closed slot, or an answer that matched several of its values:
        # the options, composed by code ("Is it A, B or C?")
        next_q = _options_question(env, frame, asked)
    else:
        next_q = env.llm.generate_response(
            task=state["task"],
            user_text=inp.user_text,
            frame=frame,
            extracted_slots=last["extractions"],
            attempts_exceeded=[s for s in over if not frame.canonical_values(s)],
            ctx=env.ctx,
            rejected=last["rejected"],
        )
        if (next_q == env.llm.phrase("unclear") and last["extractions"] and asked
                and frame.canonical_values(asked)):
            # The model failed after values were taken: never "Could you repeat that?" then
            next_q = _options_question(env, frame, asked)
    range_line = _rejected_line(env, frame, last["rejected"])
    if range_line:
        next_q = f"{range_line} {next_q}"
    reply = f"{last['ack']} {next_q}".strip() if last["ack"] else next_q
    frame.add_turn("assistant", reply)
    _save(state, frame)
    state["phase"] = "gathering"
    return SkillResult(reply=reply)


def _question_of(line: str) -> str:
    """The question a robot line asks (its last question sentence, as said), or the whole
    line when it has none."""
    end = line.rfind("?")
    if end < 0:
        return line.strip()
    start = max(line.rfind(c, 0, end) for c in ".!;:") + 1
    return line[start:end + 1].strip()


@skill("slot_filling.repeat_question")
def repeat_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The user asked to hear the question again. A ladder per slot (the count starts again
    with every turn with progress): the first time the question literally, as it was said; the
    second time in simpler words; from the third time the options of a closed slot ("Is it A,
    B or C?", by code) or, for an open slot, simpler words with an example of the kind of
    answer. Each request is a turn without progress, so many in a row end as a breakdown."""
    frame = _frame(state)
    slot = frame.next_slot()
    n = state.get("repeats", {}).get(slot or "", 1)
    question = _question_of(_robot_line(frame))
    if n <= 1 and question:
        reply = question
    elif n >= 3 and slot and frame.canonical_values(slot):
        reply = _options_question(env, frame, slot)
    else:
        reply = env.llm.rephrase_slot_question(state["task"], frame, slot, question,
                                               example=n >= 3, ctx=env.ctx)
    frame.add_turn("assistant", reply)
    _save(state, frame)
    return SkillResult(reply=reply)


# ==== CONFIRMING ====


def _value_said(value, line: str, language: str) -> bool:
    """Whether a read-back line says a value: as a phrase, every word of it of three or more
    letters, or its numbers (also in words; a time by its hour and minutes)."""
    text = f" {_plain(line)} "
    if _plain(value) and f" {_plain(value)} " in text:
        return True
    words = _words_of(value)
    if words and words <= _words_of(line) and not any(ch.isdigit() for ch in str(value)):
        return True
    numbers = _numbers(line, "English") | _numbers(line, "Spanish")
    return _number_match(value, numbers) >= 1


def _readback_ok(line: str, frame: DialogFrame, language: str) -> bool:
    """Whether a generated read-back says every value of the frame (a yes/no slot is not
    checked: it is said in words) and no number that no value has (an added time or price)."""
    allowed: set[float] = set()
    for slot, value in frame.filled_slots().items():
        if frame.slot_type(slot) == "bool":
            continue
        for v in value if isinstance(value, list) else [value]:
            if not _value_said(v, line, language):
                _log.info(f"Read-back without {slot}={v!r}: built by code instead.")
                return False
            allowed |= _numbers(str(v), "English") | _numbers(str(v), "Spanish")
            time = _TIME.match(str(v).strip())
            if time:
                hour, minute = int(time.group(1)), int(time.group(2))
                allowed |= {hour, hour % 12 or 12, minute}
    strong = set(spoken_numbers(line, language)[0])
    extra = strong - allowed
    if extra:
        _log.info(f"Read-back with numbers the frame does not have ({sorted(extra)}): built by "
                  f"code instead.")
    return not extra


def _readback_template(env: SkillEnv, frame: DialogFrame) -> str:
    """The read-back built by code: the values in the order of the schema, the slots left
    blank, and the yes/no question."""
    yes_no = load_language(_language(env))
    details = []
    for slot, value in frame.filled_slots().items():
        if frame.slot_type(slot) == "bool":
            word = yes_no.yes_words[0] if value else yes_no.no_words[0]
            details.append(f"{slot.replace('_', ' ')}: {word}")
        elif isinstance(value, list):
            details.append(", ".join(str(v) for v in value))
        else:
            details.append(str(value))
    blank = frame.unanswered_slots()
    if blank:
        details.append(env.llm.phrase("readback_blank").format(
            slots=", ".join(s.replace("_", " ") for s in blank)))
    return env.llm.phrase("readback").format(details="; ".join(details))


def _readback(env: SkillEnv, state: dict, frame: DialogFrame) -> str:
    """The read-back with its yes/no question: the generated one when it says every value and
    adds no number, else the one built by code (also when the model failed: its fallback has no
    data)."""
    question = env.llm.get_confirmation_question(state["task"], frame, env.ctx)
    if _readback_ok(question, frame, _language(env)):
        return question
    return _readback_template(env, frame)


@skill("slot_filling.confirmation_question")
def confirmation_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Read back the frame and ask whether it is right (after the acknowledgment of the last
    turn, if any): the generated read-back is checked against the frame (_readback). option
    "opening": the frame was complete from the start (not recorded in
    the history, as the other openings)."""
    frame = _frame(state)
    question = _readback(env, state, frame)
    ack = _last(state)["ack"]
    reply = f"{ack} {question}".strip() if ack else question
    if inp.option != "opening":
        frame.add_turn("assistant", reply)
    if state.get("last"):
        state["last"]["ack"] = ""
    _save(state, frame)
    state["phase"] = "confirming"
    return SkillResult(reply=reply)


def _starts_negative(user_text: str) -> bool:
    """Whether the line starts with a no word of a language file ("no", "nope")."""
    words = _plain(user_text).split()
    return bool(words) and words[0] in {_plain(w) for w in bool_words()[1]}


def _named_new_values(frame: DialogFrame, user_text: str, language: str) -> dict:
    """The corrections a reply to the read-back makes by naming values the data do not have
    ("Yes, free swim and aquagym" when only free swim was read back): for a list slot, the
    canonical values it names that the list lacks (added); for a slot with one value, the one
    canonical value it names, when it names only that one and it differs from the stored value.
    A value with a denial word of the language file among the three words before it ("not the
    sauna", "sin jamón") is not counted. Only canonical values (whole phrases) are looked at: free text and numbers
    are left to the model."""
    try:
        denial = tuple(load_language(language).denial_words)
    except Exception:
        denial = ()
    said = f" {_plain(user_text)} "
    filled = frame.filled_slots()
    out: dict = {}
    for slot in frame.all_keys():
        canon = frame.canonical_values(slot) or []
        if not canon or frame.slot_type(slot) == "bool":
            continue
        named = []
        for c in _named(canon, user_text):
            at = said.find(f" {_plain(c)} ")
            before = f" {' '.join(said[:at].split()[-3:])} "
            if any(f" {d} " in before for d in denial):
                continue
            named.append(c)
        if not named:
            continue
        value = filled.get(slot)
        if frame.slot_type(slot) == "list_str":
            have = {_plain(v) for v in (value or [])}
            new = [c for c in named if _plain(c) not in have]
            if new:
                out[slot] = {"_add": True, "items": new}
        elif len(named) == 1 and (value is None or _plain(named[0]) != _plain(value)):
            out[slot] = named[0]
    return out


@skill("slot_filling.classify_confirmation")
def classify_confirmation(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Understand the answer to the confirmation question and apply its corrections.

    label: "confirms", "question", "repeat" (asks to hear the data again: they are read back
    again, a turn without progress), "corrects_reopened" (a correction emptied a slot: gather
    again), "corrects_applied" (corrected and still complete: confirm again) or "unclear"
    (also a "correction" that changed nothing: the user is almost certainly affirming). A
    confirmation that names canonical values the data lack ("Yes, free swim and aquagym" when only
    free swim was read back) is read as a correction of them (_named_new_values)."""
    frame = _frame(state)
    frame.add_turn("user", inp.user_text)
    intent, corrections = env.llm.classify_intent(inp.user_text, frame, env.ctx)
    corrections = corrections or {}
    if intent == "confirms" and _starts_negative(inp.user_text):
        # "No, ..." without a value the model could read is never a confirmation (a garbled
        # correction was taken as one): the data are read back again
        _log.info("A confirmation that starts with a negation: read as unclear.")
        intent = "unclear"
    if intent == "confirms" and not corrections:
        # "Yes, free swim and aquagym" when only free swim was read back: the model took the
        # yes and left out the value it adds; a yes that names values the data lack is read as
        # a correction of them (applied, and the data read back again)
        named = _named_new_values(frame, inp.user_text, _language(env))
        if named:
            _log.info(f"A confirmation that names values the data lack: read as a correction "
                      f"({named}).")
            intent, corrections = "corrects", named
    state["last"] = {"extractions": [], "rejected": {}, "ack": "", "corrections": corrections}
    label = intent if intent in ("confirms", "question", "repeat") else "unclear"

    if intent == "corrects":
        operations = extractions_to_operations(
            frame, corrections_to_extractions(corrections), default_list_op=OpKind.ADD
        )
        applied = apply_operations(frame, operations)
        if applied == 0 and not frame.has_rejected():
            # The correction could not be applied (unreadable): read the turn again as in the
            # gathering phase, so that "No, three tickets, not two" is not lost
            _, extractions = env.llm.extract_turn(inp.user_text, frame, env.ctx)
            grounded, _ = _grounded(frame, extractions, inp.user_text, _robot_line(frame),
                                    _language(env))
            extractions = [e for e in grounded
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
    """The answer to a question at the read-back, followed by the confirmation question again
    (a fixed yes/no question, by code): without it, an "okay" to the answer was taken as a
    confirmation of the data."""
    reply = env.llm.answer_confirmation_question(
        inp.user_text, state["task"], _frame(state), env.ctx
    )
    return SkillResult(reply=f"{reply} {env.llm.phrase('confirm_again')}".strip())


@skill("slot_filling.correction_reply")
def correction_reply(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """After a correction: an acknowledgment and the frame read back as it is recorded, with
    the confirmation question (provisional, 2026-11-01: a free sentence about the correction
    said that a value had been changed when it had not)."""
    frame = _frame(state)
    question = _readback(env, state, frame)
    reply = f"{_pop_ack(env, state)} {question}".strip()
    frame.add_turn("assistant", reply)
    _save(state, frame)
    return SkillResult(reply=reply)
