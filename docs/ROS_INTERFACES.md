# ROS interfaces

Every action, service, topic and message of the interaction system and of the subsystems around
it, with its type, its server or publisher and its users. How the nodes fit together is in
[ARCHITECTURE.md](ARCHITECTURE.md), and the parameters of each node in
[CONFIGURATION.md](CONFIGURATION.md).

## The public interface

What the behaviors of the robot use.

| Name | Type | Server / publisher | Purpose |
|---|---|---|---|
| `/dialog/execute_task` | action `ros2_dialog_interfaces/action/DialogTask` | `/interaction_orchestrator` | Hold a conversation |
| `/interaction_system/status` | topic `interaction_system_interfaces/msg/InteractionSystemStatus` | `/interaction_orchestrator` | The state of the system, every control cycle |
| `/robot_utterance` | topic `std_msgs/String` | `Speak` (tree node) | Each robot line, when it starts to be said (also read by `/interaction_intelligence` for the transcript, and by the GUI) |
| `/robot_filler` | topic `std_msgs/String` | `WithFillers` (tree node) | Each conversational filler ("Hmm…"), when it starts to be said: a robot utterance that is not a line of the conversation, kept off `/robot_utterance` (read by `/interaction_intelligence` for the transcript, marked `filler`) ([Conversational fillers](behaviors/fillers.md)) |
| `/barge_in` | topic `ros2_dialog_interfaces/msg/BargeIn` | `Speak` | The user cut the robot off (experimental): `utterance` (the text cut short) and `spoken_ms` (`uint32`, how long it had been said, in ms) |

### `DialogTask` goal

