# Evaluation

What is evaluated is the method, the bounded conversation: the design decisions of the interaction
system, in which a Behavior Tree per strategy runs the conversation and the LLM only answers narrow
questions. The models are chosen for it (local models served by Ollama), not compared for their
own sake. The designs of the related work are reproduced as ablations inside the same system, so
that only one decision changes between conditions. The main comparison is the bounded
conversation against the LLM-led one, each with a small local model and with a large cloud model;
the other experiments validate the judge, check the choice of the dialogue behavior, isolate each
shared mechanism and repeat the method with real participants. The experiments, their hypotheses
and their decision rules are in [doc/EXPERIMENTS.md](doc/EXPERIMENTS.md). This page is the index of
`evaluation/` and describes its tools, not results.

**Reviewing or annotating?** Start with [HUMAN_REVIEW.md](HUMAN_REVIEW.md): every hand task of the
evaluation, who does it, when, how long, and the exact instructions.

## Reproduce in 9 steps

1. **Read the design** of each experiment: [doc/EXPERIMENTS.md](doc/EXPERIMENTS.md); the measures:
   [doc/METRICS.md](doc/METRICS.md).
2. **Install and build** the workspace with pixi ([docs/INSTALL_PIXI.md](../docs/INSTALL_PIXI.md));
   `pixi run build` also builds `interaction_sim` and `interaction_ablations`.
