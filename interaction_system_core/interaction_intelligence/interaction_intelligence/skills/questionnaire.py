# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Standardized questionnaire administration: read an instrument faithfully and score it.

The validity of an instrument requires that it is administered as it was validated: every item
read as written, in order, with its answer options. So the robot reads the items and the
options literally (no LLM), and the LLM only maps the user's free answer to one of the options
of the scale. An answer between options is not decided for the user: the options are said again,
without suggesting any; a question about the meaning gets the clarification of the instrument,
if it has one, or the item again; the user may decline to answer. The score is computed by code
with the rule of the instrument (sum or mean, reversed items). The robot does not say the score.

The instrument comes in the resources of the request: the first resource that is a JSON object
with a list "items":

    {"name": "Satisfaction", "instructions": "I will read you four statements...",
     "options": ["Not at all", "A little", "Quite", "Very much"],
     "items": ["The robot understood me", {"text": "...", "reverse": true,
               "clarification": "...", "options": [...]}],
     "scoring": "sum"}

Options are strings (values 1..n in order) or {"label", "value"}; an item may have its own
options. "scoring": "sum" (default), "mean" or "none". State keys: "instrument", "idx",
"answers" ([{item, status: answered | refused | unanswered, answer, value, said}]; an answer the
user changed afterwards also has "corrected_from").
"""

import unicodedata

from ..languages import load_language
from ..utils import normalize_text, parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill


def _options(raw) -> list[dict]:
    options = []
    for i, o in enumerate(raw or []):
        if isinstance(o, str) and o.strip():
            options.append({"label": o.strip(), "value": i + 1})
        elif isinstance(o, dict) and str(o.get("label", "")).strip():
            options.append({"label": str(o["label"]).strip(),
                            "value": float(o.get("value", i + 1))})
    return options


def parse_instrument(resources: list[dict]) -> dict | None:
    for resource in resources:
        content = (resource.get("content", "") or "").strip()
        if not content.startswith("{"):
            continue
        try:
            data = parse_loose_json(content)
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            continue
        shared = _options(data.get("options"))
        items = []
        for entry in data["items"]:
            if isinstance(entry, str) and entry.strip():
                entry = {"text": entry}
            if not isinstance(entry, dict) or not str(entry.get("text", "")).strip():
                continue
            items.append({
                "text": str(entry["text"]).strip(),
                "options": _options(entry.get("options")) or shared,
                "reverse": entry.get("reverse") is True,
                "clarification": str(entry.get("clarification", "")).strip(),
            })
        if items and all(item["options"] for item in items):
            return {"name": str(data.get("name", "")).strip(),
                    "instructions": str(data.get("instructions", "")).strip(),
                    "scoring": data.get("scoring", "sum"), "items": items}
    return None


def _init(env: SkillEnv, state: dict) -> None:
    instrument = parse_instrument(state["resources"])
    if instrument is None:
        raise ValueError(
            "The questionnaire requires a resource with a JSON object with 'items' (the "
            "statements or questions) and their answer 'options'."
        )
    state.update({"instrument": instrument, "idx": 0, "answers": []})


def _value(item: dict, option: dict) -> float:
    if not item["reverse"]:
        return option["value"]
    values = [o["value"] for o in item["options"]]
    return min(values) + max(values) - option["value"]


def _score(state: dict):
    rule = state["instrument"]["scoring"]
    values = [a["value"] for a in state["answers"] if a["status"] == "answered"]
    if rule == "none" or not values:
        return None
    return sum(values) / len(values) if rule == "mean" else sum(values)


def _outcome(state: dict) -> tuple[str, str]:
    if not state.get("completed"):
        return "failed", "The questionnaire did not reach its end"
    missing = [f"{i + 1} ({a['status']})" for i, a in enumerate(state["answers"])
               if a["status"] != "answered"]
    if missing:
        return "partial", "Items without an answer: " + ", ".join(missing)
    return "completed", ""


register_mode(
    "questionnaire",
    DialogueMode(
        init=_init,
        phase=lambda state: ("done" if state.get("completed")
                             else f"item_{state['idx'] + 1}_of_{len(state['instrument']['items'])}"),
        data=lambda state: {
            "instrument": state["instrument"]["name"],
            "answers": list(state["answers"]),
            "answered": sum(1 for a in state["answers"] if a["status"] == "answered"),
            "score": _score(state),
            "scoring": state["instrument"]["scoring"],
        },
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        outcome=_outcome,
        pending=lambda env, state: (
            "" if state.get("completed") or state["idx"] >= len(state["instrument"]["items"])
            else _item_text(env, state)),
        expected_answers=lambda state: (
            [] if state.get("completed") or state["idx"] >= len(state["instrument"]["items"])
            else [o["label"] for o in state["instrument"]["items"][state["idx"]]["options"]]),
        # Where the conversation is, for the cancel detector: the items are closed-choice
        # questions, so cancel-like words are usually answers ("I stop to rest quite often").
        # Not strict mode: a hesitant wish to stop ("maybe that's enough for today?") is still
        # asked about
        cancel_stage=lambda state: (
            "The robot is reading the items of a questionnaire one by one; the user answers "
            "each with one of its options."),
    ),
)


def _options_text(env: SkillEnv, item: dict) -> str:
    labels = [o["label"] for o in item["options"]]
    joined = labels[0] if len(labels) == 1 else (
        ", ".join(labels[:-1]) + f" {env.llm.phrase('questionnaire_or')} {labels[-1]}")
    return env.llm.phrase("questionnaire_options").replace("{options}", joined)


def _item_text(env: SkillEnv, state: dict) -> str:
    """The current item and its options, as written (no LLM)."""
    items = state["instrument"]["items"]
    item = items[state["idx"]]
    head = (env.llm.phrase("questionnaire_item")
            .replace("{n}", str(state["idx"] + 1)).replace("{total}", str(len(items))))
    # The wording stays as written; only a final stop is added, so that the item and "The options
    # are" are not read as one sentence
    text = item["text"].rstrip()
    if not text.endswith((".", "?", "!", ":", "…")):
        text += "."
    return f"{head} {text} {_options_text(env, item)}"


@skill("questionnaire.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The instructions of the instrument (or a fixed introduction), then the first item."""
    instrument = state["instrument"]
    intro = instrument["instructions"] or env.llm.phrase("questionnaire_intro").replace(
        "{total}", str(len(instrument["items"])))
    return SkillResult(reply=f"{intro} {_item_text(env, state)}")


