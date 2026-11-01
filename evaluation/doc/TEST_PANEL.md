# The Tests tab: try a scenario as the user

The **Tests** tab of the [control panel](CONTROL_PANEL.md) (its code: `evaluation/scripts/test_panel.py`)
tries any scenario of the experiments by voice, with a person as the user, through the speech
capability (simple_hri: the microphone and speaker of this computer, or those of the robot).
Nothing is stored, nothing is changed, and no measure comes from it: it is not part of the
evaluation. The experiments and the sessions with real participants run from the other tabs of
the same page.

## Before you start

The same as for the control panel ([CONTROL_PANEL.md](CONTROL_PANEL.md#before-you-start)): the
workspace built with the evaluation packages, the LLM server answering with the model of the
condition, a microphone and a speaker, and, for a cloud model, its API key in the environment of
the terminal that starts the page.

## Starting it

Start the control panel ([CONTROL_PANEL.md](CONTROL_PANEL.md#starting-it)) and open its Tests tab:

```sh
python3 evaluation/scripts/control_panel.py      # then open http://localhost:8095/#tests
```

Until 2026-10-30 the test panel was a page of its own (`scripts/test_panel.py`, port 8097). It is
now a tab of the control panel, so that a single server runs one job at a time (two servers would
each run a job, sharing the GPU, the microphone and the ROS domains). Running
`scripts/test_panel.py` only says where it went.

## Trying a scenario

1. **Ready example** (optional): one scenario per strategy and one per mechanism, to try each by
   voice ([below](#ready-examples)). Choosing one fills the scenario file, the strategy, the
   scenario and the condition, and says which mechanisms the scenario provokes. **Example
   language**: Spanish, or the English twin where there is one (`e3.yaml` for the scenarios of
   `e3_es.yaml`; the E4 scenarios are only in Spanish).
2. **Scenario file**: any file of `experiments/scenarios/` (`dialogues.yaml`, `e3.yaml` and
   `e0a_dev.yaml` in English; `dialogues_es.yaml`, `e3_es.yaml`, `e4_es.yaml` and
   `e0a_dev_es.yaml` in Spanish; `e4_es.yaml` is selected first). The language of the
   conversation follows the file.
3. **Strategy** and **Scenario** (e.g. `pr_tea`, making a tea). The page shows the
   [tester card](#the-tester-card) of the scenario: what the person playing the user must do.
4. **Condition**: `bounded` (the method) or `llm_led` (the LLM leads the conversation). **System
   model**: a list of the models on the Ollama server of `real/sessions_es.yaml` (its `ollama`
   host and port, asked with `/api/tags`; ↻ asks again, e.g. after a pull) and the cloud
   models of its conditions (`provider/model`, not those still written `<pin>`); the model of the
   chosen condition is selected when the server has it. If the server does not answer, the page
   says so and the list is empty. A cloud model asks for confirmation first: what the user says,
   transcribed on this computer (never the audio), goes to its provider.
5. **ROS_DOMAIN_ID** (0 by default) and **DDS** (`RMW_IMPLEMENTATION`): those of the robot, or of
   this computer. The first DDS option, **inherited (...)**, leaves the `RMW_IMPLEMENTATION` of the
   terminal that started the panel (shown in brackets; "not set" means the distribution's
   default); the others set it for the job. **Barge-in by lip activity**: disabled, future work (the environment has no
   camera driver nor the perception's dependencies;
   [REAL_SESSIONS.md](REAL_SESSIONS.md#barge-in) lists the steps to enable it).
6. **How the robot sounds**, for this test only ([below](#voice-and-filler-delay)): a
   **Configuration** of the real sessions (`real/configs/`, any, drafts included: it fills the
   fields and fixes them, and the checks and the test pass `--config`, with its microphone and
   speech-recognition model too), or ad-hoc values: **Voice**, **Voice configuration** (speed,
   expressiveness, rhythm variation) and **Filler after (s)**: another Piper voice (default:
   simple_hri's voice of the language), how it speaks (empty: the voice's own values) and the
   seconds the robot may take to answer before it says a filler ("Mmm…"; empty: the default).
7. Optionally, the checks: **speak** (the chosen voice says the phrase of the field above the
   buttons; **What the robot says** fills it with a question with options in the voice's
   language, to hear its intonation (the default), or the loudspeaker test greeting of the real
   sessions, `check_phrase` of `real/sessions_es.yaml`; writing in the box switches it to "my own
   text"), **listen** (one
   transcription, which you confirm) and
   **LLM** (the model of every condition of the sessions file answers a call of one token).
8. **Start the test**. The stack starts as for a trial with nothing stored
   (`real_sessions.py try-scenario`), with the limits of `real/sessions_es.yaml` and the
   configuration or the values chosen.
   When it is ready, the blue button starts the conversation; `q` quits without running it. The
   conversation appears under **Running now** as it goes, and its ending at the end. **Stop**
   cancels it and stops the stack.

The same test from a terminal:

```sh
cd src/interaction_system/evaluation
python3 real/real_sessions.py try-scenario pr_tea --scenarios experiments/scenarios/e4_es.yaml \
    --control bounded [--model <model>] [--barge-in on|off] [--allow-cloud] [--keep] \
    [--config <configuration>] [--voice <voice>] [--filler-after <s>]
```

## Voice and filler delay

They apply to one test or check only: nothing is stored and nothing is changed. The voice of the
real sessions is not chosen here: it is the configuration of their campaign, frozen once the
campaign uses it ([REAL_SESSIONS.md](REAL_SESSIONS.md#campaigns-and-configurations)); a
configuration is edited, and tried with the same fields and Check: speak, in the **Configurations**
tab, and any configuration can be tried here in a whole conversation (its microphone and
speech-recognition model too). Without a configuration, the microphone is `default` and the model
`small`, those of the starting configuration.

**Voice.** The [Piper](https://huggingface.co/rhasspy/piper-voices) voices to compare
(`VOICES` of `real/campaigns.py`, shared with the Configurations tab). **Voice of** filters the list to a man's or a woman's voice;
each voice shows which it is (from its card, checked on the downloaded ones by their median pitch):

| Language | A man | A woman |
|---|---|---|
| Spanish | `es_ES-sharvard-medium` speaker 0 (the default), `es_ES-davefx-medium`, `es_ES-carlfm-x_low` (16 kHz), `es_MX-ald-medium` | `es_ES-sharvard-medium` speaker 1, `es_MX-claude-high`, `es_AR-daniela-high` |
| English | `en_US-ryan-high`, `en_US-hfc_male-medium`, `en_GB-northern_english_male-medium` | `en_US-lessac-medium` (the default), `en_US-lessac-high`, `en_US-amy-medium`, `en_US-hfc_female-medium`, `en_GB-alba-medium`, `en_GB-cori-high` |

simple_hri looks a voice up in its models folder (`SIMPLE_HRI_MODELS_DIR`, or `models/` of the
folder it runs from: the workspace, for the panels) and downloads a missing one from the Piper
catalog the first time (63-120 MB each; the first check with it takes longer).

- To **compare voices and configurations** without any model: choose a voice (and a
  configuration, below) and press **Check: speak**. Only the voice starts (no speech recognition,
  no microphone, no LLM), says the phrase, and stops when you confirm. The default phrase is a question with several options ("¿Quieres el café con leche
  entera, desnatada, de avena o de soja?", or its English version for an English voice), where the
  intonation of a question shows. In a terminal:
  `real_sessions.py check speak --voice es_ES-davefx-medium --noise-scale 0.9 --phrase "..."`. With a
  voice or a setting, the check refuses to run while another simple_hri serves `/tts_action` (its
  voice cannot be changed).
- In a **test**, the voice and its settings are passed to `try-scenario` (`--voice`,
  `--length-scale`, `--noise-scale`, `--noise-w-scale`); the voice must be of the language of the
  scenario file. The stack then starts without its speech nodes (`speech:=none`) and
  `real_sessions.py` starts simple_hri itself with them (its logs, `speech_0.log` and
  `speech_1.log`, are in the test's temporary folder with the stack's).
- `--voice` takes `NAME` (a voice of the Piper catalog) or `NAME:SPEAKER` for a voice with several
  speakers, passed to simple_hri's voice node as its `voice` and `speaker` parameters.

**Voice configuration.** Three settings of simple_hri's Piper voice node; an empty field leaves
the voice's own value (that of its `.onnx.json`; `es_ES-sharvard-medium`: expressiveness 0.667,
rhythm 0.8; speed 1). Out of its range a value is refused.

| Field | Parameter (`real_sessions.py` option) | What it does | Usual | Allowed |
|---|---|---|---|---|
| Speed | `length_scale` (`--length-scale`) | < 1 faster, > 1 slower | 0.8-1.3 | 0.5-2.0 |
| Expressiveness | `noise_scale` (`--noise-scale`) | The variation of the voice: higher is livelier, lower is flatter | 0.3-1.0 | 0-1.5 |
| Rhythm variation | `noise_w_scale` (`--noise-w-scale`) | The variation of the sound durations: higher is less regular | 0.3-1.2 | 0-1.5 |

The **Voice configuration** list fills the three fields (`PRESETS` of `real/campaigns.py`):
voice default (all empty), more expressive (0.9, 1.0), slower and expressive (speed 1.15, 0.9,
1.0), very expressive (1.0, 1.2) and steadier (0.4, 0.5); editing a field turns it to "custom".
The values are not stored: once they sound right, they become a configuration in the
Configurations tab (a draft until a campaign uses it).

**Filler after (s).** The robot says a fixed filler ("Mmm…", "Un momento…") when its answer takes
longer than `filler_after_s` ([conversational fillers](../../docs/behaviors/fillers.md)); with a
warm, fast model it may never be heard. The field is passed to `try-scenario --filler-after`, which
sets `filler_after_s` in the request of the test (over the scenario's own). Empty: the scenario's,
else the orchestrator's `filler_after_s`; `0`: no fillers; e.g. `0.5` to hear them.

## Ready examples

`scripts/test_panel_examples.yaml` lists them; they are scenarios of the experiments (none is
written for the panel), from the Spanish files first, and run in the `bounded` condition unless
the list says otherwise.

- **One per strategy**: the scenario of the study with real participants (`e4_es.yaml`) of each
  implemented strategy: `sf_coffee` (slot filling), `qz_geography` (quiz), `pr_tea` (procedure),
  `ex_bus` (explanation, with teach-back), `qa_museum` (grounded question answering),
  `qn_museum_satisfaction` (questionnaire) and `rp_interview` (role play). Each also provokes a
  mechanism and a detour, as in the sessions. topic_chat and the urgency escalation are not
  implemented and have no example.
- **One per mechanism**: every mechanism that a scenario of `e3_es.yaml` (E3: one mechanism per
  scenario, provoked by events at fixed turns) or `e4_es.yaml` provokes on purpose, with a short
  id (C cancellation, S silence, O endings, N breakdown, SF slot filling, Q quiz, P procedure, EX
  explanation, R role play, G grounded question answering, QN questionnaire, SYS staying in the
  task). When several scenarios provoke the same mechanism, the example is the shortest one (fewest
  events and turns), or the one whose check measures it (the teach-back repair, EX2, comes from
  the E3 scenario with a planted wrong point, not from `ex_bus`). The exact stop command (C1) is
  a Spanish one ("Para."): each language has its exact commands (its language file,
  [ADDING_A_LANGUAGE.md](../../docs/ADDING_A_LANGUAGE.md)); until 2026-10-30 the list was English
  only and C1 was the only English example. A few mechanisms that no E3 or E4 scenario provokes are planned by the role of
  a scenario of `dialogues_es.yaml` (its `user_goal`, with no events): a step stuck, a step
  skipped, a step repeated, going back a step, declining the teach-back, giving up an explanation
  (ending failed), a hint, ending a role play, the scene's own end, a questionnaire item declined
  or repeated. Their note says so: the person does it when the role says, not at a fixed turn.

The list's `provokes` maps every scenario of `e3_es.yaml` and `e4_es.yaml` (and the scenarios of
`dialogues_es.yaml` used as examples) to the mechanisms it provokes; `scripts/tests/test_test_panel.py`
checks that every one of them has an example, that every example is a scenario of an existing
file, that every implemented strategy has one and that every key of `expect.mechanisms` has its
words on the card (`python3 -m pytest scripts/tests/test_test_panel.py`, from `evaluation/`,
without ROS). Mechanisms that no scenario provokes on purpose (for example a question during a
step, or the score of a questionnaire) have no example: they can still be tried with any scenario
of the strategy by doing them yourself.

## The tester card

For the chosen scenario, in the language of the scenario file (the page itself is in English):

- **Your role**: the scenario's `user_goal`, what the simulated users are asked to do.
- **Turn by turn**: one line per event of the scenario (`events`), at its turn (a scenario
  without events says that the role provokes what is tried): what to say
  (`text`), the request to stop (`cancel`), staying silent until the robot speaks again
  (`silence`), the detour and its kind (`detour`: a question outside the task, something the robot
  must decline, a value the task does not allow) and the barge-in (`barge_in`, off in this panel).
  The turns are counted as the simulated user's: each time the robot finishes a line of the
  dialogue it is the user's turn, so turn 1 is the reply to the robot's first line; a silence uses
  up its turn (the robot's prompt after it starts the next one), a quiet wait, in which the robot
  says nothing, does not, and the robot's fillers ("Hmm…" while it thinks) are not lines. On the
  other turns the person plays the role. For the E4 scenarios the card adds that the participants
  choose the moment themselves ("at some point"); the panel uses the turn of the simulated twin.
- **Expected ending**: `expect.ending` (else `expect.outcome`), in words, and the slots left
  unanswered (`expect.unanswered`).
- **What should happen**: `expect.mechanisms` in plain words: the shared mechanisms
  (`analysis/mechanisms.py`: the confirmation questions, the silence prompts, the quiet waits, the
  waits inside a timed step, the planned actions, the closing line) and the strategies' own checks
  (`analysis/strategy_checks.py`: a number out of range rejected, the slots left unanswered, the
  key points listed by the LLM, the teach-back marks and repair, a contradicted step not done, a
  repeat word for word then in other words, the exchanges of a scene); then the scenario's
  `mechanism` and the mechanisms the list of examples says it provokes.
- **Timing**: the limits of the test (`limits` and `max_turns` of `real/sessions_es.yaml`, or the
  scenario's own `max_turns`). The experiments of E3 and E4 plan their silence events on the same
  limits (the tests check it), so every silence example behaves as in the experiments. When other
  experiments with the same scenario file wait longer before the robot speaks (E1, E0a and E0b),
  the card says so: a pause that is not a silence there may be one here.

## Shared with the control panel

The tab runs its jobs as the rest of the control panel: one job at a time across all its tabs,
none while a campaign started from the page runs, and the output of each job in
`~/.ros/interaction_panel/`. Its ROS_DOMAIN_ID and DDS are its own fields (the Real sessions tab
has its own). Like a trial, a test refuses to start when the Ollama server holds
models of other work (the page never passes `--force`).
