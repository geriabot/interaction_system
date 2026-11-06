# Choosing the models

This page explains which model answers each question of the interaction system, how to change
it, and how each answer is kept to the expected format. The text of the questions is in
[PROMPTS.md](PROMPTS.md), every parameter in [CONFIGURATION.md](CONFIGURATION.md), and how to
serve the models from a shared GPU server in [GPU_SERVER.md](GPU_SERVER.md).

## The models of the system

| Model | Node | What it does | How it is chosen |
|---|---|---|---|
| The LLM | `/llm_manager_node` (the gateway), asked by `/interaction_intelligence` | Every question of the conversation: the choice of the dialogue behavior, the understanding of each turn, the phrasing of each utterance | `ollama.model` of `llm_manager_node`, or `llm_provider` and `llm_model` of `interaction_intelligence` ([below](#the-local-model)) |
| A second LLM (optional) | The same | The questions routed to it, e.g. the slot extraction | `routes` of `interaction_intelligence` ([below](#one-model-per-question-routes)) |
| Speech-to-text | simple_hri `stt_service_node` | Transcribes the user: faster-whisper with Silero VAD (default) or Whisper | `stt:=`, `whisper_model:=` of `main.launch.py` ([SPEECH.md](SPEECH.md)) |
| Text-to-speech | simple_hri `tts_service_node` | Says the robot's utterances: a Piper voice of the language (default) or MMS-TTS | `tts:=`, `language:=` ([SPEECH.md](SPEECH.md)) |
| Face landmarks, emotion (optional) | `interaction_perception` | Lip activity for barge-in (experimental), facial emotion (preliminary, off by default) | [interaction_perception](../interaction_perception/interaction_perception/README.md) |

The simulated users and the judge of the evaluation are not models of the system; they are set in
the experiment files ([EXPERIMENTS.md](../evaluation/doc/EXPERIMENTS.md#who-plays-each-role)).

## How a question reaches a model

```text
/interaction_intelligence ──/llm/session/create──▶ /llm_manager_node ──▶ provider of the session
                          ──/llm/inference──────▶                       ├─ ollama  → HTTP → Ollama (local or remote)
                                                                        ├─ gemini  → Gemini API
                                                                        ├─ claude  → Claude API
                                                                        └─ openai  → OpenAI API, or an OpenAI-compatible server
```

- At the start of a conversation, the intelligence creates two stateless sessions in the gateway:
  one for understanding and one for generation
  ([ARCHITECTURE.md](ARCHITECTURE.md#the-interaction-intelligence)). Each session fixes a provider
  and a model: `llm_provider` and `llm_model` of `interaction_intelligence`; when they are empty,
  the gateway's `default_provider` and that provider's `model`.
- Each routed model adds one more pair of sessions, with the same provider.
- The sessions are deleted when the conversation ends.

The gateway logs every answer with its provider and model (`[ollama/qwen2.5:7b] session=...
tokens=... time=...`), and `/llm/session/list` lists the open sessions with theirs.

## The local model

The shipped configuration uses one local model, `qwen2.5:7b`, served by
[Ollama](https://ollama.com) at `127.0.0.1:11434`. It is `ollama.model` of `llm_manager_node` in
`interaction_system_core/interaction_system/config/app_params.yaml`
([CONFIGURATION.md](CONFIGURATION.md#llm_manager_node)).

To use another model:

1. Pull it on the Ollama server: `ollama pull <model>`. Ollama does not download a model on
   request: a question to a model it does not have fails.
2. Set it as `ollama.model` in `app_params.yaml`, or pass a copy of the file with
   `params_file:=<file>` to `main.launch.py`. A `--symlink-install` build links the installed file
   to the source, so an edit needs no rebuild; restart the stack.

At startup the gateway loads `ollama.model` into memory, and every request asks Ollama to keep it
there (`keep_alive: -1`): the model stays loaded until Ollama stops or it is unloaded
(`ollama stop <model>`). `ollama.think: false` keeps reasoning models from thinking before they
answer, which keeps the spoken replies fast.

The Ollama server must be reachable at `ollama.host` and `ollama.port`. For a server on another
computer, open an SSH tunnel so that it answers at `localhost:11434`
([GPU_SERVER.md](GPU_SERVER.md)), or set `ollama.host`.

## One model per question: routes

`routes` of `interaction_intelligence` sends some questions to a model other than `llm_model`,
for example the understanding questions to a stronger model and the phrasing to a faster one.
Each entry is `question=model`, where the question is the name of the method of
`LLMDialogClient` (`interaction_intelligence/llm_client.py`) that asks the LLM:

```yaml
interaction_intelligence:
  ros__parameters:
    llm_model: "qwen2.5:14b"
    routes: ["extract_turn=qwen3:14b", "evaluate_quiz_answer=qwen3:14b"]
```

The key is the method that calls the LLM, not the skill or the template. The understanding
questions, which are the usual candidates:

| Dialogue behavior | Methods |
|---|---|
| The choice of the dialogue behavior | `choose_dialogue_behavior` |
| Slot filling | `create_frame`, `audit_frame` (a schema designed from the task), `extract_turn` (the values and the act of each turn; its template is `extract_slots`), `classify_intent` (the confirmation) |
| Explanation | `extract_key_points`, `judge_teach_back` |
| Quiz | `evaluate_quiz_answer` |
| Procedure | `classify_step_turn` |
| Role play | `classify_roleplay_turn`, `evaluate_roleplay`, `roleplay_feedback` |
| Grounded question answering | `answer_from_material`, `verify_answer` |
| Questionnaire | `map_scale_answer` |
| Cancellation (every dialogue behavior) | `detect_cancel_intent`, `classify_cancel_reply` |

A generation method (e.g. `generate_response`) can be routed too; it then uses the generation
session of the routed model. `extract_slots` is accepted as another name of `extract_turn`. An
entry that names no question stops `interaction_intelligence` at startup, with the list of the
valid names.

Every routed model must be on the same server as the main one, and pulled. The gateway loads only
`ollama.model` at startup, so the first question to a routed model also waits for it to load.
Both models then stay loaded if they fit together in the GPU memory and the server allows two
loaded models (`OLLAMA_MAX_LOADED_MODELS`); otherwise Ollama swaps them at every change of model,
which works but adds the load time to the latency ([GPU_SERVER.md](GPU_SERVER.md)).

The experiments of the evaluation do not use routes: they run one local model for every
question, chosen by the model-choice experiment
([evaluation/doc/EXPERIMENTS.md](../evaluation/doc/EXPERIMENTS.md#e0a-choosing-the-local-model)),
because the LLM-led condition they compare against has a single prompt and cannot route it.

## Cloud providers

The gateway also has Gemini, Claude and OpenAI (or any OpenAI-compatible server, such as vLLM or
LM Studio, through `openai.base_url`). They are paid per token. To hold the conversations with one
of them:

1. Put its API key in the environment of the terminal that starts the stack: `GEMINI_API_KEY`,
   `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.
2. In `app_params.yaml`, enable it in `llm_manager_node` (`gemini.enable: true`, ...) and set its
   `model` if needed.
3. Set `llm_provider` of `interaction_intelligence` to `gemini`, `claude` or `openai`
   (`llm_model` empty: the provider's `model`).

Routes then name models of that provider. Ollama can stay enabled: a provider that does not
connect at startup is left out with a warning.

The providers' Python packages are in `ros2_llm_manager/config/requirements.txt`. The pixi
environment includes `openai` and `google-genai`; for Claude, add `anthropic`
(`pixi add --pypi anthropic`, [PIXI.md](PIXI.md#what-the-configuration-contains)).

## Structured output

Every understanding question carries a JSON schema of its answer, and the gateway passes it to
the provider so that the answer has the fields and types the question expects:

| Provider | How the schema is enforced |
|---|---|
| Ollama | `format` with the schema, and temperature 0, so the same utterance always gets the same answer |
| Gemini | `response_schema`, with `response_mime_type: application/json` |
| Claude | `output_config.format` of type `json_schema`, with `additionalProperties: false` added to every object; a request the API rejects is retried once without the schema |
| OpenAI | `response_format` of type `json_schema`; strict only when every property is required, otherwise the schema guides the answer without strict mode |

The intelligence parses every answer tolerantly, validates it, retries once and falls back to a
safe answer ([PROMPTS.md](PROMPTS.md#understanding-and-generation)). Generation questions carry
no schema and keep the model's own temperature.
