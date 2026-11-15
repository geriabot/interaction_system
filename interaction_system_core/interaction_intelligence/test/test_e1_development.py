# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The fixes found in the development run of E1: the role play closed with its evaluation when
the user leaves the scene in silence."""

import json

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill


SCENE = {"scenario": "A café", "role": "The waiter", "criteria": ["Orders", "Is polite"],
         "max_exchanges": 6}


class Model:
    """A test double of the client for the role play."""

    def __init__(self, scores=True):
        self.scores = scores
        self.ended_by = None

    def phrase(self, key):
        return f"[{key}]"

    def classify_roleplay_turn(self, user_text, scene, history, ctx=None):
        return "continue"

    def roleplay_reply(self, scene, history, ctx=None):
        return "[character]"

    def evaluate_roleplay(self, scene, history, ctx=None):
        if not self.scores:
            return {}
        return {"scores": [{"criterion": c, "score": 3, "evidence": "", "comment": ""}
                           for c in SCENE["criteria"]]}

    def roleplay_feedback(self, scene, evaluation, ended_by, ctx=None):
        self.ended_by = ended_by
        return {"text": "[feedback]", "highlight": "", "improve": ""}


def start(model, mode="roleplay"):
    env = SkillEnv(llm=model, ctx=DialogContext())
    state = {}
    goal = {"dialog_mode": mode, "task_description": "Practise ordering",
            "resources_json": json.dumps([{"name": "scene", "content": json.dumps(SCENE)}])}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def test_a_silent_leave_after_a_line_of_the_scene_gets_the_evaluation():
    model = Model()
    env, state = start(model)
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="A coffee."))
    result = get_skill("silent_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("closed", "[feedback]")
    assert model.ended_by == "silence"
    assert get_mode("roleplay").outcome(state) == ("completed", "")


def test_a_silent_leave_before_any_line_is_the_silent_ending():
    env, state = start(Model())
    result = get_skill("silent_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("none", "")
    assert not state.get("completed")


def test_a_silent_leave_without_scores_is_the_silent_ending():
    env, state = start(Model(scores=False))
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="A coffee."))
    result = get_skill("silent_end")(env, state, SkillInput())
    assert result.label == "none" and not state.get("completed")
