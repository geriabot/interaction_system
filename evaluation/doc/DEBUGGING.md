# Debugging before E0a

When the method is evaluated, a failure must come only from the model, never from the program,
its logic or the method's prompts. This debugging establishes that before the first experiment
(E0a) and before the code is frozen. It has two layers and one rule to attribute every failure,
and it ends with an exit criterion. Nothing here is reported as a result.

| | Layer 1: without a model | Layer 2: with real models |
|---|---|---|
| The model | The scripted LLM, which gives the correct answer to every question | The local system models (Qwen 3 8B, then Llama 3.1 8B) and an open simulated user (Gemma 3 12B), all on the GPU server |
| What a failure means | An error of the program or its logic (there is no model to blame) | Any of the four causes below: each flagged dialogue is attributed |
| Where | This computer, free | The GPU server, nothing in the cloud |
| Files | `debug/coverage.yaml` and the scripted tests it names | `experiments/debug/*.yaml` (debugging files, not experiments) |
| Gate | Every test passes; no row of the matrix without a test in both languages or a reason | No dialogue attributed to the program (C) or the prompts (P) |

## Layer 1: the coverage matrix and the scripted tests

`debug/coverage.yaml` lists every branch of every strategy and every shared mechanism (silence,
cancellation, no progress, endings, fillers, barge-in, limits, the wait for the model, the
fallbacks when the model fails), with its expected outcome and the tests that cover it, and the
languages each test runs in. Each row has a short id whose prefix names its mechanism or strategy
(e.g. S silence, C cancellation, SF slot filling). The matrix also has the model-failure scenarios
of E3 (`e3_failures.yaml`), scripted.

The tests run with the scripted LLM (`simulation/interaction_sim/interaction_sim/scripted_llm.py`):
a deterministic stand-in that answers every question correctly by fixed rules on the user's
words, so that each test steers one branch of the trees on purpose. They run in English and in
Spanish:

- `test_dialogues.py` and `test_model_failures.py` run every test twice, with the system in each
  language (its language file): the scripted LLM's tags are the same, what the system says or
  reads itself (silence prompts, fillers, exact stop commands, the fixed phrases of the
  fallbacks) is the language's;
- `test_bilingual.py` runs the shared mechanisms in every strategy (cancellation confirmed and
  denied, silence, turn limit, breakdown, error closing, sustained model failure) and each
  strategy's fixed phrases in both languages, with a scripted LLM that says the real phrases of
  the language file;
- `test_spanish_model_failures.py`, the unit tests of `interaction_intelligence/test/`
  (`test_model_down.py`: the noise filter, the fallback reading of a yes or no, the model known to
  be down; `test_client_reading.py`: the client reads a correct answer of the model as the program
  needs it) and the oracle of phase 1 of E0a (`offline/tests/test_phase_a.py`: every expected
  answer of the critical questions is read right through the production client).

```sh
cd <workspace> && pixi shell && source install/setup.bash
cd src/interaction_system/evaluation
python3 debug/coverage_matrix.py --gaps                 # every test named exists; the rows without a test
ROS_DOMAIN_ID=<free id> python3 -m pytest -q simulation/interaction_sim/test
cd ../interaction_system_core/interaction_intelligence && python3 -m pytest -q test
cd ../../evaluation && python3 -m pytest -q offline/tests debug
```

A failure here is an error of the program or its logic. A clear bug is fixed with a regression
test in this layer. A fix that changes what the method does is a change of the method, not a bug
fix, so its reason is recorded before it is made.

## Layer 2: the debugging files

Five files in `experiments/debug/`, one repetition each, the method only (the bounded
conversation, default parameters), both system models listed (run one at a time):

