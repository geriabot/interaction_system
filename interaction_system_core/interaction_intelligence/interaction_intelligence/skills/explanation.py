# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Explanation: explain a topic and verify that the user understood it with teach-back.

The robot explains, then asks the user to say it back in their own words (teach-back: a
"yes, I understood" is not taken as evidence). The reply is judged against the KEY POINTS of
the explanation: each one correct, incomplete (part of it said right, nothing false: handled
as missing, never as wrong; decided by the author, 2026-10-31), wrong or missing. Understanding
is verified when every
critical key point has been said back correctly (paraphrase is fine). A reply that says some
points right and none wrong makes progress: the robot confirms them and asks only for the
critical points still missing. Otherwise the robot explains again only the points missing or
wrong, in other words, and asks again. If the user
does not want to say it back, the fallback is a direct question about the most critical point
not yet verified. The tree (explanation.xml) decides what follows each reply and counts the
repairs (max_attempts checks in all); after the last one the robot closes reminding the
critical points (outcome failed).

The key points come in the resources of the request (a resource whose content is a JSON object
with "key_points": strings, critical, or {"point", "critical"}); without them the LLM lists them
from the task and the reference text (the plain-text resources) when the dialogue begins. A
request with neither is refused before the dialogue (selection.py: the need "reference"). State keys: "history", "attempts",
"key_points" ([{point, critical, status: pending | correct | incomplete | wrong, said}]),
"check_mode"
(teach_back or targeted), "last_check" (the last check asked), "self_reported" (the user said they
understood without saying it back), "checks".
"""

from ..utils import parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill


def parse_key_points(resources: list[dict]) -> list[dict]:
    """The key points given in the resources: the first resource whose content is a JSON
    object with a list "key_points" (strings, critical, or {"point", "critical"})."""
    for resource in resources:
        content = (resource.get("content", "") or "").strip()
        if not content.startswith("{"):
            continue
        try:
            data = parse_loose_json(content)
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("key_points"), list):
            continue
        points = []
        for entry in data["key_points"]:
            if isinstance(entry, str) and entry.strip():
                points.append({"point": entry.strip(), "critical": True})
            elif isinstance(entry, dict) and str(entry.get("point", "")).strip():
                points.append({"point": str(entry["point"]).strip(),
                               "critical": entry.get("critical", True) is not False})
        if points:
            return points
    return []


def _init(env: SkillEnv, state: dict) -> None:
    points = parse_key_points(state["resources"]) or env.llm.extract_key_points(
        state["task"], [r for r in state["resources"] if not parse_key_points([r])]
    )
    if not any(p["critical"] for p in points):
        for p in points:
            p["critical"] = True
    state.update({
        "history": [], "attempts": 0, "phase": "explaining", "check_mode": "teach_back",
        "last_check": "", "self_reported": False, "checks": 0,
        "key_points": [dict(p, status="pending", said="") for p in points],
    })


def _pending(state: dict, critical_only: bool = True) -> list[dict]:
    return [p for p in state["key_points"]
            if p["status"] != "correct" and (p["critical"] or not critical_only)]


def _verified(state: dict) -> bool:
    return not _pending(state)


def _outcome(state: dict) -> tuple[str, str]:
    if state.get("completed"):
        return "completed", ""
    pending = "; ".join(p["point"] for p in _pending(state))
    checks = state.get("attempts", 0)
    return "failed", f"Critical points not verified after {checks} checks: {pending}"


register_mode(
    "explanation",
    DialogueMode(
        init=_init,
        phase=lambda state: (
            "done" if state.get("completed") else state.get("check_mode", "explaining")
        ),
        data=lambda state: {
            "attempts": state["attempts"],
            "completed": state["completed"],
            "checks": state.get("checks", 0),
            "self_reported": state.get("self_reported", False),
            "key_points": [{k: p[k] for k in ("point", "critical", "status", "said")}
                           for p in state.get("key_points", [])],
        },
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        outcome=_outcome,
    ),
)


def _say(state: dict, reply: str) -> SkillResult:
    state["history"].append({"role": "assistant", "content": reply})
    return SkillResult(reply=reply)


def _check(env: SkillEnv, state: dict, text: str, check: str) -> SkillResult:
    """Say the text followed by a check, and remember the check (to ask it again)."""
    state["last_check"] = check
    state["checks"] = state.get("checks", 0) + 1
    return _say(state, f"{text} {check}".strip())


@skill("explanation.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    explanation = env.llm.generate_explanation(state["task"], env.ctx)
    return _check(env, state, explanation, env.llm.phrase("teach_back"))


@skill("explanation.open_direct")
def open_direct(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The explanation without a greeting (skip_intro)."""
    explanation = env.llm.generate_explanation_direct(state["task"], env.ctx)
    return _check(env, state, explanation, env.llm.phrase("teach_back"))


