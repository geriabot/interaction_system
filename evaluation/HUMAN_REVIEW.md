# Instructions for human reviewers and annotators

Start here if you were asked to review or annotate part of the evaluation, or if you are
replicating the evaluation and need to organize the hand reviews. You do not need to know how the
interaction system works.

Most of the evaluation is automatic, but a few things can only be checked by people: whether the
expected answers of the test cases are right, whether the automatic check of the simulated users
can be trusted, whether the LLM judge labels the robot's sentences as people do, and that no
personal data of a real participant reaches the judge. This page lists every one of those hand
tasks, who does each one, when, and how long it takes, and links to a page with the exact
instructions. Why each review exists, and how much is reviewed:
[doc/EXPERIMENTS.md, "Hand reviews"](doc/EXPERIMENTS.md#hand-reviews).

Contents: [Quick start](#quick-start) · [The tasks](#the-tasks) · [Before any task](#before-any-task) ·
[Rules for everyone](#rules-for-everyone) · [For the coordinator](#for-the-coordinator) ·
[What is not supported yet](#what-is-not-supported-yet)

## Quick start

If you were told "do task N", this is all you need; the page of your task (last column of
[the tasks](#the-tasks)) has the details, the screens and what to do if something goes wrong.

1. Get the repository and Python 3. Either use the coordinator's computer, already prepared, or
   clone the repository on your own (`git clone <repository URL>`) and check that `python3
   --version` prints 3.10 or newer. The panel needs nothing else: no ROS, no pixi, no network.
2. Put your material in place, if your task has any (table below). The coordinator sends it to
   you, because the results of the evaluation are not in the repository.
3. Open a terminal at the root of the repository (the folder that holds `evaluation/`) and
   run the command of your task, with your name (always written the same way) and, for task 4,
   the letter and language of your sheet:

| Task | Material (place it inside the repository) | Command |
|---|---|---|
| 1. E0a, review the trap cases (an author) | None: the cases are in the repository | `python3 evaluation/panels/review_panel.py dataset --reviewer "Your Name"` |
| 2. E0a, blind adjudication (an author) | `evaluation/results/e0a_phase_a/gate.json` | `python3 evaluation/panels/review_panel.py dataset --adjudicate --reviewer "Your Name"` |
| 3. E0a phase 2, E1 and the E4 twin: adherence audit | The results folder of the experiment, e.g. `evaluation/results/e1_bounded_vs_llm_led/` | `python3 evaluation/panels/review_panel.py adherence evaluation/results/<experiment> --auditor "Your Name" --n 20` |
| 4. E0b, annotate robot sentences | Your sheet, `evaluation/results/e0b/<en\|es>/e0/sheet_<a\|b>.csv` | `python3 evaluation/panels/review_panel.py annotate --annotator "Your Name" --sheet a --lang en` (your letter: `a` or `b`; your language: `en` or `es`) |
| 5b, 6b. Confirm the judge | A spreadsheet from the coordinator (not in the panel yet) | None: see [judge_confirmation.md](doc/human_review/judge_confirmation.md) |
| 6a. E4, review the real transcripts (team only) | Never leaves the team's computer | In the terminal: see [e4_transcript_review.md](doc/human_review/e4_transcript_review.md) |

4. Open the address the terminal prints (`Open http://localhost:8098`, or another number) in
   a browser. Keep the terminal open while you work.
5. Follow the screens. Check that your name, task and number of items are right (if not, stop
   and tell the coordinator), do the short practice, then answer item by item. Every answer is
   saved at once; to stop, close the browser and press `Ctrl+C` in the terminal; to go on later,
   run the same command again.
6. At the end, press "Review and finish" (top right), then "Finish". If you worked on your own
   computer, send the coordinator the files listed on the final screen.

On the coordinator's computer, where the workspace is installed with pixi, the same commands run
from the root of the workspace as `pixi run python src/interaction_system/<path from step 3>`
([Before any task](#before-any-task)).

## The tasks

In the order in which they happen during the evaluation. "Author" means a member of the team that
designed the evaluation; "annotator" means a person who labels material and is not told what the
results should be; the "coordinator" is the author who prepares the material, sends each person
their command and computes the results.

| # | Task | Who | When | How long | Instructions |
|---|---|---|---|---|---|
| 1 | E0a, phase 1: review the trap cases. Is the expected answer of each test case right? | One author | Before phase 1 of E0a runs | 357 cases: about 30–40 minutes with the pre-review (1 to 1.5 hours without it), in one or several sittings | [human_review/e0a_trap_review.md](doc/human_review/e0a_trap_review.md) |
| 2 | E0a, phase 1: blind adjudication. Is the expected answer right in the cases that half or more of the models failed? | One author, blind (never sees which models failed a case, nor how many) | After phase 1 has run, before phase 2 | Known only after phase 1; about 40 s per case | [human_review/e0a_adjudication.md](doc/human_review/e0a_adjudication.md) |
| 3 | E0a, phase 2: audit the adherence checker (being replaced, see below). Did the simulated user really say what was marked, and is the automatic verdict right? | One person (a second one only for doubtful cases) | After phase 2 of E0a has run | 30 to 50 conversations at about 2 minutes each: 1 to 1.5 hours | [human_review/adherence_audit.md](doc/human_review/adherence_audit.md) |
| 4 | E0b: annotate robot sentences. Two yes/no questions on each sentence | Two annotators, each alone, one sheet each per language | After the judges have labeled the E0b dialogues (`run_e0b.py label`), before `run_e0b.py report` | About 1 hour per sheet of 150 sentences (up to 2 hours for 300), after 5 minutes of practice | [human_review/e0b_annotation.md](doc/human_review/e0b_annotation.md) |
| 5a | E1 and the simulated twin of E4: confirm the adherence checker on a sample of each campaign (being replaced, see below) | One person | After the campaign has run and been scored | About 20 conversations: about 40 minutes per campaign | [human_review/adherence_audit.md](doc/human_review/adherence_audit.md) |
| 5b | E1 and the simulated twin of E4: confirm the judge on a sample of each campaign | Two annotators, each alone | After the campaign has been judged | About 20 s per sentence; the size of the sample is still to be fixed (see the page) | [human_review/judge_confirmation.md](doc/human_review/judge_confirmation.md) (partly outside the panel) |
| 6a | E4 with real participants: review the transcripts and remove any real personal data | One member of the team | After each session, and always before the judge reads the sessions | Every conversation (64 in E4); a few minutes each | [human_review/e4_transcript_review.md](doc/human_review/e4_transcript_review.md) (in the terminal, not in the panel) |
| 6b | E4 with real participants: confirm the judge on the reviewed transcripts | Two annotators, each alone | After every transcript is reviewed and the judge has labeled them | As 5b | [human_review/judge_confirmation.md](doc/human_review/judge_confirmation.md) |

In task 1 the author answers each case's question blind, and the panel compares that answer with
the case's own; only a disagreement asks which of the two is right. An assistant's pre-review is
shown after the author's answer, so that it points out where to look harder without steering the
answer. In task 4 each annotator does one sheet in English and one in Spanish, at about 20 s per
sentence.

Tasks 3 and 5a are being replaced. The simulated user no longer declares what it says, since
doing so would add to its load while it plays the user; a local annotator marks its lines after
each dialogue instead. That annotator is validated once with a light scheme, about 1
hour in all: a person settles only the lines where the annotator and the simulator's own
declarations disagree, reads about 15 random conversations where they agree, and checks every
case that changes a verdict. Per campaign, only the disagreements of two local annotators are
then settled, which takes minutes. Until this scheme is in place, tasks 3 and 5a follow the
audit of their page.

The times are estimates: the panel shows its own estimate on the confirmation screen and the time
left while you work, from your own pace.

## Before any task

Every task except 6a is done in the review panel, a web page served by your own computer
(nothing goes to the network, and it makes no LLM call). You need:

- A computer with the workspace installed with pixi ([docs/INSTALL_PIXI.md](../docs/INSTALL_PIXI.md)).
  Usually it is the coordinator's computer, which the coordinator prepares for you. The panel does
  not need the workspace to be built.
- The material of your task, prepared by the coordinator. The page of each task says which folder
  or file must exist.
- One exact command, sent to you by the coordinator, with your name and your assignment. Run it
  from the root of the workspace (the folder that holds `pixi.toml`):

  ```sh
  cd <workspace>
  pixi run python src/interaction_system/evaluation/panels/review_panel.py <task and options>
  ```

  The terminal prints `Open http://localhost:8098` (or another number if 8098 is taken). Open that
  address in a browser. Keep the terminal open while you work; `Ctrl+C` in it stops the panel.

What every task has in common:

- Your name is part of the command and identifies your files. Write it the same way every time
  (capitals and accents do not matter, extra or missing words do).
- The page is locked to your assignment. A confirmation screen shows your name, the task, the
  number of items and the estimated time. If something does not match what you were told, do not
  start: tell the coordinator.
- A short practice with worked examples comes before the first item, the first time you do a
  task. It is not saved.
- The panel is in English, with a button at the top right that switches it to Spanish (and back);
  the browser remembers the choice. The material itself (sentences, cases, conversations) is
  always shown as it is, in its own language.
- Every answer is saved the moment you give it. There is no save button. You can close the window
  or stop the panel at any time; running the same command again continues where you stopped.
- If you are in doubt, give the answer you find most likely and write the doubt in the "Doubt or
  comment" field (key `/`). Doubts are useful: they help to improve the guides.
- At the end, "Review and finish" (top right) lists what is still unanswered and what you marked
  as doubtful. When nothing is unanswered, "Finish" shows a final screen with the files where your
  answers are saved.
- `?` (or "How to use") shows the keys of the task again.

## Rules for everyone

1. Work alone. Do not look at anybody else's sheet or answers, and do not open any other file of
   the results folder (in particular `key.json` or any file with the judge's labels).
2. Do not discuss the items with the other annotator, or with anyone, until both of you have
   finished. The agreement between two independent people is part of what is measured.
3. Questions about how to use the panel go to the coordinator. Questions about a particular item
   do not: answer what you find most likely and write the doubt in the note.
4. Do not edit the files by hand. To change an answer, go back in the panel and answer again; the
   last answer is the one kept.
5. Use exactly the command you were sent. If you think it is wrong, ask before starting.

## For the coordinator

Each task page ends with a section "For the coordinator": how to prepare the material, how to
collect the answers and compute the result, and where the files go. In short:

- The panel runs from the workspace root with `pixi run python ...` (never with
  `--manifest-path`). The other scripts run from `src/interaction_system/evaluation` in a prepared
  terminal ([doc/REPRODUCING.md](doc/REPRODUCING.md#before-anything)).
- Everything that carries a person's name stays under `evaluation/results/`, which is git-ignored
  and never published; the real sessions stay in `~/.ros/interaction_real/`, outside the
  repository. The only public outputs of the hand reviews are the corrected phase 1 datasets and
  their `CHANGELOG.jsonl`, which carry no names ("reviewed by an author").
- Run one panel per computer at a time. The panel records who holds which sheet in
  `results/reviews/registry.json`, and two panels running at once on the same results folder
  could both write that file.
- Every session is logged in `results/CATALOG.jsonl` (`review_start`, `review_done`, with the
  counts; `dataset_apply`; `adjudication_selection`).
- Annotators can work on their own computer, since the panel needs only Python 3 (no ROS, no
  pixi). With a copy of the repository and the sheet placed where the command expects it, `python3
  evaluation/panels/review_panel.py annotate ...` works the same. The annotator then sends back
  the files listed on the final screen. Their sessions are logged on their computer, not yours.
- The tool reference of the panel (every option, every file it writes, its tests) is
  [panels/README.md](panels/README.md).

## What is not supported yet

This section lists the gaps plainly, so that nobody looks for a button that does not exist. Each
task page gives the details.

- The confirmation of the judge (5b, 6b) cannot be done in the panel. The sample is drawn by
  `analysis/judge_runs.py --sample`, whose file has different columns from the annotation sheets
  the panel reads. It is filled in a spreadsheet, and `--agreement` prints the kappas without
  applying the decision rule of E0b. Its size is not fixed consistently in the documents.
- The transcript review of the real sessions (6a) is done in the terminal with
  `real/real_sessions.py review`, not in the panel.
- The adherence sample is stratified by language, persona and model, not by condition; the design
  asks for condition and persona in E1 and the twin of E4 (each campaign is in one language).
- A second adherence auditor for the doubtful cases only is not supported: a second auditor gets
  the whole sample.
- E0a's adherence audit covers two results folders (English and Spanish phase 2); the panel
  audits one folder at a time, so it is two audits, and the size of each is the coordinator's
  choice.
- Releasing a sheet that someone started by mistake has no command: it is a hand edit of
  `results/reviews/registry.json`.
