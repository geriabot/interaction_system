# ROS interfaces

This page lists every action, service, topic and message of the interaction system and of the
subsystems around it, with its type, its server or publisher and its users.
[ARCHITECTURE.md](ARCHITECTURE.md) explains how the nodes fit together, and
[CONFIGURATION.md](CONFIGURATION.md) lists the parameters of each node.

## The public interface

These are the interfaces that robot behaviors use.

| Name | Type | Server / publisher | Purpose |
|---|---|---|---|
| `/dialog/execute_task` | action `ros2_dialog_interfaces/action/DialogTask` | `/interaction_orchestrator` | Hold a conversation |
| `/interaction_system/status` | topic `interaction_system_interfaces/msg/InteractionSystemStatus` | `/interaction_orchestrator` | The state of the system, every control cycle |
| `/robot_utterance` | topic `std_msgs/String` | `Speak` (tree node) | Each robot line, when it starts to be said |
| `/robot_filler` | topic `std_msgs/String` | `WithFillers` (tree node) | Each conversational filler ("Hmm…"), when it starts to be said; kept off `/robot_utterance` because a filler is not a line of the conversation ([Conversational fillers](behaviors/fillers.md)) |
| `/barge_in` | topic `ros2_dialog_interfaces/msg/BargeIn` | `Speak` | The user cut the robot off (experimental): `utterance` (the text cut short) and `spoken_ms` (`uint32`, how long it had been said, in ms) |

`/interaction_intelligence` reads `/robot_utterance` and `/robot_filler` for the transcript, where
the fillers are marked `filler`; the GUI also reads `/robot_utterance`.

### `DialogTask` goal

