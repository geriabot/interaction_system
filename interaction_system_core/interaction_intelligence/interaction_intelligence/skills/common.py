# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Skills shared by every dialogue: start it from the request, the spoken cancellation, the
prompts during a silence, the closing lines of the endings decided by the trees, and the
outcome.
"""

import json
import logging

from . import SkillEnv, SkillInput, SkillResult, get_mode, skill
from ..utils import local_cancel_check, strip_leading

_log = logging.getLogger("InteractionIntelligence")


def parse_resources(resources_json: str) -> list[dict]:
    """The resources of the request: a JSON list of {name, description, content}."""
    if not resources_json:
        return []
    try:
        items = json.loads(resources_json)
    except Exception as exc:
        _log.warning(f"Could not parse resources_json: {exc}")
        return []
    if not isinstance(items, list):
        return []
    return [
        {"name": r["name"], "description": r.get("description", ""), "content": r["content"]}
        for r in items
        if isinstance(r, dict) and "name" in r and "content" in r
    ]


@skill("begin")
def begin(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Build the state of a new dialogue from the request (args: the DialogTask goal, with
    dialog_mode already resolved by the orchestrator). Strategy-specific inputs (the quiz bank,
    the steps, the scene) are parsed here, so a request that lacks them fails at once."""
    goal = inp.args
    mode = (goal.get("dialog_mode") or "").strip()
    dialogue_mode = get_mode(mode)
    state.clear()
    state.update(
        {
            "mode": mode,
            "task": goal.get("task_description", ""),
            "domain": goal.get("domain", ""),
            "resources": parse_resources(goal.get("resources_json", "")),
            "hidden_resources": [],
            "frame_schema_json": (goal.get("frame_schema_json") or "").strip(),
            "initial_frame_json": (goal.get("initial_frame_json") or "").strip(),
            "skip_intro": bool(goal.get("skip_intro", False)),
            "max_turns": int(goal.get("max_turns", 0) or 0),
            "wait_timeout": float(goal.get("wait_timeout", 0.0) or 0.0),
            # The request's acknowledgments: -1 the system's (ack_enabled), 0 none, 1 yes
            "acknowledgments": int(goal.get("acknowledgments", -1)),
            # The request's reasoning: -1 the system's (reasoning), 0 none, 1 on the critical
            # questions (the main behavior may switch it off, e.g. when turns are too slow)
            "reasoning": int(goal.get("reasoning", -1)),
            "phase": "",
            "awaiting_cancel": False,
            "user_cancelled": False,
            "completed": False,
        }
    )
    dialogue_mode.init(env, state)
    return SkillResult(label=mode)


def _frame_or_none(state: dict):
    if state.get("mode") == "slot_filling" and state.get("frame"):
        from ..dialog_frame import DialogFrame

        return DialogFrame.from_state(state["frame"])
    return None


