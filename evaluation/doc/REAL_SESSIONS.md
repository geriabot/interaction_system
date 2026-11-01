# The sessions with real participants

How to run the conversations of real participants with the robot: the conditions, the sessions
file, the counterbalancing, the checks before a session, the procedure of a session, what is
stored and how it is scored. The design of the study (E4: why only the method, the cards and the
mechanism each provokes, the criteria) is in
[EXPERIMENTS.md](EXPERIMENTS.md#e4-validation-with-real-users); the measures in
[METRICS.md](METRICS.md). The scripts are in [`evaluation/real/`](../real); the
[control panel](CONTROL_PANEL.md#real-sessions) runs all of it with buttons.

The records of the trials have the format of the simulated dialogues, so that the same scorers
measure both. Participants are healthy adults, in Spanish, and never play another kind of person:
the task cards give them a goal (an order, an answer, a step to skip), never a character. The logic
without ROS (plans, counterbalancing, cards) runs with plain Python (`real_sessions.py plan`,
`card` and `balance`) and is tested by `python3 -m pytest real/test_sessions_common.py`.

**The words used here.** A *session* is one participant's visit. A *trial* is one conversation
of the participant with the robot, in one *condition* and one *scenario*. A condition is a
control of the conversation (*bounded*, the method; *LLM-led*, the `monolithic` ablation) with a
system model. A scenario is one of
[`experiments/scenarios/e4_es.yaml`](../experiments/scenarios/e4_es.yaml): the request the robot
receives, the ground truth that scores the conversation, the planned ending and the mechanism the
card provokes. The *task card* tells the participant what to achieve; its *detour* asks them to
try, at some point, to take the robot out of its task: an unrelated question (`off_task`), a
request the robot should decline (`decline`) or a value the task does not allow (`not_allowed`).
In the simulated twin the scenario says the same detour as an event (`kind: detour`, with the card's
detour kind) at a fixed turn ([EXPERIMENTS.md](EXPERIMENTS.md)).

## Contents

- [The conditions](#the-conditions)
- [The sessions file](#the-sessions-file)
- [Campaigns and configurations](#campaigns-and-configurations)
- [The counterbalancing](#the-counterbalancing)
- [What you need](#what-you-need)
- [Before each session: the checks](#before-each-session-the-checks)
- [Technical check of the speech failures](#technical-check-of-the-speech-failures)
- [Barge-in](#barge-in)
- [The procedure of a session](#the-procedure-of-a-session)
- [Running a trial](#running-a-trial)
- [During a trial](#during-a-trial)
- [What is stored](#what-is-stored)
- [Scoring the sessions](#scoring-the-sessions)
- [Cloud models and the participants' data](#cloud-models-and-the-participants-data)
- [Ethics and consent](#ethics-and-consent)
- [Troubleshooting](#troubleshooting)

## The conditions

Every participant has four conversations with the robot, each in a different scenario, so that a
participant never does the same task twice. The conditions are set in the sessions file. E4
enables only the method with the local model (`bounded`); the other conditions stay defined,
disabled, for other studies:

| Condition | Control | System model | In E4 |
|---|---|---|---|
| `bounded` | The method: the Behavior Trees run the conversation and ask the LLM narrow questions (`main.launch.py`) | The local model chosen in E0a (Ollama) | yes |
| `llm_led` | The LLM-led conversation: the LLM runs the whole strategy (`interaction_ablations/llm_led.launch.py`) | The large cloud model of E1 | no (disabled) |
| `llm_led_local` | The LLM-led conversation | The local model: separates the effect of the control from that of the model | no (disabled) |

With one condition and four trials per condition, the four conversations are all with the method,
and the scenarios and their order are counterbalanced over the participants
([below](#the-counterbalancing)). With more conditions enabled, each participant goes through them
in a balanced order. Speech recognition runs on this computer in every condition. The same
scenarios, with the same events and limits, run with simulated users in
[`experiments/e4_sim_es.yaml`](../experiments/e4_sim_es.yaml), the simulated twin.

## The sessions file

[`real/sessions_es.yaml`](../real/sessions_es.yaml) (the conditions, the models, the scenarios and
the wording of every card; the cards are adjusted after the pilot and then frozen):

| Key | What it is |
|---|---|
| `name`, `scenarios_file`, `language` | The name of the sessions, the scenario file (`../experiments/scenarios/e4_es.yaml`: the request, the ground truth, the planned ending and the mechanism of each card) and the language of the conversations (`Spanish`) |
| `barge_in`, `camera_topic` | Whether the robot stops talking when the participant starts speaking (`false` by default; [Barge-in](#barge-in)) and the camera image of the lip activity detector |
| `model_params` | Parameter overrides for one system model, e.g. the `routes` of the local model's questions to a second model (both must be on the Ollama server; the checks and trials verify and load them); a cloud model does not use them |
| `conditions` | Each with its `control` (`bounded` or `llm_led`) and its `model`: an Ollama model, or a cloud one written `provider/model` with its dated version (`openai/<model>-<date>`; the scripts refuse `<pin>`). `enabled: false` leaves a condition out of the plans |
| `trials_per_condition` | Scenarios per condition and participant (4: the four conversations of each participant, all with the method) |
| `launch` | The speech capability: `speech: simple_hri`, `language: es`, `stt`, `tts` (as `main.launch.py`). How the robot sounds and the speech hardware (the voice, its settings, the filler delay, the microphone, the speech-recognition model) are not here: they are the [configuration](#campaigns-and-configurations) of the campaign |
| `ollama`, `limits`, `max_turns`, `dialogue_timeout` | The LLM server, the orchestrator's parameters, the turn limit and the longest conversation, as in the experiments |
| `scenarios` | The pool: `id` (of `e4_es.yaml`), `title`, `card` (the lines the participant reads) and `detour` (`kind`, `text`) |
| `card_footer`, `detour_label`, `check_phrase` | The lines under every card, the label before the detour, and the phrase of the Speak check and of the pre-session check |

Every card, exactly as the participant reads it, is in [evaluation/real/cards](../real/cards/README.md):
`es/`, in Spanish, what the participant reads, and `en/`, an English translation (from
`real/cards_en.yaml`) for readers who do not read Spanish. Both are generated from
`sessions_es.yaml` by `evaluation/real/make_cards.py` (run it after changing a card or its
translation).

The participants see and hear Spanish: the cards, the footer, the label and the check phrase are
in Spanish. Each card follows the `user_goal` of its scenario, so that the scenario's ground truth
still scores the conversation (the card of the quiz says from which question to talk about
something else, the card of the recipe which step to skip). The names and addresses on the cards
are fictitious, and the footer of every card asks the participant to use them and never their own
data (name, address, phone), so that nothing that identifies the participant enters the
conversation.

A plan keeps the assignment of each participant (the order, the scenario of each trial); a trial
runs with what the sessions file says now of its condition and scenario (the model, the card). So
the cloud model can be pinned, or a card reworded, after the plans are made. Changing the
conditions or the pool changes the plans that would be made: an existing plan is kept, and
`plan --replace` takes the new one, only before the participant's first trial.

## Campaigns and configurations

What the participants hear is a condition of the study, the same for all of them. So it is named,
kept in the repository and frozen before the first participant hears it
([`real/campaigns.py`](../real/campaigns.py)).

**A configuration** (`real/configs/<name>.yaml`) is how the robot sounds and the hardware of its
speech:

| Key | What it is |
|---|---|
| `voice` | The Piper voice (`NAME`, or `NAME:SPEAKER` for a voice with several speakers); always named, so that the participants' voice never depends on a default ([TEST_PANEL.md](TEST_PANEL.md#voice-and-filler-delay) lists the voices offered) |
| `length_scale`, `noise_scale`, `noise_w_scale` | Speed, expressiveness and rhythm variation of the voice (empty: the voice's own) |
| `filler_after_s` | The seconds the robot may take to answer before a conversational filler ("Mmm…"); `0`: none; empty: the scenario's, else the system's. A number goes over the scenario's own `filler_after_s` |
| `input_device` | The microphone (a sounddevice name, not an index); `default` goes through the system's sound server, which converts the sample rate (PortAudio's own default can be a raw ALSA device that does not record at 16 kHz) |
| `whisper_model` | The speech-recognition model of faster-whisper (`small`, `medium`...) |
| `note`, `created`, `duplicated_from` | What it is for, and where it comes from |

`real/configs/default.yaml` is the starting configuration: what the sessions used before
campaigns existed (simple_hri's Spanish voice `es_ES-sharvard-medium` with its own settings, the
system's filler delay, `default`, `small`). A check or a test without a configuration uses the same
microphone and model.

**A campaign** (`real/campaigns/<name>.yaml`, created by the control panel or by
`campaigns.py new-campaign`) is a set of real sessions: its name, its sessions file (the study
design: conditions, scenarios, cards, which stay in it), one configuration, the hash of that
configuration's file taken when it was associated, and the dates. Its participants are stored in
their own folder, `~/.ros/interaction_real/<campaign>/<P07>/`, so that a pilot (a campaign like any
other) never mixes with the study. The `campaigns/` folder appears with the first campaign.

**The rules.** Every state is derived from the files; nothing else is kept:

- A configuration that no campaign references is a **draft**: it is edited, renamed and tried
  freely. Once a campaign references it, it is **frozen**: it is never edited in place (the panel
  and `campaigns.py set` refuse); **duplicate and edit** makes a new draft with everything copied
  (`<name>-2`, or a name of your choice).
- A campaign **with no stored trial** may switch to another configuration (the hash is taken
  again; the configuration it leaves becomes a draft again if no other campaign uses it). After
  its first stored trial (a trial replaced by a later run also counts), it cannot.
- **Integrity.** Every stored record keeps the name and the hash of its configuration
  (`configuration`, `configuration_hash`, and its values, `configuration_values`). If the
  configuration file of a campaign changed after it was associated (edited by hand), is missing, or
  a stored trial ran with another configuration, `real_sessions.py` refuses every trial and check of
  that campaign, saying why (its plans and cards can still be read). Restore the file (e.g. from
  git), or, before the first stored trial, switch the campaign to another configuration.

Freezing at association, and allowing a switch until the first stored trial, lets the pre-session
check find a bad voice or microphone without breaking the rule.

**How a trial uses it.** A trial, its test (`--try`), the checks and the rehearsal of a campaign
run with its configuration: the stack starts without its speech nodes (`speech:=none`) and
`real_sessions.py` starts simple_hri with the configuration's voice and settings, microphone and
speech-recognition model; its filler delay goes into the request. Barge-in is not part of a
configuration: it stays in the sessions file (and the same for every trial of a participant).

```sh
python3 real/campaigns.py list                                   # configurations (draft, frozen) and campaigns
python3 real/campaigns.py new-campaign pilot --config default    # sessions_es.yaml by default (--sessions-file)
python3 real/campaigns.py duplicate default --as woman           # a new draft, everything copied
python3 real/campaigns.py set woman voice=es_ES-sharvard-medium:1 length_scale=1.1   # a draft only
python3 real/campaigns.py switch pilot woman                     # only before the first stored trial
```

`list` also shows the participants stored before campaigns existed (directly in
`~/.ros/interaction_real/<P07>/`), if any: they are kept as they are, never moved nor deleted, and
belong to no campaign, so none of their trials runs again (a new campaign never takes a folder that
already holds participants).

## The counterbalancing

From the participant's number (P07 is 7), with n enabled conditions, m scenarios in the pool and
t trials per condition (`real/sessions_common.py`):

1. **The order of the conditions** is a row of a balanced Latin square (Williams design): every
   condition is in every position, and follows every other condition, equally often. With two
   conditions the rows are AB and BA; with three, six orders. Every group of consecutive
   participants that takes all the rows (2, or 6) is balanced.
2. **The scenarios.** Condition c (its place in the file, from 0) gets the scenarios
   (p − 1 + (c·t + j)·d) mod m, j = 0…t−1, with d = m // (n·t): over m consecutive
   participants, every condition goes through every scenario once, and a participant never gets
   the same scenario twice.
3. **The position of each scenario.** Within each group, the rows go in the order that, over a
   block of lcm(m, rows) participants (or twice that), also puts every scenario in every position
   equally often (found by a short search; when there is none, the rows go in their order,
   shifted by one every block).

With the sessions file (one condition, eight scenarios, four trials) every block of 8 participants
is fully balanced: each scenario four times, once in each position. `real_sessions.py balance N`
prints, for the first N participants, each scenario under each condition, each condition and each
scenario in each position, and which condition follows which. The first participants with the
sessions file:

| Participant | Trial 1 | Trial 2 | Trial 3 | Trial 4 |
|---|---|---|---|---|
| P01 | `sf_coffee` | `qz_geography` | `rp_interview` | `qa_museum` |
| P02 | `ex_bus` | `pr_tea` | `sf_pizza` | `qn_museum_satisfaction` |
| P03 | `qz_geography` | `rp_interview` | `qa_museum` | `sf_coffee` |
| P04 | `pr_tea` | `sf_pizza` | `qn_museum_satisfaction` | `ex_bus` |

```sh
cd src/interaction_system/evaluation
python3 real/real_sessions.py balance 16                      # how balanced the plans of P01-P16 are
python3 real/real_sessions.py --campaign pilot plan P07       # create (or show) the plan of P07 in pilot
python3 real/real_sessions.py --campaign pilot card P07 1     # the card of its first trial
```

## What you need

**For every session**

| What | Why |
|---|---|
| The computer that runs the stack, on mains power, with the workspace built | The stack, the speech recognition and the voice run on it |
| A microphone and a speaker (those of the robot, or of the computer next to the robot), set to the level checked in the checks | The speech capability (simple_hri) listens and speaks through them |
| The robot on the network, with its `ROS_DOMAIN_ID` (and DDS) | The checks and the trials use them |
| Only with barge-in: a camera facing the participant, publishing its image (`camera_topic` of `sessions_es.yaml`) | The lip activity detector (live and local, no images stored, no emotion) tells the robot to stop talking when the participant starts speaking |
| The LLM server (Ollama) answering, with the local model, and nothing else running on its GPU | A busy GPU slows the robot down: the runner refuses to start a trial when the server holds models of other work |
| Only for a condition with a cloud model (none in E4): the API key of the provider in the environment (`OPENAI_API_KEY`, `GEMINI_API_KEY`) of the terminal that starts the panel or the runner | A cloud condition |
| A screen in front of the participant for the task card (or the control panel on a second screen, `#card:<P>:<k>`) | The participant reads the card before each trial and may glance at it during the trial |
| A quiet room, the participant seated in front of the robot at a fixed distance | Speech recognition |
| Consent forms and the information sheet | [Ethics and consent](#ethics-and-consent) |
| The questionnaires of the study | At the end of the session (in E4: the SUS and two Godspeed scales, likeability and perceived intelligence) |

## Before each session: the checks

The checks show that everything works before a participant arrives. They are never stored. With
`--campaign` they run with the campaign's configuration (refused, like its trials, when the
configuration file changed); with `--config` with any configuration, drafts included; without
either, with the defaults. In the control panel they are the **Pre-session check** and the table
**Before the session: the other checks** of Real sessions, each with its result (OK, FAILED or
SKIPPED, with why) and its time. In a terminal: `real_sessions.py --campaign <C> check <what>`.

**The pre-session check** (`check presession`), on every day of sessions before the first
participant: the loudspeaker (the configuration's voice says `check_phrase`; you confirm you heard
it), the microphone (one transcription with the configuration's microphone and model; you confirm
the words) and the model of every enabled condition, in sequence, each with its verdict.
simple_hri is started for it with the configuration (it refuses to run while another simple_hri
serves the speech actions, whose voice and microphone are not known). With `--campaign`, the
result is kept in `~/.ros/interaction_real/<campaign>/presession.json`, with the date of its last
pass (every part OK, with the configuration as it is now). The trial that counts warns when it has
not passed that day: the runner prints a warning, and the control panel asks for a confirmation.

| # | Check | What it does | OK when |
|---|---|---|---|
| 1 | `speak` | Starts simple_hri (unless a stack already runs it) and says `check_phrase` (or the phrase of `--phrase`; the test panel has a field for it) through `/tts_action`. With `--voice` (a Piper voice, `NAME` or `NAME:SPEAKER`) or its settings (`--length-scale`, `--noise-scale`, `--noise-w-scale`) only the voice is started, with them, to compare voices and configurations ([TEST_PANEL.md](TEST_PANEL.md#voice-and-filler-delay)) | You heard the phrase (you confirm it) |
| 2 | `listen` | One `/stt_action` goal: someone says a sentence in Spanish in front of the microphone; shows the transcription, and the cleaned utterance (`/interaction/user_utterance`) if the intelligence runs | Something was transcribed and the operator confirms that the words and the language are right (Enter; `q`: wrong, FAILED) |
| 3 | `llm` | The model of every enabled condition answers a call of one token: the Ollama server answers and has the model (`/api/tags`); a cloud model only with `--allow-cloud` | Every model answers; shows the latency |
| 4 | `system` | The whole stack starts in every enabled condition, with its control and model, and serves `/dialog/execute_task` and the speech actions; then it is stopped | Every condition starts (in up to 180 s) |
| 5 | `perception` | Only with barge-in on (`--barge-in on`, the panel's checkbox): starts the lip activity detector of `interaction_perception` (emotion off) and watches `/user_speaking`, which turns true while someone talks in front of the camera | It is published and turns true; SKIPPED with barge-in off |
| 6 | `rehearsal` | A whole conversation with a member of the team, in a scenario and condition (`--scenario`, `--condition`; `sf_coffee` and `bounded` by default): a trial run as `--try`, before any participant exists | The conversation ran; read its transcript |
| | `presession` | Speak, listen and llm in sequence, with the configuration (above) | Every part OK |

```sh
python3 real/real_sessions.py --campaign pilot check presession   # the pre-session check of pilot
python3 real/real_sessions.py --campaign pilot check speak
python3 real/real_sessions.py check speak --config woman          # any configuration, drafts included
python3 real/real_sessions.py check speak --phrase "Hola, ¿me oyes bien?"
python3 real/real_sessions.py check speak --voice es_ES-sharvard-medium:1   # another voice, for this check only
python3 real/real_sessions.py check speak --noise-scale 0.9 --noise-w-scale 1.0   # the same voice, more expressive
python3 real/real_sessions.py check llm --allow-cloud            # with a cloud condition enabled (none in E4): a paid call of one token
python3 real/real_sessions.py check rehearsal --scenario qz_geography --condition llm_led --allow-cloud
```

The runner of the checks needs the prepared terminal of a trial ([Running a trial](#running-a-trial)).
`--keep` keeps the logs of a check (a failed check keeps them anyway and says where).

## Technical check of the speech failures

Once, in the technical test with the microphone and the robot (step 2 of the procedure of E4,
[EXPERIMENTS.md](EXPERIMENTS.md#e4-validation-with-real-users)), not before every session: what the
system does when the speech capability fails ([endings](../../docs/behaviors/endings.md#eventualities),
[SPEECH.md](../../docs/SPEECH.md#how-the-trees-use-it)). It is a check of the setup, not an
experiment and not a measure of the study: the simulation serves the speech actions itself, so no
simulated experiment can provoke these failures, and they are not provoked with participants
(decided 2026-10-30, item O13 of the mechanism coverage audit). Each case runs as a rehearsal
(check 6) with a member of the team; the transcript and the logs of the trial are read.

| Case | How | Expected |
|---|---|---|
| Speech-to-text down | During a rehearsal, stop the node that serves `/stt_action` | Every wait for the user ends empty: the robot says it is waiting, then ends the conversation as `user_silent` with its closing line after `max_silence` (90 s) |
| Text-to-speech down | During a rehearsal, stop the node that serves `/tts_action` | Each utterance is skipped with a warning in the log; the conversation goes on (the transcript keeps the robot's lines) |
| An utterance longer than `tts_timeout` | A rehearsal with `tts_timeout` lowered for the check (a few seconds) | The utterance is cut with a warning in the log; the conversation goes on |

Restart the stack before the next trial (`check system`). A result other than the expected one is
a defect, fixed before the sessions.

## Barge-in

Optional, decided after the pilot: with barge-in on, the robot stops talking as soon as the
participant starts speaking, and what the participant says is the next turn. It comes from the
lip activity detector of `interaction_perception` on a camera facing the participant (live and
local: no image is stored or sent anywhere; the emotion detector is never started, and the state
of the user is never used in the sessions). With barge-in off no camera is used and the robot
finishes its utterance before listening. Set it with `barge_in` in `sessions_es.yaml` or
`real_sessions.py ... --barge-in on|off`. It is part of the system, the same in every condition,
and the same for every trial of a participant (the runner refuses a trial with the other setting);
each stored record says how it ran (`barge_in`).

**Status: future work, off.** Barge-in cannot run yet with the pixi environment: nothing publishes
the camera image on a computer (the system starts no camera driver: it expects the robot's), and
the lip activity detector's dependencies (MediaPipe, OpenCV, `cv_bridge`) are not in the
environment. So `barge_in` is `false` and the checkboxes of the control panel and the test panel
are disabled (decided 2026-10-30, to avoid a setting that does nothing). The steps to enable it:

1. **The perception's dependencies.** An optional pixi environment (a `perception` feature, so
   that the default environment does not grow by several GB) with `ros-<distro>-cv-bridge` and a
   camera driver (`ros-<distro>-v4l2-camera` or `ros-<distro>-usb-cam`) from RoboStack, and
   `opencv-python` and `mediapipe` from PyPI at the versions of
   `interaction_perception/interaction_perception/requirements.txt`. Check first that MediaPipe
   has wheels for the Python of each distribution (Jazzy: 3.12; Lyrical: its own).
2. **The camera.** On the robot, set `camera_topic` of `sessions_es.yaml` to the topic of its
   camera (the detector subscribes to it; it is a setting, nothing detects it). On a computer, add
   a `camera_command` to `sessions_es.yaml` (e.g. `ros2 run v4l2_camera v4l2_camera_node` with its
   image remapped to `camera_topic`) that `real_sessions.py` starts and stops with the perception;
   empty on the robot.
3. **The face landmarker model.** It downloads to `/tmp` on first use (lost on reboot); keep it in
   a persistent folder, as upstream DialoStack does (commit 953c62d).
4. **The panels.** Enable the checkboxes again and add a "Check: perception" button to the test
   panel (the control panel has check 5).
5. **Validation before the pilot.** Check 5 (`/user_speaking` turns true when someone talks in
   front of the camera and stays false otherwise); the time from the start of the speech to the
   cut; cuts with nobody speaking (other people in view, background noise). Then the pilot decides
   whether E4 uses it; if these steps are not done before the pilot, E4 runs without barge-in.

## The procedure of a session

1. **Before the participant arrives:** set the room (the robot, the microphone and speaker, the
   card screen); start the LLM server and, if needed, the SSH tunnel to it; start the control
   panel (or prepare a terminal). Choose the campaign. Set the robot's network on the page. Run the
   pre-session check (every day of sessions), the checks 4 and 5, and 6 (a rehearsal) at least on
   the first day and after any change.
2. **Welcome:** the information sheet and the consent form, signed before anything is recorded.
   Give the participant their code (P01, P02...) in order of arrival: the code decides the plan.
   The list that links codes and names is kept apart from the data, as the data protection plan
   says.
3. **The plan:** type the code in Participant and press **Create / open the plan**. The plan lists
   the trials in the order to run them.
4. **For each trial, in order:**
   1. Show the card (**Card**, full screen) and let the participant read it; answer questions
      about the task, not about the robot. Remind them to use the invented data of the card and
      never their own (name, address, phone). Leave the card in view if the participant wants it.
   2. Press **Run the trial** (green). The stack starts in the trial's condition (about half a
      minute); the output says READY when it can start.
   3. When the participant is ready, press **Start the conversation** (blue). The robot starts
      the conversation. Do not intervene: the conversation ends when the robot ends it (or at the
      turn limit, or after `dialogue_timeout`).
   4. END says how it ended. The record is stored and the stack stopped.
5. **At the end:** the questionnaires, thanks, and check in the panel that every trial of the
   participant is stored (Participants: 4/4).

If something goes wrong in a trial (the participant was interrupted, a device failed), press
**Stop**: the conversation is cancelled, the stack stopped, nothing of it stored, and `trials.csv`
notes it. Whether that trial is run again (the participant already knows its task) is the
researchers' decision, written in the session notes.

## Running a trial

**From the control panel:** [CONTROL_PANEL.md](CONTROL_PANEL.md#real-sessions).

**In a terminal**, prepared as for any run of the system, from the workspace (for ROS 2
Lyrical: `pixi shell -e lyrical` and `install_lyrical/setup.bash`):

```sh
pixi shell
source install/setup.bash
export ROS_DOMAIN_ID=<the robot's>        # and RMW_IMPLEMENTATION if the robot needs it
cd src/interaction_system/evaluation
python3 real/real_sessions.py --campaign pilot plan P07                    # the plan (created the first time)
python3 real/real_sessions.py --campaign pilot trial P07 1 --try           # a test: nothing is stored
python3 real/real_sessions.py --campaign pilot trial P07 1                 # the trial that counts
python3 real/real_sessions.py --campaign pilot trial P07 2 --allow-cloud   # a trial of a cloud condition
```

A participant belongs to a campaign: `plan` and `trial` need `--campaign` (its sessions file, its
configuration and the folder of its participants; `--sessions` is then ignored). Options of `trial`:

- `--try`: the whole trial, into a temporary folder deleted at the end; nothing is stored and
  `trials.csv` is not touched. `--keep` keeps the folder and says where.
- `--allow-cloud`: needed by a trial whose condition has a cloud model.
- `--force`: start even if the Ollama server holds models of other work (never during a study).
- `--barge-in on|off`: barge-in by lip activity (default: `barge_in` of the sessions file); the
  same for every stored trial of a participant.
- `--out`: the folder of the sessions (`~/.ros/interaction_real` by default; a campaign's
  participants are in `<out>/<campaign>/`).

Running a stored trial again replaces it; the old record and its logs are moved to
`<campaign>/<P>/replaced/<time>/`.

`try-scenario` (any scenario, a test that stores nothing; the test panel runs it,
[TEST_PANEL.md](TEST_PANEL.md)) takes `--model`, `--control`, `--allow-cloud`, `--force`, `--keep`
and `--barge-in` as above, and options of a test only, never of a trial: `--config` (any
configuration, drafts included; the options below go over it), `--voice` (another
Piper voice, `NAME` or `NAME:SPEAKER`) and its settings `--length-scale` (speed),
`--noise-scale` (expressiveness) and `--noise-w-scale` (rhythm variation), with which the stack
starts with `speech:=none` and simple_hri is started with that voice and settings; and
`--filler-after` (`filler_after_s` of the request: the seconds before a conversational filler;
`0`: none). Without them the defaults apply (those of the starting configuration;
[TEST_PANEL.md](TEST_PANEL.md#voice-and-filler-delay)).

## During a trial

The runner prints, for each trial:

1. **TRIAL** with the participant, the scenario, the condition (control and model) and the card,
   while the stack starts: `main.launch.py` (bounded) or `llm_led.launch.py` (LLM-led), with
   `speech:=none language:=es` and a parameter file generated from `app_params.yaml` with the
   condition's model, and simple_hri started next to it with the campaign's configuration (printed
   with its name and hash).
2. **READY** when the orchestrator and simple_hri's actions are up; then it waits: Enter starts
   the conversation, `q` quits without running it (nothing stored).
3. **CONVERSATION**: the request of the scenario is sent (the same goal as in the simulated
   experiments) and the conversation is printed as it goes: `ROBOT:` and `USER:` lines (the
   user's turns as the system took them), and the barge-ins.
4. **END** with how it ended (success or not, turns, duration), with a bell. The stack is stopped
   and the record stored.

Ctrl-C (the panel's Stop) cancels the conversation, stops the stack and stores nothing.

## What is stored

In `~/.ros/interaction_real/<campaign>/` (or `<out>/<campaign>/`; the paths below are inside it):

| File | Content |
|---|---|
| `presession.json` | The last pre-session check of the campaign and its last pass (time, configuration and hash, the verdict of each part) |
| `<P>/plan.yaml` | The plan: the order of the conditions (and its row of the Latin square), each trial's scenario and condition, the sessions file and its hash, when it was created |
| `<P>/trial_<k>_<scenario>_<condition>.json` | The record of the trial (below) |
| `<P>/trial_<k>_<scenario>_<condition>/` | The logs of the stack (`system.log`), its parameter file, and the intelligence's transcript log |
| `trials.csv` | One row per trial run or cancelled: `time`, `participant`, `trial`, `scenario`, `condition`, `model`, `status`, `success`, `outcome`, `turns`, `duration_s`, `stored`, `barge_in`, `configuration`, `notes` |

The record has the keys of the simulated experiments' (`run_experiment.py`): `scenario`,
`strategy`, `condition`, `model`, `status`, `success`, `outcome` (how it ended:
[ROS_INTERFACES.md](../../docs/ROS_INTERFACES.md#dialogtask-feedback-on-every-robot-utterance-and-result)),
`failure_reason`, `data` (the final frame
or results), `total_turns`, `duration_s`, `expect` (the ground truth), `goal`, `transcript`
(`role` robot or user, `text`, `t` in seconds from the request), `feedback`, `skills` (the skill
trace: every question to the intelligence), `barge_ins`, `user_goal`, `language`; `persona` is
`real` and `rep` the trial number. It adds `participant`, `trial`, `order`, `control`, `barge_in`
(whether barge-in was on), `campaign`, `configuration`, `configuration_hash` and
`configuration_values` (how the robot sounded), `provider`, `model_version` (the digest
of an Ollama model; a cloud model is identified by its dated name, since the gateway does not
report the version that answered), `heard` (every transcription of the speech recognition, also
those not taken as turns), `title`, `card`, `detour`, `launch`, `sessions_file`, `sessions_hash`
and `started`.

No audio is recorded by these scripts.

**The participant's code stays out of the system.** The stack of a trial runs in a temporary
folder whose name says nothing of the participant (its parameter file, its logs, the transcript
log of the intelligence), the request sent to the robot is the scenario's, and the dialogue is
identified by the random id of the request. So the code never reaches the interaction system,
its parameters or what it sends to an LLM; only the stored record (on this computer) carries it.
When the trial ends, that folder is moved next to the record.

## Scoring the sessions

The scorers of the simulated experiments read the folder of a campaign as they read the results
of an experiment (one campaign at a time: a pilot is never scored with the study). `score_runs.py` runs on this computer, with no model, and can run after every
session. The judge runs once, at the end, on all the sessions, and only after **the review of the
transcripts**: a member of the team reads each stored trial and replaces any real personal data
the participant said by mistake (a name, an address, a phone that is not on the card), as the
ethics protocol says. The replacement runs over the whole record and the logs of the trial, what
was replaced is not kept, and the record is marked as reviewed (by whom, when); `judge_runs.py`
stops if any record of a real session is not reviewed, so nothing a participant said reaches the
judge or the annotators before a person has read it. The judge is the one chosen in E0b
([JUDGE_VALIDATION.md](JUDGE_VALIDATION.md)), the same as for the simulated twin.

```sh
python3 analysis/score_runs.py ~/.ros/interaction_real/<campaign>          # scores.csv and a summary per model and condition
python3 real/real_sessions.py --campaign <campaign> review P07 --by <name>  # each transcript, then "real text => replacement" lines
python3 analysis/judge_runs.py ~/.ros/interaction_real/<campaign> --provider <p> --model <judge> --tag <tag> [--allow-cloud]
python3 analysis/judge_runs.py ~/.ros/interaction_real/<campaign> --tag <tag> --summary
```

The instructions for the person who reviews the transcripts:
[human_review/e4_transcript_review.md](human_review/e4_transcript_review.md).

`<p>`, `<judge>` and `<tag>` are those of the judge chosen in E0b, as for the simulated twin. Both
scripts write their files in that folder (`scores.csv`, `judgments_<tag>.jsonl`); what each
measures is in [METRICS.md](METRICS.md). `review --k <k>` reviews one trial, and `--again` reviews
trials already reviewed.

## Cloud models and the participants' data

E4 has no condition with a cloud model: during its conversations nothing the participants say
leaves the laboratory's computers. For other studies that enable one: in a condition with a cloud
model, what the participant says is transcribed on this computer, and only that text goes to the
provider, with the conversation so far (the audio never leaves the computer). The runner refuses
such a trial without `--allow-cloud`, and the control panel asks the operator to confirm it once
per participant. The consent form and the information sheet must say
that the transcribed text of those trials is sent to an external provider, which one, and what the
provider does with it. The model is pinned to a dated version in the sessions file, and the
records store it.

## Ethics and consent

- The study needs the approval of the research ethics committee of the institution before the
  first participant, and the sessions follow the approved protocol.
- Every participant reads the information sheet and signs the consent form before anything is
  recorded. The form covers what is recorded (the transcripts and the logs, no audio by these
  scripts), the questionnaires, the use of the data in publications, and, if the judge is a cloud
  model, that the reviewed transcripts (without personal data) are sent to its provider after the
  sessions.
- Participants can stop at any time, without giving a reason; their data are then handled as the
  consent form says.
- The data are pseudonymous: the records carry only the participant's code. The list that links
  codes and identities is kept apart, with restricted access, and the data are stored and deleted
  as the data protection plan says.
- The cards use fictitious names and addresses, and ask the participants to use them and never
  their own data; the participant's code never reaches the interaction system nor a cloud
  provider ([What is stored](#what-is-stored)).

## Troubleshooting

| Symptom | What to do |
|---|---|
| `... did not start in 180 s` | Read `system.log` in the trial's folder (the runner says where). The first start of simple_hri downloads its models |
| `The Ollama server ... holds models of other work` | Something else runs on the LLM server: wait until it ends. Never use `--force` with a participant |
| `... is not on the Ollama server` | Pull the model on the server (`ollama pull <model>`) |
| `Write the dated version of the model` | The cloud model is still `<pin>` in `sessions_es.yaml` |
| The robot does not hear the participant | Run the Listen check; check the microphone level and the distance |
| The Speak check plays nothing | Check the output device and its volume; `play_sound` of the TTS node |
| `Campaign ... refuses its trials and checks: ...` | Its configuration file changed after it was associated (or is missing, or a stored trial ran with another): restore the file (e.g. from git); before the first stored trial, the campaign can also switch to another configuration ([Campaigns and configurations](#campaigns-and-configurations)) |
| `A participant belongs to a campaign` | Give `--campaign <name>` (`real/campaigns.py list`), or create one |
| `The sessions file now gives this participant another plan` | The conditions or the pool changed after the plan was made. Keep the plan, or `plan --replace` before the first trial |
