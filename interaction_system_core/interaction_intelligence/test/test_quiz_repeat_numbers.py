# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A reply to a numeric quiz question is first read by the LLM (an answer, or a request to hear
the question again): "one more time" says the number 1 but is not an answer. Only an answer is
then graded by comparing the number it says, whatever the LLM's own grade."""

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

QUESTION = {"question": "How many legs does a spider have?", "answer": "8"}


class Llm:
    def __init__(self, language, kind, llm_correct=True):
        self.language = language
        self.kind = kind
        self.llm_correct = llm_correct
        self.calls = 0

    def evaluate_quiz_answer(self, question, expected, given):
        self.calls += 1
        if self.kind == "repeat":
            return {"kind": "repeat", "correct": False, "feedback": ""}
        return {"kind": "answer", "correct": self.llm_correct, "feedback": "[llm]"}

    def quiz_feedback(self, correct, expected):
        return "[right]" if correct else "[wrong]"


def evaluate(llm, text):
    state = {"questions": [dict(QUESTION), dict(QUESTION)], "idx": 0, "results": [],
             "feedback": ""}
    env = SkillEnv(llm=llm, ctx=DialogContext())
    result = get_skill("quiz.evaluate")(env, state, SkillInput(user_text=text))
    return result, state


@pytest.mark.parametrize("text,language", [
    ("One more time, please", "English"),
    ("Can you say question two again?", "English"),
    ("Una vez más, por favor", "Spanish"),
    ("¿Me repites la pregunta dos?", "Spanish"),
])
def test_a_request_to_repeat_that_says_a_number_is_not_graded(text, language):
    llm = Llm(language, "repeat")
    result, state = evaluate(llm, text)
    assert result.label == "repeat" and state["results"] == [] and llm.calls == 1


@pytest.mark.parametrize("text,language,correct", [
    ("Eight.", "English", True), ("I think it has eight legs", "English", True),
    ("Six", "English", False), ("ocho", "Spanish", True), ("Tiene seis patas", "Spanish", False),
])
def test_a_numeric_answer_is_graded_by_its_number_not_by_the_llm(text, language, correct):
    # The LLM's grade is the opposite: the number decides, with the fixed feedback
    llm = Llm(language, "answer", llm_correct=not correct)
    result, state = evaluate(llm, text)
    assert result.label == "next" and llm.calls == 1
    assert state["results"][0]["correct"] is correct
    assert state["feedback"] == ("[right]" if correct else "[wrong]")


def test_an_answer_without_a_single_number_keeps_the_llm_grade():
    llm = Llm("English", "answer", llm_correct=True)
    result, state = evaluate(llm, "the same as an octopus")
    assert state["results"][0]["correct"] is True and state["feedback"] == "[llm]"


def test_the_grading_prompt_takes_a_tentative_answer_said_as_a_question_as_an_answer():
    # E1 validation (qz_geography): "Is it Rome?" was graded as a question back
    import os
    import yaml
    from interaction_intelligence.prompt_builder import PromptBuilder
    path = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
    with open(path) as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]
    prompt, _ = PromptBuilder(prompts, language="English").evaluate_quiz_answer(
        "What is the capital of Italy?", "Rome", "Is it Rome?")
    assert '"Is it Rome?", "maybe Paris?"' in prompt and "never take it for a question back" in prompt
