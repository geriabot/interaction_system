# Auditing the adherence checker of the simulated user

> This audit is being replaced. The simulated user no longer declares what it says: a local
> annotator marks its lines after each dialogue (`analysis/annotate_turns.py`). The annotator is
> validated once with a light scheme, about 1 hour in all. A person settles only the lines where
> the annotator and the simulator's own declarations in the debugging runs disagree
> (`annotate_turns.py <results> --compare`), reads about 15 random conversations where they agree,
> and checks every case that changes a verdict. Per campaign, only the disagreements of two local
> annotators are settled. The procedure below still applies to the sample of agreements and to
> the verdict-changing cases.

[All the hand tasks](../../HUMAN_REVIEW.md) · The same procedure for three reviews:

| Review | Who | When | Sample |
|---|---|---|---|
| E0a, phase 2: the audit, once | One person (a second one only for doubtful cases) | After phase 2 of E0a has run | 30 to 50 conversations, English and Spanish |
| E1: the confirmation on the campaign | One person | After the campaign has run and been scored | About 20 conversations |
| The simulated twin of E4: the confirmation | One person | After the twin has run and been scored | About 20 conversations |

How long: about 2 minutes per conversation (40 conversations: about 1.5 hours).

## What and why

In the simulated experiments an LLM plays the user, following a goal: the details it must give
(a drink and a size, a wrong answer and then the right one...). Each test is scored against that
plan, so it is valid only if the simulated user really said what the plan says. After each
conversation a local annotator marks which details each line of the simulated user gave (in
earlier runs, the simulated user declared them itself). A script, the adherence checker, compares
those marks with the plan and leaves out of the evaluation the conversations where the simulated
user went off its plan. On a random sample of conversations, you check whether the checker can be
trusted. Does each line really say what was marked? Are the details the checker says were never
given really absent? Is its conclusion right?

## Before you start

- The experiment has run and its results folder exists, with its conversations in `runs/`:

  | Review | Results folder (under `src/interaction_system/evaluation/results/`) |
  |---|---|
  | E0a, phase 2 | `e0a_dialogues` (English) and `e0a_dialogues_es` (Spanish): two audits, one per folder |
  | E1 | `e1_bounded_vs_llm_led` (and its role swap, `e1_role_swap`) |
  | Twin of E4 | `e4_sim_es` |