@skill("questionnaire.ask")
def ask(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The current item and its options again, or the next one after a recorded answer."""
    return SkillResult(reply=_item_text(env, state))


def _words(text) -> set[str]:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return set("".join(c if c.isalnum() else " " for c in text).split())


def _contradicted(chosen: int, labels: list[str], said: str) -> bool:
    """Whether the reply says another option in full, with a word the chosen option does not
    have: every word of that option is in the reply, and one of them is not in the chosen one.
    The LLM's choice then is not taken: a dropped modifier ("very satisfied" mapped to
    "Satisfied", "not sure" to "Yes", "no mucho" to "Un poco") picks a neighbour option, and the
    instrument must never choose for the person. A synonym or a paraphrase that says no other
    option is kept ("absolutely" for "Yes"), and so is an option said with the words of a
    shorter one inside it ("very satisfied" for "Very satisfied"). A one-word option ("Yes",
    "No", "Partly") counts only when the chosen option is not said in full: such words also
    appear as discourse words ("Partly comfortable, yes." is "Partly"), while "no mucho",
    which does not say "Un poco", still says "Mucho"."""
    words = [_words(label) for label in labels]
    line = _words(said)
    chosen_said = bool(words[chosen]) and words[chosen] <= line
    return any(j != chosen and other <= line and other - words[chosen]
               and (len(other) >= 2 or not chosen_said)
               for j, other in enumerate(words))


def _undecided(env: SkillEnv, chosen: int, labels: list[str], said: str) -> bool:
    """Whether the reply leaves the option the LLM chose undecided, so that the options are said
    again rather than one chosen for the person: the reply hedges (a hedge word of the language:
    "between dissatisfied and neither...", "satisfied or very satisfied"), or every word it shares
    with the chosen option, which it does not say in full, is in another option too ("true" for
    "Completely true" when "Mostly true" is an option; "to agree" for "Somewhat agree" next to
    "Completely agree"): a recognizer that drops the word that tells two options apart leaves the
    LLM to guess it. A reply with no word of the chosen option (a synonym: "absolutely" for "Yes")
    is the LLM's reading and is kept."""
    try:
        hedges = {normalize_text(w) for w in
                  load_language(getattr(env.llm, "language", "English")).hedge_words}
    except Exception:
        hedges = set()
    line = _words(said)
    text = f" {normalize_text(said)} "
    if any(h and f" {h} " in text for h in hedges):
        return True
    words = [_words(label) for label in labels]
    support = words[chosen] & line
    if not support or words[chosen] <= line:
        return False
    return any(j != chosen and support <= other for j, other in enumerate(words))


def _previous(state: dict) -> dict | None:
    """The item before the current one and the answer recorded for it (answered or declined),
    for a correction of it ("for the last one I meant quite"); None for the first item or one
    left without an answer."""
    idx = state["idx"]
    if idx == 0 or not state["answers"]:
        return None
    item = state["instrument"]["items"][idx - 1]
    last = state["answers"][-1]
    if last["item"] != item["text"] or last["status"] not in ("answered", "refused"):
        return None
    return {"item": item["text"], "options": [o["label"] for o in item["options"]],
            "answer": last["answer"] or ""}


