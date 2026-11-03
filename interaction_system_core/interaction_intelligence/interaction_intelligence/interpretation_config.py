# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The parameters of the interaction system's interpretation server, written by its launch file
from the system's parameter files.

The model of the questions is configured as it was for the former LLM gateway: the section
`llm_manager_node` of the parameter files (the model binding: default_provider, default_model
and the options of each provider: ollama, openai, gemini, claude) and the intelligence's
llm_provider, llm_model and routes ("question=model"). From them the server gets the routes
that send each question the request the gateway sent:

  dialogue            the model of the questions (llm_provider/llm_model, else the binding's)
  reasoning           the same model with think on (Ollama): the variant `reasoning` of the
                      questions on which the model may reason
  model_<i>, model_<i>_reasoning   the same for each model of the intelligence's routes; the
                      questions routed to it get those routes (and their variant, the second)

The questions are the package's (config/interpretation/questions.yaml); when the intelligence
loads the LLM-led baseline's skills (interaction_ablations.skills), the baseline's question too.
When the questions change (routes, the baseline), they are written to a file of their own.
The templates are prompts.yaml and the prompts.* of every parameter file that has them (the
Strategies editor's), in the order the intelligence reads them.

An API key is never a parameter of the server: each backend reads it from its environment
variable (OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY). A key given in the binding
(<provider>.api_key, as the GUI writes it) is put in that variable of the server's environment
(server_environment), as the gateway took it from its parameter.
"""

import copy
import os
import tempfile

import yaml

from .interpretation_context import CLIENT_NAMES, REASONING_VARIANT

GATEWAY = "llm_manager_node"
INTELLIGENCE = "interaction_intelligence"
ABLATION_SKILLS = "interaction_ablations.skills"
ABLATION_MODULE = "interaction_ablations.interpretation_questions"
MODULE = "interaction_intelligence.interpretation_questions"
DEFAULT_ROUTE = "dialogue"
REASONING_ROUTE = "reasoning"

# The defaults of the gateway's parameters (llm_manager_node._declare_parameters)
GATEWAY_DEFAULTS = {
    "default_provider": "", "default_model": "",
    "ollama": {"host": "127.0.0.1", "port": 11434, "model": "llama3.2:3b", "timeout": 120.0,
               "think": False, "num_ctx": 8192},
    "gemini": {"api_key": "", "model": "gemini-2.5-flash", "timeout": 60.0, "base_url": ""},
    "claude": {"api_key": "", "model": "claude-haiku-5-5", "timeout": 60.0, "effort": "low"},
    "openai": {"api_key": "", "model": "gpt-6-luna", "timeout": 60.0,
               "reasoning_effort": "none", "base_url": ""},
}


class ConfigurationError(ValueError):
    """Parameters from which no route of the interpretation server can be written."""


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(
            out.get(key), dict) else value
    return out


def node_parameters(params_files: list[str], node: str) -> dict:
    """The parameters of a node in the parameter files, the later ones taking precedence."""
    out: dict = {}
    for path in params_files:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        out = _merge(out, (data.get(node) or {}).get("ros__parameters") or {})
    return out


KEY_VARIABLES = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY",
                 "claude": "ANTHROPIC_API_KEY"}


def server_environment(params_files: list[str]) -> dict:
    """The environment variables of the server: the API key that the binding gives for the
    provider of the questions, in the variable its backend reads ({} without one)."""
    gateway = _merge(GATEWAY_DEFAULTS, node_parameters(params_files, GATEWAY))
    intelligence = node_parameters(params_files, INTELLIGENCE)
    provider = (intelligence.get("llm_provider") or "").strip() or gateway["default_provider"]
    key = (gateway.get(provider) or {}).get("api_key") if provider in KEY_VARIABLES else ""
    return {KEY_VARIABLES[provider]: key} if key else {}


def route_of(provider: str, model: str, options: dict, reasoning: bool) -> dict:
    """The route that sends the gateway's request for this provider and model."""
    if provider == "ollama":
        route = {"backend": "ollama", "dialect": "llm_manager",
                 "url": f"http://{options['host']}:{options['port']}", "model": model,
                 "timeout_s": float(options["timeout"]), "keep_alive": -1,
                 "num_ctx": int(options.get("num_ctx") or 0),
                 # The request's think ("true" for a question that may reason; "" the
                 # provider's own)
                 "think": True if reasoning else options.get("think", False)}
        return route
    if provider == "openai":
        route = {"backend": "openai", "dialect": "llm_manager", "model": model,
                 "timeout_s": float(options["timeout"]),
                 "reasoning_effort": options.get("reasoning_effort", "none")}
        if options.get("base_url"):
            route["url"] = options["base_url"]
            if os.environ.get("OPENAI_API_KEY") or options.get("api_key"):
                route["api_key_env"] = "OPENAI_API_KEY"
        return route
    if provider == "gemini":
        route = {"backend": "gemini", "dialect": "llm_manager", "model": model,
                 "timeout_s": float(options["timeout"])}
        if options.get("base_url"):
            route["url"] = options["base_url"]
        return route
    if provider == "claude":
        return {"backend": "anthropic", "dialect": "llm_manager", "model": model,
                "timeout_s": float(options["timeout"]), "effort": options.get("effort", "low")}
    raise ConfigurationError(f"Unknown provider '{provider}' (ollama, openai, gemini, claude)")


def _client_question(name: str) -> str:
    """The question of the interpretation server of a name of the client's routing."""
    from .llm_client import LLMDialogClient
    method = LLMDialogClient.ROUTE_ALIASES.get(name, name)
    return next((q for q, c in CLIENT_NAMES.items() if c == method), method)


def server_parameters(params_files: list[str], questions_file: str, prompts_file: str,
                      ablation_questions_file: str = "", log_dir: str = "",
                      out_dir: str | None = None) -> tuple[dict | None, list[str]]:
    """The parameters of the interpretation server, and messages for the operator. None (no
    server) when the intelligence uses a test double instead of the model
    (llm_client_factory), or when no provider is configured (its questions then fail, and fall
    back, as with the former gateway without a provider)."""
    warnings = []
    gateway = _merge(GATEWAY_DEFAULTS, node_parameters(params_files, GATEWAY))
    intelligence = node_parameters(params_files, INTELLIGENCE)
    if (intelligence.get("llm_client_factory") or "").strip():
        return None, ["the intelligence uses a test double (llm_client_factory): no server"]
    provider = (intelligence.get("llm_provider") or "").strip() or gateway["default_provider"]
    if not provider:
        return None, ["no provider (llm_manager_node's default_provider or the intelligence's "
                      "llm_provider): no server, the questions to the model fall back"]
    gateway_name = "claude" if provider == "claude" else provider
    if gateway_name not in GATEWAY_DEFAULTS:
        raise ConfigurationError(f"Unknown provider '{provider}'")
    options = gateway[gateway_name]
    model = ((intelligence.get("llm_model") or "").strip()
             or (gateway["default_model"] if provider == gateway["default_provider"] else "")
             or options["model"])
    routes = {DEFAULT_ROUTE: route_of(provider, model, options, False),
              REASONING_ROUTE: route_of(provider, model, options, True)}

    with open(questions_file, encoding="utf-8") as f:
        questions = yaml.safe_load(f)["questions"]
    changed = False
    routed = {}
    for entry in intelligence.get("routes") or []:
        if not entry or not entry.strip():
            continue
        name, _, routed_model = entry.partition("=")
        if not routed_model.strip():
            raise ConfigurationError(f"Invalid route '{entry}': use question=model")
        routed[_client_question(name.strip())] = routed_model.strip()
    for i, routed_model in enumerate(sorted(set(routed.values())), 1):
        routes[f"model_{i}"] = route_of(provider, routed_model, options, False)
        routes[f"model_{i}_reasoning"] = route_of(provider, routed_model, options, True)
        for question, m in routed.items():
            if m != routed_model:
                continue
            if question not in questions:
                raise ConfigurationError(f"Route for an unknown question '{question}'")
            spec = questions[question] = copy.deepcopy(questions[question])
            spec["route"] = f"model_{i}"
            variant = (spec.get("variants") or {}).get(REASONING_VARIANT)
            if variant is not None:
                variant["route"] = f"model_{i}_reasoning"
            changed = True

    modules = [MODULE]
    skill_modules = [m.strip() for m in intelligence.get("skill_modules") or [] if m.strip()]
    if ABLATION_SKILLS in skill_modules and ablation_questions_file:
        with open(ablation_questions_file, encoding="utf-8") as f:
            questions.update(yaml.safe_load(f)["questions"])
        modules.append(ABLATION_MODULE)
        changed = True

    if changed:
        out = tempfile.NamedTemporaryFile("w", prefix="interaction_questions_", suffix=".yaml",
                                          dir=out_dir, delete=False, encoding="utf-8")
        with out:
            yaml.safe_dump({"questions": questions}, out, sort_keys=False, allow_unicode=True)
        questions_file = out.name

    templates_files = []
    for path in params_files:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if ((data.get(INTELLIGENCE) or {}).get("ros__parameters") or {}).get("prompts"):
            templates_files.append(path)

    parameters = {
        "questions_file": questions_file,
        "templates_file": prompts_file,
        "templates_files": templates_files or [""],
        "question_modules": modules,
        "default_route": DEFAULT_ROUTE,
        "log_dir": log_dir,
    }
    for name, route in routes.items():
        for key, value in route.items():
            parameters[f"routes.{name}.{key}"] = value
    return parameters, warnings


def warmup_of(params_files: list[str]) -> dict | None:
    """The model that the former gateway loaded when it started (its Ollama provider enabled:
    ollama.model, whatever the model of the questions), as {url, model, num_ctx}; None when
    the Ollama provider is not enabled or a test double answers the questions."""
    gateway = _merge(GATEWAY_DEFAULTS, node_parameters(params_files, GATEWAY))
    intelligence = node_parameters(params_files, INTELLIGENCE)
    ollama = gateway["ollama"]
    if (intelligence.get("llm_client_factory") or "").strip() or not ollama.get("enable"):
        return None
    return {"url": f"http://{ollama['host']}:{ollama['port']}", "model": ollama["model"],
            "num_ctx": int(ollama.get("num_ctx") or 0)}
