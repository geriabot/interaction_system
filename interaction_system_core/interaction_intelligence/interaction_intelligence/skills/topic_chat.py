# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Topic-bounded social conversation: the robot talks with the user about a topic set by the
requester, with nothing to collect (companionship, reminiscence: "your village in the sixties",
"the photograph of your wedding").

The topic comes in the resources of the request: the content of the first resource that parses
as a JSON object with "topic":
    {"topic": "...",
     "material": "..." or ["...", ...] (optional: the description of a photograph, facts given
                 by the family or the staff),
     "questions": ["...", ...] (optional: supporting questions the robot may bring in),
     "avoid": ["...", ...] (optional: topics the robot does not go into),
     "max_exchanges": 12 (optional), "max_minutes": 10 (optional)}
The resource is hidden from the context of the prompts (hidden_resources): the prompts get the
topic explicitly, each with the part it needs.

Each user turn is read by one question (classify_topic_turn): on the topic, drifting away from
it (the robot bridges back gently, not with an abrupt change), a topic to avoid (the robot
changes the subject without going into it), a request to end the conversation, a request to
hear the robot's last line again, or a turn the model could not read ("unclear": the robot asks
to repeat it with a fixed phrase, a turn without progress, so that a sustained failure of the
model ends as a breakdown). The robot's line comments on what the user said and ends with one
open question on the topic: the robot keeps the initiative. It never states facts about the
person that are not in the material. The conversation ends when the user wants to (the closing:
a normal end, also when the user asks to stop, without a confirmation, since there is nothing to
collect: DialogueMode.on_finished), or at the limit (max_exchanges user turns on the topic, or
max_minutes since the opening): a short summary of what the user shared and a goodbye, never an
abrupt end. After the closing, one question (review_topic_chat) reads the conversation for the
result: the supporting questions covered and the topics that came up.

State keys: "chat", "max_exchanges", "max_s", "started_at", "exchanges", "history", "turns"
([{kind}] per user turn read), "ended_by" (user | limit | time), "covered", "topics".
"""

import time

from ..utils import parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill

DEFAULT_MAX_EXCHANGES = 12
# Fewer leaves no room for a conversation: a greeting, one memory and the goodbye
MIN_EXCHANGES = 3
# What the robot answers each kind of turn with (the kinds of classify_topic_turn that go on)
CONTINUE = ("on_topic", "drifting", "avoid")

# The clock of the time limit (wall time: the state travels between the requests of a dialogue)
_now = time.time


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value
                if isinstance(v, (str, int, float)) and str(v).strip()]
    return []


def parse_topic(resources: list[dict]) -> tuple[dict | None, int | None]:
    """The topic of the conversation and the index of the resource it came from, or
    (None, None)."""
    for index, resource in enumerate(resources):
        content = (resource.get("content", "") or "").strip()
        if not content.startswith("{"):
            continue
        try:
            data = parse_loose_json(content)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        topic = str(data.get("topic") or "").strip()
        if not topic:
            continue
        try:
            max_exchanges = int(data.get("max_exchanges") or 0)
        except (TypeError, ValueError):
            max_exchanges = 0
        try:
            max_minutes = float(data.get("max_minutes") or 0.0)
        except (TypeError, ValueError):
            max_minutes = 0.0
        chat = {
            "topic": topic,
            "material": _strings(data.get("material")),
            "questions": _strings(data.get("questions")),
            "avoid": _strings(data.get("avoid")),
            "max_exchanges": max(0, max_exchanges),
            "max_minutes": max(0.0, max_minutes),
        }
        return chat, index
    return None, None


def exchange_limit(chat: dict, max_turns: int) -> int:
    """The conversation's limit of user turns (at least MIN_EXCHANGES). The dialogue fails once
    max_turns is reached: the robot closes on the last allowed turn, so that the user still gets
    the summary and the goodbye."""
    limit = max(chat.get("max_exchanges") or DEFAULT_MAX_EXCHANGES, MIN_EXCHANGES)
    if max_turns > 0:
        limit = min(limit, max_turns)
    return limit


def _init(env: SkillEnv, state: dict) -> None:
    chat, index = parse_topic(state["resources"])
    if not chat:
        raise ValueError(
            "The topic-bounded conversation requires a resource whose content is a JSON object "
            "with at least 'topic'."
        )
    state.update({
        "chat": chat,
        "hidden_resources": [index],
        "max_exchanges": exchange_limit(chat, state["max_turns"]),
        "max_s": chat["max_minutes"] * 60.0,
        "started_at": _now(),
        "exchanges": 0,
        "history": [],
        "turns": [],
        "ended_by": None,
        "covered": [],
        "topics": [],
    })


def _phase(state: dict) -> str:
    if state.get("completed"):
        return "done"
    return f"chat_{state['exchanges']}_of_{state['max_exchanges']}"


def _count(state: dict, kind: str) -> int:
    return sum(1 for t in state.get("turns", []) if t == kind)


def _data(state: dict) -> dict:
    chat = state["chat"]
    covered = set(state.get("covered", []))
    return {
        "topic": chat["topic"],
        "exchanges": state["exchanges"],
        "max_exchanges": state["max_exchanges"],
        "duration_s": round(state.get("duration_s", _now() - state["started_at"]), 1),
        "ended_by": state["ended_by"],
        "covered": sorted(covered),
        "questions_covered": [q for i, q in enumerate(chat["questions"], 1) if i in covered],
        "questions_not_covered": [q for i, q in enumerate(chat["questions"], 1)
                                  if i not in covered],
        "topics": list(state.get("topics", [])),
        "drifts": _count(state, "drifting"),
        "avoided": _count(state, "avoid"),
    }


register_mode(
    "topic_chat",
    DialogueMode(
        init=_init,
        phase=_phase,
        data=_data,
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        on_finished=lambda env, state: finish(env, state, SkillInput(option="user")).reply,
    ),
)


def _say(state: dict, reply: str, label: str = "") -> SkillResult:
    state["history"].append({"role": "assistant", "content": reply})
    return SkillResult(reply=reply, label=label)


def _limit_reached(state: dict) -> str:
    """"time" or "limit" when the conversation has reached a limit, else ""."""
    if state.get("max_s", 0) > 0 and _now() - state["started_at"] >= state["max_s"]:
        return "time"
    if state["exchanges"] >= state["max_exchanges"]:
        return "limit"
    return ""


@skill("topic_chat.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The greeting, the topic and the first open question."""
    return _say(state, env.llm.topic_chat_opening(state["task"], state["chat"], True, env.ctx))


