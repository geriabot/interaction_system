# Reproducing the evaluation

From a clean clone to the scored results of every experiment, in order. The design of each
experiment (its question, conditions, sizes and decision rules) is in
[EXPERIMENTS.md](EXPERIMENTS.md); the measures are defined in [METRICS.md](METRICS.md); the tools
are described in the [evaluation README](../README.md). The campaigns of the simulated experiments
and the sessions with real participants can also be run from the
[control panel](CONTROL_PANEL.md), the whole simulated evaluation in order included
([Whole evaluation](CONTROL_PANEL.md#whole-evaluation)).

Contents: [Before anything](#before-anything) · [Rules for every run](#rules-for-every-run) ·
[Provenance of the results](#provenance-of-the-results) · [The order](#the-order) · [The pilot](#the-pilot-of-every-experiment) ·
[E0a](#e0a-the-local-model) · [E0b](#e0b-the-judge) ·
[E1](#e1-and-the-role-swap) · [E2](#e2-the-choice-of-the-dialogue-behavior) ·
[E3](#e3-the-shared-mechanisms) · [E4](#e4-real-participants-and-the-simulated-twin) ·
[Technical check](#the-technical-check) · [Where the results go](#where-the-results-go)

The hand reviews (the trap cases and the adjudication of E0a, the audits of the adherence checker,
the annotation of E0b, the confirmations of the judge, the review of the real transcripts) are done
by people at fixed points of this order. Who does each one, when, and the exact commands to
prepare and collect them are in [HUMAN_REVIEW.md](../HUMAN_REVIEW.md).

## Before anything

1. Install and build the workspace with pixi: [docs/INSTALL_PIXI.md](../../docs/INSTALL_PIXI.md).
   `pixi run build` (ROS 2 Jazzy, `install/`) and `pixi run build-lyrical` (ROS 2 Lyrical,
   `install_lyrical/`) also build the two packages of the evaluation, `interaction_sim` and
   `interaction_ablations`.
2. Prepare a terminal. Every command below runs from `src/interaction_system/evaluation` in a
   terminal prepared as follows (for Lyrical: `pixi shell -e lyrical` and
   `install_lyrical/setup.bash`):

   ```sh
   cd <workspace>
   pixi shell
   source install/setup.bash
   cd src/interaction_system/evaluation
   ```

3. Check the system without hardware. The system tests hold whole conversations through every
   tree with a scripted LLM; the quick checks start the system as the experiments do
   ([README](../README.md#quick-checks-without-hardware)). No model, no network:

   ```sh
   ROS_DOMAIN_ID=<free id> python3 -m pytest simulation/interaction_sim/test
   ROS_DOMAIN_ID=<free id> python3 scripts/sim_checks.py start
   ROS_DOMAIN_ID=<free id> python3 scripts/sim_checks.py scripted
   python3 -m pytest real/test_sessions_common.py real/test_campaigns.py
   ```

4. Set up the LLM server. The local models are served by Ollama on a GPU server with 24 GB, reached
   from this computer at `localhost:11434` (an SSH tunnel:
   `ssh -N -L 11434:localhost:11434 <user>@<gpu server>`). The experiment runner pulls the system
   and simulator models a server lacks; the models of the offline benchmarks and of the local
   judges must be pulled by hand (`ollama pull <model>` on the server):

   | Models | Used by |
   |---|---|
   | The local model of each language chosen in E0a (`qwen3:8b` in the files until then, to debug) | The system in E0b, E1, E3, E4 |
   | `mistral-small3.2:24b`, `llama3.1:8b`, `gemma3:27b`, `phi4:14b`, `qwen3:32b` | The local candidate judges of E0b (`run_e0b.py` pulls and removes them) |
   | The 16 candidates of E0a (`offline/e0a_candidates.txt`: 13 from the literature, 3 exploratory), pulled and removed one at a time by the scripts | E0a |

   The experiments use one local model for every question (no routes). A server must allow the
   models it holds at once (`OLLAMA_MAX_LOADED_MODELS`: 2 when a local judge or a second model
   shares the server); otherwise Ollama swaps them and the latency includes the reloading. To let the
   runner check that nobody else uses the GPU, export the SSH target of the server:
   `export GPU_SERVER_SSH=<user>@<gpu server>` (read by the hardware profiles; unset, only the
   loaded models are checked). Check the server: `curl -s localhost:11434/api/tags`.
5. Prepare the cloud models. The simulated user of E0a (phase 2), E0b, E1, E3 and the twin of E4
   is Gemini 2.5 Flash-Lite, since Gemini 2.0 Flash is no longer served; the large system model of
   E1 is OpenAI's and that of the role swap Google's; some candidate judges are cloud models.
   Before running:
   - export the API keys: `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and `OPENAI_API_KEY`;
   - replace every `<pin>` of the experiment file with the dated version of the model (e.g.
     `openai/<model>-<date>`); the runner refuses to start while one is left;
   - for a Gemini system model (the role swap), install the `google-genai` package in the
     environment of the LLM gateway.

   Every command that calls a cloud provider needs `--allow-cloud`
   ([README](../README.md#cloud-models)).

## Rules for every run

- Run `--dry-run` before every campaign, to see the dialogues per lane and the paid calls.
- Run `--check-servers` before a campaign; each lane checks again before it starts and refuses a
  server used by other work ([README](../README.md#hardware-profiles-and-parallel-runs)). Never
  pass `--force-busy` on a shared server.
- Run one campaign at a time per server, each lane with its own `ROS_DOMAIN_ID` (the hardware
  profile gives them).
- Every dialogue is saved when it ends, and saved dialogues are skipped, so running the same
  command again resumes the campaign. A dialogue cut by Ctrl-C is not saved.
- Prompts, trees and configuration do not change during a campaign, so the method stays frozen
  ([EXPERIMENTS.md](EXPERIMENTS.md#rules-for-every-campaign)).

## Provenance of the results

Every run writes under `evaluation/results/<name>/`, which is not part of the repository. Each folder
starts with a `provenance.json`: a hash of the code and configuration of the system and the
evaluation, the git commit, the digest of every local model, the command and the time of the start
and of every resume. A folder holds the results of one version of the code: resuming it with changed
code is refused (use a new folder; `--force-provenance` resumes it anyway, only when the
change cannot alter the runs already saved, and the resume is recorded in the file).
`evaluation/results/CATALOG.jsonl` has one line per start, resume and end of every run, per
decision of E0a and E0b and per human review saved;
`python3 scripts/provenance.py` lists it. Human reviews and annotations are kept under
`evaluation/results/` as well (they carry the reviewers' names).

## The order

The debugging comes first, then the pilot of every experiment, then the freeze commit; only then
the experiments. The local model comes first (E0a), one per language, since every later
experiment file, E0b's included, uses the model of its language (`qwen3:8b` is written in the
files until E0a decides; `run_e0a.py apply` writes the chosen ones); the validation and the
debugging are then repeated with it. The judge comes next (E0b), since its labels are used in
every later experiment, including the safety measures of E1's go/no-go gate. Then come the gate,
E1 and the role swap, E3 and the twin of E4, with E2, offline, where it fits. The reasons for this
order are in [EXPERIMENTS.md](EXPERIMENTS.md#order-of-the-work).

| Step | What | Files | Paid calls |
|---|---|---|---|
| 1 | [The debugging](#before-e0a-the-debugging) (exit: no program or prompt failure), [the pilot of every experiment](#the-pilot-of-every-experiment) (`scripts/run_pilot.py`), the review of its check, the freeze commit | `experiments/debug/`; `experiments/pilot/` | None (the pilot's cloud calls go to local stand-ins) |
| 2 | The review of the trap cases, then [E0a](#e0a-the-local-model): choosing the local model of each language (`scripts/run_e0a.py`) | `offline/` (phase 1); `e0a_dialogues.yaml`, `e0a_dialogues_es.yaml` (phase 2) | Phase 2: the simulated user |
| 3 | [E0b](#e0b-the-judge): validating the judge (`scripts/run_e0b.py`) | `e0b_judge_dialogues.yaml`, `e0b_judge_dialogues_es.yaml` | The simulated user; the cloud judges only if no local judge passes, on the annotated utterances only |
| 4 | [E1](#e1-and-the-role-swap): the go/no-go gate (after E0b), then E1 and the role swap (English only) | `e1_bounded_vs_llm_led.yaml`, `e1_role_swap.yaml` | System (cloud conditions), simulated user, cloud judge if E0b chose one |
| 5 | [E2](#e2-the-choice-of-the-dialogue-behavior) | `offline/benchmarks/bench_behavior_choice.py` | None |
| 6 | [E3](#e3-the-shared-mechanisms) (English only) | `e3_mechanisms.yaml`, `e3_noise.yaml`, `e3_failures.yaml` | Simulated user |
| 7 | [E4](#e4-real-participants-and-the-simulated-twin) | `e4_sim_es.yaml`, `real/sessions_es.yaml` | Twin: the simulated user. Sessions: none |
| 8 | [Technical check](#the-technical-check) | No script yet | None |

A cloud smoke check runs before every paid campaign. The pilot calls no cloud model; what it
cannot check (keys, pinned names, a setting a provider rejects, quotas, real latency) is checked
right before each campaign with a cloud model by one or two real calls per cloud model, through
the code the campaign uses (cents):

```sh
python3 scripts/cloud_smoke.py experiments/e1_bounded_vs_llm_led.yaml [--judge <p>/<judge>] --allow-cloud
```

(without `--allow-cloud` it lists the calls). The preflights of E0a and E0b do the same for their
simulated user; `run_e0b.py label-cloud` pings each cloud judge before it labels.

`scripts/whole_evaluation.py` runs the whole order in one run, with the commands below. Its step 8
is the tables (the scores and the judge's summaries of every experiment, without the pilot), since
the technical check has no script yet. It stops where a person must act and says what it awaits
(the debugging's exit, the review of the pilot's check, the freeze commit, the trap review, among
others). It asks for your permission before every step with paid cloud calls, showing its dry run
(the smoke checks have permissions of their own). It warns when the method's code hash has changed
since it started, and writes the run health of every step. It is the **Whole evaluation** of the
control panel's Campaign tab: [CONTROL_PANEL.md](CONTROL_PANEL.md#whole-evaluation).

## Before E0a: the debugging

Before E0a (and before the code is frozen), every strategy and mechanism is checked with the
scripted LLM in English and Spanish, then with real local models on the GPU server, and every
failure is attributed (program, prompt, simulator or model): [DEBUGGING.md](DEBUGGING.md). E0a
starts when no failure is the program's or the prompts'.

## The pilot of every experiment

After the debugging and before the freeze commit, every experiment file runs a few dialogues
through its whole chain (launch, store, score, judge, run health, provenance) to check that it is
well programmed and works, not the method's quality: its results are never reported. Each pilot
file of `experiments/pilot/` is derived from its experiment file when it is loaded and changes only
the dialogues, the models (one strong local system model for every local and cloud system cell)
and the stand-ins of the cloud providers (a local server that answers in the providers' own REST
shapes: no paid call, every cloud code path exercised). The design, the choice of the dialogues,
the stand-ins and the exit criteria: [PILOT.md](PILOT.md).

```sh
python3 scripts/run_pilot.py list            # the pilots and their dialogues
python3 scripts/run_pilot.py dry-run         # no call
python3 scripts/run_pilot.py run             # all of them, in the order of the evaluation
python3 scripts/run_pilot.py check           # the exit criteria (scripts/pilot_check.py)
```

A pilot finding may only fix a program or pipeline error; anything that would change the method,
a prompt or a scenario is recorded with its reason as a change of the method. Then come the freeze
commit, the trap review and E0a.

## E0a: the local model

Phase 1 (`run_e0a.py phase-a`) runs the critical questions offline with every candidate
(`offline/e0a_candidates.txt`); phase 2 (`phase-b`) runs whole dialogues of the finalists of each
language on the development scenarios
(`scenarios/e0a_dev.yaml`, `e0a_dev_es.yaml`), which are disjoint from those of E1–E4
([EXPERIMENTS.md](EXPERIMENTS.md#e0a-choosing-the-local-model)). The decision rule is fixed before
running and applied per language: one model for English, one for Spanish.

In stages, with `scripts/run_e0a.py` (each stage resumes where it stopped; one model on the GPU
server at a time: pulled, used, removed):

```sh
python3 scripts/run_e0a.py preflight --allow-cloud   # server free, API key, one tiny Gemini call
python3 scripts/run_e0a.py phase-a                   # every candidate; gate and finalists per language
python3 scripts/run_e0a.py phase-b --allow-cloud     # the finalists of each language (Gemini user)
python3 scripts/run_e0a.py decide                    # the rule per language: results/e0a/decision.json and .md
python3 scripts/run_e0a.py apply                     # the chosen model of each language into the files
# or preflight and phase-a in a row (then phase-b, decide and apply as above):
python3 scripts/run_e0a.py all --allow-cloud
```

`apply` replaces the provisional `qwen3:8b` with the English model in the English files (E0b, E1,
the role swap, E3) and with the Spanish model in the Spanish ones (E0b, the twin of E4,
`real/sessions_es.yaml`). Then the validation and the debugging are repeated with the chosen
model(s), before E0b.

Step by step, the same:

```sh
# Phase 1: the critical questions, offline, three repetitions per candidate
(cd offline && python3 run_models.py --config config_ollama.yaml --reps 3 --pull --remove-pulled \
    --out ../results/e0a_phase_a)
# Phase 2: whole dialogues of the finalists (--models is required: the file holds a placeholder)
python3 scripts/run_experiment.py experiments/e0a_dialogues.yaml --models $(paste -sd, results/e0a_phase_a/finalists_en.txt) --dry-run
python3 scripts/run_experiment.py experiments/e0a_dialogues.yaml --models $(paste -sd, results/e0a_phase_a/finalists_en.txt) --allow-cloud
python3 scripts/run_experiment.py experiments/e0a_dialogues_es.yaml --models $(paste -sd, results/e0a_phase_a/finalists_es.txt) --allow-cloud
python3 analysis/score_runs.py results/e0a_dialogues
python3 analysis/score_runs.py results/e0a_dialogues_es
```

The safety gate is in `results/e0a_phase_a/gate.json` and `report.md`; the latency and task success
criteria read the summary of `score_runs.py` of phase 2 (task success without the dialogues
excluded by the adherence check, 95th percentile of the latency per turn).

## E0b: the judge

Two folders of dialogues (English and Spanish), the split into development and validation
halves, the labels of each candidate judge, the two annotation sheets and the report that chooses
the judge. The commands, step by step, and the annotation guide:
[JUDGE_VALIDATION.md](JUDGE_VALIDATION.md#running-e0b); in stages with `scripts/run_e0b.py`
(`preflight`, `dialogues`, `label`, `report`, and `label-cloud` only if no local judge passes).
`dialogues` takes the model E0a decided for each language and refuses a file that names a
different one. The free judges come first: `label` makes no cloud call, and if a local judge passes in the
report, E0b ends there; otherwise `label-cloud --allow-cloud` has the cloud judges label the same
annotated utterances and `report` runs again. The result is the judge (its `--tag`, provider and
model version) used in every later `judge_runs.py` command below.

## E1 and the role swap

The go/no-go gate comes first, after E0b, since its safety measures need the validated judge. It
runs only the local cells (A and B), with the criterion of "the same" written down before running
([EXPERIMENTS.md](EXPERIMENTS.md#e1-bounded-against-llm-led-conversation)), scored and judged.
If the method performs the same as the LLM-led conversation, nothing else runs:

```sh
python3 scripts/run_experiment.py experiments/e1_bounded_vs_llm_led.yaml --only-model <the E0a model> --dry-run
python3 scripts/run_experiment.py experiments/e1_bounded_vs_llm_led.yaml --only-model <the E0a model> --allow-cloud
python3 analysis/score_runs.py results/e1_bounded_vs_llm_led
```

(`--allow-cloud` because the simulated user is a cloud model.) Then the cloud smoke check
(`scripts/cloud_smoke.py experiments/e1_bounded_vs_llm_led.yaml [--judge <p>/<judge>]
--allow-cloud`, [The order](#the-order)) and the whole campaign: on the profile `single_gpu.yaml`,
the local conditions in one lane on the GPU, the cloud conditions in a second lane at the same
time.

```sh
python3 scripts/run_parallel.py experiments/e1_bounded_vs_llm_led.yaml experiments/hardware/single_gpu.yaml --dry-run
python3 scripts/run_parallel.py experiments/e1_bounded_vs_llm_led.yaml experiments/hardware/single_gpu.yaml --check-servers
python3 scripts/run_parallel.py experiments/e1_bounded_vs_llm_led.yaml experiments/hardware/single_gpu.yaml --allow-cloud
python3 analysis/score_runs.py results/e1_bounded_vs_llm_led
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --provider <p> --model <judge> --tag <tag> --dry-run
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --provider <p> --model <judge> --tag <tag> [--allow-cloud]
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --tag <tag> --summary
```

`<p>`, `<judge>` and `<tag>` are those of the judge chosen in E0b. `judge_runs.py` labels every
utterance of the folder. A judge never counts for a system of its own family: if the chosen judge
shares the family of the cloud system model, run the next judge of the ladder that passed with
its own `--tag` and read its summary for the cloud conditions
([JUDGE_VALIDATION.md](JUDGE_VALIDATION.md#the-ladder-of-judges)).

E1 has no pilot of its own: it is piloted with every other experiment, before the freeze
([The pilot](#the-pilot-of-every-experiment)).

To confirm the judge on the campaign, the two annotators fill a blind sample (columns `human_a_*`
and `human_b_*`, 1 or 0), and then the agreement is computed. Use the same `--tag`:

```sh
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --tag <tag> --sample 200 \
    --sample-out results/e1_bounded_vs_llm_led/annotation.csv
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --tag <tag> \
    --agreement results/e1_bounded_vs_llm_led/annotation.csv
```

The role swap runs with the same commands on its own file, `e1_role_swap.yaml` (pin
`gemini/<pin>` and the simulated user's `<pin>` first); its results go to `results/e1_role_swap`.
E1 runs in English only: there is no Spanish variant (the transfer to Spanish is observed with real
users in E4).

## E2: the choice of the dialogue behavior

Offline, with the local model and a configuration in English (the language of
`offline/datasets/behavior_choice.json`). Copy `offline/config_ollama.yaml`, set
`llm.model: <the E0a model>` and `dialog.language: English`, then:

```sh
python3 offline/benchmarks/bench_behavior_choice.py --config <config in English> --method all --save
```

`--method all` runs `method`, `no_filter` and `keywords` (only `keywords` needs no LLM).
`--verbose` prints every case, for the review of each error by hand. With `--save`, the results
are written to `offline/results/behavior_choice_<method>_*.json`.

## E3: the shared mechanisms

Three experiment files, all in English, run with the commands of E1, each on its own: the
mechanisms without noise, the noise levels with the method and the LLM-led condition, and
the failures of the system's model (`e3_failures.yaml`, English only: the method on every
scenario, the LLM-led condition on the sustained failures). The model failures are injected by
the runner itself (the skill module `interaction_sim.fault_injection` and the switch
`.llm_faults.json`, hidden, of each condition's run folder); nothing has to be started for them.

```sh
python3 scripts/run_parallel.py experiments/e3_mechanisms.yaml experiments/hardware/single_gpu.yaml --dry-run
python3 scripts/run_parallel.py experiments/e3_mechanisms.yaml experiments/hardware/single_gpu.yaml --allow-cloud
python3 analysis/score_runs.py results/e3_mechanisms
# the same for e3_noise.yaml and e3_failures.yaml; the judge only on the noise:
python3 analysis/judge_runs.py results/e3_noise --provider <p> --model <judge> --tag <tag> [--allow-cloud]
```

The ablations they use are built with the workspace; after changing the method's trees, regenerate
them with `python3 ablations/interaction_ablations/make_ablations.py` and rebuild
([README](../README.md#ablations)).

## E4: real participants and the simulated twin

The simulated twin, before the sessions:

```sh
python3 scripts/run_parallel.py experiments/e4_sim_es.yaml experiments/hardware/single_gpu.yaml --dry-run
python3 scripts/run_parallel.py experiments/e4_sim_es.yaml experiments/hardware/single_gpu.yaml --allow-cloud
python3 analysis/score_runs.py results/e4_sim_es
python3 analysis/judge_runs.py results/e4_sim_es --provider <p> --model <judge> --tag <tag> [--allow-cloud]
```

The sessions with the 16 participants: the checks, the plans, the trials, the review of the
transcripts and their scoring are in [REAL_SESSIONS.md](REAL_SESSIONS.md). The sessions are
scored with the same scripts, on the folder of their campaign
(`~/.ros/interaction_real/<campaign>`), and compared with `results/e4_sim_es` scenario by
scenario.

## The technical check

No script yet: it will replay offline the narrow questions logged in condition A of E1, with and
without the JSON schema
([EXPERIMENTS.md](EXPERIMENTS.md#technical-check-schema-constrained-against-free-text-answers)).

## Where the results go

Nothing under `results/` is part of the repository.

| What | Where |
|---|---|
| The dialogues of an experiment | `results/<name>/runs/<model>/<condition>/<scenario>__<persona>__r<k>.json` (`<name>` is the `name` of the experiment file; `:` and `/` of the model become `_`); one line per dialogue in `results/<name>/runs.jsonl`; `--out` changes the folder |
| What each lane ran | `results/<name>/experiment_<lane>.yaml` (`experiment.yaml` without a lane), with its servers and models; written also by `--dry-run` |
| The log of each lane | `results/<name>/lane_<lane>.log` (`run_parallel.py`) |
| Scores | `results/<name>/scores.csv` (`score_runs.py`) |
| The judge's labels | `results/<name>/judgments_<tag>.jsonl` (`judgments.jsonl` without `--tag`) |
| The validation of the judge | `results/e0b_judge_dialogues*/e0/`: `split.json`, `sheet_a.csv`, `sheet_b.csv`, `key.json`, `report.json` |
| The offline benchmarks | `results/model_selection/m1/` (`run_models.py --out`): `<model>/rep<k>/<benchmark>.json` and `summary.csv`; `offline/results/` (`--save`) |
| The sessions with real participants | `~/.ros/interaction_real/<campaign>/` ([REAL_SESSIONS.md](REAL_SESSIONS.md#what-is-stored)) |
| The output of the panels' jobs | `~/.ros/interaction_panel/` |