| Field | Type | Meaning |
|---|---|---|
| `task_description` | `string` | What the robot must accomplish, in natural language. Required |
| `dialog_mode` | `string` | `slot_filling`, `explanation`, `quiz`, `procedure`, `roleplay`, `grounded_qa` or `questionnaire`; empty: the system chooses ([ARCHITECTURE.md](ARCHITECTURE.md#the-choice-of-the-dialogue-behavior)) |
| `frame_schema_json` | `string` | Slot filling: the slot schema ([SLOT_SCHEMA.md](SLOT_SCHEMA.md)); empty: the LLM designs it from the task |
| `initial_frame_json` | `string` | Slot filling: a JSON object of values that pre-fill the frame (to resume) |
| `resources_json` | `string` | A JSON array of `{name, description, content}`. Reference text for any strategy, and what each strategy needs, in the format of its page: the key points ([explanation](behaviors/explanation.md#what-it-needs-and-returns)), the question bank ([quiz](behaviors/quiz.md#what-it-needs-and-returns)), the steps ([procedure](behaviors/procedure.md#what-it-needs-and-returns)), the scene ([role play](behaviors/roleplay.md#what-it-needs-and-returns)), the material and the `handover` sentence ([grounded question answering](behaviors/grounded_qa.md#what-it-needs-and-returns)), the instrument ([questionnaire](behaviors/questionnaire.md#what-it-needs-and-returns)) |
| `domain` | `string` | A hint injected into the context of the prompts (e.g. "hospital", "coffee shop") |
| `skip_intro` | `bool` | Skip the greeting (chained conversations) |
| `max_turns` | `int32` | Most user turns (outcome `turn_limit`). Default `-1` |
| `max_silence` | `float64` | Seconds of continuous silence of the user that end the conversation (outcome `user_silent`). Default `-1.0` |
| `max_stalled` | `int32` | User turns in a row without progress (not understood, asked to repeat) that end the conversation (outcome `breakdown`). Default `-1` |
| `filler_after_s` | `float64` | Seconds the robot may take to answer before it says a fixed conversational filler ("Hmm…", then a ladder of fixed phrases as the wait grows; not turns) ([Conversational fillers](behaviors/fillers.md)); `0`: no fillers. Default `-1.0` |
| `max_attempts` | `int32` | Attempts per element (explanations of a topic, helps with a step, askings of a slot, repetitions of a quiz question) ([recovery and breakdown](behaviors/no_progress.md)). Default `-1` |
| `max_off_task` | `int32` | Requests in a row outside the task, in grounded question answering (a question within the task resets the count, a reaction does not), that end the conversation (outcome `breakdown`, reason `Off-task: …`) ([grounded QA](behaviors/grounded_qa.md)); `0`: unlimited. Default `-1` |
| `filler_repeat_s` | `float64` | After the ladder of fillers (2, 5, 9 and 14 s), seconds between fillers. Default `-1.0` |
| `wait_timeout` | `float64` | Seconds of silence of the user before each prompt that the robot is waiting ([silence](behaviors/silence.md)); `-1.0` or `0`: the system's value. Default `-1.0` |
| `reply_budget`, `opening_budget` | `float64` | Seconds the intelligence may spend asking the model for a reply (the opening) before it answers with the fallbacks ([the wait for the model](behaviors/endings.md#the-wait-for-the-model)); `0`: no budget. Default `-1.0` |
| `acknowledgments` | `int32` | Whether the robot prefixes its replies with a short acknowledgment ("Noted."): `1` yes, `0` no. Default `-1` |

Every configurable limit of the mechanisms travels in the goal (decided by the author,
2026-11-01): `-1` (`USE_DEFAULT`) takes the system's value (the parameters of the orchestrator and
the intelligence, [CONFIGURATION.md](CONFIGURATION.md)). The safety limits of the system itself
(`tts_timeout`, `intelligence_timeout`, `llm_timeout`) are not per request.

The limits are optional. The constants of the goal name their special values:

| Constant | Value | Meaning |
|---|---|---|
| `DialogTask.Goal.USE_DEFAULT` | `-1` (`int32`) | Use the parameter of the orchestrator of the same name ([CONFIGURATION.md](CONFIGURATION.md#interaction_orchestrator)) |
| `DialogTask.Goal.USE_DEFAULT_SECONDS` | `-1.0` (`float64`) | The same, for `max_silence` and `filler_after_s` |
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
| `outcome` | `string` | How the conversation ended: one of the constants `DialogTask.Result.OUTCOME_COMPLETED` (`completed`), `OUTCOME_PARTIAL` (`partial`), `OUTCOME_FAILED` (`failed`), `OUTCOME_CANCELLED` (`cancelled`), `OUTCOME_USER_SILENT` (`user_silent`), `OUTCOME_BREAKDOWN` (`breakdown`), `OUTCOME_TURN_LIMIT` (`turn_limit`), `OUTCOME_ERROR` (`error`). Their meaning: [Endings and outcome](behaviors/endings.md#the-outcomes) |
| `final_frame_json` | `string` | The data of the conversation (the frame, the quiz results, the step statuses, the scores), also when it did not complete. Its fields are on the page of each strategy |
| `failure_reason` | `string` | Why it did not complete; empty when `completed` ([examples](behaviors/endings.md#the-outcomes)) |
| `total_turns` | `int32` | User turns |

The goal is rejected when another conversation is being prepared or run. A goal that completes
succeeds; any other outcome aborts it; cancelling the goal stops the conversation within a control
cycle (the voice and the listening are halted) and returns `canceled` with outcome `cancelled`.

### `InteractionSystemStatus`

| Field | Type | Meaning |
|---|---|---|
| `status` | `int8` | `IDLE` (0), `REASONING` (1: choosing the behavior, preparing the request), `RUNNING` (2), `FAILED` (3: the last conversation did not succeed; until the next request) |
| `behavior` | `string` | The behavior running (e.g. `SlotFilling`), while `RUNNING` |
| `phase` | `string` | Its phase, while `RUNNING` |

## Inside the interaction system

| Name | Type | Server / publisher | Client / subscriber | Purpose |
|---|---|---|---|---|
| `/interaction/ask_intelligence_for_behavior` | service `interaction_system_interfaces/srv/AskForDialogueBehavior` | `/interaction_intelligence` | `/interaction_orchestrator` | Which behavior fits a request without `dialog_mode` |
| `/interaction/ask_intelligence` | service `interaction_system_interfaces/srv/AskIntelligence` | `/interaction_intelligence` | `AskIntelligence` (tree node), `/interaction_orchestrator` (`finish`) | One skill of a tree |
| `/behavior_status` | topic `std_msgs/String` | behavior runners | `/interaction_orchestrator` | `RUNNING`, `SUCCESS`, `FAILURE`, `DEACTIVATED` |
| `/interaction/user_utterance` | topic `std_msgs/String` | `/interaction_intelligence` | `Listen` | The transcriptions that are not noise |
| `/interaction/user_turn` | topic `std_msgs/String` | `Listen` | `/interaction_intelligence` (transcript) | The utterances taken as turns |
| `/interaction/skill_trace` | topic `std_msgs/String` (JSON) | `/interaction_intelligence` | tools, evaluation | Every skill asked: `dialogue_id`, `skill`, `label`, `success`, `time_s`, `message`, `budget_s` (the time budget it got; 0: none), `llm_timeouts` and `llm_skipped` (its calls to the model that timed out, and those not made) |
| lifecycle services of `/interaction_orchestrator` and the runners | `lifecycle_msgs` | | `mission_executor` | Activation |

`AskForDialogueBehavior`: request `task_description`, `domain`, `resources_json`, `behaviors`
(`DialogueBehaviorDetails[]`: `behavior_id` (`int8`), `name`, `mode`, `explanation`, `needs`);
response `success`, `message`, `behavior_id` (`int8`, `-1` if none can be offered),
`offered_ids` (`int8[]`).

`AskIntelligence`: request `dialogue_id`, `skill`, `state` (JSON), `user_text`, `option`, `number`
(`int32`), `args` (JSON), `budget_s` (`float64`: what is left of the time budget of the reply
being prepared, after which the skill's questions fall back without asking the model; 0 = no
budget, each call waits at most `llm_timeout`); response `success`, `message`, `state`, `reply`,
`label`, `phase`, `data`.

## The speech capability

simple_hri ([SPEECH.md](SPEECH.md)).

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

## The LLM gateway

| Name | Type | Server | Client |
|---|---|---|---|
| `/llm/inference` | action `ros2_llm_interfaces/action/LlmInference` | `/llm_manager_node` | `/interaction_intelligence` |
| `/llm/session/create`, `/llm/session/delete`, `/llm/session/list` | services `ros2_llm_interfaces/srv/CreateSession`, `DeleteSession`, `ListSessions` | `/llm_manager_node` | `/interaction_intelligence` |
| `/llm/error` | topic `ros2_llm_interfaces/msg/LlmError` | `/llm_manager_node` | the GUI |

`LlmInference`: goal `session_id`, `prompt`, `max_tokens`, `format_schema` (a JSON schema, empty
for free text), `cache_prefix_len` (characters of the prompt that repeat across calls, a caching
hint; 0 for none); result `success`, `answer`, `total_time`, `prompt_tokens`,
`completion_tokens`, `status`; feedback `partial_text`, `tokens_generated`.

`CreateSession`: request `session_id`, `provider`, `model`, `system_prompt`, `stateful`;
response `success`, `session_id`, `message`. `DeleteSession`: request `session_id`; response
`success`, `deleted_count`, `message`. `ListSessions`: response `session_ids`, `providers`,
`models`, `stateful`, `history_turns`.

`LlmError` (every failed inference): `provider`, `model`, `session_id`, `code` (HTTP or status
code, 0 if unknown), `error_type` (`http`, `timeout`, `connection`, `api`, `unknown`), `message`.

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
