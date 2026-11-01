# Installation

Two ways to install the interaction system, each with its own guide:

| | [With pixi](INSTALL_PIXI.md) (recommended) | [Natively](INSTALL_NATIVE.md) (system ROS 2 Jazzy) |
|---|---|---|
| Runs on | Any x86-64 Linux, without ROS installed | Ubuntu 24.04 with ROS 2 Jazzy |
| Dependencies | One environment from `pixi.lock`: ROS 2, the build tools, the Python packages and Node.js | rosdep for the ROS packages, pip for the Python packages (`requirements.txt`), apt for the GUI |
| ROS 2 Lyrical | Yes, a second environment ([PIXI.md](PIXI.md#ros-2-lyrical)) | No |
| Choose it to | Develop, run on a robot, run the evaluation, reproduce the experiments | Integrate with an existing ROS 2 Jazzy installation |

Both guides end with a built workspace. What follows applies to both.

## Models

- **The LLM.** The interaction system asks an LLM served by [Ollama](https://ollama.com) at
  `localhost:11434`: on this computer (`curl -fsSL https://ollama.com/install.sh | sh`, which
  uses `sudo`), or on a GPU server reached through an SSH tunnel
  ([GPU_SERVER.md](GPU_SERVER.md)). Pull the model of `app_params.yaml`, `qwen2.5:7b`
  (`ollama pull qwen2.5:7b`). Which model answers each question, how to change it, and the cloud
  providers: [MODELS.md](MODELS.md).
- **simple_hri's speech models** (faster-whisper or Whisper, the Piper voice of the language or
  MMS-TTS) download themselves on the first run into `models/` of the directory where the stack is
  launched; launch from the same directory each time. Silero VAD goes to `~/.cache/torch/hub`.
- **The perception's models** (the MediaPipe face landmarker, the emotion classifier) download
  themselves on the first run.

## The web GUI

`gui.sh` starts rosbridge and the GUI server; the GUI then starts and stops the stack
(`main.launch.py speech:=simple_hri` with the engines of its Configuration page, or
`speech:=none` to talk by typing only). simple_hri listens and speaks through the microphone and
the speaker of this computer, and downloads its models into `models/` of the directory `gui.sh` is
run from. `gui.sh` uses the ROS 2 of its environment when there is one (pixi), or else
`/opt/ros/jazzy`, and sources the workspace's `install/setup.bash` itself. How to start it:
[with pixi](INSTALL_PIXI.md#the-web-gui), [natively](INSTALL_NATIVE.md#the-web-gui). The GUI's
code: [dialostack_gui](../dialostack_gui/src/README.md).

## Next steps

- The first conversation: [README, Quick start](../README.md#quick-start).
- Every command in one page: [INDEX.md](../INDEX.md).
- The speech capability: [SPEECH.md](SPEECH.md).
- Every parameter: [CONFIGURATION.md](CONFIGURATION.md).
- Using it from your robot: [INTEGRATING.md](INTEGRATING.md), [ADDING_A_ROBOT.md](ADDING_A_ROBOT.md).
