# E0b: annotating robot sentences

[All the hand tasks](../../HUMAN_REVIEW.md) · Who: **two annotators, each alone** · When: **after
the judges have labelled the E0b dialogues, before the E0b report** · How long: about 5 minutes of
practice, then about 20 s per sentence: a sheet of 150 sentences takes about an hour, in as many
sittings as you like. Each annotator does one sheet in English and one in Spanish.

This page is the annotators' guide (it replaces the earlier Spanish one-page guide,
[panels/GUIA_ANOTADORES.md](../../panels/GUIA_ANOTADORES.md), which now points here).

## What and why

You will read sentences that a robot said in conversations and answer two simple questions about
each one. You do not need to know how the robot works. Some measures of the evaluation come from
an LLM (the "judge") that reads each sentence the robot said; before its labels are used, they are
compared with those of two people who label the same sentences on their own. Your labels are that
reference: what matters is your honest reading of each sentence, not agreeing with anybody.

The two questions:

1. **Does the robot's sentence stay within its task?** Yes if it asks, confirms, explains, guides
   or plays its task, answers about it, manages the conversation (waits, repeats, says goodbye…)
   or politely declines something else and comes back to the task. No if it leaves the task, gives
   advice the task does not include, does another task or accepts something the task does not
   allow.
2. **Does the robot state something that the task and its material do not support?** Yes if it
   states as true something that is not in the task or its material, or that contradicts it (an
   opening hour, a price, a step, a rule). Greetings, questions, feedback on what the user said and
   politeness do not count.