@skill("topic_chat.open_direct")
def open_direct(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The topic and the first open question, without a greeting (skip_intro)."""
    return _say(state, env.llm.topic_chat_opening(state["task"], state["chat"], False, env.ctx))


@skill("topic_chat.classify")
def classify(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Read the user's turn (classify_topic_turn).

    label: "end" (the user wants to end the conversation), "limit" or "time" (the turn reached
    the limit of exchanges or of time: the closing follows), "repeat" (reply: the robot's last
    line again, without the LLM; a turn without progress), "unclear" (reply: a request to repeat
    with a fixed phrase; the line is kept out of the conversation, a turn without progress), or
    the kind of a turn that goes on: "on_topic", "drifting" or "avoid" (the robot's line
    follows, topic_chat.reply)."""
    kind = env.llm.classify_topic_turn(inp.user_text, state["chat"], state["history"], env.ctx)
    if kind == "unclear":
        return SkillResult(reply=env.llm.phrase("unclear"), label="unclear")
    if kind == "repeat":
        last = next((t["content"] for t in reversed(state["history"])
                     if t["role"] == "assistant"), "")
        return SkillResult(reply=last or env.llm.phrase("unclear"), label="repeat")
    state["history"].append({"role": "user", "content": inp.user_text})
    state["turns"].append(kind)
    if kind == "end":
        return SkillResult(label="end")
    state["exchanges"] += 1
    return SkillResult(label=_limit_reached(state) or kind)


@skill("topic_chat.reply")
def reply(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The robot's line: a comment on what the user said and one open question on the topic
    (option: the kind of the turn: on_topic, drifting, avoid)."""
    kind = inp.option if inp.option in CONTINUE else "on_topic"
    line = env.llm.topic_chat_reply(state["chat"], state["history"], kind, env.ctx)
    return _say(state, line)


@skill("topic_chat.finish")
def finish(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The closing: a short summary of what the user shared and a goodbye (option: who or what
    ended the conversation, user | limit | time); then the reading of the conversation for the
    result (the supporting questions covered, the topics that came up)."""
    state["ended_by"] = inp.option or "user"
    state["duration_s"] = round(_now() - state["started_at"], 1)
    if state.get("exchanges", 0) < 1:
        # The user ended it before saying anything on the topic: nothing to sum up or read, a
        # fixed goodbye (a generated summary of nothing could only be invented)
        state["covered"], state["topics"], state["completed"] = [], [], True
        return _say(state, env.llm.phrase("topic_chat_bye_early"))
    line = env.llm.topic_chat_closing(state["chat"], state["history"], env.ctx)
    review = env.llm.review_topic_chat(state["chat"], state["history"], env.ctx)
    n = len(state["chat"]["questions"])
    state["covered"] = sorted({i for i in review.get("covered", []) if 1 <= i <= n})
    state["topics"] = list(review.get("topics", []))
    state["completed"] = True
    return _say(state, line)
