# dialostack_bt_client

A [BehaviorTree.CPP](https://www.behaviortree.dev) leaf node that runs a conversation of the
interaction system from inside a robot's Behavior Tree, plus a generic runner and an example. It is
how a behavior of the robot's architecture requests a conversation
([INTEGRATING.md](../docs/INTEGRATING.md#from-a-behavior-tree-of-the-robot)).

## What it provides

- `ExecuteDialog`: a `StatefulActionNode` that sends a goal to the `/dialog/execute_task`
  action and returns `RUNNING` until the conversation ends, then `SUCCESS` (outcome `completed`)
  or `FAILURE`. Halting it cancels the goal.
- `bt_runner`: an executable that loads a tree XML, puts an `rclcpp::Node` on the blackboard
  (the node needs it), and ticks the tree until it finishes.
- `config/example.xml` and `launch/example.launch.py`: a runnable example.

## Ports

The input ports carry the fields of the `DialogTask` goal, with the same names and meanings
([ROS_INTERFACES.md](../docs/ROS_INTERFACES.md#dialogtask-goal)); the output ports carry the
result.

| Port | Direction | Default | Meaning |
|------|-----------|---------|---------|
| `task_description` | in | — | Required |
| `dialog_mode`, `domain`, `frame_schema_json`, `initial_frame_json`, `resources_json` | in | `""` | As in the goal (empty `dialog_mode`: the system chooses the dialogue behavior) |
| `skip_intro` | in | `false` | |
| `max_turns`, `max_stalled` | in | `-1` | `USE_DEFAULT` |
| `max_silence`, `filler_after_s` | in | `-1.0` | `USE_DEFAULT_SECONDS` |
| `action_name` | in | `/dialog/execute_task` | The action server |
| `timeout` | in | `300.0` | Seconds after which the node cancels the goal and returns `FAILURE`; set it above the longest conversation the tree expects |
| `final_frame_json` | out | | The data of the conversation, also when it did not complete |
| `outcome` | out | | How it ended ([Endings and outcome](../docs/behaviors/endings.md#the-outcomes)). The node writes `error` when it gives up (`timeout`) or the goal is rejected |
| `failure_reason` | out | | Why it did not complete; `Dialogue timeout` or `Goal rejected by DialoStack` when the node itself fails |
| `total_turns` | out | | User turns |

## Run the example

Start the interaction system (`ros2 launch interaction_system main.launch.py`), then:

```bash
ros2 launch dialostack_bt_client example.launch.py
ros2 run dialostack_bt_client bt_runner --ros-args -p bt_xml:=/path/to/my_tree.xml   # your own tree
```

The parameters of `bt_runner` (node `dialostack_bt_runner`) are in
[CONFIGURATION.md](../docs/CONFIGURATION.md#dialostack_bt_runner).

## Use it in your own tree

```xml
<root BTCPP_format="4" main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <Sequence>
      <ExecuteDialog
        task_description="Take a coffee order"
        dialog_mode="slot_filling"
        final_frame_json="{order}"/>
    </Sequence>
  </BehaviorTree>
</root>
```

Any tree that uses `ExecuteDialog` must have an `rclcpp::Node` on its blackboard under the key
`node`; `bt_runner` and behavior_architecture's runners put it there. The node is also built as a
plugin, `libdialostack_bt_client_plugin.so`, for `registerFromPlugin()`.
