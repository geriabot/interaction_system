# interaction_system

![ros-distro](https://img.shields.io/badge/ROS%202-Jazzy%20%7C%20Lyrical-blue)
![pixi](https://img.shields.io/badge/pixi-any%20Linux%20x86--64-orange)
![system-ros](https://img.shields.io/badge/system%20ROS%202-Ubuntu%2024.04-orange)

An autonomous **spoken interaction system for social robots**, built on ROS 2 Jazzy (and Lyrical
with pixi).

A social robot in a home, a care facility or a classroom holds conversations with a purpose:
take an order, register a patient, explain the instructions before a test and check they were
understood, run a memory quiz, guide an exercise step by step, or play an interviewer so that the
user can practise. In this system, the behaviors of the robot ask for a conversation with its
task, in natural language, and get back its result as structured data. The interaction system
chooses how to hold the conversation, holds it by voice, and keeps it on track when the user
deviates, falls silent, interrupts or wants to stop. A large language model (LLM) understands the
user and phrases the robot's replies; it never decides what happens next.

The sections below explain the idea and the design decisions behind it, the architecture it
belongs to, and how a request is processed. The [evaluation](evaluation/README.md) holds the
experiments that test each decision, so that anyone can run them again.

```sh
ros2 action send_goal --feedback /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
  "{task_description: 'Take a coffee order: drink type, size and customer name', domain: 'coffee shop'}"
```

## The idea

Five design decisions define the system.

1. **The frame of the task bounds the LLM.** Every conversation goes through a frame: the
   structured list of what it must cover, each element with its state (the fields of a form, the
   key points of an explanation, the questions of a quiz, the steps of a procedure, the items of a
   questionnaire, the criteria of a role-play rubric). The behavior of the robot sends it with its
   request (or the material it is built from) and gets it back filled. The frame is held outside
   the LLM: each question to the model is about one element, its answer is validated against the
   frame before it enters, the reply is phrased for the element the tree chose, and the model
   never decides that an element is done. Frames are as old as dialogue systems (form filling);
   here they are the contract that governs a generative model
   ([the frame of the task](docs/ARCHITECTURE.md#the-frame-of-the-task)).
2. **Deterministic control, probabilistic understanding.** The phases of a conversation, the turn
   limits, the confirmation of the collected data and the cancellation are explicit control. The
   LLM is only asked narrow questions with a JSON schema (which slots does this utterance fill? is
   this a confirmation or a correction?) or asked to phrase one utterance. Every call has a
   timeout, bounded retries and a deterministic fallback, so a misbehaving model degrades the
   conversation but never derails it.
3. **One Behavior Tree per dialogue strategy.** Slot filling, explanation with a teach-back
   check of understanding, quiz, guided procedure, role play, grounded question answering and
   standardized questionnaire are each a Behavior Tree. The trees share the subtrees that keep
   any conversation on track ([the shared mechanisms](#the-dialogue-behaviors)). **Every
   conversation closes with a closing line and an outcome with its reason**, so the behavior
   that asked for it knows why it ended and can act on it. A new strategy is a new tree and its
   questions, not a change to the system.
4. **The strategy is chosen from the task.** A request can name its strategy, or the system
   chooses it: a strategy is offered only if the request provides what it needs (a quiz needs a
   question bank, a procedure its steps, a role play its scene), and the LLM chooses among the
   offered ones from their descriptions.
5. **Dialogue as a capability of the robot.** The interaction system has the same layers as the
   robot's behavioral control architecture: a finite state machine (FSM) coordinates each request
   and activates the tree of the chosen strategy. The rest of the robot sees one action and one
   status topic. Speech recognition and synthesis, the perception of the user and the LLM are
   services it uses, and can be replaced.

The interaction system **only holds the conversation**. Moving, pointing or gesturing are done by
other capabilities of the robot; the embodiment layer (e.g. the NAO's gestures and eye LEDs)
follows the generic signals the conversation publishes.

## Interaction as a capability of the robot

![The interaction system as an autonomous subsystem that follows the pattern of the robot's behavioral control architecture](./media/interaction_fractal_architecture.drawio.svg)

The robot is controlled by a layered behavioral control architecture (left), built with
[`behavior_architecture`](https://github.com/geriabot/behavior_architecture): an FSM coordinates
the plan of the task and activates, phase by phase, a *behavior forest* of Behavior Trees. The
capabilities of the robot (perception, navigation, attention, interaction) are autonomous
subsystems with a standard interface. The interaction system (right) is one of them, and has the
same layers inside:

| Layer | In the robot's architecture | In the interaction system |
|---|---|---|
| Deliberation, planning | Decide the goals, when needed | Not needed: the goal comes in the request |
| Coordination | An FSM, the plan of the task | An FSM, the life of a request: `IDLE`, `SETTING_BEHAVIOR_ACTION`, `SETTING_BEHAVIOR_INPUT`, `ACTING` |
| Action | The behavior forest | One tree per dialogue strategy; only the chosen one is active |
| Capabilities | Perception, navigation, interaction... | Speech (speech-to-text, text-to-speech), perception of the user (lip activity for barge-in, experimental; emotion, preliminary) |
| External service | — | The LLM, asked by the FSM (the strategy) and by the trees (understanding, phrasing) |

The nodes of the interaction system, and the behavioral control architecture, speech,
perception of the user, embodiment and models around it:

![The nodes of the interaction system, and the behavioral control architecture, speech, perception of the user, embodiment and models around it](./media/interaction_system_architecture.drawio.svg)

For the rest of the robot, the interaction system is one more capability. `mission_executor`
configures and activates it through the lifecycle of `/interaction_orchestrator`; it receives the
task as the goal of an action, reports every utterance as feedback, and returns the result of the
conversation ([Using it from the behaviors of the robot](#using-it-from-the-behaviors-of-the-robot)).
The LLM is served by Ollama, locally or on a GPU server, or by a cloud provider
([MODELS.md](docs/MODELS.md)). [ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the design and why
it was chosen.

## How a request is processed

![The interaction pipeline: the processing of one request, and one user turn of the conversation](./media/interaction_pipeline.drawio.svg)

The figure follows one request, and one user turn of the conversation it starts. The columns are
the states of the FSM. Question 1 chooses the strategy when the request does not name it and
more than one fits; question 2 is the strategy's narrow question of each turn.

1. **Request.** A behavior of the robot sends a `DialogTask` goal to `/dialog/execute_task`: the
   task, and optionally the strategy (`dialog_mode`), a slot schema, values to resume from,
   resources (a question bank, the steps, a scene, reference text), a domain and the limits of
   the conversation (turns, seconds of silence, turns without progress).
   One conversation runs at a time; another request is rejected while one runs.
2. **The strategy.** If the request names it, it is used. Otherwise the intelligence offers the
   strategies whose needs the request provides and, if more than one, the LLM chooses.
3. **The request to the blackboard.** The FSM writes the request, the limits and the counters to
   the blackboard of the behaviors and activates the tree of the strategy.
4. **The conversation.** The tree opens the conversation (`begin` builds the state of the dialogue
   from the request), then runs one user turn per cycle: check the turn limit, listen, check for a
   spoken cancellation, let the strategy handle the turn, speak the reply. Each step that needs
   language asks the intelligence one *skill*; the state of the dialogue travels with each skill
   and back to the blackboard, so the intelligence keeps none.
5. **Feedback.** Every utterance is reported in the action's feedback before it is spoken.
6. **Result.** When the tree ends, the FSM returns the result and the intelligence closes the
   dialogue (its LLM sessions, its transcript). `/interaction_system/status` shows `IDLE`
   again, or `FAILED` if the conversation did not achieve its goal.

## The dialogue behaviors

The catalog is in `interaction_system_core/interaction_system/config/interaction_orchestrator_params.yaml`
and the trees in `interaction_system_behaviors/behavior_tree_xml`.

| Strategy (`dialog_mode`) | The robot... | Needs from the request | Returns |
|---|---|---|---|
| `slot_filling` | Asks for the data it needs, accepts corrections, validates types, allowed values, ranges and conditions, then reads the data back for confirmation | Nothing (a schema is optional: without it the LLM designs one from the task) | The frame as JSON |
| `explanation` | Explains a topic and verifies it was understood with teach-back (the user says it back in their own words; each key point is checked and only what is missing or wrong is explained again; a direct question if the user does not want to), answers questions | Nothing (reference text and key points are optional) | The status of each key point |
| `quiz` | Asks the questions of a bank, grades each spoken answer and gives the score | A question bank | The result of each question |
| `procedure` | Guides through steps, helps when the user is stuck, repeats, goes back or skips on request | The steps | The outcome of each step |
| `roleplay` | Plays a character in a scene, gives hints on request, then scores the user on each criterion of a rubric (1 to 5) | The scene and its rubric | The scores and the feedback |
| `grounded_qa` | Answers the user's questions only from the given information (each answer verified against it), says when it does not have the information and declines what is outside the task | The material (plain text) | Each question with its resolution |
| `questionnaire` | Administers a standardized questionnaire word for word, maps each answer to an option of the scale without choosing for the user, and computes the score (never said) | The instrument | The answer to each item and the score |

Every strategy shares the same mechanisms, decided by the trees with counters (the LLM only
classifies the turn):

| Mechanism | What the robot does |
|---|---|
| [Silence](docs/behaviors/silence.md) | Silence is not abandonment: the robot says it is waiting, repeats what it asked, and closes only after `max_silence` seconds (90 by default). A procedure step may declare how long it takes |
| [Spoken cancellation](docs/behaviors/cancellation.md) | "Stop, forget it": the robot asks for confirmation; only a yes ends; any other answer resumes with what it had asked |
| [Recovery and communication breakdown](docs/behaviors/no_progress.md) | An element the user cannot answer is repeated, simplified, then left unanswered; after `max_stalled` turns in a row without progress (4 by default) the robot closes |
| [Endings](docs/behaviors/endings.md) | No silent endings: every ending has a closing line, and the result says how and why the conversation ended |
| [Conversational fillers](docs/behaviors/fillers.md) | A reply not ready in two seconds is preceded by a fixed filler ("Hmm…", "Just a moment…"), and by one more four seconds later; a filler is not a turn and changes no counter |
| [Barge-in](docs/behaviors/barge_in.md) *(experimental)* | The user can interrupt the robot: it stops speaking and listens (with the lip activity of the perception of the user; without it, the robot finishes each utterance). Implemented, not validated |

The result's `outcome` is one of `completed` (the only success), `partial` (ended normally with
an element left unfinished: an unanswered slot or question, a skipped or stuck step),
`failed`, `cancelled`, `user_silent`, `breakdown`, `turn_limit` and `error`, with
`failure_reason` saying why. Each strategy and mechanism, with its Behavior Tree, is described in
detail in [docs/behaviors](docs/behaviors/README.md).

## Using it from the behaviors of the robot

The rest of the robot sees one action and one topic. Integration step by step:
[INTEGRATING.md](docs/INTEGRATING.md). Every interface: [ROS_INTERFACES.md](docs/ROS_INTERFACES.md).

| | Interface | Content |
|---|---|---|
| **In** | `/dialog/execute_task` (action `ros2_dialog_interfaces/action/DialogTask`) | `task_description`: the task in natural language. Optional: `dialog_mode`, `frame_schema_json` ([format](docs/SLOT_SCHEMA.md)), `initial_frame_json` (resume), `resources_json`, `domain`, `skip_intro` (chained conversations), and the limits `max_turns`, `max_silence`, `max_stalled` (unset: the system's values) |
| **Out** | feedback | `state` (the phase), `current_frame_json` (the data so far), `last_utterance`, `turns` |
| **Out** | result | `success` (only when `outcome` is `completed`), `outcome` (how it ended), `final_frame_json` (the data, also when it did not complete), `failure_reason` (why), `total_turns` |
| **Out** | `/interaction_system/status` | `IDLE` (0), `REASONING` (1), `RUNNING` (2), `FAILED` (3), with the running strategy and its phase |

From a Behavior Tree of the robot, the `ExecuteDialog` node of
[`dialostack_bt_client`](dialostack_bt_client/README.md) sends the goal and returns its result
on the tree's blackboard. Examples of goals for every strategy are in
[INTEGRATING.md](docs/INTEGRATING.md#goals-for-every-strategy). The interaction system has no code
for a particular robot: what a robot provides (the speech, the embodiment, the perception of the
user) is in [ADDING_A_ROBOT.md](docs/ADDING_A_ROBOT.md).

## Languages

The system speaks **English and Spanish**, chosen with the parameter `language` or the launch
argument (`main.launch.py language:=es`). The method does not depend on the language: the code
decides what the conversation does, and the model answers narrow questions in the language of the
conversation. Everything that depends on the language is in one file per language,
`interaction_system_core/interaction_intelligence/config/languages/<language>.yaml`: the fixed
phrases (what the robot says when the model fails, the silence prompts, the closings), the
acknowledgments and fillers, the exact stop commands ("stop"; "para"), the yes/no words, the noise
phrases of the recognizer, and the recognition language and voice of simple_hri. The prompts are
one English set that asks for the replies in the language of the conversation.

**Adding a language** is writing its file (a copy of `english.yaml`, translated: the loader and
the tests check that it has every key and placeholder), choosing a voice, and checking it; step by
step, with what the evaluation needs to evaluate it too: [ADDING_A_LANGUAGE.md](docs/ADDING_A_LANGUAGE.md).
The evaluation validates the method in English in simulation; the study with real users is in
Spanish ([evaluation/doc/EXPERIMENTS.md](evaluation/doc/EXPERIMENTS.md)).

## Documentation

Read in this order to go from what the system does to how to extend it.

1. [INDEX.md](INDEX.md): every command in one page.
2. [ARCHITECTURE.md](docs/ARCHITECTURE.md): the design (an FSM that coordinates a forest of
   Behavior Trees, an intelligence that asks the LLM), the nodes, a request node by node, and the
   configuration files.
3. [docs/behaviors](docs/behaviors/README.md): each strategy and shared mechanism in detail, with
   its Behavior Tree drawn.
4. [SPEECH.md](docs/SPEECH.md): the speech capability (simple_hri), how the trees listen and
   speak, and barge-in (experimental).
5. [MODELS.md](docs/MODELS.md): which model answers each question, and how to change it.
6. [PROMPTS.md](docs/PROMPTS.md): the questions to the LLM, which skill asks each one, and how to
   change them.
7. [INTEGRATING.md](docs/INTEGRATING.md): using the interaction system from your robot's software.
8. [ADDING_A_ROBOT.md](docs/ADDING_A_ROBOT.md): what a robot provides, with the NAO as the example.
9. [ADDING_DIALOGUE_BEHAVIORS.md](docs/ADDING_DIALOGUE_BEHAVIORS.md): adding a new dialogue
   strategy.
10. [ADDING_A_LANGUAGE.md](docs/ADDING_A_LANGUAGE.md): adding a language (the language files).
11. [evaluation/README.md](evaluation/README.md) and
    [evaluation/doc/EXPERIMENTS.md](evaluation/doc/EXPERIMENTS.md): the evaluation tools, and the
    experiments ([Running the experiments](#running-the-experiments)); the instructions for the
    people who review and annotate: [evaluation/HUMAN_REVIEW.md](evaluation/HUMAN_REVIEW.md).

**Reference**:

- [INSTALLATION.md](docs/INSTALLATION.md): the two ways to install, with
  [pixi](docs/INSTALL_PIXI.md) (recommended) or [natively](docs/INSTALL_NATIVE.md) (system ROS 2).
- [PIXI.md](docs/PIXI.md): the pixi environment, adding a package, ROS 2 Lyrical.
- [GPU_SERVER.md](docs/GPU_SERVER.md): serving the models from a shared GPU server, without `sudo`.
- [CONFIGURATION.md](docs/CONFIGURATION.md): every parameter of every node.
- [SLOT_SCHEMA.md](docs/SLOT_SCHEMA.md): the slot schema (types, allowed values, ranges,
  conditional slots).
- [ROS_INTERFACES.md](docs/ROS_INTERFACES.md) and [LAUNCH_ARGUMENTS.md](docs/LAUNCH_ARGUMENTS.md):
  every node, topic, service, action and launch argument.
- [VALIDATION_AND_TROUBLESHOOTING.md](docs/VALIDATION_AND_TROUBLESHOOTING.md): checks and common
  problems.
- Packages: [interaction_perception](interaction_perception/interaction_perception/README.md),
  [ros2_llm_manager](ros2_llm_manager/README.md),
  [dialostack_bt_client](dialostack_bt_client/README.md), [robots/nao](robots/nao/README.md),
  [the web GUI](dialostack_gui/src/README.md).

## Repository structure

<details>
<summary>Show repository structure</summary>

| Package | Description |
|---|---|
| `interaction_system_core/interaction_system` | The orchestrator (the FSM of a request, a `behavior_architecture` plugin), the catalog of dialogue behaviors, the configuration and the launch files |
| `interaction_system_core/interaction_system_interfaces` | Status, and the questions of the orchestrator and the trees to the intelligence |
| `interaction_system_core/interaction_intelligence` | The interaction intelligence: the skills (the questions to the LLM), the dialogue frame, the prompts |
| `interaction_system_behaviors` | The Behavior Trees of the strategies (XML) and their nodes (`AskIntelligence`, `Speak`, `Listen`, `WithFillers`) |
| `ros2_dialog_interfaces` | The public interface: the `DialogTask` action and the `BargeIn` message |
| `ros2_llm_manager`, `ros2_llm_interfaces` | The LLM gateway: one inference action and session services for Ollama and cloud providers |
| `interaction_perception/*` | The perception of the user: lip activity (barge-in, experimental), facial emotion (preliminary, not validated), and the user-state service |
| `dialostack_gui` | Web GUI to configure, launch and monitor conversations |
| `dialostack_bt_client` | BehaviorTree.CPP node that runs a conversation as a step of a robot's tree |
| `robots/nao` | NAO embodiment: URDF, talking gestures, eye LEDs, pose tooling |
| `evaluation/*` | Offline benchmarks, the simulation (simulated users and speech), ablations, experiments and analysis ([README](evaluation/README.md)) |

At the root, `thirdparty.repos` lists the source dependencies (`behavior_architecture`, of which
only the base is used, without its optional LLM orchestrators, and `simple_hri`); `pixi.toml` and
`pixi.lock` define the environments, and `pixi/setup_workspace.sh` links them at the root of the
workspace; `requirements.txt` gathers the Python packages of the native installation, and
`activate.sh` prepares a terminal with system ROS and a Python virtual environment
([INSTALL_NATIVE.md](docs/INSTALL_NATIVE.md)); `gui.sh` starts the web GUI.

</details>

## Usage

The system is a ROS 2 Jazzy workspace (ROS 2 Lyrical with pixi). Below, `<workspace>` is the
colcon workspace (e.g. `~/interaction_ws`).

### Requirements

- An x86-64 PC with Linux: any distribution with pixi, or Ubuntu 24.04 with system ROS 2 Jazzy.
- An LLM served by [Ollama](https://ollama.com), on this computer or on a GPU server
  ([GPU_SERVER.md](docs/GPU_SERVER.md)). The default model, `qwen2.5:7b`, needs a GPU with about
  8 GB; the configuration of the experiments, two 14B models, about 24 GB. A cloud provider can
  be used instead ([MODELS.md](docs/MODELS.md)).
- A microphone and a speaker.
- About 10 GB of disk for the pixi environment, and Internet access on the first run, which
  downloads the environment and the speech models.

### Installation

With pixi ([INSTALL_PIXI.md](docs/INSTALL_PIXI.md) has the details, ROS 2 Lyrical and the
optional parts):

```sh
curl -fsSL https://pixi.sh/install.sh | sh          # once; then open a new terminal
pixi global install vcstool                         # once

mkdir -p <workspace>/src && cd <workspace>/src
git clone https://github.com/geriabot/interaction_system.git
vcs import . < interaction_system/thirdparty.repos  # behavior_architecture and simple_hri
cd ..
./src/interaction_system/pixi/setup_workspace.sh    # once: links pixi.toml and pixi.lock here
pixi run build
```

Then, in every terminal where you run the system:

```sh
cd <workspace>
pixi shell
source install/setup.bash
```

Run pixi from `<workspace>`, not from inside the repository
([PIXI.md](docs/PIXI.md#where-it-lives)). With a system ROS 2 Jazzy instead of pixi:
[INSTALL_NATIVE.md](docs/INSTALL_NATIVE.md).

### Quick start

1. Serve the LLM so that Ollama answers at `localhost:11434`: install
   [Ollama](https://ollama.com/download) on this computer, or open a tunnel to a GPU server that
   runs it ([GPU_SERVER.md](docs/GPU_SERVER.md)). Then pull the default model:
   ```sh
   ollama pull qwen2.5:7b
   ```
2. In a prepared terminal, start the whole stack (speech with simple_hri, the LLM gateway and the
   interaction system). The first run downloads the speech models into `models/` of the current
   directory:
   ```sh
   ros2 launch interaction_system main.launch.py            # language:=es for Spanish
   ```
3. In another prepared terminal, ask for a conversation, and speak to the robot:
   ```sh
   ros2 action send_goal --feedback /dialog/execute_task ros2_dialog_interfaces/action/DialogTask \
     "{task_description: 'Take a coffee order: drink type, size and customer name', dialog_mode: slot_filling}"
   ```

The robot greets the user, asks for each missing piece of data, handles corrections ("actually,
make it a large"), reads the order back and returns it in `final_frame_json`. The feedback shows
every utterance; `ros2 topic echo /interaction_system/status` shows the state of the system.

### Running the system

| What | Command |
|---|---|
| The whole stack | `ros2 launch interaction_system main.launch.py [speech:=simple_hri\|none] [stt:=faster_whisper\|whisper] [tts:=piper\|mms] [language:=en\|es]` ([languages](#languages)) |
| Only the interaction system (speech and LLM started apart) | `ros2 launch interaction_system interaction_system.launch.py` |
| The LLM gateway alone | `ros2 launch ros2_llm_manager llm_manager.launch.py` |
| The perception of the user (optional) | `ros2 launch interaction_perception interaction_perception.launch.py image_topic:=<camera>` |
| The web GUI | `./src/interaction_system/gui.sh`, then open <http://localhost:5173> ([INSTALLATION.md](docs/INSTALLATION.md#the-web-gui)) |

`speech:=none` starts no speech nodes, for the simulation or for a robot that serves the speech
actions itself ([SPEECH.md](docs/SPEECH.md#using-your-robots-own-speech)). Every launch argument is
in [LAUNCH_ARGUMENTS.md](docs/LAUNCH_ARGUMENTS.md), every parameter in
[CONFIGURATION.md](docs/CONFIGURATION.md), and every command in [INDEX.md](INDEX.md).

### Tests

```sh
pixi run test     # unit tests of the intelligence (no ROS)
# whole conversations through the running system, with a scripted LLM (no model, no audio);
# in a prepared terminal, with a ROS_DOMAIN_ID that nothing else uses
ROS_DOMAIN_ID=77 python -m pytest src/interaction_system/evaluation/simulation/interaction_sim/test
cd src/interaction_system/dialostack_gui && npm ci && npm test   # GUI logic (npm ci once)
```

## Running the experiments

The experiments run the system against simulated users (local or cloud models) with ground
truth, and with real participants; the scripts score the conversations. The repository includes
the scenarios, the experiment files and the tools, not the results.

The simplest way is the web control panel. It runs an experiment on a hardware profile (a
*campaign*), checks first that the GPU servers are free, shows its progress, runs one dialogue of
an experiment or the quick checks, and runs the sessions with real participants (in campaigns,
each with a frozen configuration of how the robot sounds). Its Tests tab lets you play the user of
any scenario yourself, by voice; it stores nothing:

```sh
cd <workspace>/src/interaction_system
python3 evaluation/scripts/control_panel.py      # then open http://localhost:8095 (#tests: the Tests tab)
```

The same from a prepared terminal:

```sh
cd <workspace>/src/interaction_system/evaluation
ROS_DOMAIN_ID=<free id> python scripts/sim_checks.py start                    # quick check, no model
python scripts/run_experiment.py experiments/<experiment>.yaml --dry-run      # list the dialogues
python scripts/run_parallel.py experiments/<experiment>.yaml experiments/hardware/<profile>.yaml   # run it
python analysis/score_runs.py results/<name>                                  # score them
```

They need the workspace built with pixi and the models answering (Ollama at `localhost:11434`).
Cloud models are paid: every command that would call one needs `--allow-cloud`. The guides:
[the control panel](evaluation/doc/CONTROL_PANEL.md), [the tools](evaluation/README.md) and
[the experiments](evaluation/doc/EXPERIMENTS.md).

## Troubleshooting

- `pixi` cannot find a manifest: run it from the root of the workspace, after
  `./src/interaction_system/pixi/setup_workspace.sh` (once).
- pixi starts downloading a second environment, or warns `Could not find activation scripts`: it
  was run inside `src/interaction_system` or with `--manifest-path`; run it from the root of the
  workspace ([PIXI.md](docs/PIXI.md#where-it-lives)).
- A second request is rejected: one conversation runs at a time; cancel the running goal first.
- The robot does not answer: check that Ollama answers (`curl -s localhost:11434/api/version`) and
  that the model of `app_params.yaml` is pulled ([MODELS.md](docs/MODELS.md#the-local-model)).

More checks and runtime problems: [VALIDATION_AND_TROUBLESHOOTING.md](docs/VALIDATION_AND_TROUBLESHOOTING.md).

## Credits

This system started as a final degree thesis (TFG) at Universidad Rey Juan Carlos, supervised
by Rodrigo Pérez-Rodríguez and Juan Diego Peña Narváez and carried out by Álvaro Quintana Camacho:
[*Sistema Inteligente de Interacción Autónoma Persona-Robot*](https://burjcdigital.urjc.es/items/4d0ae885-da6c-4f7f-8d03-08067adf94f5)
(2026). The code of the thesis, as it was delivered (DialoStack), is in
[aquintan4/DialoStack](https://github.com/aquintan4/DialoStack).

## License

This project is licensed under the **MIT License**. See the [LICENSE](./LICENSE) file. The vendored
`robots/nao/nao_description/` (NAO URDF) is third-party, under Apache-2.0 and BSD with its own
`LICENSE` and `NOTICE`.
