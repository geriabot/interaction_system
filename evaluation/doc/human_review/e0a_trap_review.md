# E0a, phase A: reviewing the trap cases

[All the hand tasks](../../HUMAN_REVIEW.md) · Who: **one author** · When: **before phase A of
E0a runs** · How long: about 330 cases at roughly 12 s each (1 to 1.5 hours, more when cases need
correcting), in as many sittings as you like.

## The assistant's pre-review (decided by the author, 2026-11-01)

Before you start, an AI assistant has reviewed every trap case and left a verdict: "right", or
"doubtful" with the reason. You answer every case yourself, blind, before seeing the case's answer
or the pre-review (below); the pre-review is shown after your answer and only tells you where to
look harder, so that the review takes about 30–40
minutes instead of 1–1.5 hours. The verdicts are in `results/reviews/prereview_phase_a.json`; the
blind adjudication after phase A never shows them. The paper declares that the trap review was
done by an author with this pre-review.

## What and why

E0a chooses the local model by asking every candidate model the same narrow questions the system
asks during a conversation (is this a confirmation or a correction? did the person ask to stop?
is this quiz answer right?), on annotated test cases with a known expected answer. The cases were
drafted with the help of language models. The **trap cases**, built to provoke the errors that
matter most, are checked by a person before any model is run, so that no model is failed (or
passed) because of a wrong expected answer. You read each case in plain words and say whether its
expected answer is right; if not, you give the right one.

## Before you start

- The phase A datasets are in the repository (`evaluation/offline/datasets/phase_a/`): nothing has
  to be run before this review.
- Phase A must **not** have started yet. A correction changes the datasets, and the datasets are
  part of the code fingerprint of a results folder: a phase A run interrupted and resumed after a
  correction would be refused.
