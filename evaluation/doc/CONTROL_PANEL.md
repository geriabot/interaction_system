# The control panel: run and follow the evaluation from a web page

`evaluation/scripts/control_panel.py` serves a web page from which the evaluation runs without a
terminal. From it you can:

- run the whole simulated evaluation, step after step in the order of
  [REPRODUCING.md](REPRODUCING.md#the-order), detached from the page: it stops where a person must
  act, asks for your permission before every step with paid cloud calls, warns when the method
  changed and gives the run health of every step ([Whole evaluation](#whole-evaluation));
- run an experiment on a hardware profile (a *campaign*): see what it would run and the paid
  cloud calls it would make, check that the servers are free, start, pause and resume it, and
  follow the progress of every experiment;
- run one dialogue of an experiment with its simulated user, without storing anything, and the
  quick checks of the system without hardware;
- run the sessions with real participants, in campaigns of real sessions: the campaign and its
  configuration, the pre-session check and the other checks, the plan of each participant, the
  task card full screen for the participant, a test that stores nothing and the trial that counts;
- edit and try the configurations of the real sessions (how the robot sounds: drafts, frozen once
  a campaign uses them);
- try any scenario yourself, as the user, by voice, with nothing stored (Tests).

The page has one tab for each: **Campaign**, **Simulation test**, **Real sessions**,
**Configurations** and **Tests**. It opens the last tab used; a link can also open one, e.g.
<http://localhost:8095/#real> or `#tests`. Under every tab but Campaign, **Running now** shows the
job under way.

The page only calls the scripts of the repository (`scripts/whole_evaluation.py`, `scripts/run_parallel.py`,
`scripts/run_experiment.py`, `scripts/sim_checks.py`, `real/real_sessions.py`), with the same
options as in a terminal. Everything it does can also be done by hand:
[REPRODUCING.md](REPRODUCING.md) and [REAL_SESSIONS.md](REAL_SESSIONS.md). The Tests tab is described in
[TEST_PANEL.md](TEST_PANEL.md) (until 2026-10-30 it was a page of its own, `scripts/test_panel.py`
on port 8097; it is now part of this one, so that one server runs one job at a time: two servers
would each run a job, sharing the GPU, the microphone and the ROS domains).

## Before you start

The panel needs what the scripts need ([REPRODUCING.md](REPRODUCING.md#before-anything)):

1. The workspace built (`pixi run build`, which includes the evaluation packages). The panel runs
   every script inside pixi with the workspace sourced: `install/` (ROS 2 Jazzy, the default) or,
   with `--env lyrical`, `install_lyrical/`.
2. The LLM servers of the hardware profiles answering (Ollama on this computer, or on a GPU
   server reached through an SSH tunnel), and, for the cloud models, their API keys in the
   environment of the terminal that starts the panel (`OPENAI_API_KEY`, `GEMINI_API_KEY`): the
   scripts launched by the panel inherit it.
3. For the real sessions: the microphone and speaker of the speech capability (simple_hri) on
   this computer, and the robot on the network ([REAL_SESSIONS.md](REAL_SESSIONS.md#what-you-need)).
4. Python 3 with PyYAML to run the panel itself.

## Starting it

From the repository (`src/interaction_system`):

```sh
python3 evaluation/scripts/control_panel.py                  # then open http://localhost:8095
python3 evaluation/scripts/control_panel.py --port 8096      # another port
python3 evaluation/scripts/control_panel.py --env lyrical    # ROS 2 Lyrical (install_lyrical/)
```

Options: `--sim-domain` is the `ROS_DOMAIN_ID` of the simulation tests and quick checks (91 by
default; never one of the lanes'), `--real-out` the folder of the real sessions
(`~/.ros/interaction_real` by default; each campaign's participants in a folder of its own inside),
and `--whole-root` the folder of the whole evaluation (`evaluation/results/whole_evaluation` by
default).

The page listens on `127.0.0.1` only. To use it from another computer, open an SSH tunnel to the
computer that runs it and open `http://localhost:8095` there:

```sh
ssh -N -L 8095:localhost:8095 <user>@<computer-that-runs-the-panel>
```

The panel runs one job at a time (a simulation test, a check or a trial): they share the GPU, the
LLM servers and the ROS domains. It starts none while a campaign started from the page (or the
whole evaluation) runs. An
experiment run from a terminal is not controlled by the page, but the page notices it (a dialogue
saved in the last 15 minutes) and says so. The output of each job is kept in
`~/.ros/interaction_panel/`, with the last result of each check (`checks.json`).

## The colours of the buttons

Each tab shows the meaning of its own buttons:

| Colour | Campaign | Simulation test, Real sessions |
|---|---|---|
| White | A look or a check: nothing is stored (dry run, servers) | A test or a check: nothing is stored |
| Blue | Starts or resumes the campaign | Acts on the job under way (answer the runner, show the card); Tests: starts the test; Configurations: saves a draft |
| Green | | Real sessions: the trial counts, stored with the campaign |
| Red | Pauses the campaign | Stops the job |

## Campaign

Two modes, chosen at the top of the tab (the page remembers the last one): **Whole evaluation**,
every step of the simulated evaluation in order, and **One experiment**, an experiment on a
hardware profile. Only one runs at a time, and nothing else (no test, check or trial) runs while
either does. The table of the experiments, below both, shows the progress of every experiment
whichever mode runs it.

### Whole evaluation

The whole simulated evaluation, in the order of [REPRODUCING.md](REPRODUCING.md#the-order) and
with its exact commands: the same as `scripts/whole_evaluation.py`, which can also be run from a
terminal (`python3 scripts/whole_evaluation.py status | run [--from STEP] | allow STEP | done
STEP`, from `src/interaction_system/evaluation` in a prepared terminal). Unlike a campaign of one
experiment, not everything can run unattended: it stops where a person must act, and it never
makes a paid cloud call without your permission for that step.

- **The steps**, numbered and in order, each with its parts. A click on a step, or on one of its
  parts, chooses where to start: the parts that will run are outlined, those skipped are faint,
  and the chosen one is explained below (what it does, its exact commands, what it awaits if a
  person must act, whether it pays and its permission, its run health). Each part shows its state
  (✓ done, ✗ failed, ❚❚ paused, ■ stopped there, ● running) and marks: `person` (a person must act),
  `$` (paid cloud calls; `$ allowed` once you allowed them; `$?` known later: the judge's steps pay
  only if the judge chosen in E0b is a cloud model) and the run health (`health OK` or `CHECK`).

  | Step | Parts (a person's stops in italics) | What runs |
  |---|---|---|
  | 1. Pilot | *debugging exit*, pilot of every experiment, *pilot review*, *freeze commit* | `scripts/run_pilot.py run` (every pilot file of `experiments/pilot/`, free: the cloud calls go to local stand-ins; its check, `pilot_check.py`, at the end: [PILOT.md](PILOT.md)) |
  | 2. E0a | *trap review*, preflight, phase A, *adjudication*, phase B, decide, *the decision*, apply, *validation again* | `scripts/run_e0a.py preflight --allow-cloud`, `phase-a`, `phase-b --allow-cloud`, `decide`, `apply` |
  | 3. E0b | preflight, dialogues, label (development half), *tune the instructions*, label, report, cloud judges (only if no local judge passed) | `scripts/run_e0b.py preflight --allow-cloud` (the simulated user only), `dialogues --allow-cloud`, `label --development-only`, `label` (free), `report` with the four filled sheets; only when no local judge passed, `label-cloud --allow-cloud` and `report` again (`--openai` when "also the OpenAI judges" is ticked); when a local judge passed, that part runs nothing and pays nothing |
  | 4. E1 | go/no-go gate, *gate decision*, cloud smoke check, campaign, judge, role swap smoke check, role swap, role swap judge | the gate: `run_experiment.py e1_bounded_vs_llm_led.yaml --only-model <the E0a model> --allow-cloud`; each smoke check: `cloud_smoke.py <files> [--judge <cloud judge>] --allow-cloud`; the campaign and the role swap: `run_parallel.py <file> single_gpu.yaml --check-servers`, then `--allow-cloud`, then `score_runs.py`; each judge: `judge_runs.py` with the judge of E0b (`--dry-run`, the labels, `--summary`) |
  | 5. E2 | behavior choice | `offline/benchmarks/bench_behavior_choice.py --config <config in English> --method all --save`, with a copy of `offline/config_ollama.yaml` that it writes (the E0a model, English) |
  | 6. E3 | cloud smoke check, mechanisms, noise, model failures, noise judge | `cloud_smoke.py` on the three files, then `run_parallel.py` on `e3_mechanisms.yaml`, `e3_noise.yaml`, `e3_failures.yaml` (as in E1), the judge on the noise |
  | 7. E4 | cloud smoke check, twin, twin judge | `cloud_smoke.py`, then `run_parallel.py` on `e4_sim_es.yaml` (as in E1), the judge |
  | 8. Tables | tables | `score_runs.py` on every results folder with dialogues and the judge's `--summary` where it labelled, each output kept in `tables/` of the whole-evaluation folder (E0b is not scored, nor the pilot; the technical check has no script yet) |

  Every run uses the hardware profile of REPRODUCING.md, `experiments/hardware/single_gpu.yaml`.
- **Start / resume**: runs `whole_evaluation.py run --from <the chosen part>` detached from the
  page, in its own process group: closing the page or the panel does not stop it. It runs that part
  and every one after it, and stops at the first that cannot go on. By default it starts where it
  stopped. Starting at another part runs it and every later one again (their scripts skip what
  they saved) and asks again the stops of a person among them; the permissions stay.
- **Pause**: Ctrl-C to its process group, as in a terminal: the script under way stops what it
  launched (the dialogue under way is not saved and runs again); Start resumes at that part.
- **Where it stops**, in the box at the top, with what it awaits and the button to go on:
  - *a person must act* (the debugging's exit, the review of the pilot's check, the freeze commit,
    the hand reviews of E0a, the decision of E0a before apply, the validation with the chosen
    model, the tuning of the judge's instructions, the decision of the go/no-go gate): do it, then
    **Done: continue**;
  - *your permission of its paid calls*: **Show the estimate and allow**;
  - *waiting* for what a script needs before it runs: the finalists of phase A, the decision of E0a
    applied to the experiment files (the provisional local model is refused), the dated version of
    a cloud model (`<pin>`), an API key in the environment of the panel, the pilot files, the
    four annotation sheets of E0b filled (read from the review panel's progress files),
    the judge chosen by E0b, a judge of another family than a cloud system model, servers free:
    fix it, then **Continue**, which checks again;
  - *failed* (a script ended with an error): read the output of the part, then **Run it again**.
- **Paid cloud calls.** A part with paid calls never starts without your permission for that
  part. **Show the estimate and allow** runs its dry run (no call made) and shows what it would pay
  for: the dialogues per cloud model (and how many are saved already), the utterances a cloud judge
  would label, or the tiny test calls of a preflight or a cloud smoke check (each smoke check has
  its own permission, apart from its campaign's); and, when the clone has the authors' cost
  estimate (`paper/costs_estimate.py` and `paper/COSTES.md`, not part of the public repository),
  its lines for that part. Your confirmation is recorded with what was shown; if what the part would
  pay for changes (another file, a new pin, other counts), it stops and asks again. You can allow a
  part in advance from its explanation (and revoke it), so that a night's run does not stop there.
  Only then does the page pass `--allow-cloud`.
- **The method changed.** The code hash of `provenance.py` (the sources of the system and the
  evaluation, prompts, experiment files and datasets included) is recorded when the evaluation
  starts. If it changed since, the page says so before Start, lists the parts that ran with the
  recorded method (their results come from another method than those to come), and Start asks you
  to accept the current code as the method of the evaluation (recorded, with the earlier one).
  Some stops change it on purpose (the corrections of phase A, apply, the judge's instructions, the
  pilot): accept it then. A results folder begun with other code is also listed: its script refuses
  to resume it (move it aside to run that part again). A change while the evaluation runs stops it
  before the next part.
- **Run health** at the end of each part, in its log and in its explanation, each measure with `OK`
  or `CHECK` where the evaluation gives the rule, and the value only where it gives none:

  | Measure | From | OK |
  |---|---|---|
  | Exit status | the part's scripts | 0 |
  | GPU server free | the server check of `run_parallel.py --check-servers` after the part (the experiment's own, or that of `run_e0a.py preflight`) | free (in use: the latencies of the part may not be comparable) |
  | Model latency per turn | the dialogues the part saved (`score_runs.py`): median, 95th percentile, worst turn, share over two seconds, and the `latency_summary` per model and condition | no verdict: descriptive ([METRICS.md](METRICS.md)) |
  | LLM calls cut | `llm_timeouts`, `llm_skipped` of those dialogues | both 0 (normal operation); no verdict for `e3_failures.yaml`, whose failures are injected |
  | Dialogues excluded by the simulator's rule | `sim_excluded` of those dialogues | no verdict: reported as a check |
  | Simulated user's failures | `simulator_errors.jsonl` (not saved, run again on resume) | no verdict |

  Then a sentence: all as usual; or what to check before going on; or, if the part failed, what it
  may have failed because of. The campaigns of one experiment get the same run health once they end
  (in the table).
- **Things done by people that do not stop it** are named in the explanation of the part they
  follow: the audit of the adherence checker after phase B of E0a, and the confirmations of the
  checker and the judge after the campaigns of E1 and the twin
  ([HUMAN_REVIEW.md](../HUMAN_REVIEW.md)).
- **Its folder** (`--whole-root`): `state.json` (the method's code hash and its history, each
  part's status, commands and run health, the stops done, the permissions with what was shown),
  `campaign.log` (one line per event), `logs/<part>.log` (the output of each part, ending with its
  run health), `runner.out`, `tables/`, and the configuration of E2. To start a new evaluation,
  move the folder aside.

### One experiment

An experiment on a hardware profile: the same as
[`run_parallel.py`](../README.md#hardware-profiles-and-parallel-runs).

- **Experiment** and **Hardware profile**: the files of `experiments/` and `experiments/hardware/`.
  The page describes the experiment (its models, conditions, scenarios, personas, simulated
  user, the dialogues in all), marks the paid cloud calls, and warns when a cloud model is
  still written `<pin>` (the runner refuses it).
- **Lanes**: the lanes of the profile to run (all by default), each with its models, its servers
  and its `ROS_DOMAIN_ID`.
- **What it would run (dry run)**: `run_parallel.py --dry-run`: the dialogues of each lane and the
  paid cloud calls, counted per cloud model.
- **Check the servers are free**: `run_parallel.py --check-servers`: each lane checks, as before a
  run, that its Ollama servers answer and hold no models of other work and, when the profile gives
  the server's `ssh`, that no other processes use its GPU.
- **Allow paid cloud calls**: when the dry run shows cloud calls, Start needs this ticked, and
  then asks to confirm the counts; only then does it pass `--allow-cloud`.
- **Start / resume**: runs the dry run and the check of the servers, and refuses if a server is
  not free (the page never passes `--force`). Then it starts `run_parallel.py` detached from the
  page, in its own process group; its process is recorded in
  `results/<experiment>/panel_campaign.json` and its output in
  `results/<experiment>/panel_<date>.out`. Closing the page or the panel does not stop it.
  Dialogues already saved are skipped, so Start also resumes.
- **Pause**: stops the campaign as Ctrl-C would: each lane stops its system; the dialogue under
  way is not saved, and runs again on resume.
- **Run health**: once the campaign ends (or is paused), its row shows its run health (as in the
  [whole evaluation](#whole-evaluation)), kept in `panel_campaign.json`.
- **The table**: one row per experiment: its status (pending, partial, running, running from a
  terminal, done), the dialogues saved of those expected (`results/<experiment>/runs`), how many
  ended in success, the time of the last one and the end of the log of each lane. For the
  campaign under way (or an experiment running from a terminal), the pace and an estimate of
  when it ends.

## Simulation test

One dialogue through the whole system with the simulated user of the experiment, to see what it
does. Nothing is stored: it runs into a temporary folder, deleted at the end. It is the same as
`run_experiment.py <experiment> --only-model M --only-condition C --only-scenario S
--only-persona P --only-rep 1 --show`, on the lane of the hardware profile that runs the model,
with the `ROS_DOMAIN_ID` of the tests. The lane checks first that its servers are free.

- **Experiment**, **Hardware profile**, **Condition**, **System model**, **Scenario**, **Persona**:
  the dialogue to run.
- **Run one dialogue**: a cloud system model or a cloud simulated user asks for a confirmation of
  the paid calls first. Running now shows the conversation as it goes.

**Quick checks without hardware** (`scripts/sim_checks.py`; nothing stored; the system with the
simulated speech, `speech:=none`):

| Check | What it does |
|---|---|
| System starts | The system starts with the method's trees (bounded) and with the LLM-led ablation's, with the scripted LLM (no model) |
| Dialogue, scripted LLM | One coffee order with the scripted LLM and fixed user lines: it must succeed, as in the system tests |
| Dialogue, the model | One coffee order (`sf_coffee`) with the system model and condition chosen above and fixed user lines: the model must answer |

Each result (OK, FAILED or SKIPPED, with why) stays in the table, with its time.

## Real sessions

The sessions with real participants. [REAL_SESSIONS.md](REAL_SESSIONS.md) explains the design and
the procedure of a session, and the rules of the
[campaigns and configurations](REAL_SESSIONS.md#campaigns-and-configurations).

- **Campaign**: the campaign of real sessions everything below works on (the page remembers the
  last one; `#real:<campaign>` opens one). Its box shows its sessions file, its configuration
  (read-only, **frozen**, with its hash and every value), its participants and stored trials, and
  where they are stored. When the configuration file changed after it was associated, it says
  **REFUSES ITS TRIALS AND CHECKS** and why, and its buttons are disabled. While no trial is
  stored, **Switch configuration** associates another one (the hash is taken again); after the
  first stored trial the page says it can no longer change.
- **New campaign**: a name (lowercase, digits, `-`, `_`; e.g. `pilot`), a sessions file of `real/`
  and a configuration (draft or frozen). It asks to confirm: the configuration is frozen from then
  on. The page also lists the participants stored before campaigns existed, if any: kept as they
  are, in no campaign.
- **The robot's network**: the `ROS_DOMAIN_ID` and, if needed, the DDS
  (`RMW_IMPLEMENTATION`) used by the checks and the trials. The first DDS option, **inherited
  (...)**, leaves the `RMW_IMPLEMENTATION` of the terminal that started the panel (shown in
  brackets; "not set" means the distribution's default).
- **Barge-in by lip activity (camera)**: disabled, future work: the environment has no camera
  driver nor the perception's dependencies, so the page always runs with barge-in off and check 5
  is SKIPPED ([REAL_SESSIONS.md](REAL_SESSIONS.md#barge-in) lists the steps to enable it). Once
  enabled: when ticked, the trials and the checks `system`, `perception` and `rehearsal` also start
  the lip activity detector, and the robot stops talking when the participant starts speaking
  (`real_sessions.py --barge-in on`); its default is `barge_in` of `real/sessions_es.yaml`, and it
  must stay the same for every trial of a participant (the runner refuses a trial with the other
  setting).
- **Pre-session check** (`real_sessions.py --campaign <C> check presession`): with the
  campaign's configuration, in sequence, the loudspeaker (the voice says the check phrase; the blue
  button confirms you heard it), the microphone (one transcription; confirm the words) and the
  model of each condition. Each part shows green (OK) or red, with why; below, whether it **passed
  today** and the date of its last pass (kept in the campaign's `presession.json`). Run it on every
  day of sessions: the green **Run the trial** asks for a confirmation when it has not passed that
  day (and the server refuses the trial without it).
- **Before the session: the other checks**, in order, each a white Run button
  (`real_sessions.py --campaign <C> check <what>`, with the campaign's configuration; nothing
  stored); the result of each stays in the table, per campaign, with its time:

  | Check | What it does |
  |---|---|
  | 1. Speak | The voice says a fixed Spanish phrase; you confirm that you heard it (blue button) |
  | 2. Listen | One transcription of what someone says; shows what was heard |
  | 3. LLM | The model of each condition answers a call of one token, with its latency |
  | 4. System | The whole stack starts in each condition and serves the conversation; then it stops |
  | 5. Lip activity (only with barge-in) | Only with barge-in ticked (otherwise SKIPPED): the lip activity detector starts on the camera and `/user_speaking` turns true when someone talks in front of it |
  | 6. Rehearsal dialogue | A whole conversation with a member of the team, in the scenario and condition chosen; not stored |

  The checks that touch a cloud model (LLM, System, a rehearsal in a cloud condition) ask first
  whether to include it.
- **Participant** and **Create / open the plan**: the plan of the participant in the campaign
  (`P01`, `P02`...; `real_sessions.py --campaign <C> plan`): the order of the conditions and the scenario of each. An existing
  plan is opened, never changed.
- **The plan**: one row per trial, in the order to run them, with its scenario, its condition
  (control and model) and what is stored of it, and three buttons:
  - **Card** (blue): the task card, full screen, in large Spanish text, for the participant to
    read. Click or press Esc to leave. A link opens it directly, e.g. on a second screen in
    front of the participant: `http://localhost:8095/#card:<campaign>:P05:1`
    (`#real:<campaign>:P05` opens the plan; without the campaign, the one chosen last on that
    browser).
  - **Try** (white): `real_sessions.py --campaign <C> trial <P> <k> --try`: the whole trial, with
    the campaign's configuration, nothing stored.
  - **Run the trial** (green): the trial that counts, stored in
    `~/.ros/interaction_real/<campaign>/<P>/` with the name and hash of the configuration. A trial
    already stored asks before it is replaced; without a pre-session check passed today, it asks
    first.

  A condition with a cloud model asks once per participant to confirm that the participant's
  transcribed text (never the audio) goes to that provider, as the consent form states.
- **Participants of the campaign**: every participant with a plan, its order, the trials stored
  and when the last one was.

## Configurations

How the robot sounds and the hardware of its speech in the real sessions (`real/configs/`;
[REAL_SESSIONS.md](REAL_SESSIONS.md#campaigns-and-configurations)). The simulated experiments are
not configured here: their files are frozen.

- **The list**: every configuration with its state, **draft** (no campaign uses it) or **frozen**
  (with the campaigns that use it), its note, where it was duplicated from, and its values.
- **Edit** (a draft) or **View** (a frozen one) opens the editor: name (a draft can be renamed),
  note, the voice (**Voice of** filters it to a man's or a woman's), the voice configuration
  presets and the three Piper fields, the filler delay, the microphone (`input_device`) and the
  speech-recognition model (`whisper_model`). A frozen configuration shows its values disabled.
- **Check: speak**: the voice says the phrase with the values in the editor, saved or not (only the
  voice starts; the robot's network of Real sessions). **What the robot says** fills the phrase as
  in Tests. Its last result is shown below the buttons.
- **Save the draft** (blue): writes the draft (refused for a frozen one, and for a value out of its
  range).
- **Duplicate and edit**: a new draft with everything copied, named as you write (empty: `<name>-2`,
  or the next free), opened in the editor.

## Tests

Any scenario of the experiments tried by voice, with a person as the user; nothing is stored and
nothing is changed. How the robot sounds there is any configuration (drafts included) or ad-hoc
values. The whole tab is described in [TEST_PANEL.md](TEST_PANEL.md).

## Running now

The job launched from the page (a simulation test, a check, a trial or a test): the conversation, robot
and user, as it goes; the output of the job; and the buttons to answer it or stop it.

- The blue button answers the runner when it waits for you; its label is the runner's question
  (e.g. **Start the conversation**, **I heard it**). The red button next to it is the other answer
  (e.g. **Quit without running it**).
- **Stop** stops the job as Ctrl-C would: a conversation under way is cancelled, the stack is
  stopped, and nothing of it is stored.
- **Full-screen card**: the card of the trial under way (of its campaign).

The campaign is not shown here: see [Campaign](#campaign).
