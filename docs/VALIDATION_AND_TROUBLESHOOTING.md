# Validation and troubleshooting

Checks that a built and sourced workspace runs, ways to test it without audio or a model, and
the causes and fixes of common problems at runtime. Build problems are in the
[README](../README.md#troubleshooting).

## Basic checks

```sh
ros2 node list                                    # /interaction_orchestrator, /interaction_intelligence, the runners, /llm_manager_node, the speech nodes
ros2 lifecycle get /interaction_orchestrator      # active
ros2 action list                                  # /dialog/execute_task, /llm/inference, /stt_action and /tts_action (simple_hri)
ros2 service list | grep -E "interaction|llm"     # /interaction/ask_intelligence, /interaction/ask_intelligence_for_behavior, /llm/session/*
ros2 topic echo /interaction_system/status        # 0 IDLE, 1 REASONING, 2 RUNNING, 3 FAILED
curl -s localhost:11434/api/version               # Ollama answers (local or through a tunnel)
```

During a conversation:

```sh
ros2 topic echo /robot_utterance                  # what the robot says
ros2 topic echo /interaction/user_turn            # the utterances taken as the user's turns
ros2 topic echo /interaction/skill_trace          # every skill asked: label, time, errors
ros2 topic echo /behavior_status                  # the active tree: RUNNING, SUCCESS, FAILURE
tail -f /tmp/dialog_conversations.log             # the transcript (conversation_log_path)
```

The active tree can be watched in Groot2 (port 5555).

## The interaction system without speech or LLM

Two tools test the system without audio and without a model:

- **Type as the user.** With any speech capability running, publish what the user says where a
  transcription would arrive: `ros2 topic pub --once /transcription std_msgs/msg/String "{data:
  'a large latte'}"`. The intelligence cleans it and `Listen` takes it as the next turn. (With
  simple_hri the robot also keeps asking its `/stt_action` to listen.)
- **The simulation and the scripted LLM.** `evaluation/simulation/interaction_sim` replaces the
  speech capability and the user, and `scripted_llm` replaces the LLM with fixed rules:

  ```sh
  ROS_DOMAIN_ID=<free id> python -m pytest src/interaction_system/evaluation/simulation/interaction_sim/test
  ```

  runs whole conversations through every branch of every tree
  ([evaluation/README.md](../evaluation/README.md)).

## Common problems

| Symptom | Cause and fix |
|---|---|
| The goal is rejected | Another conversation is running (one at a time). Cancel it, or wait for its result |
| The goal is aborted at once with `Unknown dialog mode` | `dialog_mode` is not a mode of the catalog. Leave it empty to let the system choose |
| Aborted with `No dialogue behavior fits the request` | No behavior of the catalog is offered for the request (none whose needs it provides) |
| Aborted with a message about the resources (`The quiz requires...`) | The strategy needs a resource that the request does not have or that does not parse ([ROS_INTERFACES.md](ROS_INTERFACES.md#dialogtask-goal)) |
| Aborted with `The interaction intelligence is not available` | `/interaction_intelligence` is not running (check its log: a malformed prompt template stops it at startup) |
| The robot speaks the fixed phrases ("Could you repeat that?") | The LLM does not answer: Ollama is not reachable, the model is not pulled (`ollama.model`), or the calls time out (`llm_timeout`, or the reply's `reply_budget`; the warnings of `/interaction_intelligence` say "did not answer" or "no call to the model", and the skill trace counts them in `llm_timeouts` and `llm_skipped`). The log of `/llm_manager_node` says which. A model that is just slow (a large model, a cloud model that reasons) can spend the budget in normal turns: use a faster one, or raise `reply_budget` |
| `LLM Manager connection failed` | `/llm_manager_node` is not running or its provider did not connect at startup |
| The robot never hears the user | Check that `/stt_action` exists (`speech:=simple_hri`) and the microphone is the default input of the computer running simple_hri ([SPEECH.md](SPEECH.md)) |
| Every utterance is ignored | The transcriptions are taken as noise (empty, fillers, subtitle credits), or they arrive on a topic not in `transcription_topics` |
| The conversation ends with outcome `user_silent` | The user stayed silent `max_silence` seconds, or speech-to-text is not running (`/stt_action`). Raise `max_silence` (parameter, or per request) for tasks in which the user needs time; for a procedure step that takes time, give the step a `duration_s` ([silence](behaviors/silence.md)) |
| The robot says "Take your time" too often, or too soon | `wait_timeout` is the silence before each prompt; raise it ([CONFIGURATION.md](CONFIGURATION.md#interaction_orchestrator)) |
| The conversation ends with outcome `breakdown` | `max_stalled` turns in a row were not understood or asked to repeat. Check the speech recognition (the user's lines in the transcript) and the LLM; raise `max_stalled` per request if the users need more repair ([recovery and breakdown](behaviors/no_progress.md)) |
| The outcome is `partial` | The conversation ended normally with an element left unfinished: `failure_reason` lists the unanswered slots or questions, or the steps skipped or stuck. The data are in `final_frame_json` |
| The robot says "something went wrong and I have to stop" (outcome `error`) | A skill failed, or the intelligence did not answer within `intelligence_timeout`: `failure_reason` says which; the log of `/interaction_intelligence` has the trace ([Endings and outcome](behaviors/endings.md)) |
| The robot does not speak, and the log says `Speak: text-to-speech not available, not said` | `/tts_action` is not served: start simple_hri (`speech:=simple_hri`) or the robot's speech. The conversation goes on without voice |
| No barge-in | It needs `/user_speaking` (the lip activity detector of `interaction_perception`) and `user_speaking_topic` set; it is experimental ([Barge-in](behaviors/barge_in.md)) |
| The NAO's gestures and LEDs do not move | They follow `/is_speaking`, published by the `Speak` node while the robot speaks: check that the NAO nodes run on the same `ROS_DOMAIN_ID` |
| The emotion never reaches the prompts | It is off by default (`user_state_service: ""`; the detector is preliminary). Set `user_state_service`, run the perception, and the emotion must be notable (not neutral, confident enough, recent) |
| Two `/dialog/execute_task` servers | A previous run left its nodes running (below) |

## Leftover nodes

Stopping a `ros2 launch` started under `pixi run` (or with a signal to the launch's parent) can
leave its nodes running, and two instances of the system then answer the same action. Look for them
and stop them by their process id:

```sh
ps -eo pid,cmd | grep -E "mission_executor|interaction_intelligence|llm_manager_node|stt_faster_whisper|stt_whisper|tts_piper|tts_mms" | grep -v grep
kill <pid> ...
```

To avoid it, run launches from a `pixi shell`, or start them in their own process group (`setsid`)
and signal the group. The web GUI has a button that stops every node of the stack.
