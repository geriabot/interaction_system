# Installing natively (system ROS 2 Jazzy)

For a computer with ROS 2 Jazzy installed on the system (Ubuntu 24.04), for instance to integrate
the interaction system with an existing ROS 2 installation. To reproduce the experiments, use pixi
([INSTALL_PIXI.md](INSTALL_PIXI.md)), whose lock file fixes every dependency. What is common to
both ways (the models, the GUI): [INSTALLATION.md](INSTALLATION.md).

## Install and build

Requires Ubuntu 24.04 with [ROS 2 Jazzy](https://docs.ros.org/en/jazzy/Installation.html).
`<workspace>` is the colcon workspace (e.g. `~/interaction_ws`).

```bash
sudo apt install ros-dev-tools
sudo rosdep init      # only the first time on this computer
rosdep update

mkdir -p <workspace>/src && cd <workspace>/src
git clone https://github.com/geriabot/interaction_system.git
vcs import . < interaction_system/thirdparty.repos    # behavior_architecture and simple_hri
cd ..
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src -r -y \
  --skip-keys "llm_planner_interfaces llm_bt_builder"   # optional dependencies of behavior_architecture
pip install --break-system-packages -r src/interaction_system/requirements.txt
colcon build --symlink-install \
  --packages-up-to interaction_system interaction_system_behaviors interaction_intelligence \
    ros2_llm_manager simple_hri interaction_perception dialostack_bt_client \
    interaction_sim interaction_ablations
```

`requirements.txt` gathers the requirement files of the LLM gateway
(`ros2_llm_manager/config/requirements.txt`) and of simple_hri
(`src/third_party/simple_hri/simple_hri/requirements.txt`, the speech models' packages, with
PyTorch). The perception of the user (optional) has its own:

```bash
pip install --break-system-packages \
  -r src/interaction_system/interaction_perception/interaction_perception/requirements.txt
```

Then, in every terminal where you run the system:

```bash
source /opt/ros/jazzy/setup.bash
source <workspace>/install/setup.bash
```

### With a Python virtual environment

To keep the pip packages out of the system Python, install them in a virtual environment instead
of using `--break-system-packages`:

```bash
python3 -m venv <venv>
<venv>/bin/pip install -r src/interaction_system/requirements.txt
```

Build as above, without the environment activated. Sourcing the workspace reorders `PYTHONPATH`
and hides the environment's packages from the nodes, which then fail with `ModuleNotFoundError`.
`activate.sh` puts them back: set the two variables at its top, `ROS_DISTRO` (`jazzy`) and
`DIALOSTACK_VENV` (the absolute path of `<venv>`), and in every terminal run, instead of the two
`source` lines above:

```bash
source <workspace>/src/interaction_system/activate.sh
```

## The web GUI

```bash
sudo apt install ros-jazzy-rosbridge-suite nodejs npm
./src/interaction_system/gui.sh                      # from <workspace>; open http://localhost:5173
./src/interaction_system/gui.sh --dev                # Vite with hot reload, for GUI development
```

`gui.sh` sources `/opt/ros/lyrical` or `/opt/ros/jazzy` (outside pixi) and the workspace's `install/` itself, and builds the GUI on its
first run. What the GUI does: [INSTALLATION.md](INSTALLATION.md#the-web-gui).

## Then

The models (the LLM and the speech models): [INSTALLATION.md](INSTALLATION.md#models). The first
conversation: [README, Quick start](../README.md#quick-start).