def _refers_to_previous(env: SkillEnv, state: dict, said: str) -> bool:
    """Whether a reply refers to the previous item: a word of previous_item_words of the language
    file ("previous", "the one before", "anterior"), or a word of four letters or more of the
    previous item's text that the current item's text does not have. A change of the previous
    answer is taken only then: after the robot explained a word ("Oh, I see. Not at all.") or
    within a self-correction ("Sorry, I meant yes"), the person answers the current item, and
    with the same options in both items the LLM may take it for a correction (E1 validation)."""
    text = f" {normalize_text(said)} "
    try:
        phrases = load_language(getattr(env.llm, "language", "English")).previous_item_words
    except Exception:
        phrases = ()
    if any(p and f" {p} " in text for p in phrases):
        return True
    items = state["instrument"]["items"]
    before = _words(items[state["idx"] - 1]["text"]) - _words(items[state["idx"]]["text"])
    return bool({w for w in before if len(w) >= 4} & _words(said))


@skill("questionnaire.record")
def record(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Map the answer to an option of the current item (LLM) and record it.

    label: "next" or "last" (an option or a refusal recorded; the item is closed), "meaning"
    (asks what the item means), "ambiguous" (between options, or an option the reply also
    contradicts: the options again), "repeat" (asks to hear it again), "corrected" (the user
    changed the answer to the previous item: recorded there, and the current item is asked
    again) or "other" (also a reply that matches no option)."""
    item = state["instrument"]["items"][state["idx"]]
    labels = [o["label"] for o in item["options"]]
    previous = _previous(state)
    mapped = env.llm.map_scale_answer(inp.user_text, item["text"], labels, env.ctx,
                                      previous=previous)
    kind = mapped["kind"]
    if kind == "corrects_previous" and previous is not None \
            and not _refers_to_previous(env, state, inp.user_text):
        # Not about the previous item: the option, said with the previous item's labels, answers
        # this one when this item has it (the same scale); otherwise nothing is recorded
        said = normalize_text(previous["options"][mapped["option"]])
        same = [i for i, label in enumerate(labels) if normalize_text(label) == said]
        if not same:
            return SkillResult(label="other")
        kind, mapped = "option", {"kind": "option", "option": same[0]}
    if kind == "corrects_previous":
        if previous is None:
            return SkillResult(label="other")
        prev_item = state["instrument"]["items"][state["idx"] - 1]
        if _contradicted(mapped["option"], previous["options"], inp.user_text):
            return SkillResult(label="other")
        option = prev_item["options"][mapped["option"]]
        state["answers"][-1] = {"item": prev_item["text"], "status": "answered",
                                "answer": option["label"], "value": _value(prev_item, option),
                                "said": inp.user_text,
                                "corrected_from": state["answers"][-1]["answer"]}
        return SkillResult(label="corrected")
    if kind == "option" and (_contradicted(mapped["option"], labels, inp.user_text)
                             or _undecided(env, mapped["option"], labels, inp.user_text)):
        return SkillResult(label="ambiguous")
    if kind == "option":
        option = item["options"][mapped["option"]]
        state["answers"].append({"item": item["text"], "status": "answered",
                                 "answer": option["label"], "value": _value(item, option),
                                 "said": inp.user_text})
        return _advance(state)
    if kind == "refuses":
        state["answers"].append({"item": item["text"], "status": "refused", "answer": None,
                                 "value": None, "said": inp.user_text})
        return _advance(state)
    return SkillResult(label=kind)


def _advance(state: dict) -> SkillResult:
    state["idx"] += 1
    last = state["idx"] >= len(state["instrument"]["items"])
    return SkillResult(label="last" if last else "next")


@skill("questionnaire.clarify")
def clarify(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The clarification the instrument gives for the item, or the item again (no LLM)."""
    item = state["instrument"]["items"][state["idx"]]
    if item["clarification"]:
        return SkillResult(reply=f"{item['clarification']} {_options_text(env, item)}")
    return SkillResult(reply=_item_text(env, state))


@skill("questionnaire.corrected")
def corrected(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The answer to the previous item was changed: say so, then the current item again (no
    LLM)."""
    return SkillResult(reply=f"{env.llm.phrase('questionnaire_corrected')} "
                             f"{_item_text(env, state)}")


@skill("questionnaire.options")
def options(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """An answer between options: the options again, without suggesting any (no LLM)."""
    item = state["instrument"]["items"][state["idx"]]
    return SkillResult(reply=_options_text(env, item))


@skill("questionnaire.unanswered")
def unanswered(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Leave the current item without an answer after max_attempts unclear answers.
    label: "next" or "last"."""
    item = state["instrument"]["items"][state["idx"]]
    state["answers"].append({"item": item["text"], "status": "unanswered", "answer": None,
                             "value": None, "said": ""})
    return _advance(state)


@skill("questionnaire.finish")
def finish(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The closing (the score is in the data, never said)."""
    state["completed"] = True
    return SkillResult(reply=env.llm.phrase("questionnaire_end"))
