# E0a, phase 1: blind adjudication of the disputed cases

[All the hand tasks](../../HUMAN_REVIEW.md) · Who: one author, blind · When: after phase 1 of E0a
has run (both passes), before phase 2 · How long: about 40 s per case; the number of cases is
known only after phase 1.

## What and why

After the candidate models have answered the phase 1 test cases, a case that half or more of the
models got critically wrong may be a case whose expected answer is wrong, not a case the models
fail. The rule, fixed before running, is that only those cases are looked at again, and blind:
you never see which models failed a case, nor how many, only the case and its current expected
answer. A wrong expected answer is corrected for every model, and the safety gate is recomputed
from the answers already saved, without running any model again. No other case is touched after
the results.

## Before you start

- Phase 1 has finished: `evaluation/results/e0a_phase_a/gate.json` exists (written by
  `scripts/run_e0a.py phase-a`). Without it the panel uses the saved runs of that folder; with
  neither it says "There are no phase A results to review yet".
- Do not open `gate.json`, `report.md` or the runs of phase 1 before adjudicating: they say which
  models failed each case.
- You need the workspace installed with pixi (see [Before any task](../../HUMAN_REVIEW.md#before-any-task)).

## Launching the panel

From the root of the workspace, one line:

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset --adjudicate --reviewer "Your Name"
```

Options, after `dataset`: `--language en|es` (only the cases of one language), `--gate <file>`
(another `gate.json`), `--port <n>`. `--reselect` draws the list of cases again from the current
gate: the list is pre-registered, so do not use it once adjudicating has started.

The first time the panel opens this task it writes the list of cases (the critical cases that half
or more of the evaluated models failed, in any repetition) to
`results/reviews/adjudication/selection.json`, with a hash of its source, and keeps it: the list
does not change afterwards, even when the gate is recomputed.

Open the address the terminal prints. The panel is in English; the button at the top right
switches it to Spanish.

## The screens

The same as in the [trap review](e0a_trap_review.md#the-screens), with two differences:

- The task is "Review disputed labels (blind)", and every case shows "Blind review: which models
  failed this case, and how many, is never shown."
- The list holds only the selected cases, traps or not.

The keys are the same: `Y`/`S` the expected answer is right (next case); `N` it is wrong, choose
the right one (`1`, `2`, `3`… and `Enter`, and "Other parts of the answer" for the secondary
fields); `D` not sure; `/` a note; `P` the exact prompt; `→`/`←` next and previous. Every answer
is saved at once; the same command resumes. "Review and finish" lists what is left; the final
screen lists the files written. Nothing has to be sent.

## Rules

- Judge each case as if it had never been run: what would a careful person answer?
- Do not look for the models' answers anywhere before you finish.
- When in doubt, `D` and a note: the expected answer is then kept.

## Troubleshooting

| What you see | What to do |
|---|---|
| "There are no phase A results to review yet" | Phase 1 has not run, or its folder is elsewhere: tell the coordinator (`--gate <file>` points to another `gate.json`) |
| The number of cases is 0 | No critical case was failed by half or more of the models: there is nothing to adjudicate |
| Another port, "This panel was opened for another task or person", "The session was closed", the browser closed | As in the [trap review](e0a_trap_review.md#troubleshooting) |

## For the coordinator

### Prepare

There is nothing to generate: run phase 1 (`scripts/run_e0a.py phase-a`), and the panel makes the
list from its `gate.json`.

### Collect, apply and recompute the gate

From the workspace root:

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset report --adjudicate --reviewer "Your Name"
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset apply --adjudicate --reviewer "Your Name" --dry-run
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset apply --adjudicate --reviewer "Your Name"
pixi run bash -c "cd src/interaction_system/evaluation/offline && python3 run_models.py --reps 3 --out ../results/e0a_phase_a --gate-only"
```

`apply --adjudicate` writes the accepted corrections with the changelog source "adjudication,
reviewed blind", keeps a backup and runs the dataset checks. `--gate-only` scores the saved answers
of every model again against the corrected datasets (no model is run, no server is needed) and
rewrites `gate.json`, `report.md` and the finalists of each language (`finalists_en.txt`,
`finalists_es.txt`), which phase 2 takes. Then phase 2 can run (`scripts/run_e0a.py phase-b`).
Commit the corrected datasets and `CHANGELOG.jsonl`.

### Where the files go

The private, git-ignored files are `results/reviews/adjudication/selection.json` (the frozen
list) and `<dataset>.<reviewer>.json` (the decisions). The backups and the private changelog are
as in the [trap review](e0a_trap_review.md#for-the-coordinator), and the recomputed gate is in
`results/e0a_phase_a/`. The public files are the corrected datasets and `CHANGELOG.jsonl`.
