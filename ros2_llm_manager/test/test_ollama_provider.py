# Copyright 2026 Álvaro Quintana Camacho
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Unit tests for OllamaProvider request payloads, against a fake HTTP client."""

from types import SimpleNamespace

import pytest

from ros2_llm_manager.providers.ollama_provider import (
    REASONING_ALLOWANCE, OllamaProvider, normalize_think)


class FakeHttp:
    def __init__(self):
        self.payloads = []

    def post(self, path, json, timeout=None):
        self.payloads.append(json)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"message": {"content": "ok"}, "prompt_eval_count": 3, "eval_count": 1},
        )


@pytest.mark.parametrize("think", [False, True])
def test_think_flag_is_sent(think):
    p = OllamaProvider("127.0.0.1", 11434, 5.0, "qwen3.5:9b", think=think)
    p._http = FakeHttp()
    r = p.generate("qwen3.5:9b", [{"role": "user", "content": "x"}], max_tokens=50)
    assert r.success and r.text == "ok"
    payload = p._http.payloads[0]
    assert payload["think"] is think
    # A model that may think gets room for its reasoning tokens; every request has the context
    assert payload["options"] == {"num_predict": 50 + (REASONING_ALLOWANCE if think else 0),
                                  "num_ctx": 8192}


def test_thinking_is_off_by_default():
    p = OllamaProvider("127.0.0.1", 11434, 5.0, "qwen3.5:9b")
    p._http = FakeHttp()
    p.generate("qwen3.5:9b", [{"role": "user", "content": "x"}])
    assert p._http.payloads[0]["think"] is False


def test_schema_calls_run_at_temperature_zero():
    p = OllamaProvider("127.0.0.1", 11434, 5.0, "llama3.2:3b")
    p._http = FakeHttp()
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}}
    p.generate("llama3.2:3b", [{"role": "user", "content": "x"}], max_tokens=50, format_schema=schema)
    payload = p._http.payloads[0]
    assert payload["format"] == schema
    assert payload["options"] == {"num_predict": 50, "num_ctx": 8192, "temperature": 0}


def test_spoken_replies_keep_the_model_temperature():
    p = OllamaProvider("127.0.0.1", 11434, 5.0, "llama3.2:3b")
    p._http = FakeHttp()
    p.generate("llama3.2:3b", [{"role": "user", "content": "x"}])
    assert p._http.payloads[0]["options"] == {"num_ctx": 8192}


def test_think_can_be_a_level():
    # gpt-oss cannot switch its reasoning off: the evaluation runs it with "low", as phase A
    p = OllamaProvider("127.0.0.1", 11434, 5.0, "gpt-oss:20b", think="low", num_ctx=4096)
    p._http = FakeHttp()
    p.generate("gpt-oss:20b", [{"role": "user", "content": "x"}], max_tokens=30)
    payload = p._http.payloads[0]
    assert payload["think"] == "low"
    assert payload["options"] == {"num_predict": 30 + REASONING_ALLOWANCE, "num_ctx": 4096}
    assert normalize_think("false") is False and normalize_think("True") is True
    assert normalize_think("") is False and normalize_think(" High ") == "high"


def test_the_model_is_loaded_with_the_context_of_the_requests():
    p = OllamaProvider("127.0.0.1", 11434, 5.0, "qwen3:8b")
    p._http = FakeHttp()
    assert p.ensure_model_ready("qwen3:8b")[0]
    assert p._http.payloads[0] == {"model": "qwen3:8b", "prompt": "", "keep_alive": -1,
                                   "options": {"num_ctx": 8192}}
