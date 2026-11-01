# Installing with pixi (recommended)

[pixi](https://pixi.sh) creates a self-contained ROS 2 environment
([RoboStack](https://robostack.github.io)) with every dependency, on any x86-64 Linux, without ROS
installed on the system. It is the way to reproduce the experiments: its lock file fixes every
dependency. How the environment is defined and why: [PIXI.md](PIXI.md). The other way, with a
system ROS 2: [INSTALL_NATIVE.md](INSTALL_NATIVE.md). What is common to both (the models, the GUI):
[INSTALLATION.md](INSTALLATION.md).

## Install and build

`<workspace>` is the colcon workspace (e.g. `~/interaction_ws`).

```bash
curl -fsSL https://pixi.sh/install.sh | sh            # once; then open a new terminal
pixi global install vcstool                           # once

mkdir -p <workspace>/src && cd <workspace>/src
git clone https://github.com/geriabot/interaction_system.git
vcs import . < interaction_system/thirdparty.repos    # behavior_architecture and simple_hri
cd ..
./src/interaction_system/pixi/setup_workspace.sh      # once: links pixi.toml and pixi.lock here
pixi run build
```

The first `pixi run` downloads the environment (about 8 GB, with PyTorch) into `<workspace>/.pixi`.
`pixi run build` builds the interaction system, simple_hri, the perception of the user, the
BehaviorTree.CPP client and the evaluation packages into `build/` and `install/`; extra arguments
go to colcon (`pixi run build --packages-select <package>`).

Run pixi from `<workspace>`, where the links are. Inside `src/interaction_system`, pixi finds the
repository's own `pixi.toml` instead and creates a second environment there whose paths do not
resolve ([PIXI.md](PIXI.md#where-it-lives)).

Then, in every terminal where you run the system:

```bash
cd <workspace>
pixi shell
source install/setup.bash
```

For ROS 2 Lyrical, use the `lyrical` environment and behavior_architecture's `lyrical` branch:
[PIXI.md](PIXI.md#ros-2-lyrical).

## The perception of the user (optional)

`interaction_perception` is built, but its dependencies are not in the environment: `cv_bridge`
(RoboStack's `ros-jazzy-cv-bridge`), and MediaPipe and OpenCV (PyPI, the versions of
`interaction_perception/interaction_perception/requirements.txt`). To use it, add them with
`pixi add` ([PIXI.md](PIXI.md#what-the-configuration-contains)); this changes `pixi.toml` and
`pixi.lock` of the repository. A camera driver is not in the environment either: barge-in in the
sessions is future work, with its steps in
[REAL_SESSIONS.md](../evaluation/doc/REAL_SESSIONS.md#barge-in).

## The web GUI

From `<workspace>`, in the environment (it has ROS 2, rosbridge and Node.js):

```bash
./src/interaction_system/gui.sh            # in a pixi shell; or pixi run ./src/interaction_system/gui.sh
```

Then open <http://localhost:5173>. What the GUI does: [INSTALLATION.md](INSTALLATION.md#the-web-gui).

## Then

The models (the LLM and the speech models): [INSTALLATION.md](INSTALLATION.md#models). The first
conversation: [README, Quick start](../README.md#quick-start).
