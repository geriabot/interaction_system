# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
One JSON answer from an LLM, for the simulated user and the judge of the experiments: an
Ollama model or a cloud one (Gemini, OpenAI), through their HTTP APIs (no SDK).

Cloud calls are paid: they are refused unless allowed explicitly. The API keys are read from
the environment, GEMINI_API_KEY (or GOOGLE_API_KEY) and OPENAI_API_KEY, and travel in a header,
never in a URL or a log. A call that fails with a rate limit or a server error is retried.
"""

import os
import time

import requests

PROVIDERS = ("ollama", "gemini", "openai")
CLOUD_PROVIDERS = ("gemini", "openai")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# The pilot's free stand-ins (doc/PILOT.md): the base URL of a local server that answers in the
# provider's own REST shape (the fake Gemini server of scripts/pilot_standin.py; Ollama's
# OpenAI-compatible endpoint), so that the pilot exercises this client's cloud code paths without
# a paid call. Unset (always, outside the pilot): the providers' URLs. Only this computer is
# accepted, and run_experiment.py refuses them in a file that is not a pilot.
PILOT_ENV = {"gemini": "INTERACTION_PILOT_GEMINI_URL", "openai": "INTERACTION_PILOT_OPENAI_URL"}
RETRIES = 4
PRO_MIN_THINKING = 128  # the smallest thinking budget Gemini 2.5 Pro accepts
# A Gemini model that must think (Gemini 2.5 Pro, every Gemini 3: their reasoning cannot be
# switched off) spends its thinking tokens out of maxOutputTokens: they get this many more, or a
# small cap would be eaten by the thinking and the answer would come back empty (and billed)
GEMINI_REASONING_ALLOWANCE = 2048
# The context window of an Ollama request (the same as the system's provider and phase A of E0a;
# the server's default depends on its version, and a longer prompt is cut silently)
DEFAULT_NUM_CTX = 8192


class CloudNotAllowed(RuntimeError):
    pass


class HttpError(RuntimeError):
    """A cloud call answered with an HTTP error (after the retries of a 429 or a 5xx)."""

    def __init__(self, status: int, text: str):
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


class NoAnswer(RuntimeError):
    """The call was answered (and billed) without any text: e.g. the output cap spent in
    thinking (finishReason MAX_TOKENS) or a blocked prompt. Worth asking again; usage: what it
    cost."""

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


def standin_base(provider: str) -> str:
    """The base URL of the pilot's stand-in for a cloud provider ('' when none: the real API).
    ValueError for one that is not on this computer."""
    from urllib.parse import urlparse
    base = os.environ.get(PILOT_ENV.get(provider, ""), "").rstrip("/")
    if base and urlparse(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"{PILOT_ENV[provider]} must point to this computer, not {base}")
    return base


def provider_url(provider: str, model: str = "") -> str:
    """The URL of a call to a cloud provider: its API, or the pilot's stand-in."""
    base = standin_base(provider)
    if provider == "gemini":
        return (f"{base}/v1beta/models/{model}:generateContent" if base
                else GEMINI_URL.format(model=model))
    return f"{base}/chat/completions" if base else OPENAI_URL


def check_provider(provider: str, allow_cloud: bool) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}': {PROVIDERS}")
    if provider in CLOUD_PROVIDERS and not allow_cloud:
        raise CloudNotAllowed(f"{provider} is paid: allow the cloud calls explicitly")


def gemini_schema(schema: dict) -> dict:
    """The JSON schema in the form of Gemini's responseSchema (OpenAPI types)."""
    out = {k: v for k, v in schema.items()
           if k not in ("type", "properties", "items", "additionalProperties")}
    out["type"] = schema["type"].upper()
    if "properties" in schema:
        out["properties"] = {k: gemini_schema(v) for k, v in schema["properties"].items()}
    if "items" in schema:
        out["items"] = gemini_schema(schema["items"])
    return out


def plain_schema(schema: dict) -> dict:
    """The JSON schema without Gemini's propertyOrdering, for the other providers (Ollama and
    OpenAI generate the properties in their order already)."""
    out = {k: v for k, v in schema.items() if k != "propertyOrdering"}
    if "properties" in schema:
        out["properties"] = {k: plain_schema(v) for k, v in schema["properties"].items()}
    if "items" in schema:
        out["items"] = plain_schema(schema["items"])
    return out


def strict_schema(schema: dict) -> dict:
    """The JSON schema as OpenAI's strict mode wants it: no extra properties, all required."""
    out = dict(schema)
    if schema.get("type") == "object":
        out["additionalProperties"] = False
        out["properties"] = {k: strict_schema(v) for k, v in schema["properties"].items()}
        out["required"] = list(schema["properties"])
    if "items" in schema:
        out["items"] = strict_schema(schema["items"])
    return out


