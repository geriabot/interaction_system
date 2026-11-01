# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The cloud smoke check before a paid campaign (doc/PILOT.md, "What the pilot cannot check"): one
or two real calls to every cloud model the campaign will call, through the same code the campaign
uses, so that what a free pilot cannot see shows up for cents and not an hour in: the API keys,
the pinned model names, a provider that rejects a setting of ours (e.g. a Gemini Pro model that
refuses thinking_budget=0), a quota, the real latency of one call.

    python3 scripts/cloud_smoke.py experiments/<experiment>.yaml [...] [--judge provider/model]
                                   [--allow-cloud]

Per experiment file: each cloud SYSTEM model through the LLM gateway's own provider class
(ros2_llm_manager: openai_provider.OpenAIProvider, gemini_provider.GeminiProvider, with the
experiment's timeout), one schema-constrained call (as the method's narrow questions) and one
streamed free-text call (as a spoken reply); the cloud SIMULATED USER through
interaction_sim/cloud_llm.py with the experiment's simulator settings (one call); with --judge, a
cloud judge through the same client (one call). Without --allow-cloud it only lists the calls.
Refused with the pilot's stand-ins in the environment (this check must reach the real APIs) or a
placeholder (<pin>) left in a file. Exit status 1 if any call fails.
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from experiment_files import load_experiment  # noqa: E402

CLOUD = ("openai", "gemini")
SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
QUESTION = ('The user said: "A large latte, please." Which size did the user ask for? Answer in '
            'JSON: {"value": "<size>"}')
REPLY = "Say, in one short friendly sentence, that the order is noted."


def split(model: str) -> tuple[str, str]:
    provider, sep, name = model.partition("/")
    return (provider, name) if sep and provider in CLOUD else ("ollama", model)


def planned_calls(exp: dict, judge: str = "") -> list[tuple[str, str, str]]:
    """(role, provider, model) of every call the check makes for an experiment."""
    calls = []
    for m in exp.get("models") or []:
        provider, name = split(m)
        if provider in CLOUD:
            calls += [("system (schema)", provider, name), ("system (streamed)", provider, name)]
    sim = exp.get("simulator") or {}
    if sim.get("provider", "ollama") in CLOUD:
        calls.append(("simulated user", sim["provider"], sim["model"]))
    if judge:
        provider, name = split(judge)
        if provider in CLOUD:
            calls.append(("judge", provider, name))
    return calls


def problems(exp: dict, judge: str = "") -> list[str]:
    from interaction_sim.cloud_llm import PILOT_ENV
    out = [f"{v} is set: the smoke check must reach the real APIs (unset it)"
           for v in PILOT_ENV.values() if os.environ.get(v)]
    if exp.get("standins"):
        out.append("a pilot file: the smoke check is for the experiment files")
    unpinned = sorted({f"{provider}/{model}" for _, provider, model in planned_calls(exp, judge)
                       if "<" in model})
    out += [f"{m} is not pinned: write its dated version first" for m in unpinned]
    return out


def system_provider(provider: str, model: str, timeout: float):
    if provider == "openai":
        from ros2_llm_manager.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key="", timeout=timeout, default_model=model)
    from ros2_llm_manager.providers.gemini_provider import GeminiProvider
    return GeminiProvider(api_key="", timeout=timeout, default_model=model)


def call(role: str, provider: str, model: str, exp: dict) -> str:
    """One real call; '' when it answered, else why it failed."""
    timeout = float((exp.get("model_wait") or {}).get("llm_timeout") or
                    (exp.get("ollama") or {}).get("timeout", 120.0)) or 120.0
    if role.startswith("system"):
        backend = system_provider(provider, model, timeout)
        ok, msg = backend.connect()
        if not ok:
            return msg
        if role == "system (schema)":
            result = backend.generate(model, [{"role": "user", "content": QUESTION}],
                                      max_tokens=60, format_schema=SCHEMA)
        else:
            result = backend.generate(model, [{"role": "user", "content": REPLY}],
                                      max_tokens=60, stream_callback=lambda token: True)
        backend.close()
        return "" if result.success and result.text else (result.error or "empty answer")
    from interaction_sim.cloud_llm import ask_json
    sim = exp.get("simulator") or {}
    temperature = sim.get("temperature", 0.7) if role == "simulated user" else 0.0
    try:
        raw, _ = ask_json(provider, model, QUESTION, SCHEMA, temperature=temperature,
                          max_tokens=60)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return "" if '"value"' in raw else f"not the JSON asked: {raw[:80]!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiments", nargs="+")
    parser.add_argument("--judge", default="", help="provider/model of a cloud judge to check")
    parser.add_argument("--allow-cloud", action="store_true", help="Make the calls (cents)")
    args = parser.parse_args()
    failed, seen = False, set()
    for path in args.experiments:
        exp = load_experiment(path)
        why = problems(exp, args.judge)
        if why:
            print(f"{path}: " + "; ".join(why), file=sys.stderr)
            return 1
        for role, provider, model in planned_calls(exp, args.judge):
            if (role, provider, model) in seen:
                continue
            seen.add((role, provider, model))
            if not args.allow_cloud:
                print(f"would call {provider}/{model} ({role})")
                continue
            t0 = time.time()
            error = call(role, provider, model, exp)
            failed |= bool(error)
            print(f"{'FAIL' if error else 'OK  '} {provider}/{model} ({role}) "
                  f"{time.time() - t0:.1f} s" + (f": {error}" if error else ""), flush=True)
    if not args.allow_cloud:
        print("No call made (--allow-cloud to make them)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