@skill("explanation.check")
def check(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Judge the reply to a check against the key points (a point once said back correctly
    stays verified; a point said wrongly needs repair).

    label: "verified" (every critical point said back correctly), "progress" (a reply that
    said a new point correctly and none wrong, with critical points still pending: the robot
    asks only for those, and it is not a failed check), "gaps" (a reply with content, but a
    critical point missing or wrong and no progress; also declining the direct question of the
    fallback), "question", "declines" (only "I understood" or a refusal, without content: the
    fallback), "not_understood" (the user did not understand, or asks to repeat) or "other"."""
    state["history"].append({"role": "user", "content": inp.user_text})
    # Only the points not yet verified are judged: a verified point stays verified, and with
    # it in the list the model could put another point's content in its entry (round 6, Qwen:
    # "the red flag..." judged as the green one, already verified, and lost)
    open_ = [i for i, p in enumerate(state["key_points"]) if p["status"] != "correct"]
    open_ = open_ or list(range(len(state["key_points"])))
    judged = env.llm.judge_teach_back(
        inp.user_text, [state["key_points"][i] for i in open_], state.get("last_check", ""),
        env.ctx
    )
    judged["points"] = [(open_[j], status, said) for j, status, said in judged["points"]]
    kind = judged["kind"]
    if (kind == "restates" and state.get("check_mode") != "targeted"
            and not any(s != "missing" for _, s, _ in judged["points"])):
        # Nothing of the explanation was said (e.g. unintelligible): no progress, not a check
        # ("I don't know" to the direct question of the fallback stays a failed check)
        return SkillResult(label="other")
    if kind == "restates":
        progress = False
        for i, status, said in judged["points"]:
            point = state["key_points"][i]
            if status == "correct":
                progress = progress or point["status"] != "correct"
                point["status"], point["said"] = "correct", ""
            elif status == "wrong":
                point["status"], point["said"] = "wrong", said
            elif status == "incomplete" and point["status"] != "correct":
                # Part of the point, nothing false: still to verify (as missing), never a
                # misconception to correct; the part said is kept to confirm it
                point["status"], point["said"] = "incomplete", said
        if _verified(state):
            return SkillResult(label="verified")
        wrong = any(p["status"] == "wrong" for p in state["key_points"])
        return SkillResult(label="progress" if progress and not wrong else "gaps")
    if kind == "declines":
        state["self_reported"] = True
        return SkillResult(label="gaps" if state.get("check_mode") == "targeted" else "declines")
    return SkillResult(label=kind)


@skill("explanation.confirmed")
def confirmed(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    state["completed"] = True
    return _say(state, env.llm.understanding_confirmed_response(state["task"], env.ctx))


@skill("explanation.answer")
def answer(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Answer a question, then ask the last check again."""
    reply = env.llm.answer_explanation_question(
        inp.user_text, state["task"], state["history"], env.ctx
    )
    return _check(env, state, reply, state.get("last_check") or env.llm.phrase("teach_back"))


@skill("explanation.ask_again")
def ask_again(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """A reply not understood: the last check again (no LLM)."""
    return _check(env, state, "", state.get("last_check") or env.llm.phrase("teach_back"))


@skill("explanation.targeted")
def targeted(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The fallback when the user does not say it back: a direct question about the most
    critical point not yet verified."""
    state["check_mode"] = "targeted"
    point = (_pending(state) or _pending(state, critical_only=False) or state["key_points"])[0]
    return _check(env, state, "", env.llm.targeted_question(point["point"], env.ctx))


@skill("explanation.ask_missing")
def ask_missing(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """A partial teach-back that made progress: confirm what was said right and ask only for
    the critical points still pending, without explaining them again (a check that is not
    counted as failed)."""
    state["check_mode"] = "teach_back"
    correct = [p["point"] for p in state["key_points"] if p["status"] == "correct"]
    # A point said only in part: its part is confirmed and only the rest is asked for
    correct += [f"part of \"{p['point']}\": {p['said']}" for p in _pending(state)
                if p["status"] == "incomplete" and p["said"]]
    missing = [p["point"] for p in _pending(state)]
    return _check(env, state, "", env.llm.ask_missing_points(state["task"], correct, missing,
                                                              env.ctx))


@skill("explanation.repair")
def repair(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Explain again (number: the checks failed so far; option: "points" for only the critical
    points missing or wrong and the wrong non-critical ones, "all" for the whole topic in
    another way), then ask the user to say it back again."""
    state["attempts"] = inp.number
    state["check_mode"] = "teach_back"
    if inp.option == "points":
        to_repair = [p for p in state["key_points"]
                     if p["status"] == "wrong" or (p["critical"] and p["status"] != "correct")]
        correct = [p["point"] for p in state["key_points"] if p["status"] == "correct"]
        text = env.llm.repair_explanation(state["task"], to_repair, correct, env.ctx)
    else:
        text = env.llm.rephrase_explanation(
            state["task"], state["history"], attempt_number=inp.number + 1, ctx=env.ctx
        )
    return _check(env, state, text, env.llm.phrase("teach_back_again"))


@skill("explanation.give_up")
def give_up(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The closing when the critical points were not verified after max_attempts checks: the
    robot reminds them (fixed phrases and the points themselves; the dialogue fails)."""
    state["attempts"] = inp.number
    points = "; ".join(p["point"] for p in state["key_points"] if p["critical"])
    reply = f"{env.llm.phrase('understand_fail')} {env.llm.phrase('key_points_summary')}"
    return _say(state, reply.replace("{points}", points))
