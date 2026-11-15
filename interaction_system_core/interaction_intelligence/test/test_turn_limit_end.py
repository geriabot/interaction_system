# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The turn limit (common.xml, TurnLimit; skill turn_limit_end): after the user's last turn was
handled, the closing line replaces the reply, so that the robot never asks a question it will not
listen to. The strategy may say something for what was done first (DialogueMode.on_turn_limit):
the quiz its score so far, the procedure how the steps went, the role play its evaluation; the
fixed closing line follows. Without it, the tree says the fixed closing line alone."""

import json

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill

SCENE = {"scenario": "A café", "role": "The waiter", "criteria": ["Orders", "Is polite"],
         "max_exchanges": 6}


class Model:
    """A test double of the client."""

    def __init__(self):
        self.calls = []

    def phrase(self, key):
        return f"[{key}]"

    def quiz_summary(self, task, correct, total, wrongs):
        self.calls.append(("quiz_summary", correct, total))
        return f"[{correct} of {total}]"

    def procedure_summary(self, task, completed, total, not_done, ctx=None):
        self.calls.append(("procedure_summary", completed, total, list(not_done)))
        return "[steps]"

    def classify_roleplay_turn(self, user_text, scene, history, ctx=None):
        return "continue"

    def evaluate_roleplay(self, scene, history, ctx=None):
        return {"scores": [{"criterion": c, "score": 3, "evidence": "", "comment": ""}
                           for c in SCENE["criteria"]]}

    def roleplay_feedback(self, scene, evaluation, ended_by, ctx=None):
        self.calls.append(("roleplay_feedback", ended_by))
        return {"text": "[feedback]", "highlight": "", "improve": ""}


def start(mode, content, model=None):
    model = model or Model()
    env = SkillEnv(llm=model, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": mode, "task_description": "A task", "max_turns": 25,
        "resources_json": json.dumps([{"name": "r", "content": json.dumps(content)}])}))
    return env, state, model


def limit(env, state):
    return get_skill("turn_limit_end")(env, state, SkillInput())


def test_the_quiz_says_its_score_so_far_then_the_fixed_closing():
    env, state, model = start("quiz", [{"question": "2+2?", "answer": "4"},
                                       {"question": "3+3?", "answer": "6"},
                                       {"question": "4+4?", "answer": "8"}])
    state["results"] = [{"question": "2+2?", "correct": True, "answered": True},
                        {"question": "3+3?", "correct": False, "answered": True}]
    result = limit(env, state)
    assert (result.label, result.reply) == ("closed", "[1 of 2] [close_turn_limit]")
    assert ("quiz_summary", 1, 2) in model.calls
    assert not state.get("completed")


def test_the_quiz_before_any_answer_leaves_the_fixed_closing():
    env, state, _ = start("quiz", [{"question": "2+2?", "answer": "4"}])
    assert limit(env, state).label == "none"


def test_the_procedure_says_how_the_steps_went_never_their_text():
    env, state, model = start("procedure", ["Boil the water", "Pour it", "Add the bag"])
    state["status"][0] = "done"
    state["status"][1] = "skipped"
    state["idx"] = 2
    result = limit(env, state)
    assert (result.label, result.reply) == ("closed", "[steps] [close_turn_limit]")
    assert ("procedure_summary", 1, 3, ["skipped", "not reached"]) in model.calls


def test_the_role_play_gives_its_evaluation_once_a_line_of_the_scene_was_said():
    env, state, model = start("roleplay", SCENE)
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="A coffee, please."))
    result = limit(env, state)
    assert (result.label, result.reply) == ("closed", "[feedback] [close_turn_limit]")
    assert ("roleplay_feedback", "limit") in model.calls


def test_the_role_play_before_any_line_leaves_the_fixed_closing():
    env, state, _ = start("roleplay", SCENE)
    assert limit(env, state).label == "none"


def test_a_strategy_without_the_hook_leaves_the_fixed_closing():
    env, state, _ = start("grounded_qa", "The museum opens at 10:00.")
    assert limit(env, state).label == "none"
