# Launch files and their arguments

This page lists every launch file of the system, what it starts, and its arguments with their
defaults. The [README](../README.md#running-the-system) shows which command to run for each case,
and [CONFIGURATION.md](CONFIGURATION.md) lists the parameters of the nodes.

## The interaction system

### The whole stack: `interaction_system main.launch.py`

This launch file starts the speech capability, the LLM gateway (`llm_manager_node`) and the
interaction system (`interaction_system.launch.py`), all with the same parameter file. The GUI
starts it too, with `speech:=none` or with `speech:=simple_hri` and the `stt`, `tts` and
`whisper_model` of its Configuration page.

| Argument | Default value | Description |
|---|---|---|
| `speech` | `simple_hri` | The speech capability: `simple_hri` or `none` (no speech nodes; see below) |
| `stt` | `faster_whisper` | simple_hri's speech-to-text: `faster_whisper` (`stt_faster_whisper`: faster-whisper with Silero VAD) or `whisper` (`stt_whisper`: Whisper with WebRTC VAD) |
| `tts` | `piper` | simple_hri's text-to-speech: `piper` (`tts_piper`: the Piper voice of the language) or `mms` (`tts_mms`: MMS-TTS) |
| `whisper_model` | `small` | Whisper model of simple_hri: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `input_device` | `''` | The microphone of `stt_faster_whisper` (its `input_device`): a sounddevice device name; empty: PortAudio's default. `stt_whisper` ignores it |
| `language` | `''` | The language of the conversation: a language file, by code or name (`en`, `es`, `English`, `Spanish`). Empty: the intelligence's `language` of the parameter files, or English if none |
| `params_file` | `''` (`config/app_params.yaml` of `interaction_system`) | The parameters of the LLM gateway, the intelligence and the orchestrator |
| `config_file` | `''` (`config/interaction_behaviors_config.yaml`) | The behavior_architecture runtime: the orchestrator and the runners (e.g. the trees of an ablation) |
| `catalog_file` | `''` (`config/interaction_orchestrator_params.yaml`) | The catalog of dialogue behaviors and the default limits |

With `speech:=simple_hri`, the launch file starts simple_hri's speech-to-text node as
`stt_service_node` and its text-to-speech node as `tts_service_node`. With `speech:=none`, another
node provides the capability, e.g. the simulation of `interaction_sim` or your robot.

`input_device:=default` goes through the system's sound server, which converts the sample rate.
Use it when the default device is a raw ALSA device that does not record at 16 kHz ("Invalid
sample rate" in the speech-to-text's log).

`language` sets the intelligence's `language` (the file's name: `English`, `Spanish`), the
language of the speech-to-text and the voice, from the file's `speech` settings
([CONFIGURATION.md](CONFIGURATION.md#simple_hris-speech-nodes),
[ADDING_A_LANGUAGE.md](ADDING_A_LANGUAGE.md)). A language without a file stops the launch.

When the environment variable `DIALOSTACK_GUI_PARAMS` points to a YAML file (the GUI writes one
from its Configuration page), it is applied after `params_file`, so its values take precedence;
`interaction_system.launch.py` does the same. The argument `language` is applied after both: it
sets the intelligence's `language` whatever the files say.

### Only the interaction system: `interaction_system interaction_system.launch.py`

This launch file starts `mission_executor` (the orchestrator and the runners of the dialogue
behaviors, in one process) and `/interaction_intelligence`. The speech capability and the LLM
gateway are started separately.

| Argument | Default value | Description |
|---|---|---|
| `params_file` | `config/app_params.yaml` | The parameters of the orchestrator and the intelligence |
| `config_file` | `config/interaction_behaviors_config.yaml` | The behavior_architecture runtime (positional argument of `mission_executor`) |
| `catalog_file` | `config/interaction_orchestrator_params.yaml` | The catalog and the default limits of the orchestrator; `params_file` is applied after it |
| `prompts_file` | `config/prompts.yaml` of `interaction_intelligence` | The prompt templates ([PROMPTS.md](PROMPTS.md)) |
| `language` | `''` | The language of the conversation for the LLM, e.g. `English`, `Spanish` (empty: `language` of the parameter files) |

## The LLM gateway: `ros2_llm_manager llm_manager.launch.py`

| Argument | Default value | Description |
|---|---|---|
| `params` | `config/params.yaml` of `ros2_llm_manager` | Parameters of `llm_manager_node` |

Its `params.yaml` ships the same values as the `llm_manager_node` section of `app_params.yaml`
([CONFIGURATION.md](CONFIGURATION.md#llm_manager_node)).

## The perception of the user

### `interaction_perception interaction_perception.launch.py`

This launch file starts `/user_state_server` and, optionally, the two detectors.

| Argument | Default value | Description |
|---|---|---|
| `image_topic` | `/camera/image_raw` | The camera image the detectors read |
| `emotion` | `true` | Run the facial emotion detector (preliminary, not validated: [interaction_perception](../interaction_perception/interaction_perception/README.md)) |
| `lips` | `true` | Run the lip activity detector (visual barge-in, experimental) |
| `visualization` | `false` | Show the detectors' debug windows (sets `show_visualization` and `show_debug`) |

The emotion estimate reaches the conversation only if the intelligence's `user_state_service`
is set (it is empty by default).

`emotion_detector.launch.py` and `lip_detector.launch.py` start one detector each with its
parameter file. They take no arguments, and the shipped files open the debug windows.

## simple_hri

`main.launch.py` starts simple_hri's nodes itself. To use its other backends and options (cloud
services, another Piper voice, the audio forwarding to the robot's speakers), start simple_hri
with its own launch file and the interaction system with `main.launch.py speech:=none`:

| Launch file | Arguments | Nodes |
|---|---|---|
| `simple_hri simple_hri.launch.py` | `stt`, `tts`, `piper_voice`, `whisper_model`, `language`, `backend`, ... ([simple_hri's README](https://github.com/rodperex/simple_hri)) | its speech-to-text and text-to-speech nodes (and Extract and Yes/No, not used here) |

## NAO

| Launch file | Arguments | Nodes |
|---|---|---|
| `nao_pose_manager arm_gesture_manager.launch.py` | `poses_file` (the package's `config/nao_saved_poses.yaml`), `transition_speed` (`0.05`), `idle_interval` (`3.0`), `hold_time_min` (`1.0`), `hold_time_max` (`2.5`), `stiffness` (`0.8`) | `arm_gesture_manager`, `eye_led_feedback` |
| `nao_pose_manager nao_sim.launch.py` | — | RViz with the NAO model and the pose tooling |
| `nao_description everything_launch.py` | `use_sim_time` (`false`) | The NAO model |

## The BehaviorTree.CPP client

| Launch file | Arguments | Nodes |
|---|---|---|
| `dialostack_bt_client example.launch.py` | — | `bt_runner` (node `dialostack_bt_runner`) with `bt_xml` set to `config/example.xml` (a slot-filling conversation as a step of a tree) |

## The evaluation: `interaction_ablations llm_led.launch.py`

This launch file starts the whole stack with the LLM-led conversation, the `monolithic` ablation
of [evaluation/README.md](../evaluation/README.md#ablations). It includes `main.launch.py` with
the arguments below, the ablation's `config_file`, and a copy of the parameter file in which the
ablation's skills module (`interaction_ablations.skills`) is added to the intelligence's
`skill_modules`.

| Argument | Default value | Description |
|---|---|---|
| `speech`, `stt`, `tts`, `whisper_model`, `input_device`, `language` | as `main.launch.py` | Passed to `main.launch.py` |
| `params_file` | `''` (`interaction_system`'s `config/app_params.yaml`) | Parameters of the nodes; the skills module is added to a copy, written to a temporary file |
| `catalog_file` | `''` (as `main.launch.py`) | The catalog of dialogue behaviors |