def gemini_thinking(model: str, thinking: bool) -> tuple[dict | None, bool]:
    """(thinkingConfig, whether the model will think) of a Gemini request. Gemini 3 takes a
    thinking level (it cannot switch its reasoning off: "low" is its least) and refuses a level
    and a budget together; Gemini 2.5 takes a budget (0: off; Pro cannot go below 128).
    thinking: let the model reason at its own default (no config)."""
    if thinking:
        return None, True
    if model.startswith("gemini-3"):
        return {"thinkingLevel": "low"}, True
    if "-pro" in model:
        return {"thinkingBudget": PRO_MIN_THINKING}, True
    return {"thinkingBudget": 0}, False


def ask_json(provider: str, model: str, prompt: str, schema: dict, *,
             temperature: float | None = 0.0, seed: int = 0, max_tokens: int = 200,
             thinking: bool = False, host: str = "http://localhost:11434",
             on_gpu: bool = True, num_ctx: int = DEFAULT_NUM_CTX) -> tuple[str, dict]:
    """The model's answer (JSON text) to the prompt, and the metadata of the call: usage
    (prompt and completion tokens) and the model version that answered (cloud).
    temperature None: the provider's default (OpenAI's reasoning models accept no other).
    thinking: let the model reason before answering (Gemini; paid tokens). num_ctx: the context
    window of an Ollama request. Raises HttpError (an HTTP error of a cloud API) or NoAnswer (a
    Gemini answer without text)."""
    if provider != "gemini":
        schema = plain_schema(schema)
    if provider == "ollama":
        options = {"seed": seed, "num_predict": max_tokens}
        if num_ctx:
            options["num_ctx"] = int(num_ctx)
        if temperature is not None:
            options["temperature"] = temperature
        if not on_gpu:
            options["num_gpu"] = 0
        r = requests.post(f"{host}/api/generate", timeout=300, json={
            "model": model, "prompt": prompt, "stream": False, "format": schema,
            "think": thinking, "options": options})
        r.raise_for_status()
        body = r.json()
        return body.get("response", ""), {"usage": {
            "prompt_tokens": body.get("prompt_eval_count", 0),
            "completion_tokens": body.get("eval_count", 0)}}

    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            raise RuntimeError("No Gemini API key: set GEMINI_API_KEY or GOOGLE_API_KEY")
        thinking_config, thinks = gemini_thinking(model, thinking)
        config = {"seed": seed,
                  "maxOutputTokens": max_tokens + (GEMINI_REASONING_ALLOWANCE if thinks else 0),
                  "responseMimeType": "application/json",
                  "responseSchema": gemini_schema(schema)}
        if temperature is not None:
            config["temperature"] = temperature
        if thinking_config:
            config["thinkingConfig"] = thinking_config
        body = _post(provider_url("gemini", model),
                     {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": config},
                     {"x-goog-api-key": key, "Content-Type": "application/json"})
        usage = body.get("usageMetadata", {})
        meta = {"usage": {"prompt_tokens": usage.get("promptTokenCount", 0),
                          "completion_tokens": usage.get("candidatesTokenCount", 0)
                          + usage.get("thoughtsTokenCount", 0)},
                "model_version": body.get("modelVersion", "")}
        candidate = (body.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            reason = candidate.get("finishReason") or (body.get("promptFeedback") or {}).get(
                "blockReason") or "no candidate"
            raise NoAnswer(f"no answer from {model} (finishReason {reason})", meta["usage"])
        return text, meta

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("No OpenAI API key: set OPENAI_API_KEY")
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "seed": seed, "max_completion_tokens": max_tokens,
               "response_format": {"type": "json_schema", "json_schema": {
                   "name": "answer", "strict": True, "schema": strict_schema(schema)}}}
    if temperature is not None:
        payload["temperature"] = temperature
    body = _post(provider_url("openai"), payload,
                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    usage = body.get("usage", {})
    return body["choices"][0]["message"].get("content") or "", {
        "usage": {"prompt_tokens": usage.get("prompt_tokens", 0),
                  "completion_tokens": usage.get("completion_tokens", 0)},
        "model_version": body.get("model", "")}


def _post(url: str, payload: dict, headers: dict) -> dict:
    for attempt in range(RETRIES + 1):
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if (r.status_code == 429 or r.status_code >= 500) and attempt < RETRIES:
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code >= 400:
            # The body explains the error (the key is in a header, not in it)
            raise HttpError(r.status_code, r.text[:300])
        return r.json()
    raise RuntimeError("unreachable")
