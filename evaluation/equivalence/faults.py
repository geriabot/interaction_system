# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Criterion 5: under model failures the client behaves the same through the former gateway and
through the interpretation server.

Every question (the first case of each in the corpus, in English and Spanish, with reasoning
off and, for the questions that may reason, on) is asked through both paths (transports.py)
while the model's server (fake_backend.py) fails in one way:

  error       every request answered with HTTP 500
  down        no server at the address
  hang        the server answers after the client's wait (llm_timeout) is over
  slow        the server answers within the client's wait
  budget      the reply's time budget runs out during the first call (the next calls are not
              made)
  reasoning   a call that may reason hangs past reasoning_timeout
  prose       an understanding question answered with prose, a line with prose
  empty       an empty answer
  wrong-json  JSON without the fields asked ({})
  fenced      the JSON in a code fence, with text around it
  list        the JSON object inside a list
  trailing    JSON with a trailing comma
  think       a <think> block before the JSON

The client's decision (what the method returns), its model status (model_down), its limits'
timeouts and skipped calls and the calls that reached the server are compared; the time the
method took must agree within TIME_TOLERANCE_S (a hung call ends at the same limit on both
paths).

The failures injected inside the client by the simulation (simulation/interaction_sim's
llm_faults: errors, wrong answers, hangs, crashes and freezes in a given turn) replace the
client's call to its transport, so they act the same on both paths; the system tests
(test_llm_faults.py, test_model_failures.py) cover them.

    python3 faults.py [--backend ollama] [--only hang,error]
"""

import argparse
import collections
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from transports import (Gateway, Interpretation, MODEL, client, gateway_provider,  # noqa: E402
                        interpretation_server, use_sources)

WAIT_S = 0.8          # llm_timeout of the client
HANG_S = 2.0          # a hung server answers after this
SLOW_S = 0.3
REASONING_S = 1.2     # reasoning_timeout
BUDGET_S = 1.3        # the reply's budget in `budget`: spent during the first call
TIME_TOLERANCE_S = 0.3

ANSWERS = {
    "prose": ("Sorry, I cannot help with that.", "Sorry, I cannot help with that."),
    "empty": ("", ""),
    "wrong-json": ("{}", "{}"),
    "fenced": ('Here it is:\n```json\n{"intent": "confirms", "kind": "answer", '
               '"correct": true, "answer": "yes"}\n```\nDone.', "```\nA line.\n```"),
    "list": ('[{"intent": "done", "answer": "no", "kind": "option", "option": 2}]',
             '["a line"]'),
    "trailing": ('{"intent": "skip", "answer": "finished", "supported": true,}', "Fine,"),
    "think": ('<think>{"intent": "x"}</think>{"intent": "repeat", "answer": "yes"}',
              "<think>hmm</think>Hello there."),
}
SCENARIOS = ("error", "down", "hang", "slow", "budget", "reasoning") + tuple(ANSWERS)


def run_one(path, transport_of, language, reasoning, call, limits) -> dict:
    from replay import decision
    llm = client(path, transport_of, language, reasoning, limits)
    started = time.monotonic()
    try:
        result = decision(call(llm))
    except Exception as exc:  # noqa: B902
        result = f"raised {type(exc).__name__}: {exc}"
    return {"result": result, "model_status": llm.model_status,
            "timeouts": llm.limits.timeouts, "skipped": llm.limits.skipped,
            "time": round(time.monotonic() - started, 3)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--only", default="", help="scenarios, comma-separated")
    parser.add_argument("--src", default=None)
    args = parser.parse_args(argv)
    use_sources(args.src)
    from corpus import REASONING, ablation_cases, cases
    from fake_backend import FakeBackend
    from interaction_intelligence.llm_client import CallLimits

    logging.getLogger("LLMDialogClient").setLevel(logging.CRITICAL)
    fake = FakeBackend().start()
    dead = FakeBackend().start()
    dead_url = dead.url
    dead.stop()  # nothing listens there any more
    provider = gateway_provider(args.backend, fake.url)
    server = interpretation_server(args.backend, fake.url)
    dead_provider = gateway_provider(args.backend, fake.url)
    dead_provider_url = dead_url
    if args.backend == "ollama":
        dead_provider._base_url = dead_url
        dead_provider._http.base_url = dead_url
    else:
        dead_provider._client = dead_provider._client.with_options(base_url=dead_url + "/v1")
    dead_server = interpretation_server(args.backend, dead_provider_url)

    scenarios = [s for s in SCENARIOS if not args.only or s in args.only.split(",")]
    totals = collections.Counter()
    differ = []
    for scenario in scenarios:
        for language in ("English", "Spanish"):
            seen = set()
            for name, question, call in cases(language) + ablation_cases(language):
                if question in seen:
                    continue
                seen.add(question)
                modes = ("none", "critical") if question in REASONING else ("none",)
                if scenario == "reasoning":
                    modes = ("critical",) if question in REASONING else ()
                for reasoning in modes:
                    fake.answers, fake.delay_s, fake.status = None, 0.0, 200
                    srv, prov = server, provider
                    limits = {"call_timeout": WAIT_S, "reasoning_timeout": REASONING_S}
                    if scenario == "error":
                        fake.status = 500
                    elif scenario == "down":
                        srv, prov = dead_server, dead_provider
                    elif scenario in ("hang", "reasoning"):
                        fake.delay_s = HANG_S
                    elif scenario == "slow":
                        fake.delay_s = SLOW_S
                    elif scenario == "budget":
                        fake.delay_s = 1.0
                        limits = {"call_timeout": 2.0, "budget_s": BUDGET_S}
                    else:
                        json_answer, line = ANSWERS[scenario]
                        fake.json_answer, fake.text_answer = json_answer, line
                    paths = {
                        "gateway": lambda lim, prov=prov: Gateway(prov, MODEL[args.backend],
                                                                  lim),
                        "interpretation": lambda lim, srv=srv: Interpretation(srv, "faults",
                                                                              lim),
                    }
                    got = {}
                    for path, transport_of in paths.items():
                        fake.clear()
                        got[path] = run_one(path, transport_of, language, reasoning, call,
                                            CallLimits(**limits))
                        got[path]["calls"] = sum(
                            1 for b in fake.bodies if b["path"].endswith(("chat",
                                                                          "completions")))
                    fake.json_answer, fake.text_answer = "{}", "What would you like to drink?"
                    g, i = dict(got["gateway"]), dict(got["interpretation"])
                    same = (abs(g.pop("time") - i.pop("time")) <= TIME_TOLERANCE_S
                            and g == i)
                    totals[(scenario, same)] += 1
                    if not same:
                        differ.append((scenario, language, question, reasoning, got))
    fake.stop()
    for scenario in scenarios:
        print(f"{scenario}: {totals[(scenario, True)]} same, {totals[(scenario, False)]} "
              "different")
    for scenario, language, question, reasoning, got in differ[:20]:
        print(f"  {scenario} {language} {question} {reasoning}: {got}")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
