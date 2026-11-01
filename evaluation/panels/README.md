# The review panel

`review_panel.py` is a web page, served on this computer only (127.0.0.1), for the hand reviews of
the evaluation ([EXPERIMENTS.md](../doc/EXPERIMENTS.md#hand-reviews)). It is made for people who
know nothing about the system: a start page asks who you are and what you are doing, a confirmation
screen shows the assignment, a short tutorial with worked examples comes before the first item,
every item must be answered before the next one, and a last screen lists what is missing or marked
as doubtful before "Finish". The interface is in English by default, Spanish with one click; the
data is shown as it is. It makes no LLM call and needs no network. Every answer is saved to a file
the moment it is given: a session can be closed at any time and resumed where it stopped.

**The instructions for the people who do the reviews** (who does each task, when, the exact
command, the screens, what to do if something goes wrong, and what the coordinator prepares and
computes): [HUMAN_REVIEW.md](../HUMAN_REVIEW.md), with one page per task in
[doc/human_review/](../doc/human_review/). The annotators' guide of E0b is
[doc/human_review/e0b_annotation.md](../doc/human_review/e0b_annotation.md)
([GUIA_ANOTADORES.md](GUIA_ANOTADORES.md), the earlier Spanish guide, now points there). This page
is the tool reference.

## Launching it

From the root of the colcon workspace, in its pixi environment (never with `--manifest-path`):

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py [--port 8098]
# then open http://localhost:8098 (the next free port if 8098 is taken: the terminal says which)
```

Without a task the start page offers every task. With a task and a name, the page is locked to that
assignment, so each person can be sent one exact command:

| Task | Command (after `review_panel.py`) | What is reviewed |
|---|---|---|
| E0b annotation | `annotate --annotator "Name" --sheet a --lang es` | The robot utterances of one annotation sheet, blind: two yes/no questions (in the frame of the task; states something unsupported), and a doubt note |
| Phase A cases (E0a) | `dataset --reviewer "Name"` (`--all` for every case, `--language en\|es`, `--file confirmation`) | Whether the expected output of each case is right; the trap cases only by default |
| Adjudication (E0a) | `dataset --adjudicate --reviewer "Name"` (`--gate <gate.json>`) | The critical cases that half or more of the evaluated models failed, blind |
| Adherence audit | `adherence <results of an experiment> --auditor "Name"` (`--n 40 --seed 7`) | Whether the simulated user really said what was marked (by the local annotator since 2026-10-31, or declared by the simulator in earlier runs), on a seeded sample of dialogues |

The start page also accepts its choices in the address, to prefill them (not locked):
`http://localhost:8098/?task=annotate&sheet=a&lang=es&name=Name`.

The commands without the page:

```sh
review_panel.py dataset report [--reviewer "Name"] [--adjudicate]   # per file: reviewed, ok, corrected, unsure, pending
review_panel.py dataset apply --reviewer "Name" [--adjudicate] [--dry-run]
review_panel.py adherence report <results> [--auditor "Name"]       # the checker's accuracy, 95% Wilson interval
```

## The tasks

**Annotation (E0b).** The sheets are those written by `analysis/judge_validation.py sheets`
(`results/e0b/<en|es>/e0/sheet_a.csv` and `sheet_b.csv`). The page shows one robot utterance at a
time with the task, the material and the conversation before it, and never the judge's labels, the
condition or the model. The answers are written back into the same CSV, in the columns
`judge_validation.py report` reads (`in_frame (1/0)`, `unsupported (1/0)`, `note`), after each item.
A sheet belongs to one person and a person holds one sheet per language: the page refuses a sheet
someone else started, and the other sheet of a language one already started. The annotation guide
is beside each utterance: the section of [JUDGE_VALIDATION.md](../doc/JUDGE_VALIDATION.md#the-annotation-guide)
in English, and its translation [annotation_guide_es.md](annotation_guide_es.md) in Spanish (which
records the hash of the English section it translates; the panel warns when they differ).

**Phase A cases (E0a).** Each case is shown in plain words: the situation of the question, the
inputs as the question gets them (and the exact prompt on request, built by the benchmark's own code
with a transport that calls no model), what the person says, and the proposed answer. "Yes" keeps it;
"No" asks for the right answer among the labels of the benchmark, validated by
`offline/core/dataset_check.py` before it is saved; "Not sure" keeps a doubt. A review is keyed by
the case id and a hash of the case, so a later edit of the case invalidates it. The time left is
estimated from the pace of the session.

**Adjudication (E0a).** Pre-registered: the critical cases that half or more of the evaluated models
failed in phase A (from `results/e0a_phase_a/gate.json`, or from the saved runs when there is no
gate) are listed once and frozen in `results/reviews/adjudication/selection.json` (with the hash of
its source; `--reselect` draws it again). Each case is shown blind: never which models failed it nor
how many, only the case and its current label. After `dataset apply --adjudicate`, the gate is
recomputed from the saved outputs (`offline/run_models.py --gate-only`).

**Adherence audit.** A seeded random sample of the dialogues with goal items of an experiment,
stratified by language, persona and model (largest remainder), drawn once and recorded with its
seed and size. For each dialogue: each item the simulated user declared (does the line really say
that value?), each goal item the checker found never revealed (was it indeed never said?), and the
checker's verdict on the dialogue. `report` gives the checker's accuracy per item (declared, never
revealed, all) and per dialogue verdict, overall and per language, with a 95% Wilson interval;
"not sure" answers are left out and counted.

## Keys

| Task | Answer | Move |
|---|---|---|
| Annotation | `S` or `Y` yes, `N` no on the highlighted question (first 1, then 2); `Tab` switches question | `→` next (once both are answered), `←` previous |
| Phase A, adjudication | `S`/`Y` yes (and next), `N` no (then `1`, `2`, `3`… and `Enter`), `D` not sure; `P` the exact prompt | `→`, `←` |
| Adherence | `S`/`Y` yes, `N` no, `D` not sure on the highlighted question; `↓` `↑` switch question | `→` next dialogue, `←` previous |

In every task `/` writes a doubt or comment (saved on `Enter`), `?` shows how to use it, and
"Review and finish" lists what is missing or doubtful.

## Where the files go

Everything that carries a person's name stays under the git-ignored `evaluation/results/`:

| File | Content |
|---|---|
| `results/reviews/phase_a/<dataset>.<reviewer>.json` | The reviews of the phase A cases |
| `results/reviews/adjudication/<dataset>.<reviewer>.json`, `selection.json` | The adjudication decisions and the frozen list of cases |
| `results/reviews/registry.json` | Who holds which sheet, the tasks started and finished, the tutorials seen |
| `results/reviews/backups/`, `results/reviews/changelog.jsonl` | The datasets before each `apply`, and each correction with its reviewer and note |
| `results/<experiment>/audit/adherence_<auditor>.json`, `..._report.json` | The audit sample, the answers, the report |
| `results/e0b/<lang>/e0/sheet_<a\|b>.csv`, `sheet_<a\|b>.progress.json` | The annotated sheet (judge_validation's columns only) and its record |
| `results/CATALOG.jsonl` | One line per `review_start`, `review_done` (with counts), `dataset_apply` (corrections, hash of the datasets before and after) and `adjudication_selection` |

Every file the panel writes carries the panel version and its first and last save. The corrected
datasets stay in `offline/datasets/phase_a/`, with a public `CHANGELOG.jsonl` that gives each case,
its old and new values, the source (phase A review, or adjudication reviewed blind), "reviewed by an
author" and the date, without names. `apply` runs the dataset checks afterwards.

## Tests

Pure Python, no browser (loading, saving and resuming, apply with its backup and changelogs, the
adjudication list, the sample, the Wilson interval, the sheets read back by
`judge_validation.read_sheet`, the page served):

```sh
pixi run bash -c "cd src/interaction_system/evaluation && python -m pytest -q panels"
```
