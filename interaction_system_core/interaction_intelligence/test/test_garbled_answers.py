# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A recognizer garbles the words of a reply, and the bounded call that reads it sees only that
line. The quiz grades as correct a reply that names the whole expected answer with no word
that denies or hedges it ("A young to is all a tadpole."), whatever the model's grade. The
questionnaire says the options again when the reply hedges, or when every word it shares with
the option the model chose is in another option too ("tasty true." next to "Completely true"
and "Mostly true")."""

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.utils import local_names_answer


@pytest.mark.parametrize("text,answer,language,named", [
    ("A young to is all a tadpole.", "A tadpole", "English", True),
    ("Plants in carbon dioxide to makes food.", "Carbon dioxide", "English", True),
    ("that the bee? I remember of late husband loved honey.", "The bee", "English", True),
    ("Tadpole", "A tadpole", "English", True),
    ("Not a tadpole, a froglet.", "A tadpole", "English", False),
    ("Rome, not Paris.", "Paris", "English", False),
    ("A frog or a tadpole?", "A tadpole", "English", False),
    ("Maybe a tadpole", "A tadpole", "English", False),
    ("Actually, no, make it a tadpole.", "A tadpole", "English", False),
    ("Carbon, I think.", "Carbon dioxide", "English", False),       # not the whole answer
    ("I wouldn't say a tadpole, it's a froglet.", "A tadpole", "English", False),
    ("A froglet rather than a tadpole.", "A tadpole", "English", False),
    ("Anything but a tadpole.", "A tadpole", "English", False),
    ("It isn\u2019t a tadpole.", "A tadpole", "English", False),
    ("Un sapito, en vez de un renacuajo.", "Un renacuajo", "Spanish", False),
    ("No es un renacuajo sino una rana.", "Un renacuajo", "Spanish", False),
    ("Es un renacuajo, creo.", "Un renacuajo", "Spanish", True),
    ("No es un renacuajo.", "Un renacuajo", "Spanish", False),
    ("Una rana o un renacuajo", "Un renacuajo", "Spanish", False),
])
def test_a_reply_names_the_expected_answer(text, answer, language, named):
    assert local_names_answer(text, answer, language) is named


class QuizLlm:
    def __init__(self, language, correct):
        self.language = language
        self.correct = correct

    def evaluate_quiz_answer(self, question, expected, given):
        return {"kind": "answer", "correct": self.correct, "feedback": "[llm]"}

    def quiz_feedback(self, correct, expected):
        return "[right]" if correct else "[wrong]"


def grade(text, answer="A tadpole", language="English", llm_correct=False):
    question = {"question": "What is a young frog called?", "answer": answer}
    state = {"questions": [dict(question), dict(question)], "idx": 0, "results": [],
             "feedback": ""}
    env = SkillEnv(llm=QuizLlm(language, llm_correct), ctx=DialogContext())
    get_skill("quiz.evaluate")(env, state, SkillInput(user_text=text))
    return state


def test_a_garbled_reply_that_names_the_answer_is_correct():
    state = grade("A young to is all a tadpole.")
    assert state["results"][0]["correct"] is True and state["feedback"] == "[right]"


@pytest.mark.parametrize("text", ["A froglet.", "Not a tadpole, a froglet.",
                                  "A frog or a tadpole?"])
def test_otherwise_the_model_grade_stands(text):
    state = grade(text)
    assert state["results"][0]["correct"] is False and state["feedback"] == "[llm]"


def test_a_right_grade_is_never_changed():
    state = grade("A froglet.", llm_correct=True)
    assert state["results"][0]["correct"] is True and state["feedback"] == "[llm]"


TRUE = ["Not at all true", "A little true", "Mostly true", "Completely true"]
BETTER = ["Much worse", "A little worse", "The same", "A little better", "Much better"]
AGREE = ["Completely disagree", "Somewhat disagree", "Somewhat agree", "Completely agree"]
SATISFACTION = ["Very dissatisfied", "Dissatisfied", "Neither satisfied nor dissatisfied",
                "Satisfied", "Very satisfied"]
YES_NO = ["Yes", "No", "Not sure"]


class ScaleLlm:
    language = "English"

    def __init__(self, option):
        self.option = option

    def map_scale_answer(self, user_text, item, options, ctx=None, previous=None):
        return {"kind": "option", "option": self.option}

    def phrase(self, key):
        return f"[{key}]"


def record(labels, chosen, said):
    state = {"instrument": {"items": [{"text": "How was it?", "clarification": "",
                                       "reverse": False,
                                       "options": [{"label": lb, "value": i + 1}
                                                   for i, lb in enumerate(labels)]}] * 2},
             "idx": 0, "answers": []}
    env = SkillEnv(llm=ScaleLlm(chosen), ctx=DialogContext())
    return get_skill("questionnaire.record")(env, state, SkillInput(user_text=said)), state


@pytest.mark.parametrize("labels,chosen,said", [
    (TRUE, 3, "tasty true."),                     # "mostly" garbled: two options say "true"
    (BETTER, 3, "to better"),
    (AGREE, 2, "to agree"),
    (AGREE, 1, "I somewhat in are that the staff were to"),
    (SATISFACTION, 2, "Between and neither satisfied to"),     # a hedge
    (SATISFACTION, 2, "somewhere between dissatisfied and neither satisfied nor dissatisfied"),
])
def test_the_options_again_when_the_reply_does_not_decide(labels, chosen, said):
    result, state = record(labels, chosen, said)
    assert result.label == "ambiguous" and state["answers"] == []


@pytest.mark.parametrize("labels,chosen,said", [
    (TRUE, 3, "Completely true."),                # said in full
    (SATISFACTION, 3, "I was satisfied."),
    (SATISFACTION, 4, "Very satisfied."),
    (YES_NO, 0, "absolutely"),                    # a synonym: the model's reading is kept
    (BETTER, 4, "Much better, thanks."),
    (AGREE, 3, "I completely agree."),
])
def test_an_option_the_reply_decides_is_recorded(labels, chosen, said):
    result, state = record(labels, chosen, said)
    assert result.label == "next" and state["answers"][0]["answer"] == labels[chosen]
