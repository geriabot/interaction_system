# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Quiz: ask questions with known answers and score the spoken answers.

The question bank comes in the resources of the request: the content of the first resource
that parses as a JSON list of {"question", "answer"} objects ("q"/"a" also accepted). State
keys: "questions", "idx" (the current question), "results", "feedback" (of the last answer).

A reply that is not an attempt to answer (the user asks to repeat or clarify the question, did
not hear or understand it) is not graded: the tree asks the question again (literally first,
then in simpler words) and, after max_attempts such replies, leaves it unanswered and moves
on. "I don't know" is an answer (wrong). The outcome is "partial" if a question was left
unanswered; the score is in the data either way.
"""

from ..utils import parse_loose_json
from . import DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill


def parse_questions(resources: list[dict]) -> list[dict]:
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
            if not isinstance(entry, dict):
                continue
            q = entry.get("question") or entry.get("q")
            a = entry.get("answer") or entry.get("a")
            if isinstance(q, str) and q.strip() and a is not None:
                items.append({"question": q.strip(), "answer": str(a).strip()})
        if items:
            return items
    return []


def _init(env: SkillEnv, state: dict) -> None:
    questions = parse_questions(state["resources"])
    if not questions:
        raise ValueError(
            "The quiz requires a resource with a JSON list of "
            "{'question','answer'} items in its content field."
        )
    state.update({"questions": questions, "idx": 0, "results": [], "feedback": ""})


def _phase(state: dict) -> str:
    if state.get("completed"):
        return "done"
    return f"question_{state['idx'] + 1}_of_{len(state['questions'])}"


def _data(state: dict) -> dict:
    return {
        "total_questions": len(state["questions"]),
        "current_question": state["idx"],
        "correct_answers": sum(1 for r in state["results"] if r["correct"]),
        "unanswered": sum(1 for r in state["results"] if not r.get("answered", True)),
        "results": list(state["results"]),
    }


def _outcome(state: dict) -> tuple[str, str]:
    if not state.get("completed"):
        return "failed", "The quiz did not reach its end"
    unanswered = [r["question"] for r in state["results"] if not r.get("answered", True)]
    if unanswered:
        return "partial", "Unanswered questions: " + "; ".join(unanswered)
    return "completed", ""


register_mode(
    "quiz",
    DialogueMode(
        init=_init,
        phase=_phase,
        data=_data,
        succeeded=lambda state: bool(state.get("completed")) and not state.get("user_cancelled"),
        outcome=_outcome,
    ),
)


@skill("quiz.open")
def open_(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    intro = env.llm.quiz_opening(state["task"], len(state["questions"]))
    return SkillResult(reply=f"{intro} {state['questions'][0]['question']}")


@skill("quiz.first_question")
def first_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The first question without an introduction (skip_intro)."""
    return SkillResult(reply=state["questions"][0]["question"])


@skill("quiz.evaluate")
def evaluate(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Grade the answer to the current question and move to the next one.
    label: "next", "last" after the last question, or "repeat" when the reply is not an
    attempt to answer (nothing is recorded: the tree asks the question again)."""
    current = state["questions"][state["idx"]]
    evaluation = env.llm.evaluate_quiz_answer(
        question=current["question"], expected=current["answer"], given=inp.user_text
    )
    if evaluation.get("kind") == "repeat":
        return SkillResult(label="repeat")
    state["feedback"] = evaluation["feedback"]
    state["results"].append(
        {
            "question": current["question"],
            "expected": current["answer"],
            "given": inp.user_text,
            "correct": evaluation["correct"],
            "answered": True,
        }
    )
    return _advance(state)


def _advance(state: dict) -> SkillResult:
    state["idx"] += 1
    return SkillResult(label="last" if state["idx"] >= len(state["questions"]) else "next")


@skill("quiz.repeat")
def repeat(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Ask the current question again (number: the replies so far that were not answers to
    it): the first time literally (no LLM), then in simpler words."""
    current = state["questions"][state["idx"]]
    if inp.number <= 1:
        return SkillResult(reply=current["question"])
    reply = env.llm.quiz_rephrase_question(current["question"], current["answer"])
    return SkillResult(reply=reply)


@skill("quiz.unanswered")
def unanswered(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Leave the current question unanswered (not graded as wrong) and move to the next one.
    label: "next", or "last" after the last question."""
    current = state["questions"][state["idx"]]
    state["feedback"] = env.llm.phrase("quiz_unanswered")
    state["results"].append(
        {
            "question": current["question"],
            "expected": current["answer"],
            "given": "",
            "correct": False,
            "answered": False,
        }
    )
    return _advance(state)


@skill("quiz.next_question")
def next_question(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    reply = env.llm.quiz_next_question(
        feedback=state["feedback"],
        n=state["idx"] + 1,
        total=len(state["questions"]),
        question=state["questions"][state["idx"]]["question"],
    )
    return SkillResult(reply=reply)


@skill("quiz.summary")
def summary(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The feedback on the last answer and the score."""
    state["completed"] = True
    results = state["results"]
    text = env.llm.quiz_summary(
        task=state["task"],
        correct=sum(1 for r in results if r["correct"]),
        total=len(state["questions"]),
        wrongs=[r["question"] for r in results if not r["correct"] and r.get("answered", True)],
    )
    return SkillResult(reply=f"{state['feedback']} {text}")
