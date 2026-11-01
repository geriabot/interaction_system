# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Roleplay: the robot plays a character so the user can practise a situation, then steps out
of the role and scores the user against a rubric.

The scene comes in the resources of the request: the content of the first resource that
parses as a JSON object with "scenario" and "role":
    {"scenario": "...", "role": "...", "user_goal": "..." (optional),
     "criteria": ["...", ...], "max_exchanges": 8 (optional; at least MIN_EXCHANGES)}
The scene resource is hidden from the context of the prompts (hidden_resources): every prompt
injects the context, and the character must not see the rubric. The prompts get the scene
explicitly, and only the evaluation sees the criteria.

The tree (roleplay.xml) decides what each kind of turn does: continue (the character
replies), repeat (the character's last line again: literally first, then in other words),
coach (a hint out of character), end / scene_end / limit (close and score), unclear (the model
could not read the turn: the robot asks to repeat it; a turn without progress, so that a
sustained failure of the model ends as a breakdown). Only continue turns count as exchanges:
asking to repeat or for a hint does not use up the scene. A scene whose evaluation could not
score every criterion ends as partial, with the criteria not scored as the reason. A spoken
cancellation, once confirmed, also closes with the evaluation when the user said at least one
line of the scene (_on_cancel; outcome cancelled, the scores in the data). State
keys: "scene", "max_exchanges", "exchanges", "history", "repeats" (repeat requests in a row),
"ended_by", "evaluation".
"""

from ..utils import parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill

DEFAULT_MAX_EXCHANGES = 10
# A shorter scene leaves the user no room to show every criterion (the character's own questions
# fill it: found in the debugging with real models, 2026-10-31)
MIN_EXCHANGES = 6
# A confirmed cancellation closes the practice with its evaluation when the user said at least
# this many lines of the scene (exchanges); with fewer there is nothing to score, and the robot
# says the cancel closing. One: the evaluation scores the user's lines, and one line is already
# evidence (the strategy had no minimum before; chosen 2026-10-30)
MIN_EXCHANGES_TO_EVALUATE = 1


def parse_scene(resources: list[dict]) -> tuple[dict | None, int | None]:
    """The scene and the index of the resource it came from, or (None, None)."""
    for index, resource in enumerate(resources):
        content = (resource.get("content", "") or "").strip()
        if not content:
            continue
        try:
            data = parse_loose_json(content)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        scenario = str(data.get("scenario") or "").strip()
        role = str(data.get("role") or "").strip()
        if not scenario or not role:
            continue
        criteria = []
        for c in data.get("criteria") or []:
            text = c.get("name") if isinstance(c, dict) else c
            if isinstance(text, str) and text.strip():
                criteria.append(text.strip())
        try:
            max_exchanges = int(data.get("max_exchanges") or 0)
        except (TypeError, ValueError):
            max_exchanges = 0
        scene = {
            "scenario": scenario,
            "role": role,
            "user_goal": str(data.get("user_goal") or "").strip(),
            "criteria": criteria,
            "max_exchanges": max(0, max_exchanges),
        }
        return scene, index
    return None, None


def exchange_limit(scene: dict, max_turns: int) -> int:
    """The scene's limit (at least MIN_EXCHANGES). The dialogue fails once max_turns is
    reached: close the scene on the last allowed turn so that the user still gets the
    evaluation."""
    limit = max(scene.get("max_exchanges") or DEFAULT_MAX_EXCHANGES, MIN_EXCHANGES)
    if max_turns > 0:
        limit = min(limit, max_turns)
    return limit


def _init(env: SkillEnv, state: dict) -> None:
    scene, index = parse_scene(state["resources"])
    if not scene:
        raise ValueError(
            "The roleplay requires a resource whose content is a JSON object "
            "with at least 'scenario' and 'role'."
        )
    if not scene["criteria"]:
        raise ValueError("The roleplay requires at least one evaluation criterion.")
    state.update(
        {
            "scene": scene,
            "hidden_resources": [index],
            "max_exchanges": exchange_limit(scene, state["max_turns"]),
            "exchanges": 0,
            "history": [],
            "repeats": 0,
            "ended_by": None,
            "evaluation": {},
        }
    )


def _average(state: dict) -> float | None:
    scores = [s["score"] for s in state["evaluation"].get("scores", [])]
    return round(sum(scores) / len(scores), 2) if scores else None


def _phase(state: dict) -> str:
    if state.get("completed"):
        return "done"
    return f"scene_{state['exchanges']}_of_{state['max_exchanges']}"


def _data(state: dict) -> dict:
    scene, evaluation = state["scene"], state["evaluation"]
    return {
        "scenario": scene.get("scenario", ""),
        "role": scene.get("role", ""),
        "exchanges": state["exchanges"],
        "max_exchanges": state["max_exchanges"],
        "ended_by": state["ended_by"],
        "scores": list(evaluation.get("scores", [])),
        "average": _average(state),
        "highlight": evaluation.get("highlight", ""),
        "improve": evaluation.get("improve", ""),
    }


def _outcome(state: dict) -> tuple[str, str]:
    """completed once every criterion was scored; partial when the evaluation could not score
    some (the model failed: as the other strategies with an element left unfinished)."""
    if not state.get("completed"):
        return "failed", "The scene ended without its evaluation"
    scored = {s["criterion"] for s in state["evaluation"].get("scores", [])}
    missing = [c for c in state["scene"]["criteria"] if c not in scored]
    if missing:
        return "partial", "Criteria not scored: " + "; ".join(missing)
    return "completed", ""


def _on_cancel(env: SkillEnv, state: dict) -> str:
    """Stopping and finishing lead to the same place (decided by the author, 2026-10-30): a
    confirmed cancellation (or an exact stop command while the model is down) closes the
    practice with the evaluation of what was done, as when the user ends it (ended_by "user"),
    when enough was done (MIN_EXCHANGES_TO_EVALUATE); "" (the cancel closing) otherwise, or when
    no criterion could be scored. The outcome stays "cancelled", with the scores in the data."""
    if state.get("exchanges", 0) < MIN_EXCHANGES_TO_EVALUATE:
        return ""
    reply = finish(env, state, SkillInput(option="user")).reply
    return reply if state["evaluation"].get("scores") else ""


register_mode(
    "roleplay",
    DialogueMode(
        init=_init,
        phase=_phase,
        data=_data,
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        outcome=_outcome,
        on_cancel=_on_cancel,
    ),
)


def _character_line(env: SkillEnv, state: dict) -> str:
    line = env.llm.roleplay_reply(state["scene"], state["history"], env.ctx)
    state["history"].append({"role": "assistant", "content": line})
    return line


@skill("roleplay.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    first_line = _character_line(env, state)
    intro = env.llm.roleplay_intro(state["task"], state["scene"], env.ctx)
    return SkillResult(reply=f"{intro} {first_line}")


@skill("roleplay.open_direct")
def open_direct(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The first line of the character without an introduction (skip_intro)."""
    return SkillResult(reply=_character_line(env, state))


@skill("roleplay.classify")
def classify(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """label: "end" (the user wants to stop), "scene_end" (the scene reached its natural end),
    "limit" (the last exchange allowed), "repeat" (the user asks to hear the last line again),
    "coach" (the user asks for help), "continue", or "unclear" (the model could not read the
    turn: reply, a request to repeat it with a fixed phrase; the line is kept out of the scene's
    transcript, and it is not an exchange)."""
    state["history"].append({"role": "user", "content": inp.user_text})
    intent = env.llm.classify_roleplay_turn(
        inp.user_text, state["scene"], state["history"], env.ctx
    )
    if intent == "unclear":
        state["history"].pop()
        return SkillResult(reply=env.llm.phrase("unclear"), label="unclear")
    if intent in ("end", "scene_end"):
        return SkillResult(label=intent)
    if intent in ("repeat", "coach"):
        return SkillResult(label=intent)
    state["repeats"] = 0
    state["exchanges"] += 1
    if state["exchanges"] >= state["max_exchanges"]:
        return SkillResult(label="limit")
    return SkillResult(label=intent)


@skill("roleplay.reply")
def reply(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    return SkillResult(reply=_character_line(env, state))


@skill("roleplay.repeat")
def repeat(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The character's last line again: the first time literally (no LLM, and the request is
    kept out of the scene transcript), then the character says it in other words."""
    state["repeats"] = state.get("repeats", 0) + 1
    last = next((t["content"] for t in reversed(state["history"]) if t["role"] == "assistant"), "")
    if state["repeats"] <= 1 and last:
        state["history"].pop()
        return SkillResult(reply=last)
    return SkillResult(reply=_character_line(env, state))


@skill("roleplay.coach")
def coach(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """A hint out of character. Hints are kept out of the scene transcript."""
    hint = env.llm.roleplay_coach(inp.user_text, state["scene"], state["history"], env.ctx)
    state["history"].pop()
    return SkillResult(reply=hint)


@skill("roleplay.finish")
def finish(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Step out of the role, score the user on each criterion and give the feedback (option:
    who ended the scene, user | scene | limit)."""
    state["ended_by"] = inp.option
    evaluation = env.llm.evaluate_roleplay(state["scene"], state["history"], env.ctx)
    feedback = env.llm.roleplay_feedback(state["scene"], evaluation, inp.option, env.ctx)
    evaluation["highlight"] = feedback["highlight"]
    evaluation["improve"] = feedback["improve"]
    state["evaluation"] = evaluation
    state["completed"] = True
    return SkillResult(reply=feedback["text"])
