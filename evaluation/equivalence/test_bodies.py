# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Criterion 3: the body that reaches the model's server is the same through the former gateway
and through the interpretation server, for every question of the corpus (bodies.py), in English
and Spanish. Ollama: byte for byte. OpenAI-compatible APIs: the same JSON (the openai SDK orders
the keys of its body in its own way, which the server's llm_manager dialect does not reproduce).
"""

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bodies  # noqa: E402

bodies.use_sources(None)
pytest.importorskip("behavior_interpretation")
pytest.importorskip("httpx")


@pytest.mark.parametrize("language", bodies.LANGUAGES)
def test_ollama_bodies_are_identical(language):
    gateway = bodies.run("gateway", "ollama", language)
    interpretation = bodies.run("interpretation", "ollama", language)
    summary = bodies.compare(gateway, interpretation)
    from interaction_intelligence.interpretation_context import QUESTIONS
    assert set(summary) == set(QUESTIONS) | {"monolithic_turn"}
    for question, s in summary.items():
        assert s["calls"] and s["identical"] == s["calls"], (question, s)


@pytest.mark.parametrize("language", bodies.LANGUAGES)
def test_openai_bodies_have_the_same_content(language):
    pytest.importorskip("openai")
    gateway = bodies.run("gateway", "openai", language)
    interpretation = bodies.run("interpretation", "openai", language)
    summary = bodies.compare(gateway, interpretation)
    for question, s in summary.items():
        assert s["calls"] and s["same_json"] == s["calls"] and not s["missing"], (question, s)


def test_the_comparison_sees_a_difference():
    a = [{"case": "c", "question": "q", "reasoning": "none", "n": 0,
          "body": base64.b64encode(json.dumps({"a": 1, "b": 2}).encode()).decode()}]
    b = [dict(a[0], body=base64.b64encode(json.dumps({"b": 2, "a": 1}).encode()).decode())]
    c = [dict(a[0], body=base64.b64encode(b'{"a": 2}').decode())]
    assert bodies.compare(a, a)["q"]["identical"] == 1
    assert bodies.compare(a, b)["q"] == {"calls": 1, "identical": 0, "same_json": 1,
                                         "differ": ["none/c/0"], "missing": []}
    assert bodies.compare(a, c)["q"]["same_json"] == 0
    assert bodies.compare(a, [])["q"]["missing"] == ["none/c/0"]
