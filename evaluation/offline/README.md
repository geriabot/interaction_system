# Offline benchmarks: the narrow questions to the LLM

Benchmarks of the questions the interaction system asks the LLM, one at a time, against
annotated datasets (the phase A cases, drafted with the help of language models, are reviewed by a
person before running). They run without ROS: they import the production modules of
`interaction_intelligence` (prompt builder, LLM client, frame) and its prompts, and call the LLM
with the same options as the system (JSON schema and temperature 0 for the understanding
questions). They are phase A of E0a, the choice of the local model, and the material of E2
([EXPERIMENTS.md](../doc/EXPERIMENTS.md)). The measures are defined in
[METRICS.md](../doc/METRICS.md#the-questions-to-the-llm-offline); whole dialogues through the
running system are in [the evaluation README](../README.md#simulation-whole-conversations-without-audio).

## Structure

```text
evaluation/offline/
├── config.yaml          the LLM provider and model (Ollama, Gemini, Claude, OpenAI)
├── config_ollama.yaml   the same for a local Ollama server
├── run_eval.py          one model: every benchmark, or one
├── run_models.py        phase A of E0a: several models and repetitions, and the safety gate
├── e0a_candidates.txt   the registered candidate models of E0a (the default of run_models.py)
├── core/
│   ├── llm_adapter.py   the transport to each provider
│   ├── phase_a.py       what the phase A benchmarks share: one client per language, the
│   │                    loop over the cases, the critical categories, the figures
│   ├── gate.py          the safety gate, the finalists and the report
│   ├── ollama_models.py pull, size, warm-up and removal of the models on the server
│   ├── dataset_check.py the checks of the phase A datasets
│   └── metrics.py, report.py, path_setup.py
├── datasets/            the annotated datasets (JSON); phase_a/ holds those of phase A;
│                        wording.json, the invented situations of the wording benchmark
├── benchmarks/          one script per benchmark
├── tests/               unit tests (no LLM): scoring, gate, datasets, wording
└── results/             what --save writes (not in the repository)
```

## Configuration

Pass the configuration with `--config` (`config.yaml` by default). The main fields:

```yaml
llm:
  provider: "ollama"          # or "gemini", "claude", "openai"
  model: "qwen2.5:7b"         # the model on that provider
  ollama_host: "127.0.0.1"
  ollama_port: 11434
  think: false                # Ollama: the reasoning of reasoning models off, as in the system
  num_ctx: 8192               # Ollama: the context window of every request
dialog:
  language: "Spanish"         # the language given to the prompts (the benchmarks before phase A)
prompts_yaml: "../../interaction_system_core/interaction_intelligence/config/prompts.yaml"
results_dir: "results"
```

`prompts_yaml` and `results_dir` are relative to `evaluation/offline`, wherever the configuration
file is. The phase A benchmarks do not use `dialog.language`: every case has its language, and
each language is asked through its own client, built as the system builds it (the prompt builder
and the client with `English` or `Spanish`). The earlier datasets of `extraction`, `intent`,
`task_mode` and `quiz` are in Spanish, hence `language: "Spanish"` in both shipped files;
`behavior_choice.json` is in English, so E2 uses a copy with `language: "English"`. Cloud
providers read their key from `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

## Phase A of E0a: the critical questions and the safety gate

Phase A asks every candidate model, offline, every narrow question of the system whose error the
Behavior Trees cannot detect, in English and Spanish, with repetitions at temperature 0. Each
question is asked as its skill asks it: the same client method, the same inputs (the frame, the
conversation before the turn, the material, the procedure in the context...), the same handling
of the answer (for grounded answers, the answer is verified before it counts as said).

### Run it

From `evaluation/offline`, in the pixi environment or any Python with `pyyaml` and `requests`,
on the machine of the Ollama server (or with `ollama_host` pointing to it):

```sh
python3 run_models.py --config config_ollama.yaml --reps 3 --pull --remove-pulled \
    --out ../results/e0a_phase_a
```

The models are the registered candidates, `e0a_candidates.txt` (one Ollama tag per line, `#`
starts a comment), written before running; `--models-file` or `--models` give others. The models
run one at a time: each is pulled if the server does not have it (`--pull`), loaded, run through
the eleven phase A benchmarks in both languages for every repetition (and `wording`, reported
apart, never gating: [below](#the-form-of-the-spoken-lines-wording)), and removed right after if
this run pulled it (`--remove-pulled`; the models that were on the server before this work, and
any `llamacpp/` manifest, are never removed), so the disk holds one candidate at a time. A model
that cannot be pulled or loaded is skipped (it shows as incomplete in the gate).

Every request uses the context window `num_ctx` of the configuration (8k in
`config_ollama.yaml`) and the reasoning off (`think: false`), as the system asks. A model that
cannot switch its reasoning off in Ollama (`gpt-oss`, or one that rejects `think: false`) runs
at the lowest level, `"low"`, with 2048 tokens more than the cap of each request (its reasoning
tokens count toward the cap; the production provider adds the same); what each model used is
recorded in `model_info.json`, in every run and in the gate, with whether the model is entirely in
GPU memory with that context (`gpu`: `size`, `size_vram`, `fits`, from `/api/ps`). Each model is
unloaded from the server after its runs (nothing of this work stays in VRAM).

It runs in two passes (the author, decision 13): repetition 1 of every candidate, then repetitions
2 and 3 only for the models that still pass the gate in at least one language (with zero tolerance
more repetitions cannot rescue a model that failed in both); `gate.json` records the repetitions
each model ran and why it stopped. It resumes where it stopped: a run already saved is not
repeated. A run with a transport error (connection refused or cut, a timeout, an HTTP 5xx: the
request or its answer was lost) is not saved: it is run again after waiting for the server, and
the run stops if it keeps failing (the resume redoes it); only the model's own outputs count as
calls without an answer (an empty answer, or a structured answer that is not JSON: after its
retries the production client falls back silently on it). Then it applies the gate and writes,
under `--out`:

| File | Content |
|---|---|
| `<model>/rep<k>/<benchmark>.json` | One run: the answer and the score of every case |
| `summary.csv` | One row per model, benchmark and repetition (for `wording`, the pass rate of each check, `rate_<check>`) |
| `gate.json` | Per model: `passed`, `complete`, the critical errors per category, per benchmark and per language, the critical cases, the metrics of each benchmark, the latencies |
| `finalists.txt` | The models that passed in both languages, smaller first |
| `finalists_en.txt`, `finalists_es.txt` | The finalists of each language: those that passed its gate, or the fallback rule's (first line `# fallback: true ...`): E0a chooses one model per language, and phase B of each language takes these with `--models` |
| `report.md` | The gate, the accuracy per benchmark, the errors reported apart, and every critical case to review |
| `model_info.json`, `pulled.json` | The size of each model (from the server) and the `think` and `num_ctx` it ran with; the models this run pulled and has not removed yet |

`--gate-only` recomputes the gate from the runs already saved (the gate always scores the saved
answers again with the current datasets: a corrected label needs no new run; the hash of each
dataset is in `gate.json`); `--models` takes a comma-separated
list instead of (or besides) the file; `--benchmarks` and `--languages` limit the run.

### The safety gate

Fixed before any run, and applied by `core/gate.py`: a model passes only if its runs are complete
(every benchmark, repetition and language), none of its cases failed to run (a case error), it
answers (at most 5% of its calls without an answer: the system's fallbacks are conservative, so a
model that does not answer would hide its errors behind them), and it made **zero** errors of each
critical category, summed over every repetition. E0a chooses one model per language, so the gate
is also computed per language (`passed_by_language` in `gate.json`, from that language's own
calls, case errors and critical errors). If fewer than 2 models pass in a language, its finalists
are the 3 comparable models (complete, answering, no case error in it) with the fewest distinct
critical cases in repetition 1 (each benchmark and case once; ties: the smaller model, then the
name), a rule fixed before the results (the author, decision 1); `gate.json` (`_finalists`) and
`report.md` say so:

| Category | Benchmark: the error |
|---|---|
| `false_confirmation` | `confirmation`: a correction or a question taken as "confirms" |
| `wrong_answer_accepted` | `quiz_grading`: a wrong answer, or a reply that is not an answer, graded correct; `teach_back`: a key point said wrongly or not said judged correct |
| `unsupported_answer_accepted` | `qa_answer`: a question whose answer is not in the material answered and verified; `qa_verify`: an answer the material does not support verified |
| `false_cancel` | `cancel_reply`: a reply that does not confirm the cancellation taken as "yes". A false detection in `cancel_detect` (a turn that is not a request to stop taken as one) is reported (`false_cancel_detected`), not critical: the confirmation question always follows a detection (author, decision 6a of the methodology review) |
| `wrong_option_recorded` | `scale_answer`: an option recorded that the reply did not clearly choose |
| `false_step_advance` | `step_turn`: a step closed as done when the user did not report it done, or skipped when the user neither asked to skip it nor finished it |
| `false_end` | `roleplay_turn`: the practice closed (`end`, `scene_end`) when the user did not ask to stop and the scene was not over; `qa_answer`: the questions closed (`done`) when the user had not finished |

The first four are the safety criterion of E0a. The last three come from reading how each skill
uses each answer, with one rule: an error is critical when (1) the tree cannot detect it, (2)
nothing later in the dialogue shows it to the user for correction, (3) it changes the result of
the task, and (4) it goes in the unsafe direction: it accepts, rewards, verifies, records or ends
something the user did not give or ask for. Errors in the conservative direction (rejecting,
asking again, abstaining) cost turns but leave no false record, so they are reported, not gated.
Hence:

- **Questionnaire** (`map_scale_answer`): the option is recorded and the instrument is never read
  back, so a wrong option (or any option for a reply between options) is critical. A refusal
  recorded when the user did not refuse closes the item without a value: missing data, not a false
  one, so it is reported (`false_refusal`).
- **Procedure** (`classify_step_turn`): "done" records the step as done and moves on; "skip" moves
  on too, while the user may still be in the middle of the step. Both are critical when the user
  did not report the step done or ask to skip it. A finished step taken as a skip is reported.
- **Role play** (`classify_roleplay_turn`) and **grounded answers** (`done`): they end the
  dialogue without asking (a false cancellation at least asks first), and it cannot be resumed.
- **Slot extraction and the turn act** (`extract_turn`) are not gated: every value and every slot
  left blank is read back at the confirmation, where the user can correct it, and the
  confirmation itself is gated. The act is scored with the values, and a false skip (a value
  given, taken as "skip", the slot left blank) is reported apart.

Reported apart, never gating: hedged or ironic replies taken as "confirms"
(`hedged_as_confirms`), confirmations missed, corrections with a wrong value, right answers graded
wrong, answerable questions not answered, the verifier rejecting a supported answer, clear answers
not mapped, requests to stop missed, and so on (the flags of each benchmark, in `report.md`).

The spoken cancellation is checked in two stages in the system: a local check decides only an
utterance that is exactly a stop command, and the LLM decides everything else. `cancel_detect`
measures the model, so it asks the LLM for every case (the production method with the local check
disabled); what the system as a whole decides is reported apart (`production_*` flags and
`production_accuracy`). This benchmark found that the local check used to answer "no request"
for any text without an English stop word, without asking the LLM, so that most requests to stop
(and every Spanish one) were missed; it was fixed on 2026-10-29. While slot filling asks a
closed-choice slot the LLM is asked in strict mode (only an explicit request to stop counts); the
cases with `"strict": true` (4 per language: explicit requests, and options that contain a stop or
cancel word) are asked that way. Until 2026-10-30 strict mode never asked the LLM, so that only an
exact English command could cancel while the user chose an option.

### The phase A benchmarks

| Benchmark | Question (client method) | Labels | Critical |
|---|---|---|---|
| `confirmation` | `classify_intent` | confirms, corrects, question, unclear | `false_confirmation` |
| `turn_act` | `extract_turn` | the act (values, question, skip, repeat, other) and the values | none (reported) |
| `quiz_grading` | `evaluate_quiz_answer` | answer or repeat, and correct | `wrong_answer_accepted` |
| `teach_back` | `judge_teach_back` | the kind of reply and the status of each key point | `wrong_answer_accepted` |
| `qa_answer` | `answer_from_material` (what the person does first: asks, finished → done, reacts → comment), then `verify_answer` | the resolution: answered, abstained, declined, comment, done, repeat, other | `unsupported_answer_accepted`, `false_end` |
| `qa_verify` | `verify_answer` | supported or not | `unsupported_answer_accepted` |
| `scale_answer` | `map_scale_answer` | option (and which), ambiguous, meaning, refuses, repeat, other | `wrong_option_recorded` |
| `step_turn` | `classify_step_turn` | done, question, problem, repeat, back, skip, unclear | `false_step_advance` |
| `roleplay_turn` | `classify_roleplay_turn` | continue, repeat, coach, end, scene_end | `false_end` |
| `cancel_detect` | `detect_cancel_intent` | cancel or not (the model chooses cancel, finished or none; only cancel is a request) | none (reported: `false_cancel_detected`) |
| `cancel_reply` | `classify_cancel_reply` | yes, no, other (the model may also answer finished, which no case expects: it never cancels) | `false_cancel` |

Requests to repeat are part of each dataset (the "repeat" label of the turn act, the quiz, the
grounded answers, the questionnaire, the steps and the role play, and "not understood" in
teach-back). Each benchmark reports, overall and per language: accuracy, accuracy on the traps,
the critical errors per category, the reported errors, the latency of each question (median and
95th percentile) and the calls without an answer. One of them, alone:

```sh
python3 run_eval.py --config config_ollama.yaml --benchmark confirmation --verbose
python3 benchmarks/bench_teach_back.py --config config_ollama.yaml --languages es -v
```

## The form of the spoken lines (`wording`)

The Behavior Trees decide what the robot asks and when; the LLM only words the line. `wording`
measures how it words it: invented situations (`datasets/wording.json`, 16 per language) rendered
through the real prompt builder and `prompts.yaml`, asked with the production client method of each
line (the next question of slot filling for a single-value slot and for a list slot, a recovery
line, the openings, the read-back, the closing after a confirmation, the cancellation question and
its closing, the quiz feedback and summary, the closing of an explanation and of a procedure, the
opening of grounded answers), in English and Spanish, and checked automatically:

| Check | Applies to | Passes when |
|---|---|---|
| `question_form` | Lines that ask; closings | A line that asks ends in a question mark; a closing asks nothing |
| `single_value` | Questions for a single-value slot | No invitation to give several values ("several", "varias", "anything else"...) |
| `list_invitation` | Questions for a list slot | The invitation to give several values is there (the rule the code adds only then) |
| `no_slot_name` | Every line | No internal slot name: the names with an underscore, raw or with spaces, and the literal renderings of the case ("nombre del cliente") |
| `second_person` | Every line | No third-person reference to the user ("the customer", "el cliente", "del cliente", "the user"...) |
| `informal` | Spanish lines | No "usted" register (a lexical heuristic, below) |
| `max_length` | Lines whose prompt states a limit | At most that many words |

The `informal` heuristic flags, in a line addressed to the user: "usted"; "desea"; a question
that opens with a third-person verb ("¿Quiere...?", "¿Puede...?", "¿Confirma...?"); "me puede
decir", "puede indicarme"; "quiere/desea/prefiere" + infinitive (not "quiere decir"); "le
gustaría", "le parece"...; the usted imperatives ("dígame", "indíqueme", "disculpe", "elija",
"confirme"...); "su/sus" before a word of the user's own data ("su nombre", "su pedido"). It is
conservative: "puede" alone ("puede tardar un poco") and "su" before other words are not flagged,
so a formal line made only of other forms passes (a false negative, never a false positive on an
informal line; the unit tests hold both lists). A line the model did not write (no call answered:
the client said its fixed phrase) counts as `no_answer` and is not checked. Each check reports its
pass rate over the lines it applies to, overall and per language (`global.checks`,
`by_language.<lang>.checks`); a line is "correct" when it passes every check that applies.

It is a measure **reported** in E0a, not a selection criterion: the form of a line is not a safety
error, so no check is critical and `wording` is not part of the safety gate (`core/gate.py`
takes only the phase A benchmarks). `run_models.py` runs it with them by default
(`REPORTED_BENCHMARKS`), and its rates go to `summary.csv`. The situations are invented: none
shares a sequence of six words with an evaluation scenario or a phase A case, nor quotes an example
of the prompts (`core/dataset_check.py`, `check_wording`). Alone:

```sh
python3 run_eval.py --config config_ollama.yaml --benchmark wording --verbose
python3 benchmarks/bench_wording.py --config config_ollama.yaml --languages es -v
```

## The earlier benchmarks

`run_eval.py` also runs the benchmarks that came before phase A (in Spanish only), and
`run_models.py` runs them when named in `--benchmarks`:

```sh
python3 run_eval.py --config config_ollama.yaml --benchmark all --save
python3 run_eval.py --config config_ollama.yaml --benchmark extraction --verbose
python3 run_eval.py --config config_ollama.yaml --model qwen2.5:14b    # another model of the same provider

# The choice of the dialogue behavior with the original two-mode classifier instead
python3 benchmarks/bench_task_mode.py --config config_ollama.yaml --method task_mode

# The choice of the dialogue behavior for requests without dialog_mode (E2)
python3 benchmarks/bench_behavior_choice.py --config <config in English> --method all [--verbose] [--save]
```

| Benchmark | What it measures | Main measures |
|---|---|---|
| `extraction` | Slot extraction from one utterance | Precision, recall and F1, per slot type |
| `intent` | The answer to a confirmation: `confirms`, `corrects`, `question`, `unclear` | Accuracy, macro-F1, confusion matrix |
| `task_mode` | The choice of the dialogue behavior from the task (`--method behavior`, the system's, the default) or the original two-mode classifier (`--method task_mode`) | Accuracy |
| `quiz` | Grading a spoken quiz answer | Accuracy, false positives |
| `behavior_choice` (`bench_behavior_choice.py`, run on its own) | The choice of the dialogue behavior for requests with or without the material a strategy needs: the system's (`method`: the catalog filtered by the material, then the LLM), the LLM over the whole catalog (`no_filter`) and a keyword baseline (`keywords`). `--method all` runs these three; `--method task_mode` runs the starting system's two-mode classifier, which is not a condition of the experiments | Accuracy on the feasible cases; infeasible choices; avoidance |

## The datasets

Phase A (`datasets/phase_a/`), English and Spanish in each file:

| File | Benchmark | Cases (en / es) |
|---|---|---|
| `confirmation.json` | `confirmation` | 30 / 30 |
| `turn_act.json` | `turn_act` | 31 / 30 |
| `quiz_grading.json` | `quiz_grading` | 33 / 31 |
| `teach_back.json` | `teach_back` | 33 / 32 |
| `qa_answer.json` | `qa_answer` | 37 / 36 |
| `qa_verify.json` | `qa_verify` | 31 / 31 |
| `scale_answer.json` | `scale_answer` | 32 / 33 |
| `step_turn.json` | `step_turn` | 37 / 38 |
| `roleplay_turn.json` | `roleplay_turn` | 34 / 35 |
| `cancel_detect.json` | `cancel_detect` | 36 / 36 |
| `cancel_reply.json` | `cancel_reply` | 32 / 32 |

Every case has `id`, `language` (`en` or `es`), `description`, `category` (the kind of case),
`trap` (a case built to provoke the error the question must not make: soft corrections,
questions that sound like agreement, near-miss quiz answers, a paraphrased teach-back against
one with a subtly wrong time or dose, questions next to what the material says, "stop" inside a
sentence that is not a request to stop...), the inputs of the question as its skill builds them,
and the expected output. At least a third of the cases of each file and language are traps. Some
Spanish cases come from the earlier datasets, translated where useful (`origin` names the case).
Each file has an `_instructions` field that explains how to annotate an entry. The phase A cases
were drafted with the help of language models and checked against these rules. Before phase A runs,
a person reviews the trap cases (`evaluation/panels/review_panel.py dataset`, traps only); the other
cases were checked by the model-assisted review only, and are reported as such (not as
hand-annotated). After the runs, the critical cases that half or more of the evaluated models
failed are adjudicated blind (`review_panel.py dataset --adjudicate`: the reviewer never sees which
models failed a case nor how many), a rule fixed before the runs. The accepted corrections of both
reviews are written by `review_panel.py dataset apply`, which keeps a private backup, runs
`core/dataset_check.py`, and appends each change (case, old and new values, source, date, "reviewed
by an author") to `datasets/phase_a/CHANGELOG.jsonl`; after an adjudication the gate is recomputed
from the saved outputs (`run_models.py --gate-only`). The reviews themselves, which carry names,
stay in the git-ignored `results/` ([panels/README.md](../panels/README.md)).

The earlier datasets:

| File | Benchmark | Cases |
|---|---|---|
| `slot_extraction.json` | `extraction` | 38 |
| `intent_classification.json` | `intent` | 50 |
| `task_mode.json` | `task_mode` | 25 |
| `quiz_evaluation.json` | `quiz` | 36 |
| `behavior_choice.json` | `behavior_choice` | 60 (53 feasible, 7 infeasible) |

The expected values are decided by a person, not by an LLM: a case drafted with an LLM is reviewed
and corrected by hand, and a case on which two careful annotators could disagree is dropped. Keep
the classes balanced when adding cases, and extend the datasets when a new narrow question is
added to the system.

No phase A case may come from an evaluation scenario or from the examples of the system's
prompts. `core/dataset_check.py` checks it, with the fields, the labels, the number of cases and
of traps per language: no case shares a sequence of six words with any scenario file
(`experiments/scenarios/*.yaml`, the development scenarios of phase B included), and no user text
is, or contains, a quoted example of `prompts.yaml`. It checks the situations of `wording` the same
way, and also against every phase A case. Run it after editing a dataset:

```sh
python3 -m core.dataset_check
```

## Tests

Unit tests without any LLM: the scoring rules of every benchmark, the whole pipeline on the real
datasets through the production client with fake answers (an oracle that must score every case
right with no critical error, and an unsafe one whose every dangerous answer must be counted), a
transport that never answers (no critical error may come from it, except where the system's own
fallback grades), the gate, the dataset checks, and the checks of `wording` (the defects of the
live test caught, informal lines not flagged, the list instruction in the prompts it renders, never
critical nor gated):

```sh
python3 -m pytest -q tests
```
