# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""What the robot repeats after a silence and resumes with after a denied request to stop: the
element the user is expected to answer now (DialogueMode.pending), not its whole last utterance
with acknowledgments, feedback or introductions; the last utterance when the strategy gives none
(the read-back while confirming)."""

import json

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill
from interaction_intelligence.skills.common import pending_line

QUESTIONS = [{"question": "What is 2 plus 2?", "answer": "4"},
             {"question": "What is 3 plus 3?", "answer": "6"}]
STEPS = ["Fill the kettle", "Switch it on"]
INSTRUMENT = {"name": "Mood", "items": [
    {"text": "How did you sleep", "options": ["Well", "Badly"]}]}
SCENE = {"scenario": "A café", "role": "The waiter", "criteria": ["Orders"], "max_exchanges": 6}


class Model:
    """A test double of the client."""

    language = "English"

    def phrase(self, key):
        return f"[{key}]"

    def detect_cancel_intent(self, *args, **kwargs):
        return "cancel"

    def detect_urgency(self, *args, **kwargs):
        return False

    def ask_cancel_confirmation(self, task, ctx=None):
        return "[ask]"

    def classify_cancel_reply(self, *args, **kwargs):
        return "no"


def start(mode, content):
    env = SkillEnv(llm=Model(), ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": mode, "task_description": "A task",
        "resources_json": json.dumps([{"name": "r", "content": json.dumps(content)}])}))
    return env, state


def test_the_quiz_repeats_its_question_not_the_feedback():
    env, state = start("quiz", QUESTIONS)
    state["idx"] = 1
    said = "Correct, well done! Question 2 of 2: What is 3 plus 3?"
    assert pending_line(env, state, said) == "What is 3 plus 3?"
    silence = get_skill("silence")
    assert silence(env, state, SkillInput(number=0, option=said)).reply == env.timeout_prompt
    assert silence(env, state, SkillInput(number=1, option=said)).reply == "What is 3 plus 3?"


def test_a_denied_request_to_stop_resumes_with_the_element():
    env, state = start("quiz", QUESTIONS)
    said = "Let's start the quiz! Question 1 of 2: What is 2 plus 2?"
    ask = get_skill("cancel_check")(env, state, SkillInput(user_text="Stop.", option=said))
    assert ask.label == "ask"
    denied = get_skill("cancel_check")(env, state, SkillInput(user_text="No.", option="[ask]"))
    assert (denied.label, denied.reply) == ("denied", "[cancel_no] What is 2 plus 2?")


def test_each_strategy_gives_its_element():
    env, state = start("procedure", STEPS)
    assert pending_line(env, state, "Great. Now the first step: fill the kettle.") == STEPS[0]
    env, state = start("questionnaire", INSTRUMENT)
    line = pending_line(env, state, "Thanks for coming. [questionnaire_item] How did you sleep")
    assert line == "[questionnaire_item] How did you sleep. [questionnaire_options]"
    env, state = start("roleplay", SCENE)
    state["history"].append({"role": "assistant", "content": "What can I get you?"})
    assert pending_line(env, state, "Welcome to the practice. What can I get you?") == \
        "What can I get you?"


def test_without_an_element_the_last_utterance_is_repeated():
    env, state = start("grounded_qa", "The museum opens at 10:00.")
    assert pending_line(env, state, "Hello! What would you like to know?") == \
        "Hello! What would you like to know?"
    state["questions"] = [{"resolution": "answered"}]
    assert pending_line(env, state, "It opens at 10:00. [qa_more]") == "[qa_more]"
