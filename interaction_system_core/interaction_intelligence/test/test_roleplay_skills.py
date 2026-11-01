# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The role play's skills when the model fails: a turn it cannot read is "unclear" (a request
to repeat, kept out of the scene, not an exchange: a turn without progress for the tree), and a
scene whose evaluation could not score every criterion ends as partial, with the criteria not
scored as the reason."""

import json

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill

SCENE = {"scenario": "The returns desk", "role": "The shop assistant",
         "criteria": ["Explains the fault", "Asks for a refund"], "max_exchanges": 4}


class Model:
    """A test double of the client: the reading of every turn and the scores it returns."""

    def __init__(self, intent="continue", scores=None):
        self.intent = intent
        self.scores = scores

    def phrase(self, key):
        return f"[{key}]"

    def classify_roleplay_turn(self, user_text, scene, history, ctx=None):
        return self.intent

    def roleplay_reply(self, scene, history, ctx=None):
        return "[character]"

    def evaluate_roleplay(self, scene, history, ctx=None):
        if self.scores is None:
            return {}
        return {"scores": [{"criterion": c, "score": 3, "evidence": "", "comment": ""}
                           for c in self.scores]}

    def roleplay_feedback(self, scene, evaluation, ended_by, ctx=None):
        return {"text": "[feedback]", "highlight": "", "improve": ""}


def start(model):
    env = SkillEnv(llm=model, ctx=DialogContext())
    state = {}
    goal = {"dialog_mode": "roleplay", "task_description": "Practise a return",
            "resources_json": json.dumps([{"name": "scene", "content": json.dumps(SCENE)}])}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def test_an_unclear_turn_asks_to_repeat_and_is_no_exchange():
    env, state = start(Model(intent="unclear"))
    result = get_skill("roleplay.classify")(env, state, SkillInput(user_text="It doesn't heat."))
    assert (result.label, result.reply) == ("unclear", "[unclear]")
    assert state["history"] == [] and state["exchanges"] == 0


def test_a_line_of_the_scene_is_an_exchange():
    env, state = start(Model(intent="continue"))
    result = get_skill("roleplay.classify")(env, state, SkillInput(user_text="It doesn't heat."))
    assert result.label == "continue" and state["exchanges"] == 1
    assert state["history"] == [{"role": "user", "content": "It doesn't heat."}]


def test_every_criterion_scored_is_completed():
    env, state = start(Model(scores=SCENE["criteria"]))
    get_skill("roleplay.finish")(env, state, SkillInput(option="user"))
    assert get_mode("roleplay").outcome(state) == ("completed", "")


def test_criteria_not_scored_are_partial():
    env, state = start(Model(scores=["Explains the fault"]))
    get_skill("roleplay.finish")(env, state, SkillInput(option="limit"))
    assert get_mode("roleplay").outcome(state) == ("partial",
                                                   "Criteria not scored: Asks for a refund")
    env, state = start(Model(scores=None))
    get_skill("roleplay.finish")(env, state, SkillInput(option="limit"))
    outcome, reason = get_mode("roleplay").outcome(state)
    assert outcome == "partial" and reason.endswith("Explains the fault; Asks for a refund")
