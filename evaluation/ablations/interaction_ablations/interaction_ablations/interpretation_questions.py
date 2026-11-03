# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The question of the LLM-led baseline (the monolithic ablation) for behavior_architecture's
interpretation server, loaded with the interaction system's question module
(question_modules: ["interaction_intelligence.interpretation_questions",
"interaction_ablations.interpretation_questions"]); its definition is
config/interpretation/questions.yaml of this package.

monolithic_turn: the whole strategy in one prompt (skills.PROMPT), filled with the values that
the skill sends in the context ({"fields": skills.prompt_fields(...)}), with the answer's
schema (skills.SCHEMA). The answer is the model's text ({"text": ...}), which the skill reads.
The cancellation ablation that asks the model alone (cancel_check_llm_only) asks the system's
own question detect_cancel_intent.
"""

from behavior_interpretation import Question
from behavior_interpretation.questions import RequestError

from .skills import PROMPT, SCHEMA

FIELDS = ("task", "domain", "rules", "language", "address", "material", "history", "now")


class MonolithicTurn(Question):

    name = "monolithic_turn"

    def build_prompt(self, context, text):
        fields = context.get("fields")
        if not isinstance(fields, dict) or set(fields) != set(FIELDS):
            raise RequestError(f"monolithic_turn: the context needs fields {list(FIELDS)}")
        return PROMPT.format(**fields)

    def schema(self, context):
        return SCHEMA

    def parse(self, text):
        """The model's text as it is: the skill reads it."""
        return {"text": "" if text is None else str(text)}
