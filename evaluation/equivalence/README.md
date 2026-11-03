# Equivalence of the interpretation path

The interaction system asks its model through behavior_architecture's interpretation server
(`interpretation_server`, the service `behavior_architecture/srv/Interpret`) instead of its
former LLM gateway (`ros2_llm_manager`). The change is accepted only if the system behaves
exactly as before. These tools check it.

## Criteria

| # | Criterion | Tool |
|---|---|---|
| 1 | The workspace builds. | `colcon build` |
| 2 | The existing tests pass unchanged: the unit tests of the interaction intelligence, the light evaluation tests and the system tests. | `pytest` |
| 3 | For every question, the HTTP body that reaches the model's server is the same through both paths, in English and Spanish. | `bodies.py`, `test_bodies.py`; end to end, `latency.py` |
| 4 | Replaying recorded model answers through both paths gives the same decisions, answer by answer. | `answers.py`, `replay.py` |
| 5 | Under model failures (errors, wrong answers, hangs, time limits, the reply's budget) the behavior is the same. | `faults.py` |
| 6 | The latency added with an instant model is not appreciably higher (median, p95). | `latency.py` |

## The tools

`corpus.py` lists the calls compared: every question of `LLMDialogClient` (50) and the question
of the LLM-led baseline (`monolithic_turn`, interaction_ablations), with arguments that exercise
each part of the prompts (frames with empty, filled, conditional, list and bounded slots and a
history; a context with a domain, resources and a notable state of the user, or none; material,
key points, histories and scenes), in English and Spanish, and with reasoning off and on (the
questions on which the model may reason).

`fake_backend.py` is a local stand-in for Ollama's API and an OpenAI-compatible API: it records
the body of every request and answers at once with a text that depends only on the request (or
with given answers, a delay, or an HTTP error).

`transports.py` holds the two paths of a call of `LLMDialogClient`, without ROS:

- **gateway**: the goal that the interaction intelligence sent to `/llm/inference` and what
  `llm_manager_node` did with it (a session without system prompt or history, the schema read
  back from JSON, streaming), with the gateway's providers and HTTP clients, and the client's
  wait as the limit of the call;
- **interpretation**: the request that the intelligence sends now
  (`interpretation_context.interpret_request`: the question and the context of the marked prompt,
  the wait as the budget, the token cap, the reasoning variant), answered by an
  `InterpretationServer` with the system's questions, templates and question modules on routes of
  the `llm_manager` dialect.

`latency.py` starts both paths as ROS nodes instead: the intelligence of a checkout of the former
code with `llm_manager_node`, and the installed intelligence with `interpretation_server`.

```bash
source install/setup.bash
cd src/interaction_system/evaluation/equivalence
python3 bodies.py all [--out DIR]                     # 3: every backend and language
python3 -m pytest -q test_bodies.py                   # 3, as a test (about one minute)
python3 answers.py ../results/debug_round*            # 4: how the recorded answers read
python3 replay.py ../results/debug_round* [--out F]   # 4: the decisions from the recorded answers
python3 faults.py [--backend openai] [--only hang]    # 5
ROS_DOMAIN_ID=<yours> python3 latency.py --main-src <checkout of the former code>   # 3 and 6
```

`bodies.py run --path gateway --src <checkout>` takes the gateway path from another checkout.

## Results

**Criterion 3.** 51 questions, 256 calls per backend and language, the body of every call
identical byte for byte: Ollama 256/256 in English and 256/256 in Spanish, OpenAI-compatible APIs
256/256 and 256/256. Through the ROS nodes (`latency.py`), the 309 bodies of 103 rounds of four
skills are identical, and so are the replies. The Gemini and Claude providers of the former
gateway were not compared (the server's backends reproduce their SDKs' requests in content, not
byte for byte).

**Criterion 4.** The client reads the model's text itself on both paths (the server returns it
as `{"text": ...}`). On the debugging rounds 1 to 8 (72,624 recorded calls with an answer), the
server's own parser would read the same object as the client's for every answer of an
understanding question (52,189), but it rejects every spoken line (20,435), which is why the
server returns the text. `replay.py` gives each of the 19,636 distinct answers recorded for the 45
questions found in those rounds to its question through both paths (with reasoning off and, for
the questions that may reason, on: 22,358 replays): the decision, the model status and the calls
are the same in all of them.

**Criterion 5.** Every question in English and Spanish, with reasoning off and on, gives on both
paths the same decision, model status, timeouts, skipped calls and calls to the server, and the
same time within 0.3 s:

| Failure | Ollama | OpenAI-compatible |
|---|---|---|
| HTTP 500; no server at the address | 240/240 the same | the same decisions, not the same requests (below) |
| The server answers after the wait; a slow server; the reply's budget spent; a call that may reason hangs | 378/378 the same | slow server: 120/120 |
| Prose, empty, `{}`, a code fence, a list, a trailing comma, a `<think>` block | 840/840 the same | 840/840 |

With an OpenAI-compatible API, the openai SDK of the former gateway retried a failed request by
itself (twice, after a connection error or a 408, 409, 429 or 5xx answer, with backoff), and the
server's `openai` backend does not: the same failure takes 2 to 3 s and three requests per call
through the gateway, and one request at once through the server; the decisions are the same.
The failures injected inside the client by the simulation (`interaction_sim`'s `llm_faults`)
replace the client's call to its transport, so they act the same on both paths; the system tests
cover them.

**Criterion 6.** An instant Ollama stand-in, 100 rounds after 3 of warm-up, the time of each
request of the tree to the intelligence:

| Skill | Gateway median / p95 (ms) | Interpretation median / p95 (ms) |
|---|---|---|
| `begin` (no question) | 9.0 / 12.3 | 7.1 / 10.7 |
| `slot_filling.opening` | 53.6 / 57.0 | 11.2 / 14.5 |
| `cancel_check` | 54.1 / 57.7 | 11.0 / 15.3 |
| `slot_filling.update` | 53.6 / 57.7 | 11.2 / 15.1 |

The path through the interpretation server adds less latency than the gateway's action.

## Other differences

- When it started, the gateway loaded `ollama.model` into the Ollama server's memory; the launch
  file does the same with `interpretation_warmup` (only when the Ollama provider is enabled).
- When a dialogue ends, the gateway cancelled its call in progress; the intelligence now cancels
  the dialogue's calls in the server (`CancelInterpretation`).
