# robots/nao: NAO embodiment layer

Optional embodiment layer for the SoftBank NAO: the robot model (`nao_description`) and the
pose, gesture and eye LED tooling (`nao_pose_manager`). It only consumes the signals of the
conversation (`/is_speaking`, `/target_pose`, and `/user_vad` if something publishes it): the
interaction system has no NAO dependency and runs unchanged without it. `/is_speaking` is
published by the interaction system's `Speak` node while the robot speaks, whatever the speech
capability, so the gestures and the eye LEDs follow the conversation. How a robot connects to the
interaction system, with the NAO as the worked example, is in
[ADDING_A_ROBOT.md](../../docs/ADDING_A_ROBOT.md).

## Packages

- **nao_description**: URDF and RViz config for the NAO V40, plus a sim bringup.
- **nao_pose_manager**: pose capture, playback, gesture managers and eye-LED feedback.

The NAO 3D meshes are licensed by SoftBank Robotics and cannot be redistributed here (`meshes/`
and `texture/` are git-ignored). To visualize the full model, install them with the official
`nao_meshes` installer ([`nao_description/README.md`](nao_description/README.md)).

## Nodes (nao_pose_manager)

| Node (ros2 run name) | Description |
| --- | --- |
| `gesture_manager` | Gesture state machine for the simulation: talking gestures while `/is_speaking` is true, idle poses otherwise, published as `JointState` for RViz. |
| `arm_gesture_manager` | The same state machine on the real robot, through the nao_lola effector topics. |
| `eye_led_feedback` | Sets the color of the eye LEDs from `/is_speaking` (and `/user_vad`). |
| `pose_saver` | Saves the live `/joint_states` pose to `nao_saved_poses.yaml` on trigger. |
| `pose_tester` | Tkinter tool to preview saved poses in simulation. |
| `keyboard_trigger` / `gui_trigger` | Terminal / GUI front-ends that publish pose-save names. |

## Interfaces

Every topic these nodes publish or subscribe to is in
[ROS_INTERFACES.md](../../docs/ROS_INTERFACES.md#the-embodiment-nao).

### External dependency

The real-robot nodes (`arm_gesture_manager`, `eye_led_feedback`) require
[`nao_lola_command_msgs`](https://github.com/ijnek/nao_lola) and a running nao_lola
bridge on the robot. The simulation path does not need it.

## Usage

These packages are not in the `build` task of pixi nor in the package list of the README's
build: build them with colcon (`--packages-select nao_description nao_pose_manager`). The
simulation also needs `xacro`, `joint_state_publisher_gui` and RViz, which the pixi environment
(`ros-base`) does not include.

Simulation (RViz + pose tooling, no robot):

```bash
ros2 launch nao_pose_manager nao_sim.launch.py
```

Real robot (arm gestures + eye LEDs, needs nao_lola running):

```bash
ros2 launch nao_pose_manager arm_gesture_manager.launch.py
```

### Pose capture workflow

1. Launch the sim: `ros2 launch nao_pose_manager nao_sim.launch.py`.
2. Pose the robot with the `joint_state_publisher_gui` sliders in RViz.
3. Type a pose name in the `gui_trigger` window (or press `s` in `keyboard_trigger`).
4. `pose_saver` writes the named pose into `config/nao_saved_poses.yaml`.
5. Preview saved poses by clicking them in the `pose_tester` window.

## Configuration

Saved poses and the RViz view are in `nao_pose_manager/config/` (`nao_saved_poses.yaml`,
`config.rviz`). The parameters of the nodes are in
[CONFIGURATION.md](../../docs/CONFIGURATION.md#gesture_manager), and the arguments of
`arm_gesture_manager.launch.py` in [LAUNCH_ARGUMENTS.md](../../docs/LAUNCH_ARGUMENTS.md#nao).
