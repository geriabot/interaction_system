# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The two transports of LLMDialogClient's calls to the model, without ROS, for the comparisons:
the infer function that the interaction intelligence gives the client, (session, prompt,
max_tokens, schema, think=False) -> text | None, bounded by the client's limits (limits.wait,
the time the client allows the call in progress).

  Gateway         the former path: the goal of /llm/inference as intelligence_node._infer_sync
                  sent it, and llm_manager_node._on_inference's handling of it (a session
                  without system prompt or history, the schema read back from JSON, streaming),
                  with the gateway's provider; the call returns nothing once its wait is over,
                  and the generation is cancelled, as _action_call_sync did.
  Interpretation  the current path: the Interpret request that intelligence_node._interpret_sync
                  sends (interpretation_context.interpret_request: the question and context of
                  the marked prompt, the wait as the budget, the token cap, the reasoning
                  variant), answered by an InterpretationServer; nothing once the wait is over,
                  as _call_sync.

Only the ROS hop is left out (a goal or a service call); latency.py measures it.
"""

import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
INTELLIGENCE = os.path.join(REPO, "interaction_system_core", "interaction_intelligence")
ABLATIONS = os.path.join(REPO, "evaluation", "ablations", "interaction_ablations")
PROMPTS = os.path.join(INTELLIGENCE, "config", "prompts.yaml")
QUESTIONS_FILE = os.path.join(INTELLIGENCE, "config", "interpretation", "questions.yaml")
ABLATION_QUESTIONS_FILE = os.path.join(ABLATIONS, "config", "interpretation", "questions.yaml")
MODEL = {"ollama": "qwen2.5:7b", "openai": "gpt-6-luna"}


def use_sources(src: str | None) -> None:
    """Import the interaction intelligence, its ablations and ros2_llm_manager from this
    checkout (default: the one that holds this file)."""
    root = os.path.abspath(src) if src else REPO
    for path in (os.path.join(root, "ros2_llm_manager"),
                 os.path.join(root, "evaluation", "ablations", "interaction_ablations"),
                 os.path.join(root, "interaction_system_core", "interaction_intelligence"),
                 HERE):
        if path not in sys.path:
            sys.path.insert(0, path)


_TEMPLATES: dict = {}


def templates() -> dict:
    """The prompt templates of prompts.yaml (read once)."""
    if not _TEMPLATES:
        import yaml
        with open(PROMPTS, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _TEMPLATES.update(data["interaction_intelligence"]["ros__parameters"]["prompts"])
    return dict(_TEMPLATES)


def _wait(fn, timeout: float | None, on_timeout=None):
    """fn() in a thread, waited for at most `timeout` seconds (None: no limit); None after."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: B902 (a failed call is no answer, as in the node)
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(None if timeout is None else max(timeout, 0.0))
    if thread.is_alive():
        if on_timeout:
            on_timeout()
        return None
    if "error" in box:
        return None
    return box.get("value")


# ==== THE GATEWAY ====


def gateway_provider(backend: str, url: str, timeout: float = 120.0):
    if backend == "ollama":
        from ros2_llm_manager.providers.ollama_provider import OllamaProvider
        host, port = url.rsplit("//", 1)[1].split(":")
        provider = OllamaProvider(host=host, port=int(port), timeout=timeout,
                                  default_model=MODEL[backend], think=False, num_ctx=8192)
    else:
        from ros2_llm_manager.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider(api_key="unused", timeout=timeout,
                                  default_model=MODEL[backend], reasoning_effort="none",
                                  base_url=url + "/v1")
    ok, message = provider.connect()
    if not ok:
        raise RuntimeError(f"The gateway's {backend} provider did not connect: {message}")
    return provider


