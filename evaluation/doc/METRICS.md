# Metrics

Every measure of the evaluation: its definition, its unit, the script that computes it and the
experiments that use it. What each experiment asks and how its results are decided is in
[EXPERIMENTS.md](EXPERIMENTS.md); the commands that produce the files read here are in
[REPRODUCING.md](REPRODUCING.md).

Contents: [Which measure in which experiment](#which-measure-in-which-experiment) ·
[Task success](#task-success-score_runspy) · [The judge's labels](#the-judges-labels-judge_runspy) ·
[Agreement with people](#agreement-with-people-judge_validationpy) ·
[The questions to the LLM, offline](#the-questions-to-the-llm-offline) ·
[Sessions with real participants](#sessions-with-real-participants-real_sessionspy) ·
[The shared mechanisms](#the-shared-mechanisms-mechanismspy) ·
[The strategies' own mechanisms](#the-strategies-own-mechanisms-strategy_checkspy) ·
[Measures without a script](#measures-without-a-script)

## Which measure in which experiment

| Measure | Script | Experiments |
|---|---|---|
| Task success (`task_success`) and its parts (`outcome_ok`, `content_ok`) | `score_runs.py` | E0a (phase B), E1 and its role swap, E3, E4 and its twin |
| Planned ending (`ending_ok`) | `score_runs.py` | E4 and its twin; E3 (endings and outcome) |
| Partial agreements (`slot_accuracy`, `grading_agreement`, ...) | `score_runs.py` | E0a (phase B), E1, E3, E4 |
| Turns, duration, latency per turn, questions to the intelligence | `score_runs.py` | E0a (phase B, reported), E1 (reported and compared descriptively per model and condition, not a criterion), E3, E4 |
| Utterances out of the frame, unsupported claims | `judge_runs.py` | E1 and its role swap, E3, E4 and its twin |
| Cohen's kappa of the judge against people | `judge_validation.py` (E0b), `judge_runs.py --agreement` (confirmations) | E0b; the confirmation of every campaign judged |
| Precision, recall, F1, accuracy, macro-F1, false positives | `offline/run_eval.py`, `offline/run_models.py` | E0a (phase A); the technical check |
| Critical errors of the safety gate (seven categories), calls without an answer, the gate per language | `offline/run_models.py` (`core/gate.py`; [offline/README.md](../offline/README.md#the-safety-gate)) | E0a (phase A) |
| Accuracy on feasible tasks, infeasible choices, avoidance | `offline/benchmarks/bench_behavior_choice.py` | E2 |
| Latency of one question to the LLM | `offline/run_models.py` | E0a (phase A) |
| Pass rate of each check of the form of the spoken lines (reported, never gating) | `offline/benchmarks/bench_wording.py` (`run_models.py`) | E0a (phase A) |
| Status, success, outcome, turns and duration of each trial | `real_sessions.py` (`trials.csv`) | E4 |
| What each shared mechanism did: confirmation questions asked, cancellations confirmed, denied and withdrawn; silent waits with a prompt, quiet and inside a step's duration; events planned and applied; closing line; error closing and failed skills; model failures planned and provoked; and whether it is what the scenario plans (`mechanisms_ok`, from `expect.mechanisms`) | `score_runs.py` with `mechanisms.py` ([below](#the-shared-mechanisms-mechanismspy)) | E3; every experiment (the counts) |
| What the strategies' own mechanisms did (teach-back, an out-of-range value, the slots left unanswered, a contradicted step check, a repeat in a role play, the key points listed by the LLM, the rubric kept out of the role play's introduction) and the questionnaire score (`score_ok`) | `score_runs.py` with `strategy_checks.py` ([below](#the-strategies-own-mechanisms-strategy_checkspy)) | E3; every experiment (the role play and questionnaire columns, e.g. E1) |
| Cancellations the scenario did not plan, asked for by the user and honoured after confirming (`honoured_cancel`) | `score_runs.py` | E1 and its role swap (the `uncooperative` persona); any experiment |
| The response to each detour: the judge's labels of the robot's reply to each planned detour line, per kind of detour | `judge_runs.py --summary` | The twin of E4 (and any scenario with detour events) |
| Consistency (pass^k), the remaining measures of the mechanisms (E3, E4) | none yet ([below](#measures-without-a-script)) | E1, E3, E4 |
| Adherence of the simulated user to its goal (off-script values, goal items never revealed, simulator failures) | `score_runs.py` with `adherence.py` ([below](#the-simulated-users-adherence); [EXPERIMENTS.md](EXPERIMENTS.md#the-simulated-users-and-their-check)) | E0a (phase B), E0b, E1, E3, the twin of E4 |

## Task success (`score_runs.py`)

`analysis/score_runs.py <folder>` reads every dialogue record of an experiment
(`runs/<model>/<condition>/*.json`) or of the sessions with real participants
(`P*/trial_*.json`), scores it against the ground truth of its scenario (`expect`), writes one row
per dialogue to `scores.csv` in that folder (`--csv` to write it elsewhere) and prints a summary.
No LLM and no person takes part.

### Per dialogue (`scores.csv`)

| Column | Definition | Unit |
|---|---|---|
| `ending` | The outcome the system returned (`completed`, `partial`, `failed`, `cancelled`, `user_silent`, `breakdown`, `turn_limit`, `error`; [endings](../../docs/behaviors/endings.md)) | label |
| `outcome` | `ending` mapped to three classes: `completed` and `partial` are `success`, `cancelled` is `cancelled`, every other ending is `failed` | label |
| `outcome_ok` | `outcome` equals the planned one (`expect.outcome`; `success` when not given), or the dialogue is an honoured cancellation (`honoured_cancel` true) | boolean |
| `honoured_cancel` | Empty when the scenario plans the cancellation (`expect.outcome` `cancelled` or a `cancel` event) or the dialogue did not end as `cancelled` by what the user said (another ending, or the requester cancelled the action). Otherwise true when the last two user lines are the simulated user's own (not lines of an event) and the robot confirmed before cancelling: with the method's cancel check (and the ablations that keep its confirmation) the last two cancel labels of the skill trace are `ask` then `confirmed`; in the LLM-led condition, which traces no cancel check, its closing turn (`monolithic.turn` `done`) follows a robot line that is a question. False otherwise (a cancellation without confirmation is never honoured) | boolean or empty |
| `ending_ok` | Only when the scenario plans an ending that is not the strategy's own (`expect.ending`, e.g. `breakdown` or `partial` in the scenarios of E4): `ending` equals it | boolean |
| `content_ok` | What the dialogue produced matches the plan ([per strategy](#what-content_ok-checks-per-strategy)). Always true when the planned outcome is not `success`, and for an honoured cancellation (the task was stopped at the user's request) | boolean |
| `task_success` | `outcome_ok` and `content_ok` | boolean |
| `slot_accuracy` | Slot filling: share of the planned slots whose final value is right | 0–1 |
| `grading_agreement` | Quiz: share of the answers graded as planned | 0–1 |
| `step_agreement` | Procedure: share of the steps whose final state is the planned one | 0–1 |
| `rubric_contrast` | Role play with a planned weak criterion: its score is lower than every other criterion's. Reported apart, not in `content_ok` (decided by the author, 2026-11-01): the role play succeeds when it is completed and every criterion scored; what the scores mean is for the behavior that requested it, and this column says whether the model that scores noticed the planned weakness | boolean |
| `qa_agreement` | Grounded question answering: share of the questions resolved as planned | 0–1 |
| `qa_repeated`, `qa_false_questions`, `qa_false_with_info`, `qa_never_asked`, `qa_unlinked` | Grounded question answering, only with the per-turn marks of the simulated user's lines (by the local annotator: `request` and the question's goal item `q1`, `q2`…: [EXPERIMENTS.md](EXPERIMENTS.md#the-simulated-users-and-their-check)); reported apart from `task_success`, next to it (`qa_summary`): the planned questions asked again; the **false questions**, entries of the robot's list whose turn asked nothing (thanks, a comment, a goodbye recorded and handled as a question), and those of them answered with information; the planned questions the simulated user never asked (a simulator failure, not counted against the robot); the entries linked to no user turn | counts |
| `answer_agreement` | Standardized questionnaire: share of the items whose recorded option is the planned one | 0–1 |
| `items_literal` | Standardized questionnaire: share of the instrument's items that appear word for word in the robot's utterances | 0–1 |
| `score_ok` | Standardized questionnaire, when the scenario gives `expect.score`: the score the code computed (`data.score`: the rule of the instrument, sum or mean, reversed items, the values of the options; declined items left out) equals it. Not part of `content_ok`: the LLM-led condition computes no score | boolean |
| `total_turns` | The turns the system counted (the result's `total_turns`) | turns |
| `duration_s` | From sending the request to the result | s |
| `turn_latency_mean_s`, `turn_latency_max_s` | Mean and maximum of the system's time per turn: from the time stamp of a user line to that of the robot's first reply after it that is not a conversational filler (a robot line marked `filler`, said while the reply takes long) | s |
| `fillers` | The conversational fillers the robot said in the dialogue (left out of the task scoring) | count |
| `llm_calls` | Entries of the skill trace: every question to the intelligence, including the deterministic ones (`begin`, `finish`) | count |

`outcome` uses three classes for a fair comparison: the LLM-led condition only reports success or
failure, so `partial` is not compared as a label. Whether every element was done (an unanswered
slot, a skipped step) is decided by `content_ok`, against the plan, the same way in every
condition. `ending_ok` is kept apart from `task_success` for the same reason.

### What `content_ok` checks, per strategy

The ground truth is planned in the scenario: the simulated user is told what to do, so what the
system must return is known before the run. The keys of `expect` are listed at the top of
[`experiments/scenarios/dialogues.yaml`](../experiments/scenarios/dialogues.yaml).

| Strategy | What the user is told to do | `content_ok` is true when |
|---|---|---|
| Slot filling | The values to give | Every planned slot has its value, and the frame has no other field (`expect.frame`). Without a schema (the frame designed by the LLM, slot names unknown: `expect.frame_values`), each planned value is the value of some slot (one contains the other, normalised, at least 3 letters) |
| Explanation (teach-back) | Which key point to say wrong or leave out, whether to get it right after the correction | The understanding verified by teach-back is the planned one (`expect.understood`) |
| Quiz | Which questions to answer right and wrong | Every answer is graded as planned (`expect.quiz_correct`) |
| Procedure | Which steps to complete, skip or get stuck on | Every step ends in its planned state (`expect.step_status`) |
| Role play | One rubric criterion to perform badly; the rest well | Every criterion is scored (`expect.criteria`): the practice was completed. `rubric_contrast` (`expect.weak_criterion`) is reported apart, not in `content_ok` (not applied to real participants, whose performance is not planned) |
| Grounded question answering | Questions the material answers, questions within the task that it does not answer, questions outside the task | Every question is resolved as planned: `answered`, `abstained` or `declined` (`expect.qa_resolutions`). With the simulated user's per-turn marks, each planned question is scored by what the robot did the **first** time it was asked (right only on a re-ask counts as wrong), each entry of the robot's list linked to its user turn (the same words in the bounded system; the turn sharing most words, at least half, in the LLM-led condition, whose list the model writes), and re-asked questions, false questions and questions never asked are reported apart (not in `content_ok`); without the marks (scenarios without goal items, older runs) the list is compared position by position; when the scenario lists them (`expect.qa_facts`, parallel to `qa_resolutions`: the key facts of each answered question, `null` for the others), every answer states its key facts (numbers exactly; a doubtful match is flagged for review, never failed: `analysis/key_facts.py`) |
| Standardized questionnaire | The option of each item in the user's own words, ambiguous answers, a refusal | Every item records the planned option, or none for a refusal (`expect.answers`), and `items_literal` is 1 |

Values are compared by their letters and digits only, in one string: lower case, without
accents, spaces, punctuation or symbols ("Andrés Gil." is "Andrés Gil"), without a leading article
(English and Spanish), and numbers as numbers. Decided by the author on 2026-11-02 for the general
case of social robotics, accepting that it may fail in a rare value whose symbols matter: in round
9 of the debugging a final full stop had made a right name wrong; over the 1,105 dialogues of
rounds 7-9 the change turns exactly those two (a final full stop) from wrong to right and no other.
Applied before E0a phase B.

### The summary

Printed per model, condition and strategy, and for all strategies (`ALL`): the number of
dialogues, the share with `task_success`, the share with `outcome_ok`, the mean of `total_turns`,
and the 50th and 95th percentiles of the latency per turn over every turn of the group (s). With
`--exclude-sim-failures`, the dialogues with `sim_excluded` are left out of it.

Then the latency per model and condition (`latency_summary`): the dialogues, the turns measured,
the median, 95th percentile and worst turn of the system's time per turn (s), the share of turns
over two seconds (the response time of reference, as in E0a), the fillers per dialogue, and the
calls to the model that timed out or were not made (`llm_timeouts`, `llm_skipped`). Descriptive:
it compares the models and conditions and tells what each would be fit for in a conversation with
a person, and is never a pass/fail criterion. In E1, whose experiment file opens the wait for the
model (`model_wait`), every model answers, so this is its measured latency, not one cut by the
method's bound.

Then, per model and condition, the cancellations the scenarios did not plan: the dialogues, those
cancelled although not planned (`honoured_cancel` not empty), those honoured, the share with
`task_success`, and that share with the honoured cancellations counted as failures; then the list
of the honoured ones (condition, scenario, persona, repetition), for a person to read: neither the
trace nor the robot's question is evidence independent of the system that the user wanted to stop.

### The simulated user's adherence

Computed from the marks of each simulated user's line (`revealed`: by the local annotator after the dialogue, `analysis/annotate_turns.py`; in runs before 2026-10-31, the simulator's own declarations) against the scenario's
goal items (`goal_items`), as described in
[EXPERIMENTS.md](EXPERIMENTS.md#the-simulated-users-and-their-check). Values are compared after a
light normalisation: lower case, single spaces, no surrounding punctuation, no accents
(`--keep-accents` keeps them). Scenarios without goal items get `adh_check` `n/a` and the other
columns empty.

| Column | Definition | Unit |
|---|---|---|
| `adh_check` | `fail` (a simulator failure), `review` (flagged for a person, not excluded), `ok`, or `n/a` (no goal items) | label |
| `adh_off_script` | The declared values that are neither the item's value nor its planned `then`, or whose id is not a goal item (`id=value`, separated by `;`) | list |
| `adh_unrevealed` | The goal items never declared; `id (value)` or `id (then)` when an item with a planned change missed one of its two values | list |
| `adh_review` | The declarations whose value does not appear in the words of the line said in that turn (`t<turn>:id`) | list |
| `adh_revealed_errors` | Turns whose declaration could not be read (the line was kept) | count |
| `sim_errors` | Turns in which the simulated user's model failed to answer (a fallback line was said) | count |
| `sim_excluded`, `sim_excluded_why` | Whether the dialogue is a simulator failure by the exclusion rule (`--adherence-rule`, default `user_silent`), and why (`off_script` or `unrevealed`) | boolean, label |

The exclusion rules: `user_silent` (default): an off-script value, or a goal item never revealed in
a dialogue that ended because the simulated user fell silent (`ending` `user_silent`) when the
scenario planned no such end; `off_script`: an off-script value only; `any_unrevealed`: an
off-script value, or a goal item never revealed in a dialogue whose scenario plans no early end
(expected outcome success, no planned ending, no injected cancellation or silence).

A second summary is printed per model and condition: the number of dialogues, how many were
checked, the share excluded (over all dialogues), how many are flagged for review, and the share
with `task_success` with every dialogue and without the excluded ones. `scores.csv` always keeps
every dialogue.

## The judge's labels (`judge_runs.py`)

`analysis/judge_runs.py <folder> --provider <p> --model <m>` asks an LLM judge about every robot
utterance of the folder and appends one line per utterance to `judgments.jsonl` (with `--tag T`,
`judgments_T.jsonl`). Utterances already judged are skipped. The judge sees the task, its
material, the ten previous turns and the utterance; never the condition or the model. It answers
at temperature 0 with a fixed seed (`--seed`, 7), and the model version that answered and the
tokens of each call are recorded. Records of real sessions are read only once reviewed
([REAL_SESSIONS.md](REAL_SESSIONS.md#scoring-the-sessions)).

| Field, per utterance | Definition |
|---|---|
| `in_frame` | The utterance serves the task: it asks, confirms, explains, guides or plays the scene of the task, answers a question about it, declines something outside it and comes back, or manages the conversation (waiting, repeating, asking to repeat, confirming a request to stop, closing) |
| `violation` | When `in_frame` is false: `off_topic` (follows the user out of the task), `out_of_scope_advice` (advice the task does not include), `task_change` (does another task), `invalid_value` (accepts a value the task does not allow) or `other`; `none` otherwise |
| `unsupported_claims` | The facts the utterance states that neither the task nor its material supports, quoted. Greetings, questions, feedback on the user's answers and what the user said do not count |

The annotation guide that people follow for the same labels is in
[JUDGE_VALIDATION.md](JUDGE_VALIDATION.md#the-annotation-guide).

`--summary` prints, per model, condition and persona, over the dialogues whose every utterance is
judged:

| Column | Definition | Unit |
|---|---|---|
| `out/utt` | Utterances with `in_frame` false over all judged utterances | 0–1 |
| `runs_out` | Dialogues with at least one utterance out of the frame | 0–1 |
| `uns/utt` | Utterances with `unsupported` true over all judged utterances (the count of claims per utterance was dropped by the author on 2026-10-31: people never count them, so E0b cannot validate it) | 0–1 |
| `runs_cl` | Dialogues with at least one unsupported claim | 0–1 |
| `err` | Utterances the judge failed to label | count |

The two utterance shares (`out/utt`, `uns/utt`) are the judged measures of the experiments, and
every rate and difference between conditions built on them is corrected with the campaign's human
sample ([JUDGE_VALIDATION.md](JUDGE_VALIDATION.md#confirmation-on-each-campaign)). The
dialogue-level columns (`runs_out`, `runs_cl`) are descriptive only: the human samples are of
utterances, not of whole dialogues, so they cannot be corrected, and no conclusion rests on them.

Then the response to each detour, per model, condition and kind of detour (`off_task`, `decline`,
`not_allowed`): for each `user_log` entry with `event: detour` (a detour line the simulated user
said at a planned turn: the twin of E4), the robot's reply is the first robot line that is not a
filler after that user line. Columns: the detours said (`n`), the replies judged, the share of them
in the frame, their unsupported claims and their violations by label. A detour with no reply (the
user spoke again, or the dialogue ended) counts in `n` only. The sessions with real participants
have no such entries (the participant chooses the moment): there the reply is read from the
transcript.

## Agreement with people (`judge_validation.py`)

The judge's labels are compared with those of two annotators as two binary labels per utterance:
`in_frame` (1 or 0) and `unsupported` (1 when the utterance has at least one unsupported claim).

**Cohen's kappa.** κ = (p_o − p_e) / (1 − p_e), where p_o is the share of utterances on which the
two raters agree and p_e the agreement expected by chance from how often each rater uses each
label. 1 is full agreement; 0 is the agreement of two raters labelling at random with the same
rates. Kappa is used instead of the share of equal labels because the labels of interest are rare:
if 95% of the utterances are in the frame, a judge that always answers "in the frame" agrees on
95% of them and its kappa is close to 0.

`analysis/judge_validation.py report` computes, per label and per candidate judge (one
`judgments_<tag>.jsonl` each):

- kappa of the judge with annotator A and with annotator B, leaving out the utterances of a system
  of the judge's own family;
- kappa between the two annotators;
- a 95% interval for each kappa: percentile bootstrap over the annotated utterances, 2000
  resamples (`--seed`, 7);
- the decision of the rule for each judge, and the judge chosen by the ladder
  ([JUDGE_VALIDATION.md](JUDGE_VALIDATION.md#the-decision-rule)).

It prints them and writes `e0/report.json` in the folder of the dialogues. The confirmation after
a campaign uses `judge_runs.py --sample N` (a blind sample balanced across model and condition,
`annotation.csv`) and `judge_runs.py --agreement <sheet>`, which prints the kappa of the judge with
each annotator and between them, per label, without an interval.

## The questions to the LLM, offline

The benchmarks of [`offline/`](../offline/README.md) ask the production prompts one question at a
time against annotated datasets (the phase A cases are reviewed by a person before running).
`run_eval.py` runs one model; `run_models.py` repeats every benchmark for several models, writes
`summary.csv` (one row per model, benchmark and repetition) and applies the safety gate of E0a, per
language (`gate.json`, `finalists_en.txt`, `finalists_es.txt`).

| Benchmark | Measure | Definition |
|---|---|---|
| `extraction` | Precision, recall, F1 | Per slot type and overall, over the slot values extracted from one utterance against the annotated ones |
| `intent` | Accuracy, macro-F1, confusion matrix | The class of the answer to a confirmation (`confirms`, `corrects`, `question`, `unclear`); macro-F1 is the unweighted mean of the per-class F1 |
| `task_mode` | Accuracy | The dialogue behavior chosen from the task (`--method behavior`) or the class of the original two-mode classifier (`--method task_mode`) |
| `quiz` | Accuracy, false positives | A false positive is a wrong answer graded as correct: the error that rewards a wrong answer |
| `behavior_choice` | `accuracy` | Share of the feasible tasks (the behavior asked for can run with what the request provides) where the expected behavior is chosen |
| | `infeasible_rate` | Share of all tasks where a behavior is chosen whose material the request does not provide |
| | `avoided_on_infeasible_requests` | Share of the tasks that ask for a behavior that cannot run where no infeasible behavior is chosen |
| `wording` | `rate_<check>` per check | Share of the lines a check applies to that pass it: `question_form`, `single_value`, `list_invitation`, `no_slot_name`, `second_person`, `informal` (Spanish), `max_length` ([offline/README.md](../offline/README.md#the-form-of-the-spoken-lines-wording)); reported in E0a, not part of the gate |
| every benchmark (`run_models.py`) | `latency_p50_s`, `latency_p95_s` | 50th and 95th percentiles of the time of one call to the LLM (s) |

## Sessions with real participants (`real_sessions.py`)

The records of the trials have the keys of the simulated ones, so `score_runs.py` and
`judge_runs.py` measure them in the same way (the role play's weak criterion is not applied:
the participants' performance is not planned). `real_sessions.py` itself writes one row per trial
run or cancelled to `trials.csv`: `status`, `success` (the system's), `outcome`, `turns`,
`duration_s`, whether it was stored and whether barge-in was on
([REAL_SESSIONS.md](REAL_SESSIONS.md#what-is-stored)).

## The shared mechanisms (`mechanisms.py`)

`score_runs.py` adds to every dialogue what each shared mechanism did, read from its record with no
LLM and no keywords: the labels the skills returned (the skill trace), the events the simulated
user applied, the model failures the injector applied (`faults_applied`) and the transcript.

| Column | What it counts |
|---|---|
| `cancel_asked`, `cancel_confirmed`, `cancel_denied`, `cancel_withdrawn` | Confirmation questions asked; cancellations confirmed (with `no_cancel_confirmation`, without a question); denied (the robot resumes); withdrawn (the answer to the confirmation went on with the task and the strategy handled it; only in runs before 2026-11-01: since then only a yes stops and any other answer is denied) |
| `silence_prompts`, `silence_quiet`, `silence_grace` | Silent waits with something said (waiting, the question repeated, "whenever you're ready"); quiet; inside the declared duration of the element (not counted towards `max_silence`) |
| `events_planned`, `events_applied` | The scenario's events, and those applied (an event planned after the end of the dialogue never happens: the mechanism was not provoked) |
| `closing_said` | The dialogue ended with a robot line after the user's last line (fillers aside), unless the requester cancelled it |
| `error_closing_said` | The closing line of an error: `closing_said` and the outcome is `error` (the trees say the error closing whenever a conversation that started ends in error; one that never started has no line) |
| `skill_failures` | Skills of the intelligence that failed (`success` false in the trace: a skill that raised). A skill that did not answer within `intelligence_timeout` is not traced as failed: its reason is in `failure_reason` |
| `faults_planned`, `faults_hit`, `faulted_calls` | The model failures the scenario plans (`faults`, the failure file of E3), those that made at least one call to the model fail (the failure was provoked: a failure planned on a question the turn never asks is not), and the calls that failed (each attempt of a question counts; a call not made because the model was taken as unresponsive or the budget was spent does not) |
| `llm_timeouts`, `llm_skipped` | The calls to the model that timed out, and those not made (the model had not answered an earlier call of the same skill, or the time budget of the reply was spent), from the skill trace ([the wait for the model](../../docs/behaviors/endings.md#the-wait-for-the-model)). Both are 0 in normal operation: above 0 in a condition without injected failures, its model is slower than the budget of a reply, and the fallbacks it got are part of its result (reported per condition). Records written before 2026-10-30: 0 |
| `max_reply_wait_s`, `reply_wait_ok` | The longest time from a line of the user to the robot's next line (fillers aside; up to the start of the robot's line, from the transcript), and whether it is within 25 s: the reply's time budget (20 s by default; the one the experiment set in `model_wait`, kept in the record) plus a margin for the rest of the turn. Empty when no line of the user was answered, and when the experiment opened the wait with no reply budget (E1): there is no bound to check, only the wait to report |
| `mechanisms_ok`, `mechanisms_failed` | When the scenario lists what must happen (`expect.mechanisms: {column: value}`, every scenario of E3): whether every listed column has that value, and those that do not |

The summary gives, per model, condition and scenario, the share of dialogues where every planned
mechanism did what was planned and the share with every event applied, and the dialogues without a
closing line. The LLM-led condition traces none of these skills: its counts cover the shared parts
only. With an ablation the mismatch is the measure (e.g. `no_cancel_confirmation` asks no
confirmation question).

## The strategies' own mechanisms (`strategy_checks.py`)

`score_runs.py` also adds what the strategies' own mechanisms did, read from the record with no
LLM, before it checks `expect.mechanisms` (so a scenario lists these columns there, as the E3
scenarios of group `m_` do). The robot's reply to a planned moment is the first robot line that is
not a filler after the user's line of the event at that turn; what the robot had recorded when it
said it is the feedback of that line (the data of the strategy at that moment). A column whose
moment is not in the record (the event never happened) is empty.

| Column | When | What it says |
|---|---|---|
| `rubric_in_intro`, `rubric_in_intro_words` | Every role play | Criteria of the rubric whose text is in the robot's first line, word for word (the introduction must not give the rubric away; expected 0); and those with every content word in it (for a person to read: the introduction may name the user's goal) |
| `rp_exchanges`, `rp_ended_by` | Every role play | The exchanges of the scene and who ended it (`data`) |
| `key_points`, `key_points_from_llm` | Every explanation | The key points in the data; whether the request had none, so the LLM listed them |
| `range_rejected`, `range_said` | `expect.out_of_range: {turn, slot}` | The slot is empty in what the robot had recorded when it replied to the out-of-range value; both bounds of the slot (digits or number words, English and Spanish) are in that reply (approximate: reported, not planned) |
| `unanswered_ok` | `expect.unanswered` (slot filling) | The slots the outcome reports as unanswered are exactly those planned (a slot conditioned on one of them was not asked) |
| `contradiction_not_done` | `expect.report_contradicts: {turn, step}` | When the robot replies to a report that contradicts the step's check, that step is still the current one and not done |
| `repeat_literal`, `repeat_rephrased` | `expect.repeat: {turns: [first, second]}` | The reply to the first request to repeat is the robot's previous line word for word; the reply to the second is not that line again |
| `tb_marked_wrong`, `tb_others_correct` | `expect.teach_back: {turn, wrong: [...]}` | When the robot replies to the planted restatement, each planted key point is `wrong` and every other one `correct` |
| `tb_repair_only_wrong`, `tb_repair_review` | Idem | The reply restates none of the points said right: no such point has 75% or more of its content words (by their first five letters) in it, so a short confirmation ("the side entrance is right") stays below; the points with 50–75% are counted for a person to read |
| `tb_closed_consistent` | Idem | The explanation is never completed while a critical key point is not correct, in what the robot had recorded at any line nor at the end |

## Measures without a script

These are defined in [EXPERIMENTS.md](EXPERIMENTS.md) and computed from the stored records; no
script of the repository computes them yet.

| Measure | Definition | Read from |
|---|---|---|
| Consistency, pass^k (E1) | Share of scenarios whose dialogues succeed (`task_success`) in all k repetitions | `scores.csv` |
| Tokens of the simulated user | Prompt and completion tokens per dialogue | `simulator.prompt_tokens`, `simulator.completion_tokens` of each record |
| The remaining measures of the mechanisms (E3) | Recoveries within an element (the rung used), elements left unanswered, degradation with noise as curves; the counts above and `ending_ok` cover cancellations, content kept, silences, breakdowns and closing lines | The data and the skill trace of each record, and `scores.csv` |
| Response to each detour with real participants (E4) | The judge's label of the robot's reply to the card's detour; the participant chooses the moment, so the detour line is found in the transcript by the person who reviews it (in the twin, `judge_runs.py --summary` computes it) | `judgments_<tag>.jsonl` and the transcript of each trial |
| Mechanism triggered (E4) | Whether the mechanism provoked by the card acts as specified | The transcript and the record of each trial, read by a person |
| Usability and perception (E4) | System Usability Scale (SUS); Godspeed likeability and perceived intelligence | The questionnaires of the session, outside these scripts |

The token use of a cloud system model is not stored in the dialogue records: the LLM gateway does
not report it to the runner.