The full annotation guide, with its examples, is shown beside every sentence in the panel. Its
source is the section [The annotation guide](../JUDGE_VALIDATION.md#the-annotation-guide) of
JUDGE_VALIDATION.md (in English); the panel in Spanish shows its translation,
[panels/annotation_guide_es.md](../../panels/annotation_guide_es.md).

## Before you start

- The coordinator has prepared your sheet and tells you its **letter (A or B)** and its
  **language** (English or Spanish). Sheet A and sheet B hold the same sentences: one is for each
  annotator.
- You work on the coordinator's computer (or on your own, see [Working on your own
  computer](#working-on-your-own-computer)).

## Launching the panel

The coordinator sends you one exact command with your name, your sheet and its language. From the
root of the workspace (the folder that holds `pixi.toml`):

```sh
cd <workspace>
pixi run python src/interaction_system/evaluation/panels/review_panel.py annotate --annotator "Your Name" --sheet a --lang en
```

`--sheet a` or `--sheet b` is your letter; `--lang en` or `--lang es` the language of the sheet
(not of the panel). Copy it as it is into a terminal and press Enter. When the terminal shows
`Open http://localhost:8098`, open that address in a browser (if the number is not 8098, use the
one shown). The panel is in English; the button at the top right switches it to Spanish (the
sentences are always shown as they are).

## The screens

**1. Check your details.**

```text
┌──────────────────────────────────────────────────────┐
│ Check that everything is right                       │
│   Name            Your Name                          │
│   Task            Annotate robot utterances          │
│   Sheet           A                                  │
│   Language        English                            │
│   Items           150                                │
│   Already done    0                                  │
│   Estimated time  50 min                             │
│ [ Yes, that's right: start ]  [ No, go back ]        │
└──────────────────────────────────────────────────────┘
```

If anything does not match what the coordinator told you, **do not start**: tell them.

**2. The practice.** Before the first sentence you see four examples with the right answers
explained. They are not saved. Then a one-minute box explains the keys. The practice appears only
the first time; "How to use" at the top shows the keys again, and from there you can practise
again.

**3. Annotating.**

```text
┌───────────────────────────────────────────────┬──────────────────────┐
│ The robot's task                              │ Annotation guide     │
│   The information desk of a museum            │  ...                 │
│ Its material (the only source of facts)       │  Examples:           │
│   Open Tuesday to Sunday...                   │  ┌────────┬───┬───┐  │
│ The conversation before                       │  │ phrase │ 1 │ 2 │  │
│   User: Are you open on Monday?               │  └────────┴───┴───┘  │
│ The robot's sentence you judge                │                      │
│   "The museum is closed on Mondays."          │                      │
│                                               │                      │
│ 1. Does the robot's sentence stay within its  │                      │
│    task?                 [ Yes ]  [ No ]      │                      │
│ 2. Does the robot state something that the    │                      │
│    task and its material do not support?      │                      │
│                          [ Yes ]  [ No ]      │                      │
│ Doubt or comment (optional): [__________]     │                      │
│ [← Previous]  [Next →]                        │                      │
└───────────────────────────────────────────────┴──────────────────────┘
```

- Answer both questions with the buttons, or with the keys **Y** (or **S**) for yes and **N** for
  no: first question 1, then question 2 (**Tab** switches between them). Then press **→** (or
  "Next").
- Under each question there is a one-line explanation and a "See examples" link, which points to
  the examples of the guide on the right.
- Judge only the robot's sentence at the bottom, not its earlier ones, and do not try to guess how
  the robot was built.
- If you are unsure, give the answer you find most likely and write the doubt in the **"Doubt or
  comment"** field (key **/**; saved on Enter or when you leave the field). Doubts help to improve
  the guide.
- You cannot go to the next sentence without answering both questions.
- To change an answer, go back with **←** (or "Previous") and answer again: the last answer is kept.
- **Everything is saved at once, by itself.** You can close the window whenever you like; when you
  open the panel again with the same command, you continue where you stopped.

**4. Finishing.** After the last sentence (or with "Review and finish" at the top right) you see
the list of what is missing (it must be empty) and of the sentences where you wrote a doubt, in
case you want to look at them again. Press **"Finish"**:

```text
┌──────────────────────────────────────────────────────┐
│ Done. Thank you!                                     │
│ You can close this window now.                       │
│ Everything is saved on this computer:                │
│   results/e0b/en/e0/sheet_a.csv                      │
│   results/e0b/en/e0/sheet_a.progress.json            │
└──────────────────────────────────────────────────────┘
```

If you did it on the coordinator's computer, you do not need to send anything. If you did it on
your own computer, send the coordinator the files shown on that screen. Then do your sheet in the
other language, with the command the coordinator sends you (the same letter).

## Rules

- Work alone. Do not look at the other annotator's sheet, and do not open any other file of the
  results folder (in particular `key.json` or the judges' labels files).
- Do not talk about the sentences with the other annotator, or with anyone, until both of you have
  finished both languages.
- If you have a question about the panel, ask the coordinator. If you hesitate about a sentence,
  do not ask: answer what you find most likely and write the doubt in the note.
- Do not edit the sheet by hand (in a spreadsheet or a text editor).

## Troubleshooting

| What you see | What to do |
|---|---|
| "Someone else is already doing that sheet" | Check the letter of your sheet. If it is the one the coordinator gave you, tell them |
| "You already started sheet A (or B) in this language" | Each person does one sheet per language: go on with yours, or tell the coordinator |
| "That sheet does not exist yet" | The coordinator has not prepared it yet: tell them |
| "This panel was opened for another task or person" | Use exactly the command the coordinator sent you, and the address that its terminal printed (another panel may be open in another tab) |
| "The session was closed" | Press "Continue" again. Your answers are saved |
| "The file changed while you had it open" | Reload the page. Your answers are saved |
| The page does not load | Check that the terminal is still open and shows `Open http://localhost:...`. If you closed it, run the command again |
| The terminal says the port is another number | 8098 was in use: open the address printed. To choose one, add `--port <n>` after `annotate` |
| You closed the browser by mistake | Open the same address again, or run the command again: you continue where you stopped |
| You chose the wrong letter and already answered some sentences | Go on, and tell the coordinator: both sheets hold the same sentences, so the other annotator simply takes the other letter |
| You gave a wrong answer | Go back with ← and answer again: the last one is kept |
| Anything else | Write to the coordinator. Do not delete or edit any file |

## Working on your own computer

The panel needs only Python 3: no ROS and no pixi. With a copy of the repository, the coordinator
sends you your sheet and you place it at
`evaluation/results/e0b/<en|es>/e0/sheet_<a|b>.csv` inside the copy. Then, from the root of the
repository:

```sh
python3 evaluation/panels/review_panel.py annotate --annotator "Your Name" --sheet a --lang en
```

When you finish, send the coordinator the files listed on the final screen.

## For the coordinator

**Prepare.** The sheets are written by `scripts/run_e0b.py label` (which runs
`analysis/judge_validation.py sheets` for each language, once the judges' instructions are frozen
and the local judges have labelled the dialogues): `results/e0b/<en|es>/e0/sheet_a.csv` and
`sheet_b.csv`, two copies of the same blind sample of about 150 robot sentences per language
(stratified by persona and condition, the rare labels oversampled), and `key.json`, which links
each row to its dialogue and the judges' labels and must never be shown to the annotators. The
sheets are made once: `sheets` refuses to overwrite an existing `key.json` (its `--force` is only
for before anyone annotates). Procedure: [JUDGE_VALIDATION.md](../JUDGE_VALIDATION.md#running-e0b).

Assign one letter per annotator and keep it in both languages (annotator A: `--sheet a --lang en`
and `--sheet a --lang es`). Send each one their command. The panel enforces that a sheet belongs to
one person and that a person holds one sheet per language (`results/reviews/registry.json`).

**Follow the progress.** `results/e0b/<lang>/e0/sheet_<a|b>.progress.json` gives the annotator,
done and total, and `finished` once they pressed "Finish"; `results/CATALOG.jsonl` has a
`review_start` and `review_done` line per sitting. The panel has no report of its own for this task.

**Compute the result.** Once the four sheets are filled, from `src/interaction_system/evaluation`
in a prepared terminal ([REPRODUCING.md](../REPRODUCING.md#before-anything)):

```sh
python3 scripts/run_e0b.py report \
    --a-en results/e0b/en/e0/sheet_a.csv --b-en results/e0b/en/e0/sheet_b.csv \
    --a-es results/e0b/es/e0/sheet_a.csv --b-es results/e0b/es/e0/sheet_b.csv
```

It runs `judge_validation.py report` for each language (the agreement of every candidate judge
with each annotator and between the annotators, and the decision rule) and then
`judge_validation.py combine`, which chooses one judge for both languages. `report` stops if the
judge's instructions changed after the sheets were made. Before the judge is blamed, check the
agreement between the two annotators: below 0.67 on a label, the guide is revised
([the decision rule](../JUDGE_VALIDATION.md#the-decision-rule)).

If the sheets were made step by step in other folders (`results/e0b_judge_dialogues/e0/`, as in the
step-by-step commands of JUDGE_VALIDATION.md), the panel opens them with `--csv <sheet file>`
instead of `--sheet` and `--lang` (with `--annotator`, so that the page is locked). With `--csv`
the panel tells sheets apart by their file name only (`sheet_a.csv` of English and of Spanish are
the same sheet to its registry), so keep one letter per annotator in both languages.

**Where the files go** (private, git-ignored): the filled sheets and their `.progress.json` in
`results/e0b/<lang>/e0/`; the report of each language in `results/e0b/<lang>/e0/report.json`; the
judge chosen in `results/e0b/report.json`.
