# Quick reference

Every command in one page. `<workspace>` is the colcon workspace (e.g. `~/interaction_ws`), and the
commands run from it. The `ros2` commands need a prepared terminal:

```sh
cd <workspace>
pixi shell                 # ROS 2 Lyrical: pixi shell -e lyrical
source install/setup.bash  # ROS 2 Lyrical: source install_lyrical/setup.bash
```

Without pixi, `source /opt/ros/jazzy/setup.bash` and `source install/setup.bash` instead
([INSTALL_NATIVE.md](docs/INSTALL_NATIVE.md)). The explanations are in the [README](README.md) and
the documents it links.

## Build

| Job | Command |
|---|---|
| Fetch the sources | `cd src && vcs import . < interaction_system/thirdparty.repos` |
| Prepare the workspace for pixi (once) | `./src/interaction_system/pixi/setup_workspace.sh` |
| Build | `pixi run build` |
| Build for ROS 2 Lyrical | `git -C src/third_party/behavior_architecture checkout lyrical`, then `pixi run build-lyrical` (into `install_lyrical/`; [PIXI.md](docs/PIXI.md#ros-2-lyrical)) |
| Rebuild some packages | `pixi run build --packages-select interaction_system interaction_system_behaviors` |
| Build the NAO embodiment | `colcon build --symlink-install --packages-select nao_description nao_pose_manager` |
| Without pixi | [INSTALL_NATIVE.md](docs/INSTALL_NATIVE.md) |

## Models

| Job | Command |
|---|---|
| Pull the default model | `ollama pull qwen2.5:7b` (the model is `ollama.model` in `app_params.yaml`; [MODELS.md](docs/MODELS.md)) |
| Reach Ollama on a GPU server | `ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 11434:localhost:11434 <user>@<server>` ([GPU_SERVER.md](docs/GPU_SERVER.md)) |
| Check that Ollama answers, and its loaded models | `curl -s localhost:11434/api/version`, `curl -s localhost:11434/api/ps` |
| Free the GPU afterwards | `ollama stop <model>` on the server ([GPU_SERVER.md](docs/GPU_SERVER.md#on-a-shared-server)) |

## Run

| Job | Command |
|---|---|
| The whole stack (simple_hri, English) | `ros2 launch interaction_system main.launch.py` |
| In Spanish | `ros2 launch interaction_system main.launch.py language:=es` |
| In another language | Write its language file, then `language:=<code>` ([ADDING_A_LANGUAGE.md](docs/ADDING_A_LANGUAGE.md)) |
| Other speech engines (Whisper, MMS-TTS) | `ros2 launch interaction_system main.launch.py stt:=whisper tts:=mms whisper_model:=medium` |
| Without speech nodes (simulation, or the robot's own speech) | `ros2 launch interaction_system main.launch.py speech:=none` |
| Only the interaction system | `ros2 launch interaction_system interaction_system.launch.py` |
| The LLM gateway alone | `ros2 launch ros2_llm_manager llm_manager.launch.py` |
| The LLM-led conversation (the baseline of the evaluation) | `ros2 launch interaction_ablations llm_led.launch.py` |
| The perception of the user | `ros2 launch interaction_perception interaction_perception.launch.py image_topic:=<camera topic>` |
| The web GUI | `./src/interaction_system/gui.sh` (<http://localhost:5173>) |
| NAO gestures and eye LEDs | `ros2 launch nao_pose_manager arm_gesture_manager.launch.py` ([ADDING_A_ROBOT.md](docs/ADDING_A_ROBOT.md#the-worked-example-nao)) |

Every launch argument: [LAUNCH_ARGUMENTS.md](docs/LAUNCH_ARGUMENTS.md).

## Ask for a conversation

```sh
ros2 action send_goal --feedback /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Take a coffee order: drink type, size and customer name', dialog_mode: slot_filling}"
ros2 topic echo /interaction_system/status       # 0 IDLE, 1 REASONING, 2 RUNNING, 3 FAILED
ros2 topic echo /robot_utterance                 # what the robot says
ros2 topic echo /interaction/user_turn           # what the robot took as the user's turns
ros2 topic echo /interaction/skill_trace         # every question to the intelligence: skill, label, time
ros2 topic pub --once /transcription std_msgs/msg/String "{data: 'a large latte'}"   # type as the user
```

The goal, the feedback and the result: [ROS_INTERFACES.md](docs/ROS_INTERFACES.md#dialogtask-goal).
Goals for every strategy: [INTEGRATING.md](docs/INTEGRATING.md#goals-for-every-strategy).
Transcripts: `/tmp/dialog_conversations.log` (`conversation_log_path`).

## Tests

| Job | Command |
|---|---|
| Unit tests of the intelligence | `pixi run test` |
| Whole conversations with a scripted LLM | `ROS_DOMAIN_ID=<free id> python -m pytest src/interaction_system/evaluation/simulation/interaction_sim/test` |
| GUI logic | `cd src/interaction_system/dialostack_gui && npm ci && npm test` (`npm ci` once) |
| Redraw the Behavior Trees after changing one | `python3 src/interaction_system/interaction_system_behaviors/scripts/bt_diagrams.py` (writes `media/bt/*.svg`; [docs/behaviors](docs/behaviors/README.md)) |

## Evaluation

From `<workspace>/src/interaction_system/evaluation`, in a prepared terminal. The experiments
(design, measures, decision rules, status): [EXPERIMENTS.md](evaluation/doc/EXPERIMENTS.md). The
tools: [evaluation/README.md](evaluation/README.md). The hand reviews and annotations (who, when,
the exact commands): [evaluation/HUMAN_REVIEW.md](evaluation/HUMAN_REVIEW.md).

```sh
python3 scripts/control_panel.py                                                    # all of it from a web page: http://localhost:8095 (#tests: try a scenario as the user)
python offline/run_eval.py --config offline/config_ollama.yaml --benchmark all       # the questions to the LLM, offline
python offline/benchmarks/bench_behavior_choice.py --config offline/config_ollama.yaml   # the choice of the dialogue behavior
python offline/run_models.py --config offline/config_ollama.yaml --models <m1>,<m2> --reps 3
python scripts/run_experiment.py experiments/<experiment>.yaml --dry-run           # list the dialogues
python scripts/run_experiment.py experiments/<experiment>.yaml                     # run them
python scripts/run_parallel.py experiments/<experiment>.yaml experiments/hardware/<profile>.yaml   # on a hardware profile, lanes at once
python analysis/score_runs.py results/<name>                                       # score them
python analysis/judge_runs.py results/<name> --provider gemini --model <model>     # frame and faithfulness
ROS_DOMAIN_ID=<free id> python scripts/sim_checks.py start                         # quick checks without hardware (start, scripted, model)
python3 real/real_sessions.py plan P07                                              # sessions with real participants: a plan
python3 real/real_sessions.py check speak                                           # the checks before a session
python3 real/real_sessions.py trial P07 1 [--try] [--barge-in on|off]               # one trial with a participant
```

`results/<name>` is the folder of the experiment's `name` (e.g. `results/e1_bounded_vs_llm_led` for
`experiments/e1_bounded_vs_llm_led.yaml`). The panels: [CONTROL_PANEL.md](evaluation/doc/CONTROL_PANEL.md). The
sessions with real participants: [REAL_SESSIONS.md](evaluation/doc/REAL_SESSIONS.md).

Cloud models (system, simulated user, judge) are paid: every command that would call one needs
`--allow-cloud`; `--dry-run` counts the calls.

## Common checks

```sh
ros2 node list                                         # /interaction_orchestrator, /interaction_intelligence, runners
ros2 lifecycle get /interaction_orchestrator           # active
ros2 action list | grep -E "execute_task|stt_action|tts_action"
ros2 service list | grep -E "interaction|llm"
curl -s localhost:11434/api/version                    # Ollama answers
ps -eo pid,cmd | grep -E "mission_executor|interaction_intelligence|llm_manager"   # leftovers of a previous run
```

Symptoms and fixes: [VALIDATION_AND_TROUBLESHOOTING.md](docs/VALIDATION_AND_TROUBLESHOOTING.md).
