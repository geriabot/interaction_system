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
| 3 | For every question, the HTTP body that reaches the model's server is the same through both paths, in English and Spanish. | `bodies.py`, `test_bodies.py` |
| 4 | Replaying recorded model answers through both paths gives the same decisions (labels, data, endings), decision by decision. | `answers.py` (how the answers read) |
| 5 | Under model failures (errors, wrong answers, hangs, time limits, the reply's budget) the behavior is the same. | |
| 6 | The latency added with an instant model is not appreciably higher (median, p95). | |

## Criterion 3: the bodies sent to the model

`corpus.py` lists the calls compared: every question of `LLMDialogClient` (50) and the question
of the LLM-led baseline (`monolithic_turn`, interaction_ablations), with arguments that exercise
each part of the prompts (frames with empty, filled, conditional, list and bounded slots and a
history; a context with a domain, resources and a notable state of the user, or none; material,
key points, histories and scenes), in English and Spanish, and with reasoning off and on (the
questions on which the model may reason).

`bodies.py` makes each call through two paths, against `fake_backend.py`, a local stand-in for
Ollama's API and an OpenAI-compatible API that records the body of every request and answers at
once (the same text to the same request, so that both paths make the same calls):

- **gateway**: the goal that the interaction intelligence sent to `/llm/inference` and what
  `llm_manager_node` did with it (a session without system prompt or history, the schema read
  back from JSON, streaming), with the gateway's providers and HTTP clients;
- **interpretation**: the same question asked to `InterpretationServer`, with the system's
  questions file (`interaction_intelligence/config/interpretation/questions.yaml`, and the
  ablation's), `prompts.yaml` as its templates, the question modules
  `interaction_intelligence.interpretation_questions` and
  `interaction_ablations.interpretation_questions`, and routes of the `llm_manager` dialect. The
  client sends the arguments of the PromptBuilder method as the context
  (`interaction_intelligence/interpretation_context.py`); the server builds the prompt and the
  schema.

```bash
source install/setup.bash
cd src/interaction_system/evaluation/equivalence
python3 bodies.py all [--out DIR]          # every backend and language, both paths
python3 -m pytest -q test_bodies.py        # the same, as a test (about one minute)

# One path, and the comparison of two runs; --src takes the gateway path from another checkout
python3 bodies.py run --path gateway --backend ollama --language Spanish --src ~/main --out g.jsonl
python3 bodies.py run --path interpretation --backend ollama --language Spanish --out i.jsonl
python3 bodies.py compare g.jsonl i.jsonl
```

Result (51 questions, 256 calls per backend and language):

| Backend | English | Spanish |
|---|---|---|
| Ollama (`llm_manager` dialect) | 256/256 bodies identical byte for byte | 256/256 identical byte for byte |
| OpenAI-compatible (`llm_manager` dialect) | 256/256 identical as JSON, 0/256 byte for byte | 256/256 identical as JSON, 0/256 byte for byte |

The bodies sent to an OpenAI-compatible API have the same keys and values but not the same
order: the openai SDK writes `model`, `stream`, `messages` and then the other keys in
alphabetical order (`max_completion_tokens`, `reasoning_effort`, `response_format`,
`stream_options`), while the server's `llm_manager` dialect writes them in the order it builds
them. The Gemini and Claude providers of the former gateway are not compared.

## Criterion 4: how the recorded answers read

The client reads the answer of an understanding question with `utils.parse_loose_json` and a
spoken line as its text. The interpretation server reads every answer as a JSON object
(`parse: loose` or `strict`); an answer it cannot read is `invalid`, and the client does not
receive its text. `answers.py` reads the answers recorded in the dialogue records
(`model_calls`, written with `record_model_calls`) both ways:

```bash
python3 answers.py ../results/debug_round1 ../results/debug_round2 ...
```

On the debugging rounds 1 to 8 (72,624 calls with an answer):

| Answers | Calls | Read the same |
|---|---|---|
| Understanding questions (JSON) | 52,189 | all: 51,581 the same object, 608 unreadable by both |
| Spoken lines (no schema) | 20,435 | none: the server rejects every one (not a JSON object) |

The spoken lines cannot reach the client through the server as it is: the server needs a way
to return the model's text without reading it as JSON before the generation questions can move
to it.
