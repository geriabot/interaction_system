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
from ..utils import (local_cancel_check, local_starts_with_yes, local_step_done, local_yes_no,
                     normalize_text, strip_leading)

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
            # The waits in a row that end the dialogue on silence (0: never, or not given)
            "max_silent_waits": int(goal.get("max_silent_waits", 0) or 0),
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
    "finished" (in a strategy whose end the user decides, a request to stop or, asked whether
    they want to stop, a user who says instead that they have finished: its own closing, a
    normal end, without a confirmation). Only a yes stops:
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
    (decided by the author, 2026-11-01). Dropping what the conversation is preparing ("cancel
    the booking, I've changed my mind") is about the conversation: the detector is given the
    strategy's stage (DialogueMode.cancel_stage: nothing has been booked yet) and the robot's
    last line (decided by the author, 2026-11-13: read as about the task, a "yes" to the
    read-back after it would have confirmed the booking)."""
    return run_cancel_check(env, state, inp)


def run_cancel_check(env: SkillEnv, state: dict, inp: SkillInput, detect=None,
                     confirm: bool = True) -> SkillResult:
    """The flow of cancel_check, shared with the ablations of the evaluation, which change only
    the detector of the request or leave out the question. `detect(env, state, inp, local)`:
    the reading of the user's words, "cancel", "finished" or "none" (None: the method's,
    detect_cancel_intent, with the local check of the exact commands when `local`).
    `confirm`=False: a detected request ends the dialogue at once, without the question (with
    the strategy's own closing, as after a confirmed one)."""
    task, ctx = state["task"], env.ctx
    if state.get("awaiting_cancel") and not _asked_to_stop(state, inp.option):
        # The question is no longer the robot's last line: the request that answered it failed
        # (the intelligence did not answer in time), the strategy took that turn, and the flag
        # was left over. A later "yes" to another question must not cancel
        _drop_pending_cancel(state)
    if state.get("awaiting_cancel"):
        _drop_pending_cancel(state, keep_turn=True)
        try:
            answer = env.llm.classify_cancel_reply(inp.user_text, ctx, task=task,
                                                   request=state.pop("cancel_request", ""))
        except Exception as exc:
            # Read as when the model gives no usable answer: only an exact yes stops
            _log.warning(f"classify_cancel_reply failed: {exc}")
            answer = local_yes_no(inp.user_text, getattr(env.llm, "language", "English")) \
                or "other"
        if answer == "finished" and not get_mode(state["mode"]).on_finished:
            # Where the end is not the user's to decide, "Yes, I'm done for today" agrees to stop
            # (it resumed the task against the user's will)
            answer = "yes"
        if answer == "yes":
            finished = _finishing(env, state)
            if finished:
                return finished
            state["user_cancelled"] = True
            reply = (_strategy_closing(env, state)
                     or env.llm.cancel_confirmed_response(task, _frame_or_none(state), ctx))
            return SkillResult(reply=reply, label="confirmed")
        if answer == "finished":
            finished = user_finished(env, state)
            if finished:
                return finished
        # Not a yes: the robot resumes with what it had asked. The same words (normalised) are
        # not asked about again while the user is on the same element (a safety net for a false
        # alarm the detector did not catch: "Done, I've pressed cancel" looped otherwise). An
        # exact stop command is never set aside: it is unambiguous, and the confirmation already
        # protects it; a later sincere "Stop." must still be heard
        turn = state.pop("cancel_turn", "")
        if turn and not state.pop("cancel_exact", False):
            state.setdefault("denied_cancels", []).append([_element(state), turn])
        reply = f"{env.llm.phrase('cancel_no')} {state.get('resume_line', '')}".strip()
        return SkillResult(reply=reply, label="denied")

    # With the model known to be down (its last call failed: model_down, kept by the
    # intelligence), an exact stop command ends the conversation at once: the confirmation exists
    # to avoid stopping by mistake, an exact command is unambiguous, and the answer to the
    # question could not be read anyway (decided by the author, 2026-10-30)
    mode = get_mode(state["mode"])
    local = not _is_expected_answer(mode, state, inp.user_text)
    if state.get("model_down") and local and local_cancel_check(
            inp.user_text, getattr(env.llm, "language", "English")):
        finished = user_finished(env, state)
        if finished:
            return finished
        state["user_cancelled"] = True
        state["cancelled_without_confirmation"] = True
        reply = _strategy_closing(env, state) or env.llm.phrase("cancel_ok")
        return SkillResult(reply=reply, label="confirmed")

    if mode.done_words_are_answers and local_step_done(
            inp.user_text, getattr(env.llm, "language", "English")):
        # "Done." reports the element done: never a request to stop, and the model is not asked
        return SkillResult(label="none")
    if not local:
        # Every word is in an answer the robot expects now ("Cancel." to "open an account or
        # cancel a card?"): it is that answer, never a request to stop, and the model is not
        # asked. Given the option, a small model still read it as a wish to stop in every
        # dialogue of a scenario, and its confirmation ("Yes, I want to cancel my card") ended
        # the conversation. A user who did mean to stop hears the read-back and says so again
        return SkillResult(label="none")
    if mode.yes_is_an_answer(state) and local_starts_with_yes(
            inp.user_text, getattr(env.llm, "language", "English")):
        # A reply that opens with a yes ("Yes, that's correct.") to a question whose answer is
        # yes or no (the read-back of the data) is that answer: the model is not asked. The
        # read-back of a request to "cancel a card" made a small model read the user's
        # confirmation as a request to stop, and the data already confirmed were lost
        return SkillResult(label="none")
    strict = mode.strict_cancel(state)
    said = normalize_text(inp.user_text)
    if said and [_element(state), said] in state.get("denied_cancels", []):
        return SkillResult(label="none")
    try:
        if detect is not None:
            request = _request_of(detect(env, state, inp, local))
        else:
            request = _request_of(env.llm.detect_cancel_intent(
                inp.user_text, ctx, strict=strict, task=task, stage=mode.cancel_stage(state),
                robot_line=resume_line(env, inp.option), local=local, reading=True))
    except Exception as exc:
        # The state must come back (a failed request loses it): not a request to stop, unless an
        # exact command
        _log.warning(f"detect_cancel_intent failed: {exc}")
        request = "cancel" if local and local_cancel_check(
            inp.user_text, getattr(env.llm, "language", "English")) else "none"
    if request == "finished" and not mode.on_finished:
        # Where the end is not the user's to decide, "I'm done, that's enough" is a request to
        # stop, and is asked about (in a quiz it was graded as an answer and the quiz went on);
        # unless finishing is a normal reply to the strategy's element (a procedure: "I'm
        # relaxed enough, I want to skip it" skips the step; it was asked about, and the user
        # left: DialogueMode.finishing_is_an_answer), where the turn goes to the strategy
        request = "none" if mode.finishing_is_an_answer else "cancel"
    if request == "cancel":
        exact = local and local_cancel_check(inp.user_text,
                                             getattr(env.llm, "language", "English")) is True
        # In a strategy whose end the user decides (grounded QA), stopping and finishing are the
        # same once something was done (a question answered): a request to stop ends it with the
        # strategy's own closing, without a confirmation. Most of these requests are goodbyes
        # ("No, that's all, thank you" after "Anything else?"), and the user, already gone, does
        # not answer a confirmation: the robot was left waiting through two silences before its
        # closing. Before any answer, a request to stop is asked about ("Hmm... maybe I'll leave
        # it" is not taken for a goodbye)
        finished = _finishing(env, state)
        if finished:
            return finished
        if not confirm:
            # Without the question (an ablation): the request ends the dialogue at once
            finished = _finishing(env, state)
            if finished:
                return finished
            state["user_cancelled"] = True
            reply = (_strategy_closing(env, state)
                     or env.llm.cancel_confirmed_response(task, _frame_or_none(state), ctx))
            return SkillResult(reply=reply, label="confirmed")
        state["awaiting_cancel"] = True
        state["cancel_turn"] = said
        state["cancel_exact"] = exact
        state["cancel_request"] = inp.user_text
        state["resume_line"] = pending_line(env, state, inp.option)
        state["cancel_question"] = env.llm.ask_cancel_confirmation(task, ctx)
        return SkillResult(reply=state["cancel_question"], label="ask")
    return SkillResult(label="none")


def _is_expected_answer(mode, state: dict, user_text: str) -> bool:
    """Whether the user gave one of the answers the robot expects now
    (DialogueMode.expected_answers): every word they said is in one of them ("Cancel." to an
    option "cancel a card", "Stop." to a quiz whose answer is "stop"), or they named a whole
    answer of two or more words within a sentence ("I'd like to cancel my contract, please." to
    "cancel my contract"). A one-word answer inside a sentence does not count ("I want to stop
    now" when the quiz answer is "stop")."""
    words = normalize_text(user_text).split()
    if not words:
        return False
    said = f" {' '.join(words)} "
    for answer in mode.expected_answers(state):
        expected = normalize_text(str(answer)).split()
        if set(words) <= set(expected):
            return True
        if len(expected) >= 2 and f" {' '.join(expected)} " in said:
            return True
    return False


def _request_of(reading) -> str:
    """The detector's reading as "cancel", "finished" or "none" (a boolean reading: cancel or
    none)."""
    if reading is True:
        return "cancel"
    return reading if reading in ("cancel", "finished") else "none"


def _asked_to_stop(state: dict, last_utterance: str) -> bool:
    """Whether the robot's last line holds the question whether to stop (as it was asked); also
    when either is unknown."""
    question = normalize_text(state.get("cancel_question") or "")
    said = normalize_text(last_utterance or "")
    return not question or not said or question in said


def _drop_pending_cancel(state: dict, keep_turn: bool = False) -> None:
    """The confirmation is no longer pending (answered, or left over)."""
    state["awaiting_cancel"] = False
    if not keep_turn:
        for key in ("cancel_turn", "cancel_exact", "cancel_request", "cancel_question"):
            state.pop(key, None)


def _element(state: dict) -> str:
    """The element the user is on, for the requests to stop set aside after a denial: the
    strategy's phase (the question, step or item it is on), and in slot filling the slot being
    asked for."""
    element = get_mode(state["mode"]).phase(state)
    if state.get("mode") == "slot_filling" and state.get("frame"):
        from ..dialog_frame import DialogFrame

        element += f"|{DialogFrame.from_state(state['frame']).next_slot() or ''}"
    return element


def pending_line(env: SkillEnv, state: dict, last_utterance: str) -> str:
    """What the robot repeats after a silence and resumes with after a denied request to stop:
    the element the user is expected to answer now (DialogueMode.pending), or, when the strategy
    gives none, its last utterance (resume_line). A silence prompt the robot said ("Take your
    time, I'm still here") is not what it had asked: after one, the line kept at the first wait of
    the silence is used."""
    said = normalize_text(last_utterance or "")
    if said and said in state.get("silence_said", []) and state.get("pending_line"):
        return state["pending_line"]
    try:
        pending = (get_mode(state["mode"]).pending(env, state) or "").strip()
    except (KeyError, IndexError, TypeError):
        pending = ""
    return pending or resume_line(env, last_utterance)


def resume_line(env: SkillEnv, last_utterance: str) -> str:
    """What the robot resumes with after a denied cancellation: its last utterance, without the
    "let's continue" of an earlier denial (otherwise each denial adds one more)."""
    return strip_leading(last_utterance, env.llm.phrase("cancel_no"))


def _finishing(env: SkillEnv, state: dict) -> SkillResult | None:
    """The strategy's normal end for a request to stop where stopping is finishing (a strategy
    whose end the user decides, once something was done: DialogueMode.stop_is_finishing), or
    None."""
    mode = get_mode(state["mode"])
    if not mode.on_finished or not mode.stop_is_finishing(state):
        return None
    return user_finished(env, state)


def user_finished(env: SkillEnv, state: dict) -> SkillResult | None:
    """The strategy's normal end when the user says they have finished (label "finished"), or
    None if the strategy's end is not the user's to decide."""
    hook = get_mode(state["mode"]).on_finished
    if not hook:
        return None
    return SkillResult(reply=hook(env, state), label="finished")


def _strategy_closing(env: SkillEnv, state: dict) -> str:
    """The strategy's own closing for a confirmed cancellation ("": the generic one). When the
    strategy closes what was done (the role play: its evaluation), the stop is the end the user
    chose, not an abandoned task: the outcome is the strategy's own, not "cancelled" (decided by
    the author, 2026-11-08: the role play invites the user to finish whenever they want)."""
    hook = get_mode(state["mode"]).on_cancel
    closing = (hook(env, state) or "") if hook else ""
    if closing:
        state["user_cancelled"] = False
    return closing


@skill("silence")
def silence(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The user said nothing for wait_timeout seconds (number: the silent waits so far in a
    row; option: the robot's last utterance). Silence is not abandonment: the robot says it is
    waiting, repeats what it had asked, and says nothing every other wait. The tree counts the
    waits and ends the dialogue at max_silence.

    label: "wait" (the current element takes time, e.g. a procedure step with a duration: say
    nothing, and the wait does not count), "quiet" (counts; say nothing), "prompt" (counts;
    reply: what to say) or "end" (the wait that reaches max_silent_waits: counts, and the tree
    closes the dialogue, so no prompt is asked for it). Fixed phrases: no LLM.

    While the question whether to stop is pending, the first wait asks it again, and the next
    one ends the dialogue (label "closed"; a user who asked to stop and then left is not prompted
    for max_silence seconds). It ends as a silence ends it: with the strategy's own closing for
    what was done (on_finished where stopping is finishing; else on_silent_end, e.g. the role
    play's evaluation), whose outcome is the strategy's own (a user who said goodbye at the end
    and left completed the task); only when the strategy has no closing of its own, with the
    cancel closing and the outcome cancelled ("asked to stop, then said nothing")."""
    if state.get("awaiting_cancel"):
        if inp.number == 0:
            return SkillResult(reply=state.get("cancel_question") or env.llm.phrase("cancel_ask"),
                               label="prompt")
        _drop_pending_cancel(state)
        finished = _finishing(env, state)
        if finished:
            # Where stopping is finishing (grounded QA after an answer): its normal end
            return SkillResult(reply=finished.reply, label="closed")
        # The user left: the dialogue ends as a silence ends it, with the strategy's own closing
        # for what was done when it has one (the role play's evaluation after "Goodbye" at the
        # end of the scene: its outcome by what was done, not cancelled)
        hook = get_mode(state["mode"]).on_silent_end
        closing = (hook(env, state) or "") if hook else ""
        if closing:
            return SkillResult(reply=closing, label="closed")
        state["user_cancelled"] = True
        state["cancel_reason"] = "The user asked to stop, then said nothing"
        return SkillResult(reply=env.llm.phrase("cancel_ok"), label="closed")
    wait = float(state.get("wait_timeout") or 0.0)
    waited = float(state.get("element_waited_s") or 0.0)
    grace = get_mode(state["mode"]).silence_grace(state)
    if wait > 0 and waited + wait <= grace:
        state["element_waited_s"] = waited + wait
        return SkillResult(label="wait")
    last = int(state.get("max_silent_waits") or 0)
    if last > 0 and inp.number + 1 >= last:
        # This wait ends the dialogue (the tree closes it): no prompt is asked for it
        return SkillResult(label="end")
    if inp.number == 0:
        state["pending_line"] = pending_line(env, state, inp.option)
        return _prompt(state, env.timeout_prompt)
    if inp.number == 1 and state.get("pending_line"):
        return _prompt(state, state["pending_line"])
    if inp.number % 2 == 0:
        return SkillResult(label="quiet")
    return _prompt(state, env.llm.phrase("silence_ready"))


def _prompt(state: dict, reply: str) -> SkillResult:
    """A silence prompt, remembered (normalized) so that a request to stop said after it resumes
    with what the robot had asked, not with the prompt (pending_line)."""
    said = normalize_text(reply)
    if said and said not in state.setdefault("silence_said", []):
        state["silence_said"].append(said)
    return SkillResult(reply=reply, label="prompt")


@skill("silent_end")
def silent_end(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The user's silence is about to end the dialogue (max_silent_waits): the strategy may
    close it itself (DialogueMode.on_silent_end). label: "closed" (reply: the strategy's own
    closing; the outcome is the strategy's) or "none" (the tree ends the dialogue as
    user_silent)."""
    hook = get_mode(state["mode"]).on_silent_end
    reply = (hook(env, state) or "") if hook else ""
    return SkillResult(reply=reply, label="closed" if reply else "none")


@skill("turn_limit_end")
def turn_limit_end(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The dialogue reached its turn limit (max_turns) before its own end, after the user's last
    turn was handled: the closing line that replaces the reply (the robot never asks a question
    it will not listen to). label: "closed" (reply: what the strategy says for what was done,
    DialogueMode.on_turn_limit, then the fixed closing line; the ending is turn_limit) or "none"
    (the strategy has nothing to say: the tree says the fixed closing line).

    When the last turn was a request to stop and the question whether to stop is pending, the
    dialogue ends as the user asked: label "cancelled" (reply: the cancel closing; outcome
    cancelled, "The user asked to stop at the turn limit"), or, where stopping is finishing
    (grounded QA after an answer), the strategy's normal end (label "finished")."""
    if state.get("awaiting_cancel"):
        _drop_pending_cancel(state)
        finished = _finishing(env, state)
        if finished:
            return SkillResult(reply=finished.reply, label="finished")
        state["user_cancelled"] = True
        state["cancel_reason"] = "The user asked to stop at the turn limit"
        return SkillResult(reply=env.llm.phrase("cancel_ok"), label="cancelled")
    hook = get_mode(state["mode"]).on_turn_limit
    said = (hook(env, state) or "").strip() if hook else ""
    if not said:
        return SkillResult(label="none")
    return SkillResult(reply=f"{said} {env.llm.phrase('close_turn_limit')}", label="closed")


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
        return SkillResult(label="cancelled",
                           reply=state.get("cancel_reason") or "Cancelled by the user")
    mode = get_mode(state["mode"])
    if mode.outcome:
        outcome, reason = mode.outcome(state)
    elif mode.succeeded(state):
        outcome, reason = "completed", ""
    else:
        outcome, reason = "failed", "The dialogue ended without success"
    return SkillResult(label=outcome, reply="" if outcome == "completed" else reason)