- The coordinator tells you the folder and the size of your sample.
- You need the workspace installed with pixi (see [Before any task](../../HUMAN_REVIEW.md#before-any-task)).

## Launching the panel

From the root of the workspace, one line (here for E1, with a sample of 20):

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py adherence src/interaction_system/evaluation/results/e1_bounded_vs_llm_led --auditor "Your Name" --n 20
```

Options, after `adherence <folder>`: `--n` (conversations in the sample, 40 by default), `--seed`
(7 by default), `--port`. The sample is drawn the first time you open the task and saved with its
seed and size; from then on it is fixed (the same command, with or without `--n`, resumes it; a
different `--n` or `--seed` is refused).

Open the address the terminal prints. The panel is in English; the button at the top right
switches it to Spanish.

## The screens

1. Check that everything is right: your name, "Audit the simulated user", the experiment, the
   number of conversations, the estimated time and the file where your audit is saved.
2. The first time, "How to use it" and a practice of three worked examples (not saved).
3. One conversation at a time. At the top: the scenario, its language and the persona (how the
   simulated user was asked to behave). Never the model or the condition. Then:
   - "Details the simulated user had to give": the plan (a detail may "then change to" another
     value, a planned correction);
   - "The instructions the simulated user read";
   - "The conversation, with what the user declared in each sentence": each user line shows its
     declarations ("declared: size = "medium""), marked "off script" when the value is not in the
     plan and "not literally in the line" when its words do not appear in the line;
   - "The whole conversation, as recorded", if you need it.
4. The questions of the conversation, one block per item:

   | Question | Yes | No |
   |---|---|---|
   | Does the user's sentence really say "medium" (size)? (one per declaration) | The sentence gives that detail, in those or other words | It does not say it |
   | The check says the user never gave "Laura" (name). Is that true? (one per detail the checker found missing) | No sentence says it | Some sentence says it, even without declaring it |
   | The automatic check concludes: ... Is that conclusion right? (one per conversation) | Given what the user really said, the check should conclude the same | It should conclude otherwise |

   The conclusion is one of: the user followed its script; it followed it, but some declared
   detail does not appear in those words (to review); the user did not follow its script, so the
   conversation is excluded; not applicable.

   Keys: `Y` or `S` yes, `N` no, `D` not sure, on the highlighted question; `↓` `↑` move between
   questions; `→` next conversation, `←` previous; `/` a note on the conversation. A
   conversation is done when every question has an answer.
5. "Review and finish", then the final screen with the file of your audit. Nothing has to be sent
   if you worked on the coordinator's computer.

Every answer is saved at once, so you can stop and resume. Close the window or press `Ctrl+C`
whenever you like; the same command continues where you stopped.

## Rules

- Judge what the line says, not whether the robot did well, and not whether the plan was sensible.
- Other words for the same detail count ("a regular one" for "medium" in a café whose sizes are
  small, regular and large); a different value does not.
- "Not sure" answers are left out of the accuracy and counted separately: use them when you
  really cannot tell, with a note.

## Troubleshooting

| What you see | What to do |
|---|---|
| "That experiment has no conversations to audit" | The folder has no `runs/` with conversations, or none of its scenarios has goal items: check the folder with the coordinator |
| "... was drawn with n=40: the sample is fixed" | You already have a sample for this folder: run the command without `--n` (or with the same value) |
| Another port, "This panel was opened for another task or person", "The session was closed", the browser closed | As in the [trap review](e0a_trap_review.md#troubleshooting) |

## For the coordinator

### Prepare

Run and score the experiment (`analysis/score_runs.py results/<name>`). The sample is
drawn by the panel the first time the auditor opens the task: a seeded random sample of the
conversations that have goal items, stratified by language, persona and model (each stratum gets
its share by largest remainder), recorded with its seed, size and strata. Decide `--n` before
sending the command and keep it in the record of the review:

- For E0a, phase 2 writes one folder per language, and the panel audits one folder at a time, so
  the audit of E0a is two audits (`e0a_dialogues`, `e0a_dialogues_es`). The design asks for 30 to
  50 conversations in all; the size of each half is your choice (for example `--n 20` in each).
  The audit does not block `run_e0a.py decide`: E0a excludes no conversation, it only reports the
  exclusion rate.
- For E1 and the twin of E4, about 20 conversations per campaign (`--n 20`).

### Compute the result

From the workspace root:

```sh
pixi run python src/interaction_system/evaluation/panels/review_panel.py adherence report src/interaction_system/evaluation/results/e1_bounded_vs_llm_led --auditor "Your Name"
```

It prints the checker's accuracy, overall and per language, per kind of item (declared, never
revealed, all), per conversation verdict, and the share of conversations with every item right,
each with a 95% Wilson interval; the "not sure" answers are left out and counted. Without
`--auditor` it reports every audit of the folder. It also writes the report next to the audit.

### Where the files go

The files are private and git-ignored: `results/<name>/audit/adherence_<auditor>.json`
(the sample, the answers, and the checker's output as it was shown) and
`adherence_<auditor>_report.json`.

### Not supported yet

The design asks for a sample stratified by condition and persona in E1 and the twin of E4 (each
campaign is in one language). The panel stratifies by language, persona and model; in E1 the model
separates the local and cloud conditions, not bounded from LLM-led. A second auditor for the
doubtful cases only is not supported: a second auditor gets the whole sample (the same one, with
the same seed and size, while the folder does not change).
