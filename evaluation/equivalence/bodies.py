# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Criterion 3 of the equivalence of the interpretation path with the former LLM gateway: for
every question, the HTTP body that reaches the model's server is the same, byte for byte.

The calls of the corpus (corpus.py: every question of LLMDialogClient, in English and
Spanish, with and without reasoning) are made through two paths, each against a local stand-in
of the model's server that records the bodies (fake_backend.py):

  gateway         the client's call as the interaction intelligence made it through
                  ros2_llm_manager: the goal of /llm/inference (intelligence_node._infer_sync)
                  and the manager's handling of it (llm_manager_node._on_inference: the session's
                  messages, the schema read back from JSON, streaming), with the gateway's
                  provider (OllamaProvider, OpenAIProvider) and its HTTP client
  interpretation  the same question asked to behavior_architecture's InterpretationServer with
                  the interaction system's questions file, prompts.yaml as its templates and its
                  question module, on routes of the llm_manager dialect: the client sends the
                  arguments of the PromptBuilder method (interpretation_context.py), and the
                  server builds the prompt and the schema

Both paths make the same calls: the stand-in answers the same text to the same request, and
the client receives it in both. The bodies are then compared call by call.

    python3 bodies.py all [--out DIR]                 # every combination, in this tree
    python3 bodies.py run --path gateway --backend ollama --language Spanish --out g.jsonl
                          [--src <a checkout of the repository, e.g. of main>]
    python3 bodies.py compare g.jsonl i.jsonl

