# Integrating the interaction system in your robot

This page explains how a robot's software uses the interaction system: what to launch, how a
behavior asks for a conversation, and how it acts on the result. To the robot's architecture, the
interaction system is one more subsystem, like navigation, with the
[same contract](ARCHITECTURE.md#interaction-as-a-capability-with-the-robots-layers-inside).

Before reading this page, you need a built workspace ([INSTALLATION.md](INSTALLATION.md)), an LLM
served by Ollama ([MODELS.md](MODELS.md)), and the speech capability that the robot provides,
optionally with embodiment and user perception ([ADDING_A_ROBOT.md](ADDING_A_ROBOT.md)).

## 1. Launch

```sh
ros2 launch interaction_system main.launch.py                 # speech (simple_hri), interaction system
ros2 launch interaction_perception interaction_perception.launch.py image_topic:=<your camera>   # optional
```

If the robot serves its own speech recognition and synthesis through simple_hri's actions, start
the stack without speech nodes (`main.launch.py speech:=none`,
[SPEECH.md](SPEECH.md#using-your-robots-own-speech)). Every launch argument is in
[LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md).

## 2. Ask for a conversation

From a robot behavior, send a goal to the action `/dialog/execute_task`
(`ros2_dialog_interfaces/action/DialogTask`; every field in
[ROS_INTERFACES.md](ROS_INTERFACES.md#dialogtask-goal)):

- `task_description` says what the conversation is for, in natural language, with any useful
  context ("You are a care robot. Register the patient for the physiotherapy session").
- `dialog_mode` names the strategy; left empty, the system chooses
  ([ARCHITECTURE.md](ARCHITECTURE.md#the-choice-of-the-dialogue-behavior)).
- `frame_schema_json`, `initial_frame_json`, `resources_json`, `domain` and `skip_intro` are
  optional; what each strategy needs in `resources_json` is on its page
  ([docs/behaviors](behaviors/README.md)).
- The limits `max_turns`, `max_silence` (seconds) and `max_stalled` (turns in a row without
  progress) are optional: leave them at `USE_DEFAULT` / `USE_DEFAULT_SECONDS` to use the system's
  values, set `UNLIMITED` (0) to remove one, or give the value the task needs (e.g. a longer
  `max_silence` for a task in which the user must look something up).

The result says how the conversation ended (`outcome`; `success` is true only for `completed`),
its data (`final_frame_json`, also when it did not complete), why it did not complete
(`failure_reason`) and its turns. The action's feedback, on every robot utterance, carries the
phase and the data so far; `/interaction_system/status` gives the state of the system.

The outcome is what the behavior acts on. The meaning of each outcome is in
[Endings and outcome](behaviors/endings.md#the-outcomes); typical reactions are:

| `outcome` | What a behavior might do |
|---|---|
| `completed` | Go on |
| `partial` | Use what was obtained (`failure_reason` lists what is missing); ask a person for the rest |
| `failed` | Try another way, or tell a person |
| `cancelled` | Respect it: the user asked to stop (`Cancelled by the user`), or the behavior cancelled the goal (`Cancelled`) |
| `user_silent` | Check whether the user is still there |
| `breakdown` | Try later, change the modality, or call a person |
| `turn_limit` | Resume later, passing the data as `initial_frame_json` (slot filling) |
| `error` | Retry, or report it; `failure_reason` says what failed |

In practice:

- One conversation runs at a time. A goal sent while another runs is rejected; cancel the running
  one first (e.g. when the phase of the mission changes).
- Canceling the goal stops the robot's voice and its listening at once and returns `canceled`.
- To chain conversations without greeting again, set `skip_intro: true` from the second one on.
- An interrupted slot filling can be resumed: pass its `final_frame_json` as the
  `initial_frame_json` of the next goal.

### From a Behavior Tree of the robot

The `ExecuteDialog` node of [`dialostack_bt_client`](../dialostack_bt_client/README.md) sends the
goal and stays `RUNNING` until the conversation ends, then returns `SUCCESS` (outcome
`completed`) or `FAILURE`, and writes the data, the outcome and the reason to the blackboard:

```xml
<Sequence>
  <ExecuteDialog task_description="Register the patient for the physiotherapy session"
                 dialog_mode="slot_filling"
                 frame_schema_json='{"slots": [{"name": "full_name", "type": "str"}, {"name": "age", "type": "int", "minimum": 18}]}'
                 final_frame_json="{patient}"/>
  <ExecuteDialog task_description="Explain the exercise: raise both arms to shoulder height and hold"
                 dialog_mode="explanation" skip_intro="true"/>
</Sequence>
```

Halting the node cancels the conversation. Its ports, its plugin library and a generic runner are
in [dialostack_bt_client](../dialostack_bt_client/README.md).

### From an FSM of behavior_architecture

A state of the robot's orchestrator activates a runner whose tree contains `ExecuteDialog`, as for
any other capability; behavior_architecture's runners already put the `rclcpp::Node` that
`ExecuteDialog` needs on the blackboard. Since the interaction system is itself built on
behavior_architecture, both can run in the same robot, each with its own `mission_executor`.

## Goals for every strategy

**Slot filling** collects data and confirms it:

```sh
ros2 action send_goal --feedback /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Take a coffee order: drink type, size and customer name',
    dialog_mode: 'slot_filling', domain: 'coffee shop'}"
```

**Explanation** explains something and verifies it was understood with teach-back; the key
points the user must say back are optional (without them the LLM lists them from the material):

```sh
ros2 action send_goal /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Explain the pre-surgery fasting instructions',
    dialog_mode: 'explanation',
    resources_json: '[{\"name\": \"instructions\", \"description\": \"fasting protocol\", \"content\": \"No solid food from midnight. Water until 6 am.\"}, {\"name\": \"key points\", \"description\": \"to say back\", \"content\": \"{\\\"key_points\\\": [\\\"No solid food from midnight\\\", \\\"Water until 6 am\\\"]}\"}]',
    domain: 'hospital'}"
```

**Quiz** runs questions with known answers and scores them:

```sh
ros2 action send_goal /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Run a short geography quiz',
    dialog_mode: 'quiz',
    resources_json: '[{\"name\": \"questions\", \"description\": \"quiz bank\", \"content\": \"[{\\\"question\\\": \\\"Capital of France?\\\", \\\"answer\\\": \\\"Paris\\\"}]\"}]'}"
```

**Procedure** guides the user step by step; the user can repeat, go back or skip:

```sh
ros2 action send_goal /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Guide a short shoulder warm-up',
    dialog_mode: 'procedure',
    resources_json: '[{\"name\": \"steps\", \"description\": \"Procedure steps\", \"content\": \"[\\\"Sit up straight with your feet flat on the floor\\\", {\\\"step\\\": \\\"Roll your shoulders back slowly five times\\\", \\\"check\\\": \\\"no pain\\\"}]\"}]'}"
```

**Role play** plays a character; the user can ask for a hint at any moment, and at the end the
robot steps out of the role and scores each criterion from 1 to 5:

```sh
ros2 action send_goal /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Practice a job interview',
    dialog_mode: 'roleplay',
    resources_json: '[{\"name\": \"scene\", \"description\": \"Roleplay scene\", \"content\": \"{\\\"scenario\\\": \\\"Interview for a robotics internship\\\", \\\"role\\\": \\\"The HR interviewer: demanding but kind\\\", \\\"criteria\\\": [\\\"Answers clearly\\\", \\\"Gives concrete examples\\\"]}\"}]'}"
```

**Grounded question answering** answers only from the given information; the `handover` resource
is optional:

```sh
ros2 action send_goal /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Answer the questions of the visitors at the museum entrance',
    dialog_mode: 'grounded_qa',
    resources_json: '[{\"name\": \"information\", \"description\": \"visitor information\", \"content\": \"The museum opens from Tuesday to Sunday, 10:00 to 19:00. Admission costs 8 euros.\"}, {\"name\": \"handover\", \"description\": \"who can help\", \"content\": \"The staff at the ticket desk can help you with that.\"}]'}"
```

**Questionnaire** administers an instrument word for word and returns the score:

```sh
ros2 action send_goal /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Ask the visitor the satisfaction survey',
    dialog_mode: 'questionnaire',
    resources_json: '[{\"name\": \"instrument\", \"description\": \"survey\", \"content\": \"{\\\"options\\\": [\\\"Not at all\\\", \\\"A little\\\", \\\"Quite\\\", \\\"Very much\\\"], \\\"items\\\": [\\\"I enjoyed the visit\\\", \\\"I would recommend the museum\\\"]}\"}]'}"
```

Without `dialog_mode`, the system offers slot filling, explanation and every strategy whose
needs the resources provide (a question bank, steps, a scene, plain-text material, an
instrument), and the LLM chooses among them from the task
([ARCHITECTURE.md](ARCHITECTURE.md#the-choice-of-the-dialogue-behavior)).

## 3. Check it

```sh
ros2 topic echo /interaction_system/status       # 0 IDLE, 1 REASONING, 2 RUNNING, 3 FAILED
ros2 topic echo /robot_utterance
```

More checks: [VALIDATION_AND_TROUBLESHOOTING.md](VALIDATION_AND_TROUBLESHOOTING.md).