@skill("cancel_check")
def cancel_check(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The spoken cancellation, checked on every user turn before the strategy (option: the
    robot's last utterance, to resume with it).

    label: "none" (no cancel request: the strategy handles the turn), "ask" (the user wants to
    stop: confirm it), "confirmed" (the dialogue ends, cancelled by the user), "denied" (the
    user does not want to stop after all: the robot resumes with what it had asked; the same
    words are not asked about again) or
    "finished" (asked whether they want to stop, the user says instead that they have finished,
    in a strategy whose end the user decides: its own closing, a normal end). Only a yes stops:
    any other answer to the confirmation ("no, I want to finish the boat", a value, "can you
    repeat?", a "finished" in a strategy whose end the user does not decide) is "denied" too,
    and the robot asks its question again; the answer never reaches the strategy as if it
    answered the strategy's own question (decided by the author, 2026-11-01: until then an
    answer other than yes or no went to the strategy, and "No, I want the plumber to come" was
    recorded as a value; a value said there is asked again, one turn more). The detector itself tells a cancel request from the user saying they have finished
    (the normal end of the task, not abandoning it): only a cancel request is asked about
    (decided by the author, 2026-10-31); and it first decides whether the words are about this
    conversation or about something of the task ("I've pressed the cancel button"): only the
    former can be a cancel request, so a false alarm goes to the strategy without a question
    (decided by the author, 2026-11-01)."""
    task, ctx = state["task"], env.ctx
    if state.get("awaiting_cancel"):
        state["awaiting_cancel"] = False
        answer = env.llm.classify_cancel_reply(inp.user_text, ctx)
        if answer == "yes":
            state["user_cancelled"] = True
            reply = (_strategy_closing(env, state)
                     or env.llm.cancel_confirmed_response(task, _frame_or_none(state), ctx))
            return SkillResult(reply=reply, label="confirmed")
        if answer == "finished":
            finished = user_finished(env, state)
            if finished:
                return finished
        # Not a yes: the robot resumes with what it had asked. The same words are not asked
        # about again (a safety net for a false alarm the detector did not catch: "Done, I've
        # pressed cancel" looped otherwise)
        state.setdefault("denied_cancels", []).append(state.pop("cancel_turn", ""))
        reply = f"{env.llm.phrase('cancel_no')} {state.get('resume_line', '')}".strip()
        return SkillResult(reply=reply, label="denied")

    # With the model known to be down (its last call failed: model_down, kept by the
    # intelligence), an exact stop command ends the conversation at once: the confirmation exists
    # to avoid stopping by mistake, an exact command is unambiguous, and the answer to the
    # question could not be read anyway (decided by the author, 2026-10-30)
    if state.get("model_down") and local_cancel_check(
            inp.user_text, getattr(env.llm, "language", "English")):
        state["user_cancelled"] = True
        state["cancelled_without_confirmation"] = True
        reply = _strategy_closing(env, state) or env.llm.phrase("cancel_ok")
        return SkillResult(reply=reply, label="confirmed")

    strict = get_mode(state["mode"]).strict_cancel(state)
    said = " ".join(inp.user_text.lower().split())
    if said and said in state.get("denied_cancels", []):
        return SkillResult(label="none")
    if env.llm.detect_cancel_intent(inp.user_text, ctx, strict=strict, task=task):
        state["awaiting_cancel"] = True
        state["cancel_turn"] = said
        state["resume_line"] = resume_line(env, inp.option)
        return SkillResult(reply=env.llm.ask_cancel_confirmation(task, ctx), label="ask")
    return SkillResult(label="none")


def resume_line(env: SkillEnv, last_utterance: str) -> str:
    """What the robot resumes with after a denied cancellation: its last utterance, without the
    "let's continue" of an earlier denial (otherwise each denial adds one more)."""
    return strip_leading(last_utterance, env.llm.phrase("cancel_no"))


def user_finished(env: SkillEnv, state: dict) -> SkillResult | None:
    """The strategy's normal end when the user says they have finished (label "finished"), or
    None if the strategy's end is not the user's to decide."""
    hook = get_mode(state["mode"]).on_finished
    if not hook:
        return None
    return SkillResult(reply=hook(env, state), label="finished")


def _strategy_closing(env: SkillEnv, state: dict) -> str:
    """The strategy's own closing for a confirmed cancellation ("": the generic one)."""
    hook = get_mode(state["mode"]).on_cancel
    return (hook(env, state) or "") if hook else ""


@skill("silence")
def silence(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The user said nothing for wait_timeout seconds (number: the silent waits so far in a
    row; option: the robot's last utterance). Silence is not abandonment: the robot says it is
    waiting, repeats what it had asked, and says nothing every other wait. The tree counts the
    waits and ends the dialogue at max_silence.

    label: "wait" (the current element takes time, e.g. a procedure step with a duration: say
    nothing, and the wait does not count), "quiet" (counts; say nothing) or "prompt" (counts;
    reply: what to say). Fixed phrases: no LLM."""
    wait = float(state.get("wait_timeout") or 0.0)
    waited = float(state.get("element_waited_s") or 0.0)
    grace = get_mode(state["mode"]).silence_grace(state)
    if wait > 0 and waited + wait <= grace:
        state["element_waited_s"] = waited + wait
        return SkillResult(label="wait")
    if inp.number == 0:
        state["pending_line"] = inp.option
        return SkillResult(reply=env.timeout_prompt, label="prompt")
    if inp.number == 1 and state.get("pending_line"):
        return SkillResult(reply=state["pending_line"], label="prompt")
    if inp.number % 2 == 0:
        return SkillResult(label="quiet")
    return SkillResult(reply=env.llm.phrase("silence_ready"), label="prompt")


@skill("fillers")
def fillers(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The conversational fillers of the dialogue ("Hmm…", "Just a moment…"), one per line in
    reply: fetched when the dialogue begins, so that the tree can say one while the reply is
    being prepared (the intelligence is busy then). Fixed phrases in the language of the
    dialogue: no LLM."""
    phrases = [p.strip() for p in env.filler_phrases if p and p.strip() and "\n" not in p]
    return SkillResult(reply="\n".join(phrases), label="fillers")


# The endings decided by the trees (common.xml, EndDialogue), with the reason of the result
ENDINGS = {
    "user_silent": "The user stayed silent for max_silence seconds",
    "breakdown": "Communication breakdown: max_stalled turns in a row without progress",
    "turn_limit": "Max turns reached",
    "off_task": "Off-task: max_off_task requests outside the task in a row",
    "error": "An error of the system",
}
# The outcome of an ending that is not an outcome of its own (its reason tells it apart)
OUTCOME_OF = {"off_task": "breakdown"}


@skill("closing")
def closing(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The fixed closing line of an ending decided by the tree (option: user_silent,
    breakdown, turn_limit, off_task or error). No LLM, so that it works when the LLM is what
    failed."""
    if inp.option not in ENDINGS:
        raise ValueError(f"Unknown ending '{inp.option}'. Available: {sorted(ENDINGS)}")
    return SkillResult(reply=env.llm.phrase(f"close_{inp.option}"), label=inp.option)


@skill("result")
def result(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The outcome of the dialogue (option: the ending decided by the tree, if any).

    label: the outcome ("completed", "partial", "failed", "cancelled", "user_silent",
    "breakdown", "turn_limit" or "error"); reply: why it is not "completed"."""
    if inp.option:
        return SkillResult(label=OUTCOME_OF.get(inp.option, inp.option),
                           reply=ENDINGS.get(inp.option, inp.option))
    if state.get("user_cancelled"):
        return SkillResult(label="cancelled", reply="Cancelled by the user")
    mode = get_mode(state["mode"])
    if mode.outcome:
        outcome, reason = mode.outcome(state)
    elif mode.succeeded(state):
        outcome, reason = "completed", ""
    else:
        outcome, reason = "failed", "The dialogue ended without success"
    return SkillResult(label=outcome, reply="" if outcome == "completed" else reason)
