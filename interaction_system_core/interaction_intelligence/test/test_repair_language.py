# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The explanation said again after a wrong teach-back is said in the conversation's language.
In the simulated twin of E3 (Spanish) the small model sometimes answered this English prompt in
English ("You said you can take a game for two weeks, but..."): such a reply is asked once more
with the language stressed, and then replaced by the key points themselves, which are written
in the conversation's language."""

import os

import yaml

from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.utils import in_english_by_mistake

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
ENGLISH = ("You said you can take a game for two weeks, but you can only take it for one week. "
           "You didn't mention that if you return it late you lose a month.")
SPANISH = ("Dijiste que puedes llevarte el juego dos semanas, pero es solo una semana. Si lo "
           "devuelves tarde, no podrás llevarte otro durante quince días.")
POINTS = [{"point": "Cada juego se lleva una semana", "status": "wrong",
           "said": "dos semanas"},
          {"point": "Si lo devuelves tarde, quince días sin juegos", "status": "missing",
           "said": ""}]


def prompts():
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers, language="Spanish"):
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(prompts()),
                           language=language), calls


def test_english_line_in_another_language_detected():
    assert in_english_by_mistake(ENGLISH, "Spanish")
    assert not in_english_by_mistake(SPANISH, "Spanish")
    # In English, an English line is what is wanted
    assert not in_english_by_mistake(ENGLISH, "English")
    # A Spanish line with an English name or a loan word is still Spanish
    assert not in_english_by_mistake(
        "Puedes llevarte el juego The Mind una semana, y es free para socios.", "Spanish")
    assert not in_english_by_mistake("", "Spanish")


def test_spanish_repair_kept():
    llm, calls = client([SPANISH])
    assert llm.repair_explanation("Préstamo de juegos", POINTS, [], None) == SPANISH
    assert len(calls) == 1


def test_english_repair_asked_again_in_the_language():
    llm, calls = client([ENGLISH, SPANISH])
    assert llm.repair_explanation("Préstamo de juegos", POINTS, [], None) == SPANISH
    assert len(calls) == 2
    assert "Write the whole reply in Spanish, never in English" in calls[1]


def test_english_twice_gives_the_points_themselves():
    llm, calls = client([ENGLISH, ENGLISH])
    reply = llm.repair_explanation("Préstamo de juegos", POINTS, [], None)
    assert reply == " ".join(p["point"] for p in POINTS)
    assert len(calls) == 2


def test_english_conversation_never_asked_again():
    llm, calls = client([ENGLISH], language="English")
    assert llm.repair_explanation("Board games", POINTS, [], None) == ENGLISH
    assert len(calls) == 1


def test_the_language_is_named_only_outside_english():
    import os
    import yaml
    from interaction_intelligence.prompt_builder import PromptBuilder
    path = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
    with open(path) as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]
    english = " ".join(str(PromptBuilder(prompts, "English").repair_explanation(
        "Board games", POINTS, [])).split())
    spanish = " ".join(str(PromptBuilder(prompts, "Spanish").repair_explanation(
        "Juegos", POINTS, [])).split())
    assert '("you said X, but it is Y").' in english and "said in English" not in english
    assert '("you said X, but it is Y", said in Spanish).' in spanish