- You need the workspace installed with pixi (see [Before any task](../../HUMAN_REVIEW.md#before-any-task)).

## Launching the panel

From the root of the workspace (the folder that holds `pixi.toml`), one line:

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset --reviewer "Your Name"
```

Options, all after `dataset`:

| Option | Effect |
|---|---|
| (none) | The trap cases of every question, in both languages (the review planned) |
| `--language en` or `--language es` | Only the cases of one language: a way to split the work into two sittings |
| `--file confirmation` | Only the cases of one question (the name of its file in `datasets/phase_a/`, without `.json`); can be repeated |
| `--all` | Every case, not only the traps (not part of the planned review) |
| `--port 8100` | Another port, if 8098 is in use |

Open the address the terminal prints (`Open http://localhost:8098`). The panel is in English; the
button at the top right switches it to Spanish.

## The screens

1. **Check that everything is right.** Your name, the task ("Review test cases (phase A)"), the
   cases (traps only), the language of the cases, the number of items, how many are already done,
   the estimated time and the folder where your reviews are saved. If it is wrong, press "No, go
   back" and check the command.
2. **How to use it** (one minute) and a **practice** of two worked examples (not saved). The
   practice appears only the first time; "How to use" at the top shows the keys again.
3. **One case at a time.** At the top, the question put to the model in plain words (for
   example "Does the person ask to abandon the whole conversation?"); it changes from one block of
   cases to the next. Below: the inputs of the question as the model gets them (the details
   collected, the conversation before, the material...); inputs the model does not get are marked
   "for the reviewer only"; and what the person says. "See the exact text the model gets" (key
   `P`) shows the prompt, built by the benchmark's own code.
4. **Answer the question yourself, blind** (decided by the author, 2026-11-01: saying "yes, it is
   right" to a proposed "no" was too error-prone). You do not see the case's answer yet: choose
   yours with the buttons or `1`, `2`, `3`…

   | What happens | Keys |
   |---|---|
   | Your answer matches the case's and the case has nothing else: the case is kept and the panel goes to the next | — |
   | It matches, and the case also gives details (the corrected details of a confirmation, the values given, the key points of a teach-back, the option chosen): they are shown with the assistant's pre-review; are they right? | `Y` or `S` yes, `N` a form to correct them, `D` not sure |
   | It does not match: the panel shows "You: ... The case: ..." with the assistant's pre-review. Which one is right? | `1` yours (saved as a correction; with details, a form opens to check them), `2` the case's, `D` not sure, "Answer again" if you pressed the wrong key |
   | Write a doubt or comment (saved on `Enter`) | `/` |
   | Next / previous case | `→` / `←` |

   Your blind answer is saved with your verdict. A case you already reviewed shows the case's
   answer with Yes / No / Not sure, as before. The correction is checked against the rules of the
   dataset before it is saved; if it breaks one, the panel says why ("The correction is not valid:
   ...").
   A case can also show "The case changed after your review: review it again" (it was edited after
   you reviewed it) or "Your correction is already applied to the data".
5. **Review and finish** (top right): the cases not answered and those marked doubtful or with a
   comment, each with a link to go back to it. "Finish" is enabled when nothing is unanswered.
6. **Done. Thank you!** The final screen lists the files where your reviews are saved. Nothing has
   to be sent: you are working on the coordinator's computer (or are the coordinator).

**Stopping and resuming.** Every answer is saved as you give it. Close the window or press
`Ctrl+C` in the terminal whenever you like; the same command later continues at the first case
not yet reviewed.

## Rules

- Judge the case against what a careful person would answer, from what is shown; do not try to
  guess what any model would do.
- When in doubt, prefer `D` (not sure) and a note to a correction you are not sure of: a doubtful
  case keeps its expected answer, and the notes are read before applying.
- The reviewer of this task is an author: there is no second reviewer to keep apart from.

## Troubleshooting

| What you see | What to do |
|---|---|
| The terminal says another port (`Open http://localhost:8099`) | 8098 was in use (perhaps another panel): open the address printed. To choose one, add `--port <n>` after `dataset` |
| "This panel was opened for another task or person" | The tab belongs to another panel, or the command differs from the one you started with. Use the address this terminal printed |
| "The session was closed" | The panel was restarted. Press "Continue": your answers are saved |
| You closed the browser by mistake | Open the same address again (the terminal must still be running), or run the command again |
| "The correction is not valid: ..." | The correction breaks a rule of the dataset (a label that does not exist, a field of the wrong kind). Fix it, or mark the case `D` with a note |
| Anything else | Do not edit the dataset files by hand; note the case id and tell the coordinator |

## For the coordinator

**Prepare.** Nothing to generate: the trap cases are those marked `trap` in
`offline/datasets/phase_a/*.json`. Check that the datasets pass their checks first (from
`src/interaction_system/evaluation/offline`: `python3 -m core.dataset_check`).

**Collect and apply.** From the workspace root:

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset report --reviewer "Your Name"
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset apply --reviewer "Your Name" --dry-run
pixi run python src/interaction_system/evaluation/panels/review_panel.py dataset apply --reviewer "Your Name"
```

`report` gives, per dataset file: cases, traps, traps reviewed, reviewed, ok, corrected, applied,
not sure, stale (the case changed after its review) and pending. Before applying, read the notes
and settle the cases marked not sure (go back to them in the panel). `apply --dry-run` lists every
change (case, old value, new value) without writing; `apply` writes the corrections (verdict "No",
current, not yet applied) into the datasets, keeps a backup of each file it changes, appends one
line per change to the public `offline/datasets/phase_a/CHANGELOG.jsonl` (source "phase A review",
"reviewed by an author", no names) and runs the dataset checks. Commit the corrected datasets and
the changelog before the freeze commit of E0a ([EXPERIMENTS.md](../EXPERIMENTS.md#e0a-choosing-the-local-model),
"Freeze"); then phase A can run (`scripts/run_e0a.py phase-a`).

**Where the files go** (private, git-ignored):

| File | Content |
|---|---|
| `results/reviews/phase_a/<dataset>.<reviewer>.json` | The reviews, keyed by case id and a hash of the case |
| `results/reviews/backups/<dataset>.<time>.json` | Each dataset as it was before an `apply` |
| `results/reviews/changelog.jsonl` | Each correction with the reviewer and the note |
| `results/reviews/registry.json`, `results/CATALOG.jsonl` | Who did what; the sessions and the `dataset_apply` events |

Public: the corrected `offline/datasets/phase_a/*.json` and `CHANGELOG.jsonl`.