| Field | Type | Meaning |
|---|---|---|
| `task_description` | `string` | What the robot must accomplish, in natural language. Required |
| `dialog_mode` | `string` | `slot_filling`, `explanation`, `quiz`, `procedure`, `roleplay`, `grounded_qa` or `questionnaire`; empty: the system chooses ([ARCHITECTURE.md](ARCHITECTURE.md#the-choice-of-the-dialogue-behavior)) |
| `frame_schema_json` | `string` | Slot filling: the slot schema ([SLOT_SCHEMA.md](SLOT_SCHEMA.md)); empty: the LLM designs it from the task |
| `initial_frame_json` | `string` | Slot filling: a JSON object of values that pre-fill the frame (to resume) |
| `resources_json` | `string` | A JSON array of `{name, description, content}`: reference text for any dialogue behavior, and what each dialogue behavior needs (formats below the table) |
| `domain` | `string` | A hint injected into the context of the prompts (e.g. "hospital", "coffee shop") |
| `skip_intro` | `bool` | Skip the greeting (chained conversations) |
| `max_turns` | `int32` | Maximum number of user turns (outcome `turn_limit`). Default `-1` |
| `max_silence` | `float64` | Seconds of continuous user silence that end the conversation (outcome `user_silent`). Default `-1.0` |
| `max_stalled` | `int32` | User turns in a row without progress (not understood, asked to repeat) that end the conversation (outcome `breakdown`). Default `-1` |
| `filler_after_s` | `float64` | Seconds the robot may take to answer before it says a fixed conversational filler ("Hmm…") ([Conversational fillers](behaviors/fillers.md)); `0`: no fillers. Default `-1.0` |
| `max_attempts` | `int32` | Attempts per element (explanations of a topic, helps with a step, attempts at a slot, repetitions of a quiz question) ([recovery and breakdown](behaviors/no_progress.md)). Default `-1` |
| `max_off_task` | `int32` | Requests in a row outside the task, in grounded question answering, that end the conversation (outcome `breakdown`, reason `Off-task: …`) ([grounded QA](behaviors/grounded_qa.md)); `0`: unlimited. Default `-1` |
| `filler_repeat_s` | `float64` | After the ladder of fillers (2, 5, 9 and 14 s), seconds between fillers. Default `-1.0` |
| `reasoning` | `int32` | Whether the LLM may reason before the critical understanding questions: `-1` the system's value (the intelligence's `reasoning`), `0` no reasoning at all, `1` on the critical questions. Default `-1` |
| `wait_timeout` | `float64` | Seconds of user silence before each prompt saying that the robot is waiting ([silence](behaviors/silence.md)); `-1.0` or `0`: the system's value. Default `-1.0` |
| `reply_budget`, `opening_budget` | `float64` | Seconds the intelligence may spend asking the LLM for a reply (the opening) before it answers with the fallbacks ([the wait for the model](behaviors/endings.md#the-wait-for-the-model)); `0`: no budget. Default `-1.0` |
| `acknowledgments` | `int32` | Whether the robot prefixes its replies with a short acknowledgment ("Noted."): `1` yes, `0` no. Default `-1` |

The `resources_json` entries follow the format on the page of each dialogue behavior: the key
points ([explanation](behaviors/explanation.md#what-it-needs-and-returns)), the question bank
([quiz](behaviors/quiz.md#what-it-needs-and-returns)), the steps
([procedure](behaviors/procedure.md#what-it-needs-and-returns)), the scene
([role play](behaviors/roleplay.md#what-it-needs-and-returns)), the material and the `handover`
sentence ([grounded question answering](behaviors/grounded_qa.md#what-it-needs-and-returns)), and
the instrument ([questionnaire](behaviors/questionnaire.md#what-it-needs-and-returns)).

For `max_off_task`, a question within the task resets the count, but a reaction does not.
Selective reasoning (`reasoning`) makes the critical questions more reliable and the turns
slower; the requesting behavior may switch it off, e.g. when the turns are too slow.

Every configurable limit of the mechanisms travels in the goal, so that each request can adapt
them to its task. `-1` (`USE_DEFAULT`) takes the system's value, that is, the parameters of the
orchestrator and the intelligence ([CONFIGURATION.md](CONFIGURATION.md)). The safety limits of
the system itself (`tts_timeout`, `intelligence_timeout`, `llm_timeout`) are not per request.

The limits are optional. The constants of the goal name their special values:

| Constant | Value | Meaning |
|---|---|---|
| `DialogTask.Goal.USE_DEFAULT` | `-1` (`int32`) | Use the parameter of the orchestrator of the same name ([CONFIGURATION.md](CONFIGURATION.md#interaction_orchestrator)) |
| `DialogTask.Goal.USE_DEFAULT_SECONDS` | `-1.0` (`float64`) | The same, for the `float64` fields: `max_silence`, `filler_after_s`, `filler_repeat_s`, `wait_timeout`, `reply_budget` and `opening_budget` |
| `DialogTask.Goal.UNLIMITED` | `0` | No limit (for `max_silence`: the conversation never ends on silence; for `filler_after_s`: no fillers) |

The mechanisms behind each limit are in [docs/behaviors](behaviors/README.md).

### `DialogTask` feedback (on every robot utterance) and result

The feedback is sent when each robot utterance starts to be said (not for a conversational
filler, which is not an utterance of the conversation):

| Feedback field | Type | Meaning |
|---|---|---|
| `state` | `string` | The phase of the conversation (e.g. `gathering`, `confirming`, `question_2_of_5`, `step_3_of_6`, `done`) |
| `current_frame_json` | `string` | The data so far (the frame, the quiz results...), JSON |
| `last_utterance` | `string` | What the robot is saying |
| `turns` | `int32` | User turns so far |

| Result field | Type | Meaning |
|---|---|---|
| `success` | `bool` | `true` only when `outcome` is `completed` |
| `outcome` | `string` | How the conversation ended: one of the `DialogTask.Result.OUTCOME_*` constants listed below the table ([Endings and outcome](behaviors/endings.md#the-outcomes)) |
| `final_frame_json` | `string` | The data of the conversation (the frame, the quiz results, the step statuses, the scores), also when it did not complete. Its fields are on the page of each dialogue behavior |
| `failure_reason` | `string` | Why it did not complete; empty when `completed` ([examples](behaviors/endings.md#the-outcomes)) |
| `total_turns` | `int32` | User turns |

The outcome constants are `DialogTask.Result.OUTCOME_COMPLETED` (`completed`),
`OUTCOME_PARTIAL` (`partial`), `OUTCOME_FAILED` (`failed`), `OUTCOME_CANCELLED` (`cancelled`),
`OUTCOME_USER_SILENT` (`user_silent`), `OUTCOME_BREAKDOWN` (`breakdown`), `OUTCOME_TURN_LIMIT`
(`turn_limit`) and `OUTCOME_ERROR` (`error`).

The goal is rejected when another conversation is being prepared or run. A goal that completes
succeeds, and any other outcome aborts it. Canceling the goal stops the conversation within a
control cycle (the voice and the listening are halted) and returns `canceled` with outcome
`cancelled`.

### `InteractionSystemStatus`

| Field | Type | Meaning |
|---|---|---|
| `status` | `int8` | `IDLE` (0), `REASONING` (1: choosing the dialogue behavior, preparing the request), `RUNNING` (2), `FAILED` (3: the last conversation did not succeed; until the next request) |
| `behavior` | `string` | The running dialogue behavior (e.g. `SlotFilling`), while `RUNNING` |
| `phase` | `string` | Its phase, while `RUNNING` |

## Inside the interaction system

| Name | Type | Server / publisher | Client / subscriber | Purpose |
|---|---|---|---|---|
| `/interaction/ask_intelligence_for_behavior` | service `interaction_system_interfaces/srv/AskForDialogueBehavior` | `/interaction_intelligence` | `/interaction_orchestrator` | Which dialogue behavior fits a request without `dialog_mode` |
| `/interaction/ask_intelligence` | service `interaction_system_interfaces/srv/AskIntelligence` | `/interaction_intelligence` | `AskIntelligence` (tree node), `/interaction_orchestrator` (`finish`) | One skill of a tree |
| `/behavior_status` | topic `std_msgs/String` | behavior runners | `/interaction_orchestrator` | `RUNNING`, `SUCCESS`, `FAILURE`, `DEACTIVATED` |
| `/interaction/user_utterance` | topic `std_msgs/String` | `/interaction_intelligence` | `Listen` | The transcriptions that are not noise |
| `/interaction/user_turn` | topic `std_msgs/String` | `Listen` | `/interaction_intelligence` (transcript) | The utterances taken as turns |
| `/interaction/skill_trace` | topic `std_msgs/String` (JSON) | `/interaction_intelligence` | tools, evaluation | Every skill asked, with its timing and its LLM calls (fields below the table) |
| lifecycle services of `/interaction_orchestrator` and the runners | `lifecycle_msgs` | | `mission_executor` | Activation |

The JSON of `/interaction/skill_trace` has `dialogue_id`, `skill`, `label`, `success`, `time_s`,
`message`, `budget_s` (the time budget the skill got; 0: none), `llm_timeouts` (its calls to the
LLM that timed out) and `llm_skipped` (its calls that were not made).

`AskForDialogueBehavior`: request `task_description`, `domain`, `resources_json`, `behaviors`
(`DialogueBehaviorDetails[]`: `behavior_id` (`int8`), `name`, `mode`, `explanation`, `needs`);
response `success`, `message`, `behavior_id` (`int8`, `-1` if none can be offered),
`offered_ids` (`int8[]`).

`AskIntelligence`: request `dialogue_id`, `skill`, `state` (JSON), `user_text`, `option`, `number`
(`int32`), `args` (JSON), `budget_s` (`float64`); response `success`, `message`, `state`, `reply`,
`label`, `phase`, `data`. `budget_s` is what is left of the time budget of the reply being
prepared; once it is spent, the skill's questions fall back without asking the LLM. With 0 there
is no budget, and each call waits at most `llm_timeout`.

## The speech capability

The speech capability is provided by simple_hri ([SPEECH.md](SPEECH.md)).

| Name | Type | Server / publisher | Used by |
|---|---|---|---|
| `/stt_action` | action `simple_hri_interfaces/action/Listen` (goal `max_wait`; result `success`, `timed_out`, `text`, `message`) | simple_hri `stt_faster_whisper` or `stt_whisper` (node `stt_service_node`) | `Listen` |
| `/listened_text` | topic `std_msgs/String` | simple_hri's speech-to-text | `/interaction_intelligence` |
| `/tts_action` | action `simple_hri_interfaces/action/Say` (goal `text`; result `success`, `message`) | simple_hri `tts_piper` or `tts_mms` (node `tts_service_node`) | `Speak`, `WithFillers` |
| `/transcription` | topic `std_msgs/String` | typed text (the GUI's composer, tests) | `/interaction_intelligence` |
| `/is_speaking` | topic `std_msgs/Bool` (reliable, transient local) | `Speak`, `WithFillers` | the NAO's gesture managers and eye LEDs, the GUI |

simple_hri also offers the services `/stt_service` and `/tts_service`, which the interaction
system does not use.

## The perception of the user

| Name | Type | Server / publisher | Used by |
|---|---|---|---|
| `/interaction_perception/get_user_state` | service `interaction_perception_msgs/srv/GetUserState` (response `available`, `state`) | `/user_state_server` | `/interaction_intelligence`, when `user_state_service` is set (off by default) |
| `/user_speaking` | topic `std_msgs/Bool` | `/lip_activity_detector_node` | `Speak` (barge-in; `user_speaking_topic`), `/user_state_server`, the GUI |
| `/user_emotion` | topic `std_msgs/String` (JSON `{emotion, confidence, source}`) | `/emotion_detector` | `/user_state_server` |
| `/lip_activity` | topic `std_msgs/Float32` (the lip-gap ratio) | `/lip_activity_detector_node` | debugging |
| `/camera/image_raw` | topic `sensor_msgs/Image` | the camera | both detectors (`image_topic`, `input_topic`) |

`UserState`: `stamp`, `emotion`, `confidence` (`float32`), `source`, `speaking` (`bool`).

## The interpretation server

Every question of the interaction intelligence to the model is a request to
behavior_architecture's interpretation server, which the interaction system launches in the
namespace `interaction` ([CONFIGURATION.md](CONFIGURATION.md#the-interpretation-server)).

| Name | Type | Server | Client |
|---|---|---|---|
| `/interaction/interpretation/interpret` | service `behavior_architecture/srv/Interpret` | `/interaction/interpretation_server` | `/interaction_intelligence` |
| `/interaction/interpretation/cancel` | service `behavior_architecture/srv/CancelInterpretation` | `/interaction/interpretation_server` | `/interaction_intelligence`, when a dialogue ends |

`Interpret` (behavior_architecture's
[INTERPRETATION.md](https://github.com/geriabot/behavior_architecture/blob/devel/docs/INTERPRETATION.md#the-service)):
request `question`, `request_id`, `context`, `text`, `images`, `budget_s`, `variant`,
`max_tokens`; response `success`, `answer`, `source`, `fallback_reason`, `message`. The
intelligence sends:

| Field | Value |
|---|---|
| `question` | The method of `LLMDialogClient` that asks (`extract_turn`, `classify_intent`, `get_opening`...; `roleplay_reply`; `monolithic_turn` for the LLM-led baseline), a question of `config/interpretation/questions.yaml`. |
| `request_id` | The dialogue's id (`select_<n>` for the choice of the dialogue behavior). |
| `context` | `{"language": ..., "args": {...}}`: the dialogue's language and the arguments of the PromptBuilder method that writes the prompt (`interpretation_context.py`), from which the server builds it. |
| `budget_s` | The time the client waits for this call: `llm_timeout`, or what is left of the reply's budget if that is shorter; `reasoning_timeout` for a call that may reason. |
| `variant` | `reasoning` for a call on which the model may reason (`reasoning: critical`), else empty. |
| `max_tokens` | The token cap of the call. |

The answer is `{"text": <the model's text>}`; the intelligence reads it as it read the former
gateway's answers. Source `invalid` (the model did not answer in time, could not be reached,
or the call was cancelled) is no answer.

`CancelInterpretation`: request `request_id`; response `success`, `cancelled`, `message`. The
intelligence cancels the questions in progress of a dialogue when it ends (skill `finish`).

The former LLM gateway (`ros2_llm_manager`: the action `/llm/inference` and the services
`/llm/session/*`) is still in the repository, but the interaction system does not start or use
it.

## The embodiment (NAO)

The nodes of `robots/nao/nao_pose_manager` ([robots/nao](../robots/nao/README.md)):

| Name | Type | Publisher | Subscriber |
|---|---|---|---|
| `/is_speaking` | `std_msgs/Bool` | `Speak`, `WithFillers` | `gesture_manager`, `arm_gesture_manager`, `eye_led_feedback` |
| `/user_vad` | `std_msgs/Float32` | nothing in the stack (optional) | `eye_led_feedback` |
| `/target_pose` | `std_msgs/String` | any node (force a pose; `AUTO` resumes) | `gesture_manager`, `arm_gesture_manager` |
| `/pose_tester_joints` | `sensor_msgs/JointState` | `gesture_manager`, `pose_tester` | RViz (simulation) |
| `/joint_states` | `sensor_msgs/JointState` | the robot or the simulation | `pose_saver` |
| `/save_pose_trigger` | `std_msgs/String` | `keyboard_trigger`, `gui_trigger` | `pose_saver` |
| `/selected_pose_name` | `std_msgs/String` | `pose_tester` | `gui_trigger` |
| `/effectors/joint_positions`, `/effectors/joint_stiffnesses` | `nao_lola_command_msgs` | `arm_gesture_manager` | the nao_lola bridge |
| `/effectors/left_eye_leds`, `/effectors/right_eye_leds` | `nao_lola_command_msgs` | `eye_led_feedback` | the nao_lola bridge |
