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
"answers" ([{item, status: answered | refused | unanswered, answer, value, said}]).
"""

from ..utils import parse_loose_json
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


@skill("questionnaire.record")
def record(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Map the answer to an option of the current item (LLM) and record it.

    label: "next" or "last" (an option or a refusal recorded; the item is closed), "meaning"
    (asks what the item means), "ambiguous" (between options, or not one of them), "repeat"
    (asks to hear it again) or "other"."""
    item = state["instrument"]["items"][state["idx"]]
    mapped = env.llm.map_scale_answer(inp.user_text, item["text"],
                                      [o["label"] for o in item["options"]], env.ctx)
    kind = mapped["kind"]
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
