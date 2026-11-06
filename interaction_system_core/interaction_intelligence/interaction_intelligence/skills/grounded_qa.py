# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Grounded question answering: the user asks, the robot answers only from the material.

An information-seeking dialogue (a reception desk, a museum, a station): the initiative is the
user's. Each turn is understood in one question to the LLM, against the material of the request.
It first says what the person does: asks for something, has finished (wants nothing more: the
end of the user's questions, the closing) or only reacts (the robot invites more questions);
only a request can be answered, whatever the rest of the model's answer says, so that the robot
never volunteers information nobody asked for (decided by the author, 2026-10-31). A request is
a question answerable from the material (answered only with it), a question within the task
whose answer is not in the material (abstention: "I don't have that information", and the
handover the request offers, if any), something outside the task (declined), a request to
repeat the last answer, or none of these. A reaction that repeats back a value of the last
answer with another value ("Nine euros? Fine." after "It costs 19 euros") is a misunderstanding,
not a reaction: a question of its own (detect_wrong_check), asked only for a reaction after an
answer from the material, turns it into a request to repeat, so that the robot says the answer
again. Before an answer is said, a second question verifies that every claim in it is supported
by the material; an answer that is not is replaced by the abstention. The phrases of abstention,
declining and closing are fixed: no LLM. Asked whether they want to stop, a user who says
instead that they have no more questions gets the same closing (DialogueMode.on_finished: the
end of this strategy is the user's to decide).

The material: the resources of the request with plain text (a structured resource, such as a
question bank, is not material). A resource named "handover" holds the sentence that offers a
person when the robot does not know (e.g. "The staff at the desk can help you with that").
State keys: "material", "handover", "questions" ([{question, resolution: answered | abstained |
declined, answer}]), "last_answer", "history".
"""

from ..utils import parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill


def _is_structured(content: str) -> bool:
    content = (content or "").strip()
    if not content.startswith(("[", "{")):
        return False
    try:
        parse_loose_json(content)
        return True
    except Exception:
        return False


def parse_material(resources: list[dict]) -> tuple[list[dict], str]:
    """(the material: the plain-text resources, the handover sentence)."""
    handover = ""
    material = []
    for r in resources:
        if r.get("name", "").strip().lower() == "handover":
            handover = (r.get("content") or "").strip()
        elif (r.get("content") or "").strip() and not _is_structured(r["content"]):
            material.append(r)
    return material, handover


def _init(env: SkillEnv, state: dict) -> None:
    material, handover = parse_material(state["resources"])
    if not material:
        raise ValueError(
            "Grounded question answering requires a resource with the material (plain text) "
            "the robot may answer from."
        )
    state.update({"material": material, "handover": handover, "questions": [],
                  "last_answer": "", "history": []})


def _count(state: dict, resolution: str) -> int:
    return sum(1 for q in state["questions"] if q["resolution"] == resolution)


register_mode(
    "grounded_qa",
    DialogueMode(
        init=_init,
        phase=lambda state: "done" if state.get("completed") else "answering",
        data=lambda state: {
            "questions": list(state.get("questions", [])),
            "answered": _count(state, "answered"),
            "abstained": _count(state, "abstained"),
            "declined": _count(state, "declined"),
        },
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        on_finished=lambda env, state: _close(env, state),
    ),
)


def _close(env: SkillEnv, state: dict) -> str:
    """The normal end: the user has no more questions (the fixed closing)."""
    state["completed"] = True
    reply = env.llm.phrase("qa_bye")
    state["history"].append({"role": "assistant", "content": reply})
    return reply


def _answer_to_check(state: dict) -> str:
    """The robot's last answer from the material, the one a check can repeat back ("" when the
    last question was not answered from the material: an abstention or a decline gives no value
    to check)."""
    questions = state.get("questions") or []
    if questions and questions[-1]["resolution"] == "answered":
        return questions[-1]["answer"]
    return ""


def _is_wrong_check(env: SkillEnv, state: dict, user_text: str) -> bool:
    """Whether a turn read as a reaction repeats back a value of the last answer with another
    value. Only asked when there is an answer to check; a failed question is not a wrong check
    (the turn stays a reaction, as without it)."""
    answer = _answer_to_check(state)
    return bool(answer) and env.llm.detect_wrong_check(user_text, answer) is True


def _say(state: dict, reply: str, label: str) -> SkillResult:
    state["history"].append({"role": "assistant", "content": reply})
    return SkillResult(reply=reply, label=label)


@skill("grounded_qa.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    return _say(state, env.llm.qa_opening(state["task"], env.ctx), "")


@skill("grounded_qa.open_direct")
def open_direct(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The invitation to ask, without a greeting (skip_intro)."""
    return _say(state, env.llm.phrase("qa_invite"), "")


@skill("grounded_qa.answer")
def answer(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Understand the turn and reply (the reply is set for every label).

    label: "answered" (from the material, verified), "abstained" (not in the material, or an
    answer that did not pass the verification), "declined" (outside the task), "comment" (the
    person only reacts, without asking: the invitation to ask more; not recorded as a question),
    "done" (the person has finished: the closing), "repeat" (the last answer again) or "other"
    (not understood). What the person does is read first (llm_client.answer_from_material):
    only a request reaches the answer. A reaction that repeats back the last answer with another
    value is handled as a request to repeat it (detect_wrong_check)."""
    state["history"].append({"role": "user", "content": inp.user_text})
    result = env.llm.answer_from_material(
        inp.user_text, state["task"], state["material"], state["history"][-6:], env.ctx
    )
    kind, more = result["kind"], env.llm.phrase("qa_more")
    if kind == "comment" and _is_wrong_check(env, state, inp.user_text):
        # The person checked the last answer back with another value: they misunderstood it,
        # and the answer is said again instead of only inviting more questions
        kind = "repeat"
    if kind == "done":
        # The closing cannot be undone: verified first (provisional, 2026-11-01)
        robot = next((h["content"] for h in reversed(state["history"][:-1])
                      if h["role"] == "assistant"), "")
        if not env.llm.verify_finished(inp.user_text, robot):
            return _say(state, env.llm.phrase("unclear"), "other")
        return SkillResult(reply=_close(env, state), label="done")
    if kind == "repeat":
        last = state.get("last_answer") or ""
        if not last:
            return _say(state, env.llm.phrase("qa_invite"), "repeat")
        # An abstention was kept with the invitation already
        return _say(state, last if last.endswith(more) else f"{last} {more}", "repeat")
    if kind == "other":
        return _say(state, env.llm.phrase("unclear"), "other")
    if kind == "comment":
        return _say(state, more, "comment")
    if kind == "off_task":
        state["questions"].append({"question": inp.user_text, "resolution": "declined",
                                   "answer": ""})
        return _say(state, f"{env.llm.phrase('qa_decline')} {more}", "declined")
    if kind == "answerable" and result["answer"] and env.llm.verify_answer(
            result["answer"], state["material"], env.ctx):
        state["last_answer"] = result["answer"]
        state["questions"].append({"question": inp.user_text, "resolution": "answered",
                                   "answer": result["answer"]})
        return _say(state, f"{result['answer']} {more}", "answered")
    # Not in the material, or an answer the material does not support
    reply = " ".join(p for p in (env.llm.phrase("qa_abstain"), state.get("handover", ""), more) if p)
    state["last_answer"] = reply
    state["questions"].append({"question": inp.user_text, "resolution": "abstained",
                               "answer": "", "unverified": result.get("answer", "")})
    return _say(state, reply, "abstained")
