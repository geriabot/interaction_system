# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
LLM adapter: factory functions that return an infer_fn compatible
with LLMDialogClient's signature:
    (session_id: str, prompt: str, max_tokens: int|None, schema: dict|None) -> str|None

This replaces the ROS action client used in production.
"""

import os
from typing import Callable, Optional

InferFn = Callable[[str, str, Optional[int], Optional[dict]], Optional[str]]

# The reasoning tokens of a model that thinks (think true or a level: gpt-oss cannot switch it
# off) count toward num_predict: a cap of 30-80 tokens would be spent thinking and the answer
# would come back empty. Such a model gets this many tokens more; the same allowance as the
# production OllamaProvider, so that phase A, phase B and E1 ask alike.
REASONING_ALLOWANCE = 1024


def thinks(think) -> bool:
    """Whether a `think` value lets the model reason (True or a level such as "low")."""
    if isinstance(think, str):
        return think.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(think)


def num_predict(max_tokens: Optional[int], think) -> Optional[int]:
    """The num_predict of a request: the cap of the answer, plus the reasoning allowance for a
    model that thinks. None: no cap."""
    if not max_tokens or max_tokens <= 0:
        return None
    return max_tokens + (REASONING_ALLOWANCE if thinks(think) else 0)


class TransportError(RuntimeError):
    """The request did not reach the model or its answer did not come back (connection refused
    or cut, a timeout, an HTTP 5xx): not an answer of the model. Phase A does not count it as a
    call without an answer; the run that has one is not saved (run_models.py redoes it)."""


def make_gemini_infer_fn(
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
) -> InferFn:
    from google import genai
    from google.genai import types as gt

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("GEMINI_API_KEY not set. Export it or add api_key to config.yaml.")
    client = genai.Client(api_key=key)

    def infer(
        session_id: str,
        prompt: str,
        max_tokens: Optional[int],
        schema: Optional[dict],
    ) -> Optional[str]:
        kwargs: dict = {}
        if max_tokens and max_tokens > 0:
            kwargs["max_output_tokens"] = max_tokens
        if schema:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        try:
            kwargs["thinking_config"] = gt.ThinkingConfig(thinking_budget=0)
        except AttributeError:
            pass
        config = gt.GenerateContentConfig(**kwargs)
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[gt.Content(role="user", parts=[gt.Part.from_text(text=prompt)])],
                config=config,
            )
            return str(resp.text).strip() or None
        except Exception as exc:
            print(f"  [Gemini error] {exc}")
            return None

    return infer


def make_ollama_infer_fn(
    model: str = "qwen2.5",
    host: str = "127.0.0.1",
    port: int = 11434,
    think: bool | str = False,
    num_ctx: int | None = None,
) -> InferFn:
    """think: False (no reasoning, as the system), or a level ("low") for the models that cannot
    switch it off (they get REASONING_ALLOWANCE more tokens). num_ctx: the context window, when
    the run fixes it. A transport failure raises TransportError; an empty answer of the model,
    or an HTTP 4xx, returns None."""
    import json
    import requests

    url = f"http://{host}:{port}/api/generate"

    def infer(
        session_id: str,
        prompt: str,
        max_tokens: Optional[int],
        schema: Optional[dict],
    ) -> Optional[str]:
        payload: dict = {"model": model, "prompt": prompt, "stream": False, "think": think}
        options: dict = {}
        cap = num_predict(max_tokens, think)
        if cap:
            options["num_predict"] = cap
        if num_ctx:
            options["num_ctx"] = int(num_ctx)
        if schema:
            # Same rule as OllamaProvider: NLU calls run at temperature 0.
            payload["format"] = schema
            options["temperature"] = 0
        if options:
            payload["options"] = options
        try:
            r = requests.post(url, json=payload, timeout=120)
        except (requests.ConnectionError, requests.Timeout) as exc:
            print(f"  [Ollama transport error] {exc}")
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        if r.status_code >= 500:
            print(f"  [Ollama transport error] HTTP {r.status_code}")
            raise TransportError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            print(f"  [Ollama error] HTTP {r.status_code}: {r.text[:200]}")
            return None
        try:
            return (r.json().get("response") or "").strip() or None
        except ValueError as exc:  # the body was cut: it did not come back whole
            raise TransportError(f"unreadable answer: {exc}") from exc

    return infer


def make_claude_infer_fn(
    model: str = "claude-haiku-5-5",
    api_key: Optional[str] = None,
    effort: str = "low",
) -> InferFn:
    """Uses the production ClaudeProvider, so benchmarks see the same request
    shape (structured-output schema, effort, max_tokens floor) as the robot."""
    import sys

    llm_pkg = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ros2_llm_manager")
    )
    if llm_pkg not in sys.path:
        sys.path.insert(0, llm_pkg)
    from ros2_llm_manager.providers.claude_provider import ClaudeProvider

    provider = ClaudeProvider(
        api_key=api_key or "", timeout=120.0, default_model=model, effort=effort
    )
    ok, msg = provider.connect()
    if not ok:
        raise ValueError(msg)

    def infer(
        session_id: str,
        prompt: str,
        max_tokens: Optional[int],
        schema: Optional[dict],
    ) -> Optional[str]:
        result = provider.generate(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens or 0,
            format_schema=schema,
            cache_prefix_len=getattr(prompt, "cache_prefix_len", 0),
        )
        if not result.success:
            print(f"  [Claude error] {result.error}")
            return None
        return result.text or None

    return infer


def make_openai_infer_fn(
    model: str = "gpt-6-luna",
    api_key: Optional[str] = None,
    reasoning_effort: str = "none",
    base_url: str = "",
) -> InferFn:
    """Uses the production OpenAIProvider, so benchmarks see the same request
    shape (structured-output schema, reasoning effort) as the robot."""
    import sys

    llm_pkg = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "ros2_llm_manager")
    )
    if llm_pkg not in sys.path:
        sys.path.insert(0, llm_pkg)
    from ros2_llm_manager.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(
        api_key=api_key or "",
        timeout=120.0,
        default_model=model,
        reasoning_effort=reasoning_effort,
        base_url=base_url,
    )
    ok, msg = provider.connect()
    if not ok:
        raise ValueError(msg)

    def infer(
        session_id: str,
        prompt: str,
        max_tokens: Optional[int],
        schema: Optional[dict],
    ) -> Optional[str]:
        result = provider.generate(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens or 0,
            format_schema=schema,
        )
        if not result.success:
            print(f"  [OpenAI error] {result.error}")
            return None
        return result.text or None

    return infer


def make_infer_fn(cfg: dict) -> InferFn:
    provider = cfg.get("llm", {}).get("provider", "gemini")
    llm_cfg = cfg.get("llm", {})
    if provider == "gemini":
        return make_gemini_infer_fn(
            model=llm_cfg.get("model", "gemini-2.5-flash"),
            api_key=llm_cfg.get("api_key") or None,
        )
    if provider == "ollama":
        return make_ollama_infer_fn(
            model=llm_cfg.get("model", "qwen2.5"),
            host=llm_cfg.get("ollama_host", "127.0.0.1"),
            port=int(llm_cfg.get("ollama_port", 11434)),
            think=llm_cfg.get("think", False),
            num_ctx=llm_cfg.get("num_ctx"),
        )
    if provider == "claude":
        return make_claude_infer_fn(
            model=llm_cfg.get("model", "claude-haiku-5-5"),
            api_key=llm_cfg.get("api_key") or None,
            effort=llm_cfg.get("effort", "low"),
        )
    if provider == "openai":
        return make_openai_infer_fn(
            model=llm_cfg.get("model", "gpt-6-luna"),
            api_key=llm_cfg.get("api_key") or None,
            reasoning_effort=llm_cfg.get("reasoning_effort", "none"),
            base_url=llm_cfg.get("base_url", ""),
        )
    raise ValueError(
        f"Unknown provider '{provider}'. Use 'gemini', 'ollama', 'claude' or 'openai'."
    )
