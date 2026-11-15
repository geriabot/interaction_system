# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Role play: a garbled line was read as a line of the scene, used up the scene and was scored,
and a confused user never reached the breakdown; a breakdown after lines of the scene gave no
evaluation, while a silence did (E2 pre-run verification). A line that makes no sense is now
"unclear" (a request to repeat, not an exchange), and a breakdown after at least one line of the
scene closes with the evaluation (roleplay.breakdown_end)."""

import json
import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import _ROLEPLAY_ENDINGS, PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
SCENE = {"scenario": "The returns desk", "role": "The shop assistant",
         "criteria": ["Explains the fault", "Asks for a refund"], "max_exchanges": 6}


class Model:
    """A test double of the client: the reading of every turn and the scores it returns."""

    def __init__(self, intent="continue", scores=None):
        self.intent = intent
        self.scores = scores
        self.ended_by = None

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
        self.ended_by = ended_by
        return {"text": "[feedback]", "highlight": "", "improve": ""}


def start(model):
    env = SkillEnv(llm=model, ctx=DialogContext())
    state = {}
    goal = {"dialog_mode": "roleplay", "task_description": "Practise a return",
            "resources_json": json.dumps([{"name": "scene", "content": json.dumps(SCENE)}])}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def test_the_classifier_reads_a_line_without_sense_as_unclear():
    llm = LLMDialogClient("nlu", "nlg", lambda *a: '{"intent": "unclear"}',
                          PromptBuilder(prompts()))
    assert llm.classify_roleplay_turn("Lamp beneath forty.", SCENE, [], None) == "unclear"
    prompt, schema = PromptBuilder(prompts()).classify_roleplay_turn("x", SCENE, [])
    assert "unclear" in schema["properties"]["intent"]["enum"]
    assert '"unclear": the words make no sense' in prompt


def test_a_line_without_sense_is_no_exchange():
    env, state = start(Model(intent="unclear"))
    result = get_skill("roleplay.classify")(env, state, SkillInput(user_text="Lamp forty."))
    assert result.label == "unclear" and state["exchanges"] == 0 and state["history"] == []


def test_a_breakdown_after_a_line_of_the_scene_gives_the_evaluation():
    model = Model(intent="continue", scores=SCENE["criteria"])
    env, state = start(model)
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="These shoes are too small."))
    result = get_skill("roleplay.breakdown_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("closed", "[feedback]")
    assert state["ended_by"] == "breakdown" and model.ended_by == "breakdown"
    assert get_mode("roleplay").outcome(state) == ("completed", "")
    assert "breakdown" in _ROLEPLAY_ENDINGS


def test_a_breakdown_before_any_line_of_the_scene_has_no_evaluation():
    env, state = start(Model(scores=SCENE["criteria"]))
    result = get_skill("roleplay.breakdown_end")(env, state, SkillInput())
    assert (result.label, result.reply) == ("none", "")
    assert not state.get("completed")


def test_a_breakdown_whose_evaluation_fails_ends_as_a_breakdown():
    env, state = start(Model(intent="continue", scores=None))
    get_skill("roleplay.classify")(env, state, SkillInput(user_text="These shoes are too small."))
    assert get_skill("roleplay.breakdown_end")(env, state, SkillInput()).label == "none"
    assert state["completed"] is False
