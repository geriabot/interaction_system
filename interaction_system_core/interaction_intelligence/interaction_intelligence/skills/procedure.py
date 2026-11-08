# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Procedure: guide the user through an ordered list of steps.

The steps come in the resources of the request: the content of the first resource that parses
as a JSON list of steps, each a string or {"step", "check", "duration_s", "optional"}
("instruction" also accepted for "step"; "check" says what the user should observe or report
once the step is done; "duration_s" the seconds the step takes, during which the robot waits
in silence without prompting; "optional": skipping the step does not make the outcome
partial).

The tree (procedure.xml) decides what each kind of turn does (done, question, problem,
repeat, back, skip, unclear) and counts the help attempts on the current step. The outcome
is "completed" when every step that is not optional was done, else "partial" (the reason
lists the steps skipped or stuck). State keys: "steps", "idx", "status" (done | skipped |
stuck | None per step), "turns", "history", "element_waited_s" (the silence waited on the
current step).
"""

from ..utils import parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill


def parse_steps(resources: list[dict]) -> list[dict]:
    for resource in resources:
        content = (resource.get("content", "") or "").strip()
        if not content:
            continue
        try:
            data = parse_loose_json(content)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        items: list[dict] = []
        for entry in data:
            duration, optional = 0.0, False
            if isinstance(entry, str):
                text, check = entry, ""
            elif isinstance(entry, dict):
                text = entry.get("step") or entry.get("instruction") or ""
                check = entry.get("check") or ""
                try:
                    duration = max(0.0, float(entry.get("duration_s") or 0.0))
                except (TypeError, ValueError):
                    duration = 0.0
                optional = entry.get("optional") is True
            else:
                continue
            if isinstance(text, str) and text.strip():
                items.append({"step": text.strip(), "check": str(check).strip(),
                              "duration_s": duration, "optional": optional})
        if items:
            return items
    return []


def _init(env: SkillEnv, state: dict) -> None:
    steps = parse_steps(state["resources"])
    if not steps:
        raise ValueError(
            "The procedure requires a resource with a JSON list of steps "
            "(strings or {'step','check'} objects) in its content field."
        )
    state.update(
        {"steps": steps, "idx": 0, "status": [None] * len(steps), "turns": 0, "history": []}
    )


def _count(state: dict, status: str) -> int:
    return sum(1 for st in state["status"] if st == status)


def _phase(state: dict) -> str:
    if state.get("completed"):
        return "done"
    return f"step_{state['idx'] + 1}_of_{len(state['steps'])}"


def _data(state: dict) -> dict:
    return {
        "total_steps": len(state["steps"]),
        "current_step": state["idx"] + 1,
        "completed": _count(state, "done"),
        "skipped": _count(state, "skipped"),
        "results": [
            {"step": s["step"], "status": st}
            for s, st in zip(state["steps"], state["status"])
            if st is not None
        ],
    }


def _outcome(state: dict) -> tuple[str, str]:
    if not state.get("completed"):
        return "failed", "The procedure did not reach its end"
    unfinished = [
        f"{s['step']} ({st})" for s, st in zip(state["steps"], state["status"])
        if st != "done" and not s.get("optional")
    ]
    if unfinished:
        return "partial", "Steps not done: " + "; ".join(unfinished)
    return "completed", ""


def _step_duration(state: dict) -> float:
    if state.get("completed") or state["idx"] >= len(state["steps"]):
        return 0.0
    return float(state["steps"][state["idx"]].get("duration_s") or 0.0)


register_mode(
    "procedure",
    DialogueMode(
        init=_init,
        phase=_phase,
        data=_data,
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        outcome=_outcome,
        silence_grace=_step_duration,
    ),
)


def _say(state: dict, reply: str, label: str = "") -> SkillResult:
    state["history"].append({"role": "assistant", "content": reply})
    return SkillResult(reply=reply, label=label)


def _step_text(env: SkillEnv, state: dict, situation: str) -> str:
    """The current step, phrased for the situation: first | next | back. Without the context:
    it holds the whole procedure, and small models joined the next steps to the current one."""
    current = state["steps"][state["idx"]]
    return env.llm.procedure_step(current["step"], state["idx"] + 1, len(state["steps"]), situation)


@skill("procedure.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    step = _step_text(env, state, "first")
    intro = env.llm.procedure_opening(state["task"], len(state["steps"]), env.ctx)
    return _say(state, f"{intro} {step}")


@skill("procedure.first_step")
def first_step(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The first step without an introduction (skip_intro)."""
    return _say(state, _step_text(env, state, "first"))


@skill("procedure.classify")
def classify(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """label: "done", "question", "problem", "repeat", "back", "skip" or "unclear"."""
    state["turns"] += 1
    state["history"].append({"role": "user", "content": inp.user_text})
    current = state["steps"][state["idx"]]
    label = env.llm.classify_step_turn(
        inp.user_text, current["step"], current["check"], state["history"], env.ctx
    )
    return SkillResult(label=label)


@skill("procedure.advance")
def advance(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Close the current step with option (done | skipped | stuck) and present the next one.
    label: "next", or "finished" after the last step (the reply is the summary)."""
    state["status"][state["idx"]] = inp.option
    state["element_waited_s"] = 0.0
    if state["idx"] + 1 >= len(state["steps"]):
        state["completed"] = True
        reply = env.llm.procedure_summary(
            state["task"],
            completed=_count(state, "done"),
            total=len(state["steps"]),
            # How each step not done ended, never its text: with the text the closing told the
            # user to do it after all, sometimes with details the step does not have
            not_done=[st for st in state["status"] if st != "done"],
            ctx=env.ctx,
        )
        return _say(state, reply, "finished")
    state["idx"] += 1
    return _say(state, _step_text(env, state, "next"), "next")


@skill("procedure.answer")
def answer(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    current = state["steps"][state["idx"]]
    reply = env.llm.procedure_answer_question(
        inp.user_text, state["task"], current["step"], state["history"], env.ctx
    )
    return _say(state, reply)


@skill("procedure.help")
def help_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Help with the current step (number: the help attempts so far on it)."""
    current = state["steps"][state["idx"]]
    reply = env.llm.procedure_help_step(
        state["task"], current["step"], state["history"], inp.number + 1, env.ctx
    )
    return _say(state, reply)


@skill("procedure.repeat")
def repeat(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The current step again, literally (no LLM), as the other strategies repeat."""
    step = env.llm.phrase("procedure_step").format(
        n=state["idx"] + 1, total=len(state["steps"]), step=state["steps"][state["idx"]]["step"]
    )
    return _say(state, f"{step.rstrip('.')}. {env.llm.phrase('procedure_remind')}")


@skill("procedure.back")
def back(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Go back to the previous step (or present the first one again)."""
    if state["idx"] > 0:
        state["idx"] -= 1
        state["status"][state["idx"]] = None
    state["element_waited_s"] = 0.0
    return _say(state, _step_text(env, state, "back"))


@skill("procedure.remind")
def remind(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Remind the user to say when the step is done (an unclear turn)."""
    current = state["steps"][state["idx"]]
    return _say(state, env.llm.procedure_remind(current["step"], env.ctx))
