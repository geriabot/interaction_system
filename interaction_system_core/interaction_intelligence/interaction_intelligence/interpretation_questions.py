# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The question module of the interaction system for behavior_architecture's interpretation
server (its parameter question_modules: ["interaction_intelligence.interpretation_questions"]).

Each question of config/interpretation/questions.yaml is answered with the class
DialogueQuestion: the prompt and the schema are those that PromptBuilder writes, from the
arguments that the client sends in the context (interpretation_context.py) and the templates
of the server's templates_file (the system's prompts.yaml), so that the model receives the
same request as through the former gateway. A spoken line (a question without a schema) is
sent without a schema, as the gateway sent it.
"""

from behavior_interpretation import Question
from behavior_interpretation.questions import RequestError

from .interpretation_context import (QUESTIONS, ContextError, build_prompt, decode_context,
                                     max_tokens_of, prompt_templates)


class DialogueQuestion(Question):
    """A question of LLMDialogClient: its prompt and schema written by PromptBuilder."""

    def _built(self, context):
        """(prompt, schema) of this call, built once per call (the server makes one Question
        per call)."""
        cached = getattr(self, "_built_for", None)
        if cached is not None and cached[0] is context:
            return cached[1]
        if self.name not in QUESTIONS:
            raise RequestError(f"{self.name}: not a question of the interaction system")
        try:
            decoded = decode_context(context)
            built = build_prompt(self.name, prompt_templates(self.templates), decoded)
        except (ContextError, KeyError) as exc:
            raise RequestError(f"{self.name}: {exc}") from exc
        # The token cap of this call (judge_teach_back's depends on the number of key points)
        declared = int(self.spec.get("max_tokens") or 0)
        self.spec["max_tokens"] = max_tokens_of(self.name, decoded["args"], declared)
        self._built_for = (context, built)
        return built

    def build_prompt(self, context, text):
        return self._built(context)[0]

    def schema(self, context):
        # A spoken line has no schema: {} makes the backend send none (not None, which would
        # make the server build one from the fields)
        schema = self._built(context)[1]
        return {} if schema is None else schema
