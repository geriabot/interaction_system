# Instructions for human reviewers and annotators

Start here if you were asked to review or annotate part of the evaluation, or if you are
replicating the evaluation and need to organise the hand reviews. You do not need to know how the
interaction system works.

Most of the evaluation is automatic, but a few things can only be checked by people: whether the
expected answers of the test cases are right, whether the automatic check of the simulated users
can be trusted, whether the LLM judge labels the robot's sentences as people do, and that no
personal data of a real participant reaches the judge. This page lists every one of those hand
tasks, who does each one, when, and how long it takes, and links to a page with the exact
instructions. Why each review exists, and how much is reviewed:
[doc/EXPERIMENTS.md, "Hand reviews"](doc/EXPERIMENTS.md#hand-reviews).

Contents: [The tasks](#the-tasks) · [Before any task](#before-any-task) ·
[Rules for everyone](#rules-for-everyone) · [For the coordinator](#for-the-coordinator) ·
[What is not supported yet](#what-is-not-supported-yet)

## The tasks

In the order in which they happen during the evaluation. "Author" means a member of the team that
designed the evaluation; "annotator" means a person who labels material and is not told what the
results should be; the "coordinator" is the author who prepares the material, sends each person
their command and computes the results.

| # | Task | Who | When | How long | Instructions |
|---|---|---|---|---|---|
| 1 | **E0a, phase A: review the trap cases.** Is the expected answer of each test case right? The author answers each case's question blind and the panel compares it with the case's answer; only a disagreement asks which one is right, with an assistant's pre-review shown after the answer (decided 2026-11-01) | One author | Before phase A of E0a runs | About 357 cases: about 30–40 minutes with the pre-review (1 to 1.5 hours without it); can be split into several sittings | [human_review/e0a_trap_review.md](doc/human_review/e0a_trap_review.md) |
| 2 | **E0a, phase A: blind adjudication.** The cases that half or more of the models failed: is their expected answer right? | One author, blind (never sees which models failed a case, nor how many) | After phase A has run, before phase B | Known only after phase A; about 40 s per case | [human_review/e0a_adjudication.md](doc/human_review/e0a_adjudication.md) |
| 3 | **Being replaced (decided 2026-10-31):** the simulated user will no longer declare what it says; a local annotator marks its lines after each dialogue, validated once with a light scheme (a person settles only the lines where the annotator and the simulator's own declarations disagree, reads about 15 random conversations where they agree and every case that changes a verdict: about 1 hour once; per campaign, only the disagreements of two local annotators). Until that is built, the audit below. **E0a, phase B: audit the adherence checker.** Did the simulated user really say what it declared, and is the automatic verdict right? | One person (a second one only for doubtful cases) | After phase B of E0a has run | 30 to 50 conversations at about 2 minutes each: 1 to 1.5 hours | [human_review/adherence_audit.md](doc/human_review/adherence_audit.md) |
| 4 | **E0b: annotate robot sentences.** Two yes/no questions on each sentence | Two annotators, each alone, one sheet each per language | After the judges have labelled the E0b dialogues (`run_e0b.py label`), before `run_e0b.py report` | About 5 minutes of practice, then about 20 s per sentence: about 1 hour per sheet of 150 sentences (up to 2 hours if the sheet has 300); one sheet in English and one in Spanish per annotator | [human_review/e0b_annotation.md](doc/human_review/e0b_annotation.md) |
| 5a | **Being replaced (decided 2026-10-31)** by the disagreements of two local annotators (minutes per campaign): see task 3. **E1 and the simulated twin of E4: confirm the adherence checker** on a sample of each campaign | One person | After the campaign has run and been scored | About 20 conversations: about 40 minutes per campaign | [human_review/adherence_audit.md](doc/human_review/adherence_audit.md) |
| 5b | **E1 and the simulated twin of E4: confirm the judge** on a sample of each campaign | Two annotators, each alone | After the campaign has been judged | About 20 s per sentence; the size of the sample is still to be fixed (see the page) | [human_review/judge_confirmation.md](doc/human_review/judge_confirmation.md) (partly outside the panel) |
| 6a | **E4 with real participants: review the transcripts** and remove any real personal data | One member of the team | After each session, and always before the judge reads the sessions | Every conversation (64 in E4); a few minutes each | [human_review/e4_transcript_review.md](doc/human_review/e4_transcript_review.md) (in the terminal, not in the panel) |
| 6b | **E4 with real participants: confirm the judge** on the reviewed transcripts | Two annotators, each alone | After every transcript is reviewed and the judge has labelled them | As 5b | [human_review/judge_confirmation.md](doc/human_review/judge_confirmation.md) |

The times are estimates: the panel shows its own estimate on the confirmation screen and the time
left while you work, from your own pace.

## Before any task

Every task except 6a is done in the **review panel**, a web page served by your own computer
(nothing goes to the network, and it makes no LLM call). You need:

- **A computer with the workspace installed** with pixi ([docs/INSTALL_PIXI.md](../docs/INSTALL_PIXI.md)).
  Usually it is the coordinator's computer; the coordinator prepares it for you. Building the
  workspace is not needed for the panel.
- **The material of your task**, prepared by the coordinator: the page of each task says which
  folder or file must exist.
- **One exact command**, sent to you by the coordinator, with your name and your assignment. Run
  it from the root of the workspace (the folder that holds `pixi.toml`):

  ```sh
  cd <workspace>
  pixi run python src/interaction_system/evaluation/panels/review_panel.py <task and options>
  ```

  The terminal prints `Open http://localhost:8098` (or another number if 8098 is taken). Open that
  address in a browser. Keep the terminal open while you work; `Ctrl+C` in it stops the panel.

What every task has in common:

- **Your name** is part of the command and identifies your files. Write it **the same way every
  time** (capitals and accents do not matter, extra or missing words do).
- **The page is locked to your assignment**: a confirmation screen shows your name, the task, the
  number of items and the estimated time. If something does not match what you were told, do
  not start: tell the coordinator.
- **A short practice** with worked examples comes before the first item, the first time you do a
  task. It is not saved.
- **Language**: the panel is in English, with a button at the top right that switches it to
  Spanish (and back); the browser remembers the choice. The material itself (sentences, cases, conversations) is always shown as it
  is, in its own language.
- **Every answer is saved the moment you give it.** There is no save button. You can close the
  window or stop the panel at any time; running the same command again continues where you
  stopped.
- **Doubts**: give the answer you find most likely and write the doubt in the "Doubt or comment"
  field (key `/`). Doubts are useful: they help to improve the guides.
- **The end**: "Review and finish" (top right) lists what is still unanswered and what you marked
  as doubtful. When nothing is unanswered, "Finish" shows a final screen with the files where your
  answers are saved.
- `?` (or "How to use") shows the keys of the task again.

## Rules for everyone

1. **Work alone.** Do not look at anybody else's sheet or answers, and do not open any other file
   of the results folder (in particular `key.json` or any file with the judge's labels).
2. **Do not discuss the items** with the other annotator, or with anyone, until both of you have
   finished. The agreement between two independent people is part of what is measured.
3. **Questions about how to use the panel** go to the coordinator. Questions about a particular
   item do not: answer what you find most likely and write the doubt in the note.
4. **Do not edit the files by hand.** To change an answer, go back in the panel and answer again;
   the last answer is the one kept.
5. **Use exactly the command you were sent.** If you think it is wrong, ask before starting.

## For the coordinator

Each task page ends with a section "For the coordinator": how to prepare the material, how to
collect the answers and compute the result, and where the files go. In short:

- **Commands.** The panel runs from the workspace root with `pixi run python ...` (never with
  `--manifest-path`). The other scripts run from `src/interaction_system/evaluation` in a prepared
  terminal ([doc/REPRODUCING.md](doc/REPRODUCING.md#before-anything)).
- **Privacy.** Everything that carries a person's name stays under `evaluation/results/`, which is
  git-ignored and never published; the real sessions stay in `~/.ros/interaction_real/`, outside
  the repository. The only public outputs of the hand reviews are the corrected phase A datasets
  and their `CHANGELOG.jsonl`, which carry no names ("reviewed by an author").
- **One panel per computer at a time** is the safe choice: the panel records who holds which sheet
  in `results/reviews/registry.json`, and two panels running at once on the same results folder
  could each write it.
- **Every session is logged** in `results/CATALOG.jsonl` (`review_start`, `review_done`, with the
  counts; `dataset_apply`; `adjudication_selection`).
- **Annotators on their own computer.** The panel needs only Python 3 (no ROS, no pixi): with a copy
  of the repository and the sheet placed where the command expects it, `python3
  evaluation/panels/review_panel.py annotate ...` works the same. The annotator then sends back the
  files listed on the final screen. Their sessions are logged on their computer, not yours.
- The tool reference of the panel (every option, every file it writes, its tests):
  [panels/README.md](panels/README.md).

## What is not supported yet

Said plainly so that nobody looks for a button that does not exist. Each page gives the details.

- **The confirmation of the judge (5b, 6b) cannot be done in the panel.** The sample is drawn by
  `analysis/judge_runs.py --sample`, whose file has other columns than the annotation sheets the
  panel reads; it is filled in a spreadsheet, and `--agreement` prints the kappas without applying
  the decision rule of E0b. Its size is not fixed consistently in the documents.
- **The transcript review of the real sessions (6a)** is done in the terminal with
  `real/real_sessions.py review`, not in the panel.
- **The adherence sample** is stratified by language, persona and model, not by condition; the
  design asks for condition and persona in E1 and the twin of E4 (each campaign is in one
  language).
- **A second adherence auditor for the doubtful cases only** is not supported: a second auditor
  gets the whole sample.
- **E0a's adherence audit** covers two results folders (English and Spanish phase B); the panel
  audits one folder at a time, so it is two audits, and the size of each is the coordinator's
  choice.
- **Releasing a sheet** that someone started by mistake has no command: it is a hand edit of
  `results/reviews/registry.json`.
