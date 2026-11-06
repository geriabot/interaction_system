# pixi in this project

[pixi](https://pixi.sh) creates a self-contained ROS 2 environment (Jazzy, and optionally
Lyrical), from [RoboStack](https://robostack.github.io) and conda-forge, with every dependency of
the system.
This page explains why the project uses it and how it is configured;
[INSTALL_PIXI.md](INSTALL_PIXI.md) explains how to install and build with it.

## Why pixi

- Everyone runs the same software, because `pixi.lock` pins every package: ROS 2,
  BehaviorTree.CPP, the speech models' Python packages (PyTorch, faster-whisper, Whisper, Piper,
  MMS-TTS's transformers), the LLM providers' SDKs and the GUI's Node.js.
- The environment is independent of the system. It needs no system ROS, no particular Ubuntu
  release and no `sudo`, and it runs Jazzy even on a computer whose system has another ROS
  distribution.
- Python is isolated: the machine-learning packages do not touch the system Python.

## Where it lives

`pixi.toml` and `pixi.lock` are in the repository, but pixi runs from the root of the colcon
workspace: `./src/interaction_system/pixi/setup_workspace.sh` (once, after cloning) links both
files there. So `pixi install`, `pixi shell` and `pixi run build` run from `<workspace>`, the
environment (`.pixi/`) is created there, next to `build/` and `install/`, and every path of the
manifest is relative to the root of the workspace. What pixi writes to the lock (`pixi add`, an
update) goes to the repository's copy, through the link, ready to commit.

Two cases use the repository's `pixi.toml` as if the repository were the root of the workspace:
running pixi inside `src/interaction_system` (pixi looks for `pixi.toml` from the current directory
upwards, and finds the repository's first), and `--manifest-path <workspace>/pixi.toml` (pixi
follows the link to the repository's file). pixi then
creates a second environment in `src/interaction_system/.pixi`, the activation script and the
working directory of the `test` task are not found, and `pixi run build` builds inside the
repository. Run pixi from `<workspace>` (or `<workspace>/src`), without `--manifest-path`. A second
environment created by mistake can be removed with `rm -rf src/interaction_system/.pixi`.

## What the configuration contains

[`pixi.toml`](../pixi.toml) defines two environments, `default` (ROS 2 Jazzy) and `lyrical`
(ROS 2 Lyrical), with:

- the ROS packages (`ros-base`, BehaviorTree.CPP, `rclcpp_cascade_lifecycle`, rosbridge), the
  build tools (compilers, CMake, colcon) and the libraries (`yaml-cpp`, `nlohmann_json`,
  PortAudio);
- the Python packages of the nodes and the tests from conda-forge, and the speech models' from
  PyPI (`[pypi-dependencies]`);
- `PYTHONNOUSERSITE=1` in the activation, so that packages installed in the user's site-packages
  (`pip install --user`) never leak into the environment;
- the SDKs of the former LLM gateway's cloud providers, `openai` and `google-genai` (simple_hri's
  Extract and Yes/No services also use `openai`, but the interaction system does not use them).
  The interpretation server's backends need no SDK, so the cloud models
  ([MODELS.md](MODELS.md#cloud-providers)) work without them; Claude's SDK, `anthropic`, which
  only the former gateway would need, is not included;
- the tasks:

| Task | What it does |
|---|---|
| `build` | `colcon build --symlink-install --packages-up-to` the interaction system, `ros2_llm_manager`, simple_hri (speech), the perception, the BT client and the evaluation packages, into `build/` and `install/` (ROS 2 Jazzy, the `default` environment) |
| `build-lyrical` | The same in the `lyrical` environment, into `build_lyrical/` and `install_lyrical/` |
| `test` | The unit tests of the interaction intelligence (no ROS), with pytest |

`build` runs from the root of the workspace, and extra arguments go to colcon
(`--packages-select ...`).

pixi regenerates `pixi.lock` when `pixi.toml` changes. To add a package, run
`pixi add <package>` from `<workspace>` (with `--pypi` for a package of PyPI); it updates both
files.

User perception is built, but its dependencies (MediaPipe, OpenCV and `cv_bridge`, which only it
needs) are not in the environment
([INSTALL_PIXI.md](INSTALL_PIXI.md#the-perception-of-the-user-optional)).

## ROS 2 Lyrical

```sh
git -C src/third_party/behavior_architecture checkout lyrical   # once
pixi run build-lyrical
pixi shell -e lyrical
source install_lyrical/setup.bash
```

On Lyrical, behavior_architecture must be on its `lyrical` branch (its build no longer uses
`ament_target_dependencies`, removed from Lyrical's `ament_cmake`, and it takes service futures
explicitly); that branch also builds on Jazzy. `thirdparty.repos` fetches `main`, for Jazzy. The two
environments build into different folders, so both can be built at the same time; source the
`setup.bash` of the one you use.

## Graphical applications (RViz, rqt, Groot2)

The environment has its own fontconfig, usually a different version from the system's. Both write
their caches to `~/.cache/fontconfig` with the same names and incompatible formats, and a Qt
application of the environment that reads the system's cache may crash at startup.

The activation script `pixi/activate-fontconfig.sh` (section `[activation]` of `pixi.toml`)
prevents this. It runs every time the environment is entered (`pixi shell`, `pixi run`), writes a
copy of the environment's `fonts.conf` without the cache folders shared with the system
(`~/.cache/fontconfig` and `~/.fontconfig`), and points `FONTCONFIG_FILE` to it.

If an application still crashes, delete the shared cache once: `rm -rf ~/.cache/fontconfig`.

## The microphone and the speaker

The environment has its own ALSA library, which reads the system's configuration (where the
`default` device is the sound server, PipeWire or PulseAudio) but looks for its plugins inside the
environment, where the system's are not. `default` then falls back to the first sound card's raw
device, which on many computers records silence (the built-in microphone is another device) or
does not record at 16 kHz, the rate of the speech recognition ("Invalid sample rate" in its log,
or a listen check that hears nobody).

The activation script `pixi/activate-alsa.sh` points `ALSA_PLUGIN_DIR` to the system's ALSA
plugins when they include the PipeWire or PulseAudio one (and it is not set already), so that
`default` is the sound server and the microphone is the one chosen in the system's sound settings.
The speech recognition uses it with the launch argument `input_device:=default`
([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md)), as the sessions with participants do.

## `ros2 launch` under `pixi run`

Stopping a `ros2 launch` started with `pixi run` (Ctrl+C, or a signal to the `pixi` process) can
leave its nodes running. Run the launch from a `pixi shell` instead, or start it in its own
process group and signal the group
([VALIDATION_AND_TROUBLESHOOTING.md](VALIDATION_AND_TROUBLESHOOTING.md#leftover-nodes)).
