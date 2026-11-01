# The pilot of every experiment

Before the method is frozen and before E0a, every experiment file runs a few dialogues through its
whole chain: launch, dialogues, storage, scoring, judge, latency and run health, provenance. The
pilot checks that each experiment is **well programmed and works**, not the method's quality: its
results are never reported, and a pilot finding may only fix a program or pipeline error. Anything
that would change the method, a prompt or a scenario is recorded and decided by the author, never
tuned on the scenarios the experiments test.

Where it sits: debugging ([DEBUGGING.md](DEBUGGING.md)) → **the pilot of every experiment** →
the freeze commit → the review of the trap cases → E0a → E0b → the go/no-go gate of E1 → E1 and
the role swap → E3 → the twin of E4 (E2 offline where it fits)
([REPRODUCING.md](REPRODUCING.md#the-order), [EXPERIMENTS.md](EXPERIMENTS.md#order-of-the-work)).

Contents: [The pilot files](#the-pilot-files) · [Who plays each role](#who-plays-each-role) ·
[The dialogues of each pilot](#the-dialogues-of-each-pilot) · [Running it](#running-it) ·
[Exit criteria](#exit-criteria) · [What the pilot cannot check](#what-the-pilot-cannot-check) ·
[What the pilot gives E1](#what-the-pilot-gives-e1)

## The pilot files

One pilot file per experiment file, in `experiments/pilot/` (`<experiment>_pilot.yaml`). A pilot
file names the experiment file it pilots (`pilot_of`) and is **derived from it when it is loaded**
(`scripts/experiment_files.py`, `load_experiment`): the conditions, limits, wait for the model,
simulator, timeouts and launch options are read from the experiment file, so the pilot cannot
drift from it. A pilot file may change only:

| Key | What it changes |
|---|---|
| `name` | Its own name: results in `results/pilot/<name>/` |
| `scenarios`, `pairs` | Which dialogues: the scenario file (development scenarios where the experiment's machinery does not depend on the scenario) and the dialogues as `[scenario, persona]` pairs, each scenario with one persona of the experiment in turn, instead of every scenario with every persona |
| `repetitions` | 1 |
| `models` | `{<local>: M, <openai>: openai/M, <gemini>: gemini/M}`: every local model and every cloud system model of the experiment becomes the pilot's system model (a placeholder, the provisional model or a pinned version alike) |
| `simulator_model` | Fills the simulator's placeholder only (the role swap's unpinned OpenAI user) |
| `standins` | The base URLs of the stand-ins of the cloud providers (`gemini_base_url`, `openai_base_url`), on this computer only |

Anything else in a pilot file is refused, and an experiment file with `standins` is refused. The
runners (`run_experiment.py`, `run_parallel.py`) read pilot files as experiment files; the control
panel lists only the experiment files.

## Who plays each role

The pilot makes **no paid call**, and still runs every cloud code path of the evaluation: the cloud
calls go, through the same clients, to `scripts/pilot_standin.py`, a small server on this computer
that speaks Gemini's REST API (`generateContent`, `streamGenerateContent`) and OpenAI's Chat
Completions (streamed or not) with their usage and model version. `run_experiment.py` sends the
gateway's cloud providers to it (`openai.base_url`, `gemini.base_url`) and the evaluation's own
client (`interaction_sim/cloud_llm.py`) through two environment variables, with a dummy API key: a
call that reached a real API by mistake would be refused, not billed.

| Role | In the experiments | In the pilot |
|---|---|---|
| The system, local cells | The local model (`qwen3:8b` until E0a decides) | `qwen2.5:32b`, reasoning off, the experiments' context (8192), on the GPU server |
| The system, cloud cells (E1's OpenAI model; the role swap's Gemini model) | A large cloud model, through the gateway's OpenAI or Gemini provider | The same providers (the openai and google-genai SDKs), with their base URL on the stand-in, which has `qwen2.5:32b` answer |
| The simulated user (Gemini; OpenAI in the role swap) | A cloud model | A goal-driven script, answered by the stand-in in the provider's shape, through the simulator's own client |
| The judge | The one E0b chose | The first cloud rung of the ladder (`gemini-2.5-flash-lite`) with synthetic labels from the stand-in; in the role swap, whose system is Google's, a local judge (`qwen2.5:32b`, already on the GPU: the local judge's path); E0b, its own chain (below) |

**The system model.** `qwen2.5:32b` stands in for every system cell, local and cloud: a large model
known to answer the narrow, schema-constrained questions of the method well, so that a failure
points at the program and not at a weak model; it is one of E0a's candidates already (no other
download) and needs no reasoning switched off. About 20 GB at 4 bits plus the cache of an
8192-token context: it runs alone on a 24 GB GPU, the only model there during the pilot
(`model_fit.json` records whether it is entirely in GPU memory). With a 32B model a turn is slower
than with the 8B class the method is meant for: the latency and the time limits of the method are
checked in the debugging, not here.

**The simulated user** is a script, not a model: from the scenario's goal items and the robot's
last line it gives the value asked (the item whose name or value the robot mentions, else the next
one, in the order the conversation asks them), says the planned change of a value, answers yes to a
confirmation, declines a stop the scenario does not plan, and ends when the robot says goodbye. The
planned events (silences, cancellations, detours, replies at fixed turns) are said by the
simulator's own code, as in every experiment. A dialogue that the script derails is a simulator
failure (S), never a program failure. If the script derails too many dialogues, the fallback is a
small local model as the simulated user (`run_pilot.py run --user-model gemma3:4b`), through the
same client and stand-in; it does not fit next to the 32B model on a 24 GB GPU, so it runs on
another Ollama server (`--user-ollama`, e.g. this computer's); swapping both models on one GPU
(`OLLAMA_MAX_LOADED_MODELS=1`) would reload the 32B model at every turn and make the pilot several
times longer.

## The dialogues of each pilot

Every condition × model cell at least once; every strategy of the experiment at least once; every
persona of the experiment at least once (each scenario with one persona, in turn); both languages
where the experiment has two; every kind of event, failure, detour and noise level of the
experiment. Development scenarios (`e0a_dev*.yaml`, disjoint from E1–E4) where the experiment's
machinery does not depend on the scenario; where it does (planned events, failures, detours), the
smallest subset of the test scenarios that covers it. The applications are those of the paper's
domains: service (bookings, orders, a front desk), assistance (home repairs, a procedure),
health and care information, education (quizzes), guidance (a museum, a theatre, a station).

| Pilot of | Scenarios | Dialogues | What it covers |
|---|---|---|---|
| E0a, phase B (English; Spanish) | Development: one per strategy, those with the development set's events (a cancellation, two silences) | 7 + 7 | Every strategy, the three personas, both languages |
| E0b (English; Spanish) | Development: one per strategy, others than E0a's (the experiment uses test scenarios; its machinery does not depend on them) | 14 + 14 | Both conditions, every strategy, the three personas, both languages; then E0b's whole chain (below) |
| E1 | Development: all 21 (three per strategy) | 84 (21 per cell) | The four cells of the 2×2 with the same pairs, the eight personas (two or three times each), E1's open wait for the model |
| E1, role swap | Development: one per strategy | 28 | The four cells, the Gemini system provider and the OpenAI simulated user, the three personas |
| E3, mechanisms | Test (`e3.yaml`): a cancellation by exact command and one by a phrase that is denied, the confirmation of the data, recovery and breakdown, the silence prompts and the silent user, the turn limit, the strategies' own mechanisms (seven `m_` scenarios: every check of `strategy_checks.py`) | 24 | Every condition (the three cancellation ablations, the confirmation ablation, B), every kind of event, every strategy in the method |
| E3, noise | Development: one per strategy | 56 | The method and B at 0, 10, 20 and 30 % word error rate |
| E3, model failures | Test (`e3_failures.yaml`): cancellation with the model down and with its reading broken, a crash, a frozen intelligence, a hung call, the fallbacks of grounded answers, explanation and questionnaire, two sustained failures | 12 | Every kind of failure (error, invalid answer, crash, freeze, hang), every strategy, both conditions |
| E4, simulated twin (Spanish) | Test (`e4_es.yaml`): seven of the eight cards | 7 | Every strategy, every kind of event (silence, cancellation, reply) and of detour (off task, decline, not allowed), the four personas |

253 dialogues in all. The rationale of each file is in its header.

**E0b's chain** runs on the pilot's dialogues of each language: the split into halves, the local
judge on the development half and then on everything, small sheets (20 utterances), synthetic
annotations of the two sheets (so that the report runs; two people fill the real ones), the report
with the local judge, then every cloud judge of the ladder (OpenAI's included) on the sheets'
utterances through the stand-in, the report again, and the combination of both languages
(`results/pilot/e0b_report.json`). E0a's decision (`run_e0a.py decide`, `apply`) works on phase B's
own folders and is checked by its tests, not by the pilot.

## Running it

From `src/interaction_system/evaluation`, in a prepared terminal
([REPRODUCING.md](REPRODUCING.md#before-anything)), with the GPU server's Ollama at
`localhost:11434`:

```sh
python3 scripts/run_pilot.py list                        # the pilots, their dialogues and judges
python3 scripts/run_pilot.py dry-run                     # the dry run of each (no call)
python3 scripts/run_pilot.py run                         # every pilot, in the order of the evaluation
python3 scripts/run_pilot.py run --only e3_failures_pilot --lanes local   # part of it
python3 scripts/run_pilot.py check                       # the exit criteria again
```

`run` holds the GPU lock (E0a, E0b and the pilot never run at once), starts the stand-in for each
pilot (its calls logged in `results/pilot/<name>/standin_calls.jsonl`) and stops it by its PID,
runs the lanes of `single_gpu.yaml` one after the other (the local cells, then the cloud cells), so
that one model is on the GPU at a time, scores, judges, unloads the model and runs the check. Every
pilot resumes where it stopped. In the whole evaluation it is step 1 (after the debugging's exit,
before the review of its check and the freeze commit:
[CONTROL_PANEL.md](CONTROL_PANEL.md#whole-evaluation)).

**The GPU server's disk.** The disk of the GPU server is shared and nearly full: `qwen2.5:32b`
(about 20 GB) is pulled for the pilot after the debugging's models are removed, and removed after
the pilot. `run_pilot.py run` refuses to start while it is missing.

**Time.** About 250 dialogues with a 32B model, most of them of the method (several questions per
turn): roughly 6 to 9 hours on the GPU server. No paid call.

## Exit criteria

Checked by `scripts/pilot_check.py` on every pilot's results (no model called; also at the end of
`run_pilot.py run`; written to `results/pilot/check.json`). FAIL: a program or pipeline error, fixed
(with a regression test) and the affected pilots run again. LOOK: read and attributed as in the
debugging (C program, P prompt or method, S simulator, M model); only C and P block.

| Criterion | Passes when | Else |
|---|---|---|
| Provenance | `provenance.json` and `experiment*.yaml` are written | FAIL |
| Dialogues | Every planned dialogue is saved (the simulated user's failures, not saved, are S) | FAIL |
| System errors | No dialogue ends by a system error (status `rejected` or `timeout`, outcome `error`, a skill that raised) where no failure is injected | FAIL |
| Records | Every record has its transcript, frame, outcome, wait for the model and simulator's log; the scorer reads it (the mechanisms' measures, `mechanisms_ok` where the scenario plans mechanisms, the strategy checks of the `m_` scenarios); the latency summary has its turns | FAIL |
| Mechanisms | The mechanisms the scenarios plan happened | LOOK |
| Calls cut | `llm_timeouts` = `llm_skipped` = 0 (not applied with injected failures). With a 32B model a cut turn is looked at, not a failure by itself, unless it comes from a program error | LOOK |
| Failures injected | Every planned model failure was injected (`faults_hit`) | FAIL |
| Judge | The judge labelled every robot utterance without errors; for E0b, the split, the sheets, the key, every judge of the ladder on the sheets and the report exist | FAIL |
| Stand-ins | Every cloud call went to the stand-in (model versions `*-pilot-standin`) and the stand-in logged them | FAIL |

The check also prints the tokens of each role, from the stand-in's log: the real prompts (Ollama's
count for the cloud system cells; about four characters per token for the simulated user and the
judge). They are compared with the assumptions of the cost estimate before the paid campaigns.

## What the pilot cannot check

Authentication, the real model names and pinned versions, a setting a provider rejects (e.g. a
Gemini Pro model that refuses to switch its reasoning off), rate limits and quotas, retries on the
real API's errors, and the real latency of the cloud models. They are checked by the **cloud smoke
check** right before every paid campaign: one or two real calls per cloud model of the campaign,
through the code the campaign uses (cents), with a permission of its own in the whole evaluation:

```sh
python3 scripts/cloud_smoke.py experiments/<experiment>.yaml [...] [--judge <provider>/<model>] [--allow-cloud]
```

Each cloud system model gets one schema-constrained call and one streamed call through the
gateway's own provider class; the cloud simulated user and a cloud judge, one call each through
`cloud_llm.py`. Without `--allow-cloud` it lists the calls. It refuses the stand-ins' variables
and a placeholder left in a file. The preflights of E0a and E0b ping their simulated user, and
`run_e0b.py label-cloud` pings each cloud judge before it labels.

## What the pilot gives E1

The duration of each kind of dialogue, the token volumes per role and a checked pipeline. It does
not estimate the variability of the method with the model E0a will choose: its system model is a
stand-in and its users are scripted. The number of repetitions of E1 is fixed before its campaign
(EXPERIMENTS.md, rules for every campaign).