| File | Scenarios | Personas | Dialogues per model |
|---|---|---|---|
| `debug_dev_en.yaml` | `e0a_dev.yaml` (every strategy, 3 each) | E0a's three | 63 |
| `debug_dev_es.yaml` | `e0a_dev_es.yaml` | E0a's three | 63 |
| `debug_mechanisms_en.yaml` | `e3.yaml` (the shared and the strategies' own mechanisms, provoked by events) | cooperative | 38 |
| `debug_mechanisms_es.yaml` | `e3_es.yaml` | cooperative | 38 |
| `debug_failures_en.yaml` | `e3_failures.yaml` (model failures injected by code) | cooperative | 19 |
| | | | **221** |

The development scenarios run with E0a's personas because E0a runs them next; the mechanism and
failure scenarios with the cooperative persona only, because their events decide the mechanism
and a plain user makes the first wrong turn easier to find. The limits are those of the
experiment each file debugs. Every file has `record_model_calls: true`: every call of the system's
model is stored in its dialogue's record (`model_calls`: the user turn, the skill, the question,
its prompt and schema, the answer or the error, the latency), through a recorder loaded like the
injector of model failures (`simulation/interaction_sim/interaction_sim/call_log.py`; the
method's code is not modified). It is off in the experiment files: the prompts make each record
several times larger.

The debugging files are in their own folder: the control panel lists only `experiments/*.yaml`,
so they are in no campaign, and their results go to `results/debug_*`, which no table reads.

Layer 2 takes roughly 5 hours per system model on a 24 GB GPU (between about 3.5 and 8). This
assumes about 10 turns per dialogue at about 8 s per turn with the system's model and the
simulated user on the same GPU, plus the planned silences (60 s each in the development scenarios,
15 s in E3's) and the injected hangs. Both models together take about a working day of the GPU
server.

### Running it when the GPU server is free

Only when the server is free (no other work on its GPU: [GPU_SERVER.md](../../docs/GPU_SERVER.md#on-a-shared-server)).

1. On the server, start Ollama with room for two models (the system's and the simulated
   user's, both resident): `OLLAMA_MAX_LOADED_MODELS=2`, as in
   [GPU_SERVER.md](../../docs/GPU_SERVER.md#starting-it). Pull `qwen3:8b`, `llama3.1:8b` and
   `gemma3:12b` if they are not there (the runner also pulls a missing one).
2. On this computer, open the tunnel ([GPU_SERVER.md](../../docs/GPU_SERVER.md#the-tunnel)).
3. Run each file with the first model, on the server's lane of the `single_gpu` profile (its
   open simulator is Gemma 3 12B on the same GPU); the runner checks that the server is free
   first:

```sh
cd <workspace> && pixi shell && source install/setup.bash
cd src/interaction_system/evaluation
for f in dev_en dev_es mechanisms_en mechanisms_es failures_en; do
  ROS_DOMAIN_ID=<free id> python3 scripts/run_experiment.py experiments/debug/debug_$f.yaml \
    --only-model qwen3:8b --hardware experiments/hardware/single_gpu.yaml --lane local
done
# --dry-run first to see the counts; --show prints a dialogue as it goes
```

   On a shared GPU server, add `--keep-loaded` so that the model stays loaded between the files
   (otherwise each file unloads it at its end, and a job waiting for free GPU memory may take the
   gap); unload it when the last file ends. To change models without a gap, load the second one
   before unloading the first (the server must allow three loaded models: the two system models
   and the simulator).

4. Stop: the runner unloads its models at the end of each file; then stop the server or
   unload what is left, and close the tunnel ([GPU_SERVER.md](../../docs/GPU_SERVER.md#on-a-shared-server)).
5. Triage (below). When the exit criterion holds for Qwen 3 8B, the same with
   `--only-model llama3.1:8b` (into the same results folders: the comparison reads both).

## The attribution rule

Every flagged dialogue is read at its first wrong turn, with the model calls of that turn,
and attributed to one cause:

| Class | Cause | The test | Accepted? |
|---|---|---|---|
| **C** | Program or logic | The tree or the code did the wrong thing although the model's answers were correct | No: fix it, add a regression test to layer 1, run the affected scenarios again |
| **P** | Prompt or method | The model's input was wrong, incomplete, ambiguous, or asked for something unreasonable | No: fix the prompt or the method (a change of the method, recorded with its reason), add a test where possible, run again |
| **S** | Simulator | The simulated user broke its goal (said something the goal did not plan) | Excluded, as the adherence rule excludes it in the experiments |
| **M** | Model | Clear and correct input, wrong answer | Yes: this is what E0a and the experiments measure |

Examples (invented, to show the reasoning):

- The user gives the size at the read-back, the model's extraction returns `{"size": "small"}`,
  and the robot reads back the old size: **C** (the program did not apply a correct answer).
- The procedure's step prompt contained the whole procedure, and the model's line mixed two
  steps: **P** (the input invited the error).
- The user's goal says "a carrot cake" and the simulated user orders a chocolate cake: **S**.
- "No, actually the 14th" at the read-back is classified by the model as a confirmation, with the
  question, the read-back and the reply all clear in the prompt: **M**.

Two checks separate the classes when the reading is not enough:

- **Oracle replay** (C or not): the same dialogue again, with the wrong answer replaced by the
  correct one, every earlier answer pinned to what it was, and the user's lines up to that turn
  the same; after it, the model and the simulated user go on live. If the dialogue still goes
  wrong, the error is the program's.
- **The other family** (P or M): the same input to the other system model. The same wrong answer
  from both families to the same input points to the prompt.

## Triage

```sh
python3 debug/triage.py flag results/debug_dev_en           # flags, review sheets, the sheet to fill
python3 debug/triage.py compare results/debug_dev_en        # both models side by side, turn by turn
python3 debug/triage.py summary results/debug_dev_en        # counts and the exit criterion
```

`flag` scores every dialogue (`analysis/score_runs.py`, the mechanism and strategy checks, the
simulated user's adherence) and flags the failed ones: task not achieved, a planned ending,
mechanism or strategy check not as planned (not the descriptive columns, e.g.
`key_points_from_llm`: `strategy_checks.DESCRIPTIVE`), a failed skill, a timed-out dialogue, a simulated
user excluded by the adherence rule, an unplanned cancellation, model calls that timed out where
no failure was planned. Bugs hide in passes too: it also samples one passing dialogue per
strategy and language (`--spot N`, `--seed S`). Each one gets a review sheet,
`triage/sheets/<model>__<condition>__<dialogue>.md`: the plan (task, user's goal, expected
result, events, injected failures), then turn by turn what the user did, the tree's decisions
(the skills asked and their labels), every model call (question, skill, latency, prompt, answer
or error) and the robot's line. The reviewer fills `triage/classification.yaml`: per dialogue
the class (C, P, S, M; `OK` for a pass that is right, or a flag that is not a failure), the first
wrong turn and a note (for C and P, the fix and its regression test). Running `flag` again keeps
what was filled.

The two checks:

```sh
# Oracle replay of one dialogue: turn K, question Q, the raw answer a correct model gives
python3 debug/replay.py plan results/debug_dev_en/runs/<model>/bounded/<dialogue>.json \
  --turn K --question extract_turn --answer '{"act": "values", "extractions": [...]}'
ROS_DOMAIN_ID=<free id> python3 scripts/run_experiment.py <printed replay.yaml> \
  --hardware experiments/hardware/single_gpu.yaml --lane local --out <its folder>
python3 debug/replay.py report <the record .json> <its folder>
# The same input to the other family (asks the model on the GPU server, then unloads it)
python3 debug/replay.py same-input <the record .json> --turn K --question Q --model llama3.1:8b
```

The replay pins every answer of turns 0 to K through the injector of model failures (a fault of
kind `answer`). Its limits: a question asked several times in one turn gets its recorded answers
in order; a call that timed out is pinned as an error (the same fallback, without the wait); a
call the client did not make (the reply's budget spent) was not recorded and may be made in the
replay.

## Exit, then freeze

The exit criterion is a full pass of the five files with no C and no P, first with Qwen 3 8B,
then with Llama 3.1 8B (another family, so that the prompts are not tailored to Qwen):
`triage.py summary` says `HOLDS` for the model in every results folder. Then comes one pass with a
third family that was not used to fix anything and is held out for this check, Mistral NeMo 12B.
Gemma is not used, because it is the simulated user and would talk to itself, nor Mistral 7B,
which is older and weaker and whose failures would mostly be its own. A failure that Mistral NeMo
shares with the other two points to the prompt or the program and is fixed; a failure of Mistral
NeMo alone is the model's (M) and changes no prompt, so that the check stays independent. The
simulated user stays Gemma 3 12B for all three, since changing it as well would change two things
at once. Every C or P fix gets a regression test in layer 1, and the affected scenarios are run
again (delete their records and resume). Then come the freeze commit, the review of the trap
cases of phase 1, and E0a.

After E0a, the development file in English (`debug_dev_en.yaml`) is run once more with the model
chosen for English, before E0b: the prompts were fixed with other families, and a prompt failure
that only this model provokes would bias the experiments that use it. The model chosen for Spanish
is not debugged again. The method is the same in both languages (the same trees, code and prompts,
which ask for the reply in `{language}`); what depends on the language is program (the fixed
phrases, the stop words), which the scripted tests of layer 1 already run in Spanish; and what a
pass in Spanish with that model could add are its own failures (M), which change no prompt. Its
Spanish dialogues of E0a are the check that the method works with it.
