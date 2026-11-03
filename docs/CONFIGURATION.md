# Configuration reference

This page lists the configuration files of the interaction system and the parameters of every
node of the system and of the subsystems around it, with their types and shipped defaults.
[ARCHITECTURE.md](ARCHITECTURE.md) explains how these nodes fit together,
[ROS_INTERFACES.md](ROS_INTERFACES.md) lists their topics and services, and
[LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md) lists the launch arguments.

## Configuration files

| File | Read by | Defines |
| --- | --- | --- |
| `interaction_system_core/interaction_system/config/app_params.yaml` | The intelligence and the orchestrator of `main.launch.py` (launch argument `params_file`), and the launch file, which writes the interpretation server's parameters from it | Their parameters (below), and the model binding (`llm_manager_node`). simple_hri's nodes take theirs from the launch arguments |
| `interaction_system_core/interaction_system/config/interaction_orchestrator_params.yaml` | `/interaction_orchestrator` (launch argument `catalog_file`, applied before `params_file`) | The [catalog of dialogue behaviors](#the-catalog-of-dialogue-behaviors) and the default limits |
| `interaction_system_core/interaction_system/config/interaction_behaviors_config.yaml` | `mission_executor` (positional argument; launch argument `config_file`) | The [behavior_architecture runtime](#the-behavior-tree-runtime): the orchestrator and the runner of each tree |
| `interaction_system_core/interaction_intelligence/config/prompts.yaml` | `/interaction_intelligence` and the interpretation server (launch argument `prompts_file`) | The prompt templates, parameters `prompts.*` ([PROMPTS.md](PROMPTS.md)) |
| `interaction_system_core/interaction_intelligence/config/interpretation/questions.yaml` | The interpretation server | The questions to the model ([below](#the-interpretation-server)) |
| `ros2_llm_manager/config/params.yaml` | `llm_manager_node` when started by its own launch file | The former gateway's parameters (the same values as `app_params.yaml`); the interaction system does not start it |
| `interaction_perception/interaction_perception/config/params.yaml` | `emotion_detector` | Its parameters |
| `interaction_perception/interaction_perception/config/lip_activity_params.yaml` | `lip_activity_detector_node` | Its parameters |
| `robots/nao/nao_pose_manager/config/nao_saved_poses.yaml` | The gesture managers | The saved arm poses (not a parameter file) |

When `main.launch.py` starts the nodes, they take their values from `app_params.yaml`, and then,
when the GUI starts them, from the file named by the environment variable `DIALOSTACK_GUI_PARAMS`
([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md#the-whole-stack-interaction_system-mainlaunchpy)). A
node started through its own package's launch file takes the values of that package's parameter
file. simple_hri's nodes take their parameters from `main.launch.py`
([below](#simple_hris-speech-nodes)).

### The Behavior Tree runtime

`interaction_behaviors_config.yaml` tells `mission_executor` which orchestrator to load and which
runners to create, one per dialogue behavior, each with its tree:

```yaml
node_name: "interaction_system_bt_node"            # the ROS node shared by the tree nodes
orchestrator_type: "interaction_orchestrator"
package_name: "interaction_system"
orchestrator_libraries: ["libinteraction_system.so"]
plugin_libraries: ["libinteraction_system_behaviors_bt_plugins.so"]
behaviors:
  - name: "interaction_slot_filling"               # the runner (lifecycle node)
    behavior_file: "behavior_tree_xml/slot_filling.xml"
    control_period_ms: 50                          # tick period of the tree
    package_name: "interaction_system_behaviors"   # where behavior_file is
  # ... one entry per dialogue behavior
```

### The catalog of dialogue behaviors

The parameters `dialogue_behaviors.*` of `/interaction_orchestrator`, in
`interaction_orchestrator_params.yaml`, list the dialogue behaviors that a request can run:

```yaml
/interaction_orchestrator:
  ros__parameters:
    dialogue_behaviors:
      behaviors: ["SlotFilling", "Explanation", "Quiz", "Procedure", "Roleplay", "GroundedQA",
                  "Questionnaire"]
      Quiz:
        behavior_id: 2
        mode: "quiz"
        needs: ["question_bank"]
        explanation: "The robot ASKS the questions of a question bank, grades each answer ..."
        node_name: "interaction_quiz"
```

| Field | Type | Meaning |
| --- | --- | --- |
| `behaviors` | string[] | The dialogue behaviors to load; each has a block with its name |
| `<Behavior>.behavior_id` | int | Its unique id, which is also the answer to the choice of the dialogue behavior (`0` to `6` ship) |
| `<Behavior>.mode` | string | The `dialog_mode` it implements |
| `<Behavior>.needs` | string[] | What the request must provide for the dialogue behavior to be offered: `question_bank`, `steps`, `scene`, `material`, `instrument`; `[""]` for none ([ARCHITECTURE.md](ARCHITECTURE.md#the-choice-of-the-dialogue-behavior)) |
| `<Behavior>.explanation` | string | What the LLM reads to choose it |
| `<Behavior>.node_name` | string | Its runner, as named in `interaction_behaviors_config.yaml` |

A dialogue behavior without `behavior_id`, `mode` or `node_name` is ignored, with an error in the
log. [ADDING_DIALOGUE_BEHAVIORS.md](ADDING_DIALOGUE_BEHAVIORS.md) explains how to add a dialogue
behavior across these files.

## Conventions

The Default column of the tables below shows the shipped default: the value that the node runs
with after its YAML configuration is loaded. Where a YAML file overrides the default declared in
the code, the table lists the YAML value, and
[Code vs. YAML discrepancies](#code-vs-yaml-discrepancies) lists the differences.

## interaction_orchestrator

The parameters are declared in
`interaction_system_core/interaction_system/src/interaction_system/InteractionOrchestrator.cpp`,
except `control_cycle_rate_ms`, which comes from behavior_architecture's `BaseOrchestrator`. The
shipped values come from `interaction_orchestrator_params.yaml` and then `app_params.yaml`. They
are written to the blackboard of each conversation
([ARCHITECTURE.md](ARCHITECTURE.md#the-blackboard)).

Every limit of the mechanisms can also be set per request
([ROS_INTERFACES.md](ROS_INTERFACES.md#dialogtask-goal)): `max_turns`, `max_silence`,
`max_stalled`, `max_attempts`, `max_off_task`, `wait_timeout`, `filler_after_s`,
`filler_repeat_s`, `reply_budget`, `opening_budget`, and `ack_enabled` (as `acknowledgments`).
The values below are the defaults for the requests that leave them unset. The mechanisms behind
them are described in [docs/behaviors](behaviors/README.md).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `control_cycle_rate_ms` | int | `50` | Period of the state machine of a request. |
| `wait_timeout` | float | `15.0` | Seconds of user silence before each prompt saying that the robot is waiting ([silence](behaviors/silence.md)). |
| `max_silence` | float | `90.0` | Seconds of continuous silence that end the conversation (outcome `user_silent`), counted in waits of `wait_timeout`; `0` = never. |
| `max_stalled` | int | `4` | User turns in a row without progress (not understood, asked to repeat) that end the conversation (outcome `breakdown`); `0` = unlimited ([recovery and breakdown](behaviors/no_progress.md)). |
| `max_turns` | int | `0` | Maximum number of user turns in a conversation (outcome `turn_limit`); `0` = unlimited. |
| `max_off_task` | int | `6` | Requests in a row outside the task, in grounded question answering, that end the conversation (outcome `breakdown`); `0` = unlimited. |
| `max_attempts` | int | `3` | Attempts per element before the dialogue behavior gives up on it ([below](#notes-on-the-orchestrator-parameters)). |
| `tts_timeout` | float | `60.0` | Longest utterance (s); a longer one is cut and the conversation goes on. |
| `intelligence_timeout` | float | `30.0` | Longest wait for a skill of the intelligence (s), the hard limit of every request. Past it, the conversation ends with the closing line for an error (outcome `error`). |
| `reasoning_intelligence_timeout` | float | `300.0` | The hard limit used instead of `intelligence_timeout` when the LLM of the conversation may reason (`DialogTask.reasoning` not 0). |
| `reply_budget` | float | `20.0` | Seconds that the intelligence may spend asking the LLM for the reply to a user turn, counted from the end of the turn; `0` = no budget. |
| `opening_budget` | float | `30.0` | The same budget for the opening, which may design the frame or list the key points, and generates the longest text (an explanation). |
| `user_speaking_topic` | string | `/user_speaking` | `Bool` topic, true while the user speaks, used for barge-in; `""` disables barge-in. |
| `filler_after_s` | float | `2.0` | Seconds the robot may take to answer (a user turn, or the opening) before it says a fixed conversational filler ("Hmm…"); `0` = no fillers ([conversational fillers](behaviors/fillers.md)). |
| `filler_repeat_s` | float | `8.0` | After the filler ladder (at `filler_after_s` + 0, 3, 7 and 12 s), the seconds between fillers of the same reply, which repeat the last phrase; `0` = none after the ladder. |
| `dialogue_behaviors.*` | | | The [catalog](#the-catalog-of-dialogue-behaviors). |

### Notes on the orchestrator parameters

- `max_silence`: the closing comes at the first wait that reaches it.
- `max_off_task`: a question within the task resets the count. The conversation ends with the
  reason `Off-task: …` and the closing `close_off_task`.
- `max_attempts` counts, per element: the failed teach-back checks of an explanation before it
  closes; the problems reported on a procedure step before the step is marked stuck; the failed
  attempts on a slot before it is asked differently (one more attempt and the slot is left
  unanswered); and the replies to a quiz question that are not answers, or the answers to a
  questionnaire item that fall between options, before the question or item is left unanswered.
- `intelligence_timeout` is reached only when the intelligence itself does not answer; an LLM
  that does not answer is bounded by `reply_budget`
  ([the wait for the model](behaviors/endings.md#the-wait-for-the-model)).
- `reasoning_intelligence_timeout`: reasoning is not cut, so the tree must wait for it. The
  default leaves room for two reasoning calls of 120 s (`reasoning_timeout`), the most that one
  skill makes.
- `reply_budget`: once it is spent, the questions still pending fall back (fixed phrases, "not
  understood") and the robot says its fallback line, so an LLM that hangs never keeps the user
  waiting longer. A request never gets more than `intelligence_timeout` minus 2 s. An experiment
  that does not test this bound may open it, together with `llm_timeout` and
  `intelligence_timeout`, for all its conditions alike (`model_wait`,
  [evaluation/README.md](../evaluation/README.md)). E1 opens it, so that the latency of every
  model, including cloud models that reason, is measured rather than cut.
- `user_speaking_topic` is published by the lip activity detector of the optional perception
  subsystem. While nothing publishes it, the robot finishes every utterance.

The runners of `mission_executor` (behavior_architecture's `BehaviorRunner`) also have
`enable_groot_monitoring` (`true`) and `groot_port` (`5555`), so the active tree can be watched
with Groot2.

## interaction_intelligence

The parameters are declared in
`interaction_system_core/interaction_intelligence/interaction_intelligence/intelligence_node.py`,
and the shipped values come from `app_params.yaml`. The node is created with
`automatically_declare_parameters_from_overrides=True`, so the `prompts.*` and `phrases.*` keys
are declared from the files.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `transcription_topics` | string[] | `["/listened_text", "/transcription"]` | What the user says: simple_hri's transcriptions (`/listened_text`) and typed text (`/transcription`, e.g. the GUI's composer), cleaned of noise and republished on `/interaction/user_utterance`. |
| `user_state_service` | string | `""` | The perception subsystem's service for the user's state (emotion), `/interaction_perception/get_user_state`. Off by default ([below](#notes-on-the-intelligence-parameters)). |
| `language` | string | `English` | The language of the conversation, which selects a language file ([below](#notes-on-the-intelligence-parameters)). English and Spanish ship. |
| `history_max_turns` | int | `200` | Maximum turns kept in a frame's history. The oldest half is dropped when exceeded. |
| `ack_phrases` | string[] | `[""]` (language default) | Acknowledgments that the system (not the LLM) adds after a successful slot extraction. Empty: the set of the language file (`ack_phrases`); set a list to override. |
| `ack_enabled` | bool | `true` | `false` never prefixes replies with an acknowledgment. |
| `filler_phrases` | string[] | `[""]` (language default) | The conversational fillers, said (never generated) while a reply is prepared, one per step of the ladder, in order; the last one repeats. Empty: the set of the language file. |
| `llm_timeout` | float | `15.0` | Seconds to wait for one call to the LLM; `0` = no limit of its own. |
| `reasoning` | string | `"none"` | Whether the LLM may reason before answering: `none` (never) or `critical` (only before the understanding questions whose wrong answer is a critical error). |
| `reasoning_timeout` | float | `120.0` | Seconds that a call that may reason waits for the LLM; `0` = no limit. |
| `timeout_prompt` | string | `""` (language default) | The first phrase spoken when the user stays silent for `wait_timeout`. Empty: the phrase of the language file (`phrases.timeout`; English: `Take your time, I'm still here.`). |
| `llm_provider` | string | `""` | Provider of the model of the questions. Empty: the model binding's `default_provider`. Read by the launch file ([below](#the-interpretation-server)). |
| `llm_model` | string | `""` | Model of the questions. Empty: the binding's default (`default_model` for the default provider, else `<provider>.model`). Read by the launch file. |
| `routes` | string[] | `[""]` | Questions sent to a model other than `llm_model`, as `question=model`. `[""]`: every question goes to `llm_model`. Checked by the node; the launch file gives each routed question its route. |
| `interpretation_service` | string | `/interaction/interpretation/interpret` | The interpretation server's service, which every question to the model is asked to. |
| `interpretation_cancel_service` | string | `""` | Its cancel service. Empty: `cancel` next to `interpretation_service`. |
| `silent_mode` | bool | `false` | Disable informational logs when true. |
| `conversation_log_path` | string | `/tmp/dialog_conversations.log` | Transcript of every conversation. `""` disables it. |
| `conversation_log_max_mb` | float | `10.0` | Rotate the transcript when it exceeds this size in MB. `0` = never rotate. |
| `skill_modules` | string[] | `[""]` | Python modules with additional skills (and dialogue modes) to register at startup, e.g. those of a new dialogue behavior ([ADDING_DIALOGUE_BEHAVIORS.md](ADDING_DIALOGUE_BEHAVIORS.md)). |
| `llm_client_factory` | string | `""` | Tests only: `<module>:<callable>` returning an object with the methods of `LLMDialogClient`, used instead of the interpretation server (e.g. `interaction_sim.scripted_llm:make`); the launch file then starts no server. |
| `prompts.*` | string | `prompts.yaml` | The prompt templates ([PROMPTS.md](PROMPTS.md)). |
| `phrases.*` | string | `""` | Overrides of the fixed phrases of the active language, over those of its language file, written by the GUI's Strategies page ([below](#notes-on-the-intelligence-parameters)). |

### Notes on the intelligence parameters

- `user_state_service` is off by default because the emotion detector is preliminary work and not
  validated ([interaction_perception](../interaction_perception/interaction_perception/README.md)).
  Without it, the emotion is neutral and never reaches the prompts.
- `language` names a language file of `interaction_intelligence/config/languages/` by its name,
  code or an alias, in any case (`English`, `en`, `Spanish`, `es`, `español`), or gives the path
  of a language file. It sets the language that the prompts ask the replies in (`{language}`, the
  file's name) and every language-dependent resource: the fixed phrases, acknowledgments and
  fillers, the exact stop commands, the yes/no words and the noise phrases
  ([ADDING_A_LANGUAGE.md](ADDING_A_LANGUAGE.md)). A language without a file stops the node at
  startup and lists the available languages; the node never falls back to English silently.
- `ack_phrases`: the English set is `Noted.`, `Done.`, `Got it.`, `Understood.` and `Alright.`.
- `filler_phrases`: the English set of the language file is `Hmm…`, `One moment…`,
  `Let me think about it for a moment…`, `Almost there…` and `Still on it, just a second…`; the
  Spanish set is `Mmm…`, `Un momento…`, `Déjame pensarlo un momento…`, `Ya casi lo tengo…` and
  `Sigo con ello, un segundito…`. Set a list to override them. The orchestrator's
  `filler_after_s` sets when they are said ([conversational fillers](behaviors/fillers.md)).
- `llm_timeout`: a call that times out is not asked again, and the other calls of the same skill
  fail at once, because the LLM is taken as unresponsive for the rest of that request. The
  reply's time budget (`reply_budget`, sent with each request) can make the wait shorter
  ([the wait for the model](behaviors/endings.md#the-wait-for-the-model)). The wait of each call
  is sent to the interpretation server as the request's budget, which cuts the call there too.
  `llm_timeout` is independent of the binding's `<provider>.timeout`, the route's limit of one
  request to the model's server.
- `reasoning: critical` lets the LLM reason before these questions: the answer to a confirmation,
  quiz grading, teach-back, the answer from the material and its verification, the answer to "do
  you want to stop?", the questionnaire option, the procedure step and the role-play turn. It
  never reasons when a line is generated. Each of those requests asks the variant `reasoning` of
  its question, answered on the route with `think` on; a model that cannot reason ignores it. The evaluation decides it per
  model in E0a (the `@critical` candidates), and a request may switch it on or off
  (`DialogTask.reasoning`). A reasoning call that returns nothing usable (past its token
  allowance, or without JSON) is asked again at once without reasoning.
- `reasoning_timeout`: reasoning is not cut, so neither `llm_timeout` nor the reply's budget bound
  such a call, and its time is not charged to the budget. Only a hung model reaches
  `reasoning_timeout`; the model is then taken as unresponsive, as with `llm_timeout`, and the
  question falls back to its safe default.
- `routes`: each question is named by the method of `LLMDialogClient` that asks it, or
  `extract_slots` for `extract_turn`, e.g. `["extract_turn=qwen3:14b",
  "evaluate_quiz_answer=qwen3:14b"]`. An unknown question stops the node at startup
  ([MODELS.md](MODELS.md#one-model-per-question-routes)). The launch file gives the interpretation
  server two routes per routed model (with `think` off and on) and the routed questions those
  routes.
- `phrases.*` has these keys:
  - `ack` (a list), `filler` (a list, over `filler_phrases`) and `timeout`;
  - the lines said without the LLM: `silence_ready`, `cancel_no`, the closing lines
    `close_user_silent`, `close_breakdown`, `close_turn_limit` and `close_error`, `slot_skipped`,
    the closing of a confirmed form `confirmed_closing`, `quiz_unanswered`, the teach-back
    questions `teach_back` and `teach_back_again`, and the closing reminder `key_points_summary`;
  - grounded question answering's `qa_invite`, `qa_more`, `qa_abstain`, `qa_decline` and `qa_bye`;
  - the questionnaire's `questionnaire_intro`, `questionnaire_item`, `questionnaire_options`,
    `questionnaire_or` and `questionnaire_end`;
  - the fallbacks of each dialogue behavior.

## llm_manager_node

The section `llm_manager_node` of the parameter files is the *model binding*: which provider and
model answer the questions, and their options. It keeps the parameters of the former LLM gateway
(`ros2_llm_manager`), whose node the interaction system no longer starts: the launch file reads
them, with the intelligence's `llm_provider`, `llm_model` and `routes`, and writes the routes of
the interpretation server ([below](#the-interpretation-server)). The values below are the shipped
ones: `ros2_llm_manager/config/params.yaml` and the `llm_manager_node` section of `app_params.yaml`
ship the same values, with Ollama as the only enabled provider. Some differ from the code
([discrepancies](#code-vs-yaml-discrepancies)). The model of the conversations is `ollama.model`
unless the intelligence's `llm_model` names another. `max_concurrent`, `silent_mode` and the
`enable` of the cloud providers are not used; `ollama.enable` makes the launch file load
`ollama.model` into the server's memory at the start, as the gateway did.

The cloud providers read their API key from the environment when `<provider>.api_key` is left
empty, which is the recommended setup; a key given here is passed to the interpretation server in
that variable of its environment, never as a parameter. The variables are `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`
(a key of the Claude API, which a Claude.ai subscription does not include) and `OPENAI_API_KEY`.
The interpretation server's backends need no SDK; the former gateway's are listed in its
[README](../ros2_llm_manager/README.md#providers).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `default_provider` | string | `ollama` | Provider of the questions when the intelligence's `llm_provider` names none (`gemini`, `ollama`, `claude` or `openai`). |
| `default_model` | string | `""` | Model of the questions of `default_provider` when the intelligence's `llm_model` names none. Empty: the `<provider>.model` of the default provider. |
| `max_concurrent` | int | `4` | Maximum number of concurrent inference threads. |
| `silent_mode` | bool | `false` | Disable informational logs when true. |
| `ollama.enable` | bool | `true` | Enable the Ollama provider and connect at startup. |
| `ollama.host` | string | `127.0.0.1` | Ollama server host. |
| `ollama.port` | int | `11434` | Ollama server port. |
| `ollama.model` | string | `qwen2.5:7b` | Model loaded into the Ollama server's memory at the start, and the model of the questions when neither `llm_model` nor `default_model` names another. Pull it, or set the model you serve. |
| `ollama.timeout` | float | `120.0` | Request timeout in seconds for Ollama inference calls: the route's own limit (`timeout_s`). The interaction intelligence stops waiting earlier, at its `llm_timeout`, which the request carries as its budget. |
| `ollama.think` | bool or string | `false` | Lets reasoning models (`qwen3.5`, `deepseek-r1`…) think before answering; `false` keeps spoken replies fast. A level (`low`, `medium`, `high`) is for models that cannot switch reasoning off (`gpt-oss`). |
| `ollama.num_ctx` | int | `8192` | Context window of every request, in tokens, sent with each request ([below](#notes-on-the-model-binding)). |
| `gemini.enable` | bool | `false` | Enable the Gemini provider and connect at startup. |
| `gemini.api_key` | string | `""` | Google Gemini API key. Prefer the `GEMINI_API_KEY` environment variable and leave this empty. |
| `gemini.model` | string | `gemini-2.5-flash` | Gemini model name. |
| `gemini.timeout` | float | `60.0` | Request timeout in seconds for Gemini inference calls. |
| `gemini.base_url` | string | `""` | Optional server with Gemini's REST API instead of Google's; the evaluation's pilot points it at a local stand-in ([PILOT.md](../evaluation/doc/PILOT.md)). Empty: Google's API. |
| `claude.enable` | bool | `false` | Enable the Anthropic Claude provider and connect at startup (checks the key and model). |
| `claude.api_key` | string | `""` | Claude API key. Prefer the `ANTHROPIC_API_KEY` environment variable and leave this empty. |
| `claude.model` | string | `claude-haiku-5-5` | Claude model id. Haiku is the fastest and cheapest; `claude-sonnet-5-5` and `claude-opus-5-5` are more capable and more expensive. |
| `claude.timeout` | float | `60.0` | Request timeout in seconds for Claude inference calls. |
| `claude.effort` | string | `low` | Thinking effort: `low`, `medium`, `high`, `xhigh` or `max`. Current Claude models always think; `low` keeps spoken replies fast. Empty omits it (older models). |
| `openai.enable` | bool | `false` | Enable the OpenAI provider and connect at startup (checks the key and model). |
| `openai.api_key` | string | `""` | OpenAI API key. Prefer the `OPENAI_API_KEY` environment variable and leave this empty. |
| `openai.model` | string | `gpt-6-luna` | OpenAI model id. `gpt-6-luna` is the fastest and cheapest; `gpt-6.1-sol` and `gpt-6-astra` are more capable. With `base_url`, the model name of that server. |
| `openai.timeout` | float | `60.0` | Request timeout in seconds for OpenAI inference calls. |
| `openai.reasoning_effort` | string | `none` | `none`, `low`, `medium`, `high`, `xhigh` or `max`. `none` keeps spoken replies fast. Empty omits it (models without reasoning). |
| `openai.base_url` | string | `""` | Optional OpenAI-compatible server (vLLM, LM Studio, OpenRouter…), e.g. `http://127.0.0.1:1234/v1`. Empty: api.openai.com. |

### Notes on the model binding

- `ollama.think` is the `think` of the route `dialogue` (the route `reasoning` has it on): models
  without thinking ignore it, and thinking makes replies slower. When the
  model may think, each request gets 4096 tokens more than its cap, because the reasoning tokens
  count toward the cap and a short cap would leave the answer empty; in the measurements, no
  reasoning call of the critical questions needed that many. The evaluation sets the value per
  model: the value that the model accepts with its reasoning off, or `low` for `gpt-oss`. Phase 1
  of E0a (`run_e0a.py phase-a`) records it.
- `ollama.num_ctx` is sent with each request so that the context window does not depend on the
  server's default, which changes with its version and configuration; a longer prompt is cut
  silently. Both phases of E0a, the judges and E1 use the same 8192.

## The interpretation server

`interaction_system.launch.py` starts behavior_architecture's `interpretation_server` in the
namespace `interaction` (launch argument `interpretation`, `true` by default), with these
parameters, written from the parameter files by `interaction_intelligence`'s
`interpretation_config.py`:

| Parameter | Value |
| --- | --- |
| `questions_file` | `config/interpretation/questions.yaml` of `interaction_intelligence`, or a copy with the routed questions' routes and, when the intelligence loads `interaction_ablations.skills`, the LLM-led baseline's question (`interaction_ablations`'s `config/interpretation/questions.yaml`) |
| `templates_file`, `templates_files` | `prompts.yaml` (launch argument `prompts_file`), then each parameter file with `prompts.*` (the GUI's Prompts page), in the order the intelligence reads them |
| `question_modules` | `interaction_intelligence.interpretation_questions` (and `interaction_ablations.interpretation_questions` with the baseline) |
| `default_route` | `dialogue` |
| `routes.dialogue` | The binding's provider and model (`llm_provider`, `llm_model`, or the binding's defaults) |
| `routes.reasoning` | The same, with `think` on: the variant `reasoning` of the questions that may reason |
| `routes.model_<i>`, `routes.model_<i>_reasoning` | The same for each model of the intelligence's `routes` |
| `log_dir` | Launch argument `interpretation_log_dir` (empty: no call log) |

Each route has the `llm_manager` dialect of its backend, which sends the request of the former
gateway's provider:

| Provider | Backend | Options of the route |
| --- | --- | --- |
| `ollama` | `ollama` | `url` `http://<ollama.host>:<ollama.port>`, `timeout_s` (`ollama.timeout`), `num_ctx` (`ollama.num_ctx`), `think` (`ollama.think`; `true` on the reasoning routes), `keep_alive` `-1` |
| `openai` | `openai` | `timeout_s` (`openai.timeout`), `reasoning_effort`, `url` (`openai.base_url`, when set); key `OPENAI_API_KEY` |
| `gemini` | `gemini` | `timeout_s`, `url` (`gemini.base_url`, when set); key `GEMINI_API_KEY` |
| `claude` | `anthropic` | `timeout_s`, `effort` (`claude.effort`); key `ANTHROPIC_API_KEY` |

With a test double (`llm_client_factory`), or without a provider, no server is started. Another
server can serve the questions with `interpretation:=false` and the intelligence's
`interpretation_service`.

## simple_hri's speech nodes

`main.launch.py` starts them with these parameters, taken from its launch arguments
([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md)) and from the `speech` settings of the language file of
the conversation ([ADDING_A_LANGUAGE.md](ADDING_A_LANGUAGE.md)). The other parameters keep
simple_hri's defaults; its README lists every parameter of every backend.

| Node (name) | Parameter | Value |
| --- | --- | --- |
| `stt_faster_whisper` or `stt_whisper` (`stt_service_node`) | `model` | `whisper_model` (`small`) |
| | `language` | `speech.stt_language` of the language file (`en`, `es`) |
| | `input_device` | `input_device`, only when it is not empty (`stt_faster_whisper` only) |
| `tts_piper` (`tts_service_node`) | `lang_code` | `speech.stt_language` of the language file (`en`, `es`) |
| | `voice` | `speech.piper_voice` of the language file (`en_US-lessac-medium`, `es_ES-sharvard-medium`) |
| | `speaker` | `speech.piper_speaker`, only when it is 0 or more (a voice with several speakers); `-1`: not passed, the voice's own |
| | `play_sound` | `true` |
| `tts_mms` (`tts_service_node`) | `lang_code` | `speech.mms_voice` of the language file (`eng`, `spa`) |
| | `use_gpu`, `play_sound` | `true` |

## user_state_server

Declared in
`interaction_perception/interaction_perception/interaction_perception/user_state_server.py`. The
node keeps the latest estimates of the detectors and serves them on
`/interaction_perception/get_user_state`.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `emotion_topic` | string | `/user_emotion` | The emotion detector's output. |
| `speaking_topic` | string | `/user_speaking` | The lip activity detector's output. |

## emotion_detector

Preliminary work, not validated
([interaction_perception](../interaction_perception/interaction_perception/README.md)). Declared
in `interaction_perception/interaction_perception/interaction_perception/emotion_detector.py`;
shipped values from `interaction_perception/interaction_perception/config/params.yaml`.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `input_topic` | string | `/camera/image_raw` | Camera image topic consumed by the detector. |
| `output_topic` | string | `/user_emotion` | Topic publishing the detected emotion as JSON (`{"emotion", "confidence", "source"}`). |
| `model_name` | string | `dima806/face_emotions_image_detection` | Hugging Face model used for emotion classification. |
| `show_visualization` | bool | `true` | Show an OpenCV visualization window when true. `interaction_perception.launch.py` sets it from its `visualization` argument (`false`). |

## lip_activity_detector_node

Declared in
`interaction_perception/interaction_perception/interaction_perception/lip_activity_detector.py`;
shipped values from `interaction_perception/interaction_perception/config/lip_activity_params.yaml`.
The node detects that the user is speaking from the movement of the lips alone (MediaPipe face
landmarks, no audio). The node key of the YAML file must match the node name of the launch file
(`lip_activity_detector_node`).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `image_topic` | string | `/camera/image_raw` | Camera image topic consumed by the detector. |
| `speaking_topic` | string | `/user_speaking` | Topic publishing the user-speaking `Bool` signal. |
| `lip_activity_topic` | string | `/lip_activity` | Topic publishing the raw lip-activity signal. |
| `min_detection_confidence` | float | `0.5` | MediaPipe face detection confidence threshold. |
| `min_tracking_confidence` | float | `0.5` | MediaPipe face tracking confidence threshold. |
| `open_threshold` | float | `0.04` | Margin above the resting-mouth baseline counted as "mouth open". |
| `smoothing_frames` | int | `4` | Light smoothing window that removes single-frame spikes. |
| `roughness_threshold` | float | `7.0e-6` | Variance-of-diffs threshold used to classify movement as speech. |
| `roughness_window` | int | `10` | Window (frames) over which roughness is computed (~0.33 s at 30 fps). |
| `onset_secs` | float | `0.90` | Minimum continuous high-roughness time required to enter the SPEAKING state. |
| `silence_secs` | float | `1.20` | Continuous closed-mouth time required before leaving the SPEAKING state. |
| `show_debug` | bool | `true` | Show an OpenCV debug window with landmarks, lip bars, and state. `interaction_perception.launch.py` sets it from its `visualization` argument (`false`). |

## dialostack_bt_runner

Declared in `dialostack_bt_client/src/bt_runner.cpp` (executable `bt_runner`), the generic runner
of trees that use `ExecuteDialog`. `example.launch.py` sets `bt_xml` to the package's
`config/example.xml`.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `bt_xml` | string | `""` | The tree file to run (required). |
| `tick_rate` | float | `10.0` | Ticks per second. |

## gesture_manager

Declared in `robots/nao/nao_pose_manager/nao_pose_manager/gesture_manager.py`. The node publishes
`JointState` for the visualization in RViz (simulation). It has no YAML file: the defaults are
those of the code.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `poses_file` | string | `nao_saved_poses.yaml` | YAML file with the saved arm poses to load. |
| `transition_speed` | float | `0.03` | Interpolation speed between poses (rad/tick). |
| `idle_interval` | float | `3.0` | Seconds between idle pose changes. |
| `hold_time_min` | float | `1.0` | Minimum seconds to hold a gesture. |
| `hold_time_max` | float | `2.5` | Maximum seconds to hold a gesture. |

## arm_gesture_manager

Declared in `robots/nao/nao_pose_manager/nao_pose_manager/arm_gesture_manager.py`. The node
drives the arms of the real NAO. The defaults are those of the code; `arm_gesture_manager.launch.py` passes
its own launch arguments ([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md#nao)), noted below.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `poses_file` | string | `""` | Path to the YAML file with saved poses. Launch default: the package's `config/nao_saved_poses.yaml`. |
| `transition_speed` | float | `0.03` | Interpolation speed between poses (rad/tick at 20 Hz). Launch default: `0.05`. |
| `idle_interval` | float | `3.0` | Seconds between idle pose changes. Launch default: `3.0`. |
| `hold_time_min` | float | `1.0` | Minimum seconds to hold a gesture. Launch default: `1.0`. |
| `hold_time_max` | float | `2.5` | Maximum seconds to hold a gesture. Launch default: `2.5`. |
| `stiffness` | float | `0.8` | Arm motor stiffness (`0.0`–`1.0`). Launch default: `0.8`. |

## eye_led_feedback

Declared in `robots/nao/nao_pose_manager/nao_pose_manager/eye_led_feedback.py`. The node drives
the NAO's eye LEDs: a warm white pulse while the robot speaks (`/is_speaking`), green with the
user's voice activity (`/user_vad`, when something publishes it) and dim blue otherwise. It is
launched without a YAML file or launch arguments, so the defaults of the code apply.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `vad_threshold` | float | `0.35` | Minimum VAD value to consider the user speaking (green eyes). |
| `pulse_hz` | float | `0.7` | Pulse frequency of the warm-white "robot speaking" state. |
| `pulse_amp` | float | `0.20` | Pulse brightness amplitude (± brightness). |
| `fade_speed` | float | `0.12` | Color interpolation step per tick (10 Hz loop). |

## Code vs. YAML discrepancies

These are the differences between the defaults declared in the code (`declare_parameter`) and
the shipped YAML or launch values. A node started without its YAML file runs with the code
defaults.

### Model binding

`ros2_llm_manager/config/params.yaml` and `app_params.yaml` ship the same values:

- `default_provider`: code default `""`. Both YAML files ship `ollama`.
- `ollama.enable`: code default `false`. Both YAML files ship `true`.
- `ollama.model`: code default `llama3.2:3b`. Both YAML files ship `qwen2.5:7b` (pull it, or set
  the model you serve).
- `gemini.model`: `gemini-2.5-flash` (code default and both YAML files).

### Orchestrator

`control_cycle_rate_ms` has a code default of `100` (behavior_architecture's
`BaseOrchestrator`); `interaction_orchestrator_params.yaml` ships `50`. The limits
(`wait_timeout`, `max_silence`, `max_stalled`, `max_turns`, `max_attempts`, `max_off_task`) and
the filler times (`filler_after_s`, `filler_repeat_s`) ship the same values as the code in both
YAML files.

### Perception

`show_debug` of the lip activity detector has a code default of `false`;
`lip_activity_params.yaml` ships `true`. `show_visualization` of the emotion detector is `true`
in the code and in `params.yaml`. `interaction_perception.launch.py` sets both from its
`visualization` argument (`false`); `lip_detector.launch.py` and `emotion_detector.launch.py`
keep the YAML values.

### Arm gesture manager

`transition_speed` has a code default of `0.03`; `arm_gesture_manager.launch.py` passes `0.05`.
`poses_file` has a code default of `""`; the launch file passes the package's
`config/nao_saved_poses.yaml`.
