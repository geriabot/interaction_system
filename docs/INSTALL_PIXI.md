# Installing with pixi (recommended)

[pixi](https://pixi.sh) creates a self-contained ROS 2 environment
([RoboStack](https://robostack.github.io)) with every dependency, on any x86-64 Linux, without ROS
installed on the system. It is the way to reproduce the experiments, because its lock file fixes
every dependency. [PIXI.md](PIXI.md) explains how the environment is defined and why.
[INSTALL_NATIVE.md](INSTALL_NATIVE.md) describes the other way, with a system ROS 2, and
[INSTALLATION.md](INSTALLATION.md) covers what is common to both (the models, the GUI).

## Install and build

`<workspace>` is the colcon workspace (e.g. `~/interaction_ws`).

```bash
curl -fsSL https://pixi.sh/install.sh | sh            # once; then open a new terminal
pixi global install vcstool                           # once

mkdir -p <workspace>/src && cd <workspace>/src
git clone --recurse-submodules https://github.com/geriabot/interaction_system.git
vcs import . < interaction_system/thirdparty.repos    # koch2 and simple_hri
cd ..
./src/interaction_system/pixi/setup_workspace.sh      # once: links pixi.toml and pixi.lock here
pixi run build
```

`evaluation/` (the evaluation) and `paper/` are git submodules that stay private until the work
is published. Without access to them, the clone reports that it cannot fetch them (over HTTPS,
git may first ask for a GitHub user and password: press Enter to skip) and clones the rest as
usual. `pixi run build` then stops, because colcon does not find the two evaluation
packages it names: in a `pixi shell`, run the command of the `build` task of `pixi.toml` without
`interaction_sim interaction_ablations`. An existing clone fetches the submodules with
`git submodule update --init`.

The first `pixi run` downloads the environment into `<workspace>/.pixi`: about 8 GB, with
PyTorch, and about 3.5 GB more for the Lyrical one. `pixi run build` builds the interaction
system, simple_hri, the interfaces of the perception system, the BehaviorTree.CPP client and the evaluation packages into
`build/` and `install/`; extra arguments go to colcon
(`pixi run build --packages-select <package>`).

Run pixi from `<workspace>`, where the links are. Inside `src/interaction_system`, pixi finds the
repository's own `pixi.toml` instead and creates a second environment there whose paths do not
resolve ([PIXI.md](PIXI.md#where-it-lives)).

Then, in every terminal where you run the system:

```bash
cd <workspace>
pixi shell
source install/setup.bash
```

For ROS 2 Lyrical, use the `lyrical` environment
([PIXI.md](PIXI.md#ros-2-lyrical)).

## The perception of the user (optional)

The perception of the user (the lip activity for barge-in, the emotion) is the perception system ([`perception_system`](https://github.com/geriabot/perception_system)), a
capability of its own, installed in its own workspace with its own environment (MediaPipe, OpenCV,
`cv_bridge`). Only its interfaces (`perception_system_interfaces`, in `thirdparty.repos`) are
built here. Start it from its workspace with the robot's camera:

```bash
ros2 launch perception_system perception_system.launch.py orchestrator:=false user:=true body:=false markers:=false autostart_skills:=lip_activity image_topic:=<camera topic>
```

A camera driver is not in the environment; [REAL_SESSIONS.md](../evaluation/doc/REAL_SESSIONS.md#barge-in)
lists the steps for barge-in in the sessions.

## The web GUI

From `<workspace>`, in the environment (it has ROS 2, rosbridge and Node.js):

```bash
./src/interaction_system/gui.sh            # in a pixi shell; or pixi run ./src/interaction_system/gui.sh
```

Then open <http://localhost:5173>. [INSTALLATION.md](INSTALLATION.md#the-web-gui) describes what
the GUI does.

## Then

[INSTALLATION.md](INSTALLATION.md#models) explains how to get the models (the LLM and the speech
models), and the [README's Quick start](../README.md#quick-start) walks through the first
conversation.