`--src` runs the path with the interaction intelligence and ros2_llm_manager of another
checkout (the former code, when the client itself has changed).
"""

import argparse
import base64
import inspect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
INTELLIGENCE = os.path.join(REPO, "interaction_system_core", "interaction_intelligence")
ABLATIONS = os.path.join(REPO, "evaluation", "ablations", "interaction_ablations")
PROMPTS = os.path.join(INTELLIGENCE, "config", "prompts.yaml")
QUESTIONS_FILE = os.path.join(INTELLIGENCE, "config", "interpretation", "questions.yaml")
ABLATION_QUESTIONS_FILE = os.path.join(ABLATIONS, "config", "interpretation", "questions.yaml")
MODEL = {"ollama": "qwen2.5:7b", "openai": "gpt-6-luna"}
LANGUAGES = ("English", "Spanish")
BACKENDS = ("ollama", "openai")


def use_sources(src: str | None) -> None:
    """Import the interaction intelligence and ros2_llm_manager from this checkout (default:
    the one that holds this file)."""
    root = os.path.abspath(src) if src else REPO
    for path in (os.path.join(root, "ros2_llm_manager"),
                 os.path.join(root, "evaluation", "ablations", "interaction_ablations"),
                 os.path.join(root, "interaction_system_core", "interaction_intelligence"),
                 HERE):
        if path not in sys.path:
            sys.path.insert(0, path)


def templates() -> dict:
    import yaml
    with open(PROMPTS, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["interaction_intelligence"]["ros__parameters"]["prompts"]


# ==== THE GATEWAY ====


def gateway_provider(backend: str, url: str):
    if backend == "ollama":
        from ros2_llm_manager.providers.ollama_provider import OllamaProvider
        host, port = url.rsplit("//", 1)[1].split(":")
        provider = OllamaProvider(host=host, port=int(port), timeout=120.0,
                                  default_model=MODEL[backend], think=False, num_ctx=8192)
    else:
        from ros2_llm_manager.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider(api_key="unused", timeout=60.0, default_model=MODEL[backend],
                                  reasoning_effort="none", base_url=url + "/v1")
    ok, message = provider.connect()
    if not ok:
        raise RuntimeError(f"The gateway's {backend} provider did not connect: {message}")
    return provider


def gateway_infer(provider, model: str):
    """The client's call to the model through the gateway: the goal that
    intelligence_node._infer_sync sends and what llm_manager_node._on_inference does with it
    (a session without system prompt and without history: stateful=False)."""

    def infer(session_id, prompt, max_tokens, schema, think=False):
        if not prompt or not prompt.strip():
            return None
        # The goal (ros2_llm_interfaces/action/LlmInference)
        goal_prompt = str(prompt)
        goal_max_tokens = max_tokens or 0
        goal_schema = json.dumps(schema) if schema else ""
        goal_think = "true" if think else ""
        # The manager
        format_schema = json.loads(goal_schema) if goal_schema.strip() else None
        messages = [{"role": "user", "content": goal_prompt}]
        result = provider.generate(model=model, messages=messages, max_tokens=goal_max_tokens,
                                   format_schema=format_schema,
                                   stream_callback=lambda token: True,
                                   cache_prefix_len=getattr(prompt, "cache_prefix_len", 0),
                                   think=goal_think)
        return result.text if result.success else None

    return infer


# ==== INTERPRETATION ====


def interpretation_server(backend: str, url: str, questions_file: str = QUESTIONS_FILE):
    """The interpretation server with the system's questions and the ablation's."""
    import yaml
    from behavior_interpretation import InterpretationServer
    with open(ABLATION_QUESTIONS_FILE, encoding="utf-8") as f:
        ablation_questions = yaml.safe_load(f)["questions"]
    if backend == "ollama":
        route = {"backend": "ollama", "dialect": "llm_manager", "url": url,
                 "model": MODEL[backend], "num_ctx": 8192, "keep_alive": -1, "think": False}
        reasoning = dict(route, think=True)
    else:
        route = {"backend": "openai", "dialect": "llm_manager", "url": url + "/v1",
                 "model": MODEL[backend], "reasoning_effort": "none"}
        reasoning = dict(route)
    return InterpretationServer(
        questions_file=questions_file, questions=ablation_questions, templates_file=PROMPTS,
        question_modules=["interaction_intelligence.interpretation_questions",
                          "interaction_ablations.interpretation_questions"],
        routes={"dialogue": route, "reasoning": reasoning}, default_route="dialogue")


class PromptCalls:
    """The arguments of the last call of each PromptBuilder method of a builder (and of the
    LLM-led baseline's prompt_fields: the question monolithic_turn)."""

    def __init__(self, builder):
        from interaction_intelligence.interpretation_context import QUESTIONS
        self.last: tuple[str, dict] | None = None
        self.question_of = {method: q for q, (method, _t, _k) in QUESTIONS.items()}
        for method in self.question_of:
            original = getattr(builder, method)
            setattr(builder, method, self._wrap(method, original))
        self.question_of["prompt_fields"] = "monolithic_turn"
        try:
            from interaction_ablations import skills as ablation
        except ImportError:
            return
        original = getattr(ablation.prompt_fields, "__wrapped_original__", ablation.prompt_fields)

        def fields(*args, **kwargs):
            out = original(*args, **kwargs)
            self.last = ("prompt_fields", {"fields": out})
            return out

        fields.__wrapped_original__ = original
        ablation.prompt_fields = fields

    def _wrap(self, method, original):
        signature = inspect.signature(original)

        def wrapped(*args, **kwargs):
            self.last = (method, dict(signature.bind(*args, **kwargs).arguments))
            return original(*args, **kwargs)

        return wrapped


def interpretation_infer(server, fake, calls: PromptCalls, language: str):
    """The client's call to the model through the interpretation server: the question of the
    last PromptBuilder call, with its arguments as the context; the client gets the text that
    the model's server answered (the server's own reading of it is not compared here)."""
    from interaction_intelligence.interpretation_context import encode_context

    def infer(session_id, prompt, max_tokens, schema, think=False):
        method, args = calls.last
        question = calls.question_of[method]
        before = len(fake.bodies)
        context = (json.dumps(args, ensure_ascii=False) if question == "monolithic_turn"
                   else encode_context(question, language, args))
        result = server.interpret({"question": question, "variant": "reasoning" if think else "",
                                   "context": context, "budget_s": 0.0})
        if not result.success:
            raise RuntimeError(f"{question}: {result.message}")
        if len(fake.bodies) == before:
            return None
        return fake.bodies[-1]["answer"]

    return infer


# ==== THE RUNS ====


def run(path: str, backend: str, language: str) -> list[dict]:
    """The bodies of every call of the corpus through one path: [{case, question, reasoning,
    n, body (base64)}]."""
    import logging
    from corpus import REASONING, ablation_cases, cases
    from fake_backend import FakeBackend
    from interaction_intelligence.llm_client import LLMDialogClient
    from interaction_intelligence.prompt_builder import PromptBuilder

    logging.getLogger("LLMDialogClient").setLevel(logging.ERROR)
    fake = FakeBackend().start()
    try:
        if path == "gateway":
            infer = gateway_infer(gateway_provider(backend, fake.url), MODEL[backend])

            def infer_of(pb):
                return infer
        else:
            server = interpretation_server(backend, fake.url)

            def infer_of(pb):
                return interpretation_infer(server, fake, PromptCalls(pb), language)
        out = []
        for reasoning in ("none", "critical"):
            for name, question, call in cases(language) + ablation_cases(language):
                if reasoning == "critical" and question not in REASONING:
                    continue
                pb = PromptBuilder(templates(), language=language)
                client = LLMDialogClient("nlu", "nlg", infer_of(pb), pb, language=language,
                                         reasoning=reasoning)
                fake.clear()
                fake.label = name
                call(client)
                for n, entry in enumerate(b for b in fake.bodies
                                          if b["path"].endswith(("/chat", "/completions"))):
                    out.append({"case": name, "question": question, "reasoning": reasoning,
                                "n": n, "body": base64.b64encode(entry["body"]).decode()})
        return out
    finally:
        fake.stop()


def compare(a: list[dict], b: list[dict]) -> dict:
    """Call by call: {question: {"calls", "identical", "same_json", "differ": [case...]}}, and
    the calls that only one path made."""
    key = (lambda r: (r["reasoning"], r["case"], r["n"]))
    left, right = {key(r): r for r in a}, {key(r): r for r in b}
    summary: dict = {}
    for k in sorted(set(left) | set(right)):
        question = (left.get(k) or right.get(k))["question"]
        s = summary.setdefault(question, {"calls": 0, "identical": 0, "same_json": 0,
                                          "differ": [], "missing": []})
        s["calls"] += 1
        if k not in left or k not in right:
            s["missing"].append("/".join(map(str, k)))
            continue
        x = base64.b64decode(left[k]["body"])
        y = base64.b64decode(right[k]["body"])
        if x == y:
            s["identical"] += 1
            s["same_json"] += 1
        else:
            if json.loads(x) == json.loads(y):
                s["same_json"] += 1
            s["differ"].append("/".join(map(str, k)))
    return summary


def report(summary: dict, title: str) -> tuple[int, int, int]:
    calls = sum(s["calls"] for s in summary.values())
    identical = sum(s["identical"] for s in summary.values())
    same_json = sum(s["same_json"] for s in summary.values())
    print(f"{title}: {len(summary)} questions, {calls} calls, {identical} identical bytes, "
          f"{same_json} identical JSON")
    for question, s in sorted(summary.items()):
        if s["identical"] != s["calls"]:
            print(f"  {question}: {s['identical']}/{s['calls']} identical, "
                  f"{s['same_json']} same JSON; differ {s['differ'][:3]} missing "
                  f"{s['missing'][:3]}")
    return calls, identical, same_json


def write(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def read(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("run", help="the bodies of one path")
    one.add_argument("--path", choices=("gateway", "interpretation"), required=True)
    one.add_argument("--backend", choices=BACKENDS, default="ollama")
    one.add_argument("--language", choices=LANGUAGES, default="English")
    one.add_argument("--src", default=None)
    one.add_argument("--out", required=True)
    both = sub.add_parser("compare", help="compare the bodies of two runs")
    both.add_argument("a")
    both.add_argument("b")
    every = sub.add_parser("all", help="every backend and language, both paths, in this tree")
    every.add_argument("--out", default="")
    args = parser.parse_args(argv)

    if args.command == "compare":
        calls, identical, _ = report(compare(read(args.a), read(args.b)),
                                     f"{args.a} vs {args.b}")
        return 0 if calls and identical == calls else 1
    use_sources(getattr(args, "src", None))
    if args.command == "run":
        write(run(args.path, args.backend, args.language), args.out)
        return 0
    status = 0
    for backend in BACKENDS:
        for language in LANGUAGES:
            g = run("gateway", backend, language)
            i = run("interpretation", backend, language)
            if args.out:
                os.makedirs(args.out, exist_ok=True)
                write(g, os.path.join(args.out, f"gateway_{backend}_{language}.jsonl"))
                write(i, os.path.join(args.out, f"interpretation_{backend}_{language}.jsonl"))
            calls, identical, same_json = report(compare(g, i), f"{backend} {language}")
            if not calls or same_json != calls or (backend == "ollama" and identical != calls):
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