class Gateway:
    """The client's call to the model through the gateway."""

    def __init__(self, provider, model: str, limits=lambda: None):
        self.provider = provider
        self.model = model
        self.limits = limits

    def __call__(self, session_id, prompt, max_tokens, schema, think=False):
        if not prompt or not prompt.strip():
            return None
        limits = self.limits()
        wait = getattr(limits, "wait", None)
        # The goal (ros2_llm_interfaces/action/LlmInference)
        goal_prompt = str(prompt)
        goal_schema = json.dumps(schema) if schema else ""
        goal_think = "true" if think else ""
        cancelled = threading.Event()

        def generate():
            # The manager: the session's messages, the schema read back, streaming
            format_schema = json.loads(goal_schema) if goal_schema.strip() else None
            result = self.provider.generate(
                model=self.model, messages=[{"role": "user", "content": goal_prompt}],
                max_tokens=max_tokens or 0, format_schema=format_schema,
                stream_callback=lambda token: not cancelled.is_set(),
                cache_prefix_len=getattr(prompt, "cache_prefix_len", 0), think=goal_think)
            return result.text if result.success else None

        return _wait(generate, wait, cancelled.set)


# ==== INTERPRETATION ====


def interpretation_server(backend: str, url: str, timeout: float = 120.0, log_dir: str = ""):
    """The interpretation server with the system's questions and the ablation's, on routes of
    the llm_manager dialect (the reasoning route: think on)."""
    import yaml
    from behavior_interpretation import InterpretationServer
    with open(ABLATION_QUESTIONS_FILE, encoding="utf-8") as f:
        ablation_questions = yaml.safe_load(f)["questions"]
    if backend == "ollama":
        route = {"backend": "ollama", "dialect": "llm_manager", "url": url,
                 "model": MODEL[backend], "num_ctx": 8192, "keep_alive": -1, "think": False,
                 "timeout_s": timeout}
        reasoning = dict(route, think=True)
    else:
        route = {"backend": "openai", "dialect": "llm_manager", "url": url + "/v1",
                 "model": MODEL[backend], "reasoning_effort": "none", "timeout_s": timeout}
        reasoning = dict(route)
    return InterpretationServer(
        questions_file=QUESTIONS_FILE, questions=ablation_questions, templates_file=PROMPTS,
        question_modules=["interaction_intelligence.interpretation_questions",
                          "interaction_ablations.interpretation_questions"],
        routes={"dialogue": route, "reasoning": reasoning}, default_route="dialogue",
        log_dir=log_dir)


class Interpretation:
    """The client's call to the model through the interpretation server."""

    def __init__(self, server, request_id: str = "dialogue", limits=lambda: None):
        self.server = server
        self.request_id = request_id
        self.limits = limits

    def __call__(self, session_id, prompt, max_tokens, schema, think=False):
        from interaction_intelligence.interpretation_context import (answer_text,
                                                                     interpret_request)
        if not prompt or not prompt.strip():
            return None
        wait = getattr(self.limits(), "wait", None)
        fields = interpret_request(prompt, self.request_id, max_tokens, think, wait)
        # As the node: no cancel at the end of the wait (the server cuts the call at its budget)
        result = _wait(lambda: self.server.interpret(fields), wait)
        if result is None or not result.success:
            return None
        return answer_text(result.success, result.answer, result.source)


def client(path: str, transport_of, language: str, reasoning: str = "none", limits=None):
    """An LLMDialogClient on one path: transport_of(limits getter) gives its infer function."""
    from interaction_intelligence.llm_client import CallLimits, LLMDialogClient
    from interaction_intelligence.prompt_builder import PromptBuilder
    if path == "interpretation":
        from interaction_intelligence.interpretation_context import InterpretationPromptBuilder
        builder = InterpretationPromptBuilder(templates(), language=language)
    else:
        builder = PromptBuilder(templates(), language=language)
    holder: dict = {}
    llm = LLMDialogClient("nlu", "nlg", transport_of(lambda: holder.get("llm").limits),
                          builder, language=language, reasoning=reasoning,
                          limits=limits or CallLimits())
    holder["llm"] = llm
    return llm
