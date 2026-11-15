# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The last silent wait asks for no prompt (label end: the tree closes the dialogue, so a prompt
asked then was never said), and the turn limit reached while the question whether to stop is
pending ends the dialogue as the user asked (cancelled; where stopping is finishing, the
strategy's normal end)."""

import json

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

QUESTIONS = [{"question": "What is 2 plus 2?", "answer": "4"}]


class Model:
    """A test double of the client."""

    language = "English"

    def phrase(self, key):
        return f"[{key}]"


def start(mode="quiz", content=QUESTIONS, **goal):
    env = SkillEnv(llm=Model(), ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": mode, "task_description": "A task",
        "resources_json": json.dumps([{"name": "r", "content": json.dumps(content)}]), **goal}))
    return env, state


def test_the_wait_that_ends_the_dialogue_asks_for_no_prompt():
    env, state = start(max_silent_waits=3)
    silence = get_skill("silence")
    assert silence(env, state, SkillInput(number=0, option="Q?")).label == "prompt"
    assert silence(env, state, SkillInput(number=1, option="Q?")).label == "prompt"
    assert silence(env, state, SkillInput(number=2, option="Q?")).label == "end"


def test_without_the_limit_in_the_request_the_prompts_go_on():
    env, state = start()
    assert get_skill("silence")(env, state, SkillInput(number=5, option="Q?")).label == "prompt"


def test_the_turn_limit_with_a_pending_question_to_stop_is_a_cancellation():
    env, state = start()
    state["awaiting_cancel"] = True
    state["cancel_question"] = "[cancel_ask]"
    result = get_skill("turn_limit_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("cancelled", "[cancel_ok]")
    assert not state["awaiting_cancel"]
    outcome = get_skill("result")(env, state, SkillInput())
    assert (outcome.label, outcome.reply) == ("cancelled", "The user asked to stop at the turn limit")


def test_where_stopping_is_finishing_the_turn_limit_gives_the_normal_end():
    env, state = start("grounded_qa", "The museum opens at 10:00.")
    state["questions"] = [{"resolution": "answered"}]
    state["awaiting_cancel"] = True
    result = get_skill("turn_limit_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("finished", "[qa_bye]")