3. **Check the system without hardware**: the system tests and the
   [quick checks](#quick-checks-without-hardware).
4. **Prepare the models**: the Ollama server with the local models, the API keys of the cloud
   providers, and the dated version of every cloud model in the experiment files
   ([doc/REPRODUCING.md](doc/REPRODUCING.md#before-anything)).
5. **Debug before E0a**: every strategy and mechanism with the scripted LLM in English and
   Spanish (layer 1), then with real local models on the GPU server, attributing every failure
   (layer 2): [doc/DEBUGGING.md](doc/DEBUGGING.md). The exit (no program or prompt failure) comes
   before the pilot.
6. **Pilot every experiment** (`scripts/run_pilot.py`): a few dialogues of each experiment file
   through its whole chain, with free local stand-ins of the cloud models, and its exit criteria
   (`scripts/pilot_check.py`): [doc/PILOT.md](doc/PILOT.md). Then the freeze commit.
7. **Choose the local model** of each language (E0a, `scripts/run_e0a.py`) and **validate the
   judge** (E0b, `scripts/run_e0b.py`) before any other experiment:
   [doc/REPRODUCING.md](doc/REPRODUCING.md#e0a-the-local-model),
   [doc/JUDGE_VALIDATION.md](doc/JUDGE_VALIDATION.md#running-e0b).
8. **Run, score and judge** each experiment in order
   ([doc/REPRODUCING.md](doc/REPRODUCING.md#the-order)); the campaigns can also be run from the
   [control panel](doc/CONTROL_PANEL.md).
9. **Run the sessions with real participants** (E4) and compare them with their simulated twin:
   [doc/REAL_SESSIONS.md](doc/REAL_SESSIONS.md).

## Layout

```text
evaluation/
├── README.md                    this page: the index and the tools
├── HUMAN_REVIEW.md              the instructions for the human reviewers and annotators: start here
├── doc/
│   ├── EXPERIMENTS.md           the design of each experiment: question, conditions, criteria, status
│   ├── METRICS.md               every measure: definition, unit, script, experiments
│   ├── REPRODUCING.md           from a clean clone to the scored results, step by step
│   ├── JUDGE_VALIDATION.md      E0b: validating the judge, its commands and the annotation guide
│   ├── REAL_SESSIONS.md         the sessions with real participants, step by step
│   ├── CONTROL_PANEL.md         the web page that runs the evaluation with buttons
│   ├── TEST_PANEL.md            the web page to try any scenario yourself, by voice
│   └── human_review/            one page per hand task, for reviewers, annotators and the coordinator
├── offline/                     the questions to the LLM without ROS: benchmarks and datasets (README.md)
├── panels/                      the review panel for the hand reviews (README.md)
├── simulation/interaction_sim/  ROS package: the simulated speech capability and users, the system tests
├── ablations/interaction_ablations/   ROS package: the ablations, trees and skills derived from the method's
├── experiments/                 the experiment files (*.yaml), personas.yaml
│   ├── scenarios/               dialogues.yaml (English), dialogues_es.yaml (Spanish: the source of E4,
│   │                            not run by E1), e4_es.yaml (E4), e3.yaml (E3; e3_es.yaml, Spanish,
│   │                            for the test panel), e3_failures.yaml, e0a_dev.yaml, e0a_dev_es.yaml
│   │                            (E0a phase B: development only)
│   ├── pilot/                   one pilot file per experiment file, derived from it (doc/PILOT.md)
│   └── hardware/                the hardware profiles: where each model runs, which lanes run at once
├── scripts/
│   ├── run_experiment.py        runs an experiment, or one lane of it
│   ├── run_parallel.py          runs every lane of a hardware profile at once
│   ├── run_e0a.py               E0a in stages: the local model of each language
│   ├── run_e0b.py               E0b in stages: the judge
│   ├── whole_evaluation.py      the whole simulated evaluation in order, with its stops and permissions
│   ├── run_pilot.py             the pilot of every experiment (doc/PILOT.md); pilot_check.py, its exit criteria
│   ├── pilot_standin.py         the pilot's local stand-in of the cloud providers (Gemini and OpenAI shapes)
│   ├── cloud_smoke.py           one or two real calls per cloud model before a paid campaign
│   ├── sim_checks.py            quick checks of the system without hardware
│   ├── control_panel.py         the control panel (doc/CONTROL_PANEL.md)
│   ├── test_panel.py            the Tests tab of the control panel (doc/TEST_PANEL.md)
│   ├── test_panel_examples.yaml the test panel's ready examples: one per strategy, one per mechanism
│   └── tests/                   tests of the scripts without ROS (experiment files, E0 runners, control panel, whole evaluation, test panel, pilot)
├── analysis/
│   ├── score_runs.py            task success against the ground truth
│   ├── adherence.py             the simulated user's adherence to its goal items (tested by test_adherence.py)
│   ├── mechanisms.py            what each shared mechanism did, from the record (tested by test_mechanisms.py)
│   ├── strategy_checks.py       what each strategy's own mechanisms did (teach-back, out-of-range value,
│   │                            repeat...), from the record (tested by test_strategy_checks.py)
│   ├── judge_runs.py            the judge's labels of every robot utterance, and the response to each
│   │                            detour (tested by test_judge_and_scoring.py, test_detours.py)
│   └── judge_validation.py      E0b: split, annotation sheets, kappa report
├── real/                        the sessions with real participants
│   ├── sessions_es.yaml         the conditions, the scenarios and the task cards
│   ├── real_sessions.py         plans, checks, one trial per call, transcript review (the options of a test: test_real_sessions.py)
│   ├── sessions_common.py       counterbalancing, plans and cards (no ROS), tested by test_sessions_common.py
│   ├── make_cards.py            writes cards/ from sessions_es.yaml and cards_en.yaml
│   └── cards/                   every task card in Spanish and English (generated; cards/README.md)
└── results/                     what the runs write (not in the repository)
```

Every command on this page runs from `evaluation/` in a prepared terminal
([doc/REPRODUCING.md](doc/REPRODUCING.md#before-anything)), with the LLM served by Ollama at
`localhost:11434`.

## Offline: the questions to the LLM

`offline/` measures, without ROS, the narrow questions the system asks the LLM against annotated
datasets: the earlier ones (slot extraction, the answer to a confirmation, the choice of the dialogue
behavior, quiz grading) and the eleven critical questions of phase A of E0a, in English and Spanish,
with its safety gate (the trap cases of phase A are reviewed by a person before running, with
`panels/review_panel.py dataset`). It imports the production modules of
`interaction_intelligence` and its prompts, so it measures exactly what the system asks. The
benchmarks, the dataset formats and the configuration: [offline/README.md](offline/README.md); the
measures: [doc/METRICS.md](doc/METRICS.md#the-questions-to-the-llm-offline).

```sh
python3 offline/run_eval.py --config offline/config_ollama.yaml --benchmark all [--model <model>] [--save]
python3 offline/benchmarks/bench_behavior_choice.py --config <config> [--method method|no_filter|keywords|all]
python3 offline/run_models.py --config offline/config_ollama.yaml --models <m1>,<m2> --reps 3 --out results/<name>
```

## Simulation: whole conversations without audio

`simulation/interaction_sim` stands for the speech capability and the user, so that the real
system (orchestrator, trees, intelligence, LLM gateway) holds whole conversations without
microphones:

| Module | What it is |
|---|---|
| `harness.py` | `DialogueHarness`: one node that serves simple_hri's actions (`/tts_action`, `/stt_action`, publishing `/listened_text`) and plays the user: after each robot utterance it asks a user policy what to say, stays silent, or barges in through `/user_speaking`. It sends the `DialogTask` goal and records the transcript, the feedback, the result, the barge-ins and the skill trace. A conversational filler (announced on `/robot_filler`) is recorded as a robot line with `"filler": true`, in order and with its time, but it is not passed to the user policy, which only answers the reply ([fillers](../docs/behaviors/fillers.md)). `ScriptedUser` replays fixed lines; `HarnessRunner` spins the node in a thread |
| `scripted_llm.py` | A deterministic stand-in for the LLM (the methods of `LLMDialogClient` answered by fixed rules on the user's words), loaded by the intelligence with `llm_client_factory: interaction_sim.scripted_llm:make`; the robot's lines are tags, so a test checks the path the tree took. `slow N` in the user's words (or in the task, for the opening of slot filling) makes it take N seconds, to test the fillers |
| `llm_user.py` | `LlmUser`: a simulated user driven by an LLM, with a persona and a goal (the ground truth of the scenario). It only sees what the robot says, answers one short spoken line, and receives the events the scenario plans (silence, a cancel request, a fixed line, a barge-in). It only speaks: what each line states is marked after the dialogue by a local annotator (`analysis/annotate_turns.py`; decided 2026-10-31) |
| `goal_items.py` | The goal items of a scenario (`goal_items`: `{id, value[, then]}`), their validation, the robust reading of the simulated user's answer (`parse_line`), and of the `revealed` declarations of runs before 2026-10-31 (no ROS) |
| `cloud_llm.py` | One JSON answer from an LLM through its HTTP API: Ollama, Gemini or OpenAI (used by the simulated user and the judge). Cloud calls are refused unless allowed explicitly |
| `asr_noise.py` | `AsrNoise`: speech recognition errors injected on the user's text at a target word error rate (substitutions by sound-alike or frequent words, deletions, insertions, cut sentences) |
| `llm_faults.py` | The injector of model failures (E3): `FaultPlan` arms, user turn by user turn, the failures a scenario plans (`faults`: `error`, `invalid`, `hang`, `crash`, `freeze`, on some or every question to the model; and `answer`, not a failure: the model's answer pinned, for the oracle replay of the debugging) in a small switch file that the intelligence reads at every call, and returns the calls that failed. On the intelligence side the failures replace the call to the model, so that the client's own parsing, retries and fallbacks run; with the scripted LLM, `llm_client_factory: interaction_sim.llm_faults:make_scripted` returns the real client's fallback for a failed question |
| `call_log.py`, `call_recording.py` | The recorder of every call of the system's model (the debugging: [doc/DEBUGGING.md](doc/DEBUGGING.md)): `CallLog` marks the user turn from the harness side and returns the calls of the dialogue; the skill module `interaction_sim.call_recording` wraps `LLMDialogClient`'s call to the gateway and the skills, like the injector. `run_experiment.py` loads it, last, for a file with `record_model_calls: true` |
| `fault_injection.py` | The injector inside the interaction intelligence with the real model: a skill module (`skill_modules`, no skill registered) whose import wraps `LLMDialogClient` with the switch of `$INTERACTION_SIM_FAULTS`. `run_experiment.py` adds it where a scenario plans failures; the method's code is not modified |

The system tests run whole conversations through every branch of every tree with the scripted LLM
(no model, no audio): the method's trees (`test_dialogues.py`, also the conversational fillers),
the error endings a request cannot reach otherwise, on a system with a reduced catalog and a broken
tree (`test_errors.py`: no behavior fits, a runner that cannot be activated, the orchestrator
deactivated during a dialogue), the conversations while the model fails, through the injector of
model failures (`test_model_failures.py`: no cancellation when the detection fails, the
cancellation withdrawn when the reading of the confirmation fails and the answer is not exactly a
yes or no word (an exact one is read by the fallback), an exact stop command with the model down
ending the conversation without the question, the error closing when a skill
fails or the intelligence does not answer within `intelligence_timeout`, a hung model answered
with the fallback within the reply's time budget, a sustained failure ended as a breakdown (also
in a role play), a role play whose evaluation fails ended as partial, the quiz's and grounded
question answering's fallbacks), the same in Spanish for what depends on the language
(`test_spanish_model_failures.py`: the Spanish fixed phrases, an exact Spanish stop command that
cancels while the model is down, a stop word inside an utterance that does not), the shared
mechanisms in every strategy and each strategy's fixed phrases (`test_bilingual.py`, with
`scripted_llm.LocalizedScriptedLLM`, which says the real phrases of the language file), and the LLM-led
conversation of E1, the monolithic ablation (`test_llm_led.py`). `test_dialogues.py`,
`test_model_failures.py` and `test_bilingual.py` run every test twice, with the system in English
and in Spanish. `test_llm_faults.py` and `test_call_log.py` test the injector and the recorder
without a running system. Which test covers each branch and mechanism, by language:
`debug/coverage.yaml` ([doc/DEBUGGING.md](doc/DEBUGGING.md)). They start the interaction system themselves, once per
file; two runs in the same `ROS_DOMAIN_ID` answer each other's speech requests, so give each run
its own:

```sh
ROS_DOMAIN_ID=<free id> python3 -m pytest simulation/interaction_sim/test
```

The simulated user's goal items and the adherence check are tested without ROS, LLM or network:

```sh
python3 -m pytest analysis/test_adherence.py analysis/test_mechanisms.py analysis/test_strategy_checks.py analysis/test_detours.py
```

## Ablations

`ablations/interaction_ablations` holds the designs the method is compared with, as trees and
skills of the same system; the method's code is not modified:

| Ablation | What changes |
|---|---|
| `monolithic` | The LLM-led conversation: the LLM runs the whole strategy. It gets the task, the strategy's rules (those the method's trees follow), the scope rules (stay within the task, state only facts of the material), the method's rule on how a spoken line addresses the user (directly, in the informal second person), the material and the conversation, writes every reply and decides when the conversation ends and its result. Silences, the turn limit and the conversational fillers while a reply is prepared are the method's |
| `no_confirmation` | Slot filling closes as soon as the frame is complete, without reading it back |
| `no_cancel_confirmation` | A spoken cancel request ends the conversation at once |
| `llm_only_cancel`, `keywords_only_cancel` | The cancel request detected by the LLM alone, or by keywords alone |

`ablations/interaction_ablations/make_ablations.py` generates each ablation's trees from the
method's, so that they differ only in the decision removed, and its runtime configuration. Run it
after changing the method's trees, then rebuild; the `monolithic` tree is written by hand. An
ablation runs by launching the system with its `config_file`
(`share/interaction_ablations/config/<ablation>/interaction_behaviors_config.yaml`) and the skills
module `interaction_ablations.skills` in the intelligence's `skill_modules`; an experiment file
does both with the condition's `ablation` and `skill_modules`.

`llm_led.launch.py` launches the LLM-led conversation by hand: the whole stack, as
`interaction_system`'s `main.launch.py` with the same arguments (`speech`, `stt`, `tts`,
`whisper_model`, `input_device`, `language`, `params_file`, `catalog_file`), with the `monolithic` ablation's
`config_file` and its skills module added to `skill_modules` (in a temporary copy of the parameter
file). The sessions with real participants use it for their LLM-led conditions:

```sh
ros2 launch interaction_ablations llm_led.launch.py language:=es
ros2 launch interaction_ablations llm_led.launch.py speech:=none params_file:=<params>.yaml
```

## Experiments

`scripts/run_experiment.py` runs an experiment file: for each system model and condition it
launches the whole system (`main.launch.py speech:=none`, the harness being the speech capability)
in its own process group, pulls and loads the model on the server, runs every scenario × persona ×
repetition with a simulated user, saves each dialogue, and stops the system. Saved dialogues are
skipped, so a run can be resumed. The experiment files of the repository and what each runs:
[doc/REPRODUCING.md](doc/REPRODUCING.md#the-order).

```sh
python3 scripts/run_experiment.py experiments/<file>.yaml --dry-run   # the dialogues and paid calls it would make, and the wait for the model
python3 scripts/run_experiment.py experiments/<file>.yaml [--only-model M] [--only-condition C] [--allow-cloud] [--out DIR]
python3 scripts/run_experiment.py experiments/<file>.yaml --only-model M --only-condition C \
    --only-scenario S --only-persona P --only-rep 1 --out <dir> --show   # one dialogue, printed as it goes
python3 scripts/run_experiment.py experiments/<file>.yaml --models M1,M2 [...]   # other system models than the file's
```

`--models` (also in `run_parallel.py`) replaces the system models of the file; the files of
phase B of E0a (`e0a_dialogues.yaml`, `e0a_dialogues_es.yaml`) list a placeholder and need it,
since their models are the finalists of phase A. A model written with a placeholder (`<pin>`,
`<...>`) is refused when running.

| Key of an experiment file | What it is |
|---|---|
| `name` | The name of the experiment: its results go to `results/<name>/` unless `--out` says otherwise |
| `scenarios`, `scenario_ids`, `scenarios_per_strategy` | The scenario file (relative to `experiments/`) and, optionally, the ids to run (the role swap of E1 and the noise of E3 list theirs), or the first N scenarios of each strategy in the order of the file (all by default) |
| `personas_file`, `personas` | The personas file and the personas to run |
| `repetitions`, `language` | Repetitions of each scenario × persona; the language of the conversations (`English`, `Spanish`) |
| `models` | The system models: Ollama models, or cloud ones written `provider/model` |
| `conditions` | Each condition by name. `{}` is the method; `ablation` (the ablation's `config_file`), `skill_modules` (the intelligence's), `params` (overrides of `app_params.yaml`), `model_params` (overrides for one system model only, `{<model>: {...}}`; the experiments set none: one local model, no `routes`) and `models` (only these system models) change it; `scenario_ids` (only these scenarios: an ablation run on the scenarios of its mechanism, as in E3) and `asr_noise` (the noise of this condition's simulated users: one level per condition, as in the noise file of E3) limit or change what it runs |
| `simulator` | The simulated user: `provider` (`ollama` by default, `gemini`, `openai`), `model` (or `hardware`: the profile's `open_simulator`), `host` (its Ollama server; by default the system's), `temperature` (`null` for models that accept only their default), `max_turns` (the simulated user's own limit, 30), `on_gpu` (`false`: an Ollama simulator on the server's CPU) |
| `ollama` | The system's Ollama server and timeout (`host`, `port`, `timeout`) |
| `limits`, `max_turns`, `dialogue_timeout` | Parameters of the orchestrator (e.g. `wait_timeout`, `max_silence`, `max_stalled`), the turn limit of every dialogue and the longest dialogue (s) |
| `model_wait` | Optional: the wait for the model opened for every condition of the experiment, `{reply_budget, opening_budget, llm_timeout, reasoning_timeout, intelligence_timeout}` in seconds (all but `intelligence_timeout`: 0 = no limit of its own; `intelligence_timeout` > 0); each goes to the node that reads it (the orchestrator, or the intelligence for `llm_timeout` and `reasoning_timeout`). Absent: the method's defaults ([docs/behaviors/endings.md](../docs/behaviors/endings.md#the-wait-for-the-model)). Checked before the run (known keys, numbers ≥ 0; not in `limits`), shown by `--dry-run`, kept in `experiment.yaml`, the `params.yaml` of each condition and every dialogue's record. Only E1 sets it: it does not measure the bound, and some of its cloud models reason for many seconds before answering ([doc/EXPERIMENTS.md](doc/EXPERIMENTS.md)) |
| `pilot_of`, `pairs`, `standins` | A pilot file only (`experiments/pilot/`): the experiment file it is derived from (everything not changed below is read from it when it is loaded), the dialogues as `[scenario, persona]` pairs instead of every scenario with every persona, `models` as `{<local>: M, <openai>: openai/M, <gemini>: gemini/M}`, `simulator_model` (fills a placeholder only), and the base URLs of the local stand-ins of the cloud providers (`gemini_base_url`, `openai_base_url`, on this computer only; refused in an experiment file): [doc/PILOT.md](doc/PILOT.md) |
| `record_model_calls` | Optional, `true` in the debugging files only (`experiments/debug/`): every call of the system's model is stored in its dialogue's record (`model_calls`: turn, skill, question, prompt, schema, answer or error, latency), by a recorder loaded like the injector of model failures (`interaction_sim/call_log.py`); off by default, since the prompts make the records several times larger ([doc/DEBUGGING.md](doc/DEBUGGING.md)) |
| `asr_noise` | Optional: speech recognition errors injected on the user's lines (`AsrNoise`); a condition or a scenario can set its own (the scenario's wins) |

A scenario (in `experiments/scenarios/*.yaml`) has its `id`, `strategy`, `goal` (the `DialogTask`
fields; its own limits too: `max_turns`, `max_silence`, `max_stalled`, `filler_after_s`,
`skip_intro`), `user_goal` (what the simulated user wants), `expect` (the ground truth) and,
optionally, `events` (user turns fixed by code: `turn`, `kind` `silence`, `cancel`, `text`,
`detour` or `barge_in`, and `text`; a `detour` also has `detour`: `off_task`, `decline` or
`not_allowed`), `faults` (failures of the system's model by user turn, applied by code:
`turn`, 0 for the opening; `kind` `error`, `invalid`, `hang` or `crash`; `questions`, the
methods of `LLMDialogClient` that fail, every one when empty; `turns`, consecutive turns, 0 until
the end; `hang_s`; see `simulation/interaction_sim/interaction_sim/llm_faults.py`), `asr_noise`
and, in `e4_es.yaml`, `mechanism`. The keys of `expect` are listed at the top
of `dialogues.yaml`. The personas are in `experiments/personas.yaml`
([doc/EXPERIMENTS.md](doc/EXPERIMENTS.md#scenarios-and-personas)).

Each dialogue is saved as `results/<name>/runs/<model>/<condition>/<scenario>__<persona>__r<k>.json`
(`:` and `/` of the model become `_`): the scenario, model, condition and persona; the result
(`status`, `success`, `outcome`, `failure_reason`, `data`, `total_turns`, `duration_s`); `expect`
and `goal`; the time-stamped `transcript`; the `feedback`; the skill trace (`skills`); the
`barge_ins`; the simulated user's log (`user_log`, with the model version and tokens of each call)
and its totals (`simulator`); the model failures planned (`faults`) and the calls that failed
(`faults_applied`: the planned failure, the turn, the question, the kind). A summary line per dialogue goes to `results/<name>/runs.jsonl`.

### Hardware profiles and parallel runs

Where an experiment runs is not part of it: the same experiment runs on one GPU server, on a GPU
server and this computer, or on two GPU servers, by choosing a hardware profile
(`experiments/hardware/*.yaml`). A profile names the Ollama servers (as this computer reaches
them, e.g. through an SSH tunnel), the open simulated user that fits them (`open_simulator`, used
by the experiments with `simulator.model: hardware`), and the **lanes**: the parts of an
experiment that run at the same time, each with its own `ROS_DOMAIN_ID`, its system models
(`local`, `cloud`, `all`, a list, and optionally a `shard` [i, n] to spread several local models
over servers) and the server of its open simulated user. The runner and the ROS system always run
on this computer; only the LLMs run on the servers.

| Profile | Servers | What runs where |
|---|---|---|
| `single_gpu.yaml` | One GPU server (24 GB) | The local system model and the open simulator together on the GPU; the cloud conditions in a second lane at the same time (no GPU) |
| `gpu_and_laptop.yaml` | GPU server and this computer's small GPU (4 GB) | The GPU left whole to the system (e.g. a routing with two 14B models); a 4B simulator on this computer, for development only |
| `two_gpus.yaml` | GPU server and a second GPU server | The system on the first; a larger open simulator on the second |
| `two_gpus_split.yaml` | Two GPU servers | Half of the local system models on each, each with its own simulator |

The open simulator a profile can hold, by the memory it has (of another family than the system's
Qwen models; sizes approximate, check with `ollama show`):

| Memory for the simulator | Open simulator |
|---|---|
| About 4 GB (development only) | `gemma3:4b` |
| 16 GB, or shared with a 14B system model on 24 GB | `gemma3:12b` |
| 24 GB of its own | `mistral-small3.2:24b` |
| 48 GB | `llama3.3:70b` |

When two models do not fit together on one GPU, Ollama swaps them: each dialogue takes about twice
as long, and the system's latency includes the reloading (`load_duration` in Ollama's answer). An
Ollama server must allow the models it holds at once (`OLLAMA_MAX_LOADED_MODELS`).

```sh
python3 scripts/run_parallel.py experiments/<file>.yaml experiments/hardware/<profile>.yaml --dry-run
python3 scripts/run_parallel.py experiments/<file>.yaml experiments/hardware/<profile>.yaml --check-servers
python3 scripts/run_parallel.py experiments/<file>.yaml experiments/hardware/<profile>.yaml [--lanes local] [--allow-cloud] [--out DIR]
python3 scripts/run_experiment.py experiments/<file>.yaml --hardware experiments/hardware/<profile>.yaml --lane <lane>
```

`run_parallel.py` starts one `run_experiment.py` per lane (logs in `results/<name>/lane_<lane>.log`;
Ctrl-C stops them all). Before running, each lane checks that its servers are free: no models
loaded that are not of this experiment and, when the profile gives the server's `ssh` (read from
an environment variable, `GPU_SERVER_SSH=<user>@<host>`), no other processes on its GPU.
`--check-servers` runs only the check (exit status 3 when a server is not free); `--force-busy`
skips it (`--force`, its old name, still works with a warning). A model of the previous run that is
still unloading is waited for (up to a minute) before the check gives up. `--force-provenance`
resumes a results folder begun with other code (the change is recorded in its provenance.json).
The system model is unloaded when its dialogues end; `--keep-loaded` leaves it loaded, for runs one
after the other on a shared GPU, so that a job waiting for free GPU memory does not take the gap
between them (unload it yourself at the end). Lanes that use the same server for their models slow each other down, so their latencies are
not comparable (the runner warns). Each lane writes what it ran, with its servers and models, to
`results/<name>/experiment_<lane>.yaml`.

### Cloud models

A system model can be a cloud one, written `provider/model` (`openai/<model>`, `gemini/<model>`)
in the experiment's `models`; the simulated user (`simulator.provider: gemini` or `openai`) and
the judge can be cloud models too. Every cloud model is written with its dated version: the runner
refuses a `<pin>` placeholder. Cloud calls are paid, so they are refused unless the command has
`--allow-cloud`; `--dry-run` tells how many dialogues go to each cloud model. The API keys are read
from the environment: `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and `OPENAI_API_KEY`. The version that
answered and the tokens are recorded for the simulated user and the judge; a cloud system model is
identified by its dated name, since the LLM gateway does not report them to the runner. A Gemini
system model needs the `google-genai` package in the environment of the LLM gateway (the simulated
user and the judge call the HTTP API directly). The conditions with a cloud system model need no
GPU.

## Scoring and judging

`analysis/score_runs.py` scores each dialogue of a results folder against the ground truth of its
scenario, with no LLM. `analysis/judge_runs.py` asks an LLM judge whether each robot utterance
stays in the frame of its task and which facts it states that the task and its material do not
support; its labels are used only after the judge is validated in E0b with
`analysis/judge_validation.py`. Both read the folder of an experiment or of the sessions with real
participants. `score_runs.py` also checks the simulated user's adherence to the goal items of its
scenario (`analysis/adherence.py`) and marks the simulator failures by a rule fixed before running
(`--adherence-rule`); they stay in `scores.csv`, and the summary gives the success rate with and
without them ([doc/EXPERIMENTS.md](doc/EXPERIMENTS.md#the-simulated-users-and-their-check)). It
prints the latency per model and condition (median, 95th percentile and worst turn, the share of
turns over two seconds, fillers, calls cut by the wait for the model), descriptive only
([doc/METRICS.md](doc/METRICS.md#the-summary)). It also
counts what each shared mechanism did (`analysis/mechanisms.py`: confirmation questions, silent
waits, events applied, closing line, error closing, failed skills, model failures provoked) and what each strategy's own mechanisms did
(`analysis/strategy_checks.py`: teach-back with planted wrong points, an out-of-range value, the
slots left unanswered, a contradicted step check, a repeat in a role play, the rubric kept out of
the role play's introduction), and compares them with `expect.mechanisms` when the scenario lists
it (E3); the questionnaire score computed by code against `expect.score` (`score_ok`); and it counts
apart the cancellations that no scenario planned, asked for by the user and honoured after
confirming (`honoured_cancel`, right endings). `judge_runs.py --summary` also gives the response to
each planned detour (the twin of E4). Every measure they compute: [doc/METRICS.md](doc/METRICS.md); the validation:
[doc/JUDGE_VALIDATION.md](doc/JUDGE_VALIDATION.md).

```sh
python3 analysis/score_runs.py results/<name> [--csv <file>] [--adherence-rule <rule>] [--exclude-sim-failures]
python3 analysis/adherence.py experiments/scenarios/<file>.yaml    # check the goal items of a scenario file
python3 analysis/judge_runs.py results/<name> --provider <ollama|gemini|openai> --model <judge> --tag <tag> [--allow-cloud] [--dry-run]
python3 analysis/judge_runs.py results/<name> --tag <tag> --summary
python3 analysis/judge_runs.py results/<name> --tag <tag> --sample 200 [--sample-out <file>]   # for the annotators
python3 analysis/judge_runs.py results/<name> --tag <tag> --agreement <file>
```

## Quick checks without hardware

`scripts/sim_checks.py` checks the system before running experiments, with the simulated speech
(`speech:=none`) and nothing stored; its last line is the verdict (`CHECK <name>: OK | FAILED`):

```sh
ROS_DOMAIN_ID=<free id> python3 scripts/sim_checks.py start       # the system starts, bounded and LLM-led (scripted LLM)
ROS_DOMAIN_ID=<free id> python3 scripts/sim_checks.py scripted    # one dialogue with the scripted LLM: it must succeed
ROS_DOMAIN_ID=<free id> python3 scripts/sim_checks.py model experiments/<file>.yaml --model <model> \
    [--condition C] [--hardware <profile> --lane <lane>]          # one coffee order with the model, fixed user lines
```

Options of `model`: `--language es` (the Spanish scenario and user lines), `--allow-cloud` (a
cloud system model) and `--force` (skip the check that the lane's servers are free). `--keep`
keeps the logs of any check; a failed check keeps them anyway.

## The panels

Two web pages, served on this computer only:

- `scripts/control_panel.py` (port 8095) runs the evaluation with buttons: the whole simulated
  evaluation in order (`scripts/whole_evaluation.py`: it stops where a person must act and asks
  for the permission of every paid step), the campaign of an
  experiment on a hardware profile, one dialogue with its simulated user, the quick checks, the
  sessions with real participants (campaigns of real sessions, each with a frozen configuration of
  how the robot sounds, and the pre-session check) and the configurations themselves. It only calls
  the scripts above, with the same options: [doc/CONTROL_PANEL.md](doc/CONTROL_PANEL.md). Its
  Tests tab (`scripts/test_panel.py`, a page of its own on port 8097 until 2026-10-30) lets a
  person try any scenario as the user, by voice, with nothing stored, with ready examples (one per
  strategy, one per mechanism) and a tester card (what to do turn by turn):
  [doc/TEST_PANEL.md](doc/TEST_PANEL.md).
- `panels/review_panel.py` (port 8098) for the hand reviews, made for people who know nothing about
  the system (English by default, Spanish with one click, a tutorial, every answer saved at once): the E0b annotation
  sheets, the trap cases of phase A and their blind adjudication, and the audit of the adherence
  checker. What it writes stays in the git-ignored `results/`: [panels/README.md](panels/README.md).
  The instructions for the reviewers and annotators: [HUMAN_REVIEW.md](HUMAN_REVIEW.md).

## Sessions with real participants

`real/` runs the conversations of real participants with the robot, in Spanish: the enabled
conditions of `real/sessions_es.yaml` (in E4, only the method with the local model), four
conversations per participant, each in a different scenario, counterbalanced from the participant's
number. The records have the keys of the simulated dialogues, so `score_runs.py` and
`judge_runs.py` score them too, the judge only after each transcript is reviewed. The checks before
a session, the procedure and what is stored: [doc/REAL_SESSIONS.md](doc/REAL_SESSIONS.md).

```sh
python3 real/real_sessions.py plan P07                 # the plan of a participant
python3 real/real_sessions.py check speak              # a check before a session (speak, listen, llm, system, perception, rehearsal)
python3 real/real_sessions.py trial P07 1 [--try]      # one trial (--try: nothing stored)
python3 real/real_sessions.py review P07 --by <name>   # the transcript review, before the judge
```
