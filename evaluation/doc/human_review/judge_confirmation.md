# Confirming the judge on a campaign

[All the hand tasks](../../HUMAN_REVIEW.md) · Who: **two annotators, each alone** · When: **after
a campaign has been judged**: E1 (and its role swap), the simulated twin of E4, and the real
sessions of E4 once every transcript is reviewed · How long: about 20 s per sentence.

**Not yet supported by the panel.** The sample of this task is a CSV file that the review panel
cannot open; it is filled in a spreadsheet, by the procedure below. What is missing is listed at
the end of the page.

## What and why

The LLM judge was validated against two people once, in E0b, on dialogues made for that purpose.
Each campaign has its own sentences (in E1, those of a large cloud model; in E4, those of real
participants), so the agreement is checked again on a small sample of each campaign: the judge is
used for a campaign only if it passes there too ([JUDGE_VALIDATION.md](../JUDGE_VALIDATION.md#confirmation-on-each-campaign)).
You answer the same two questions as in E0b, on each sentence of the sample, without seeing the
judge's labels, the condition or the model.

## Before you start

- The coordinator sends you **your copy** of the sample file (a CSV), with your letter (A or B).
- Read the questions and the guide: [the E0b page](e0b_annotation.md#what-and-why) and
  [the annotation guide](../JUDGE_VALIDATION.md#the-annotation-guide) (in Spanish:
  [panels/annotation_guide_es.md](../../panels/annotation_guide_es.md)). If you did E0b, the
  questions are the same.

## Filling the sample

Open your copy in a spreadsheet program (LibreOffice Calc, Excel...) and keep it as CSV, UTF-8,
when saving. Each row is one robot sentence:

| Column | Content |
|---|---|
| `id` | Do not change it |
| `task`, `material` | The robot's task and its material (the only source of facts) |
| `history` | The conversation before the sentence |
| `utterance` | The robot's sentence you judge |
| `human_a_in_frame`, `human_a_unsupported` | Annotator A's answers |
| `human_b_in_frame`, `human_b_unsupported` | Annotator B's answers |

Fill **only the two columns of your letter**, with `1` (yes) or `0` (no):

- `..._in_frame`: does the robot's sentence stay within its task? (question 1 of E0b)
- `..._unsupported`: does it state something that the task and its material do not support?
  (question 2 of E0b)

There is no column for doubts: if you hesitate, give the answer you find most likely and write the
doubt to the coordinator (the `id` and the doubt), after you finish. Do not change, add, sort or
delete rows or other columns. A row left empty in your columns is not counted.

When you finish, save the file as CSV and send it to the coordinator.

## Rules

As in E0b: work alone, do not see the other annotator's copy, do not discuss the sentences with
anyone until both of you have finished, do not try to guess the condition or the model. In E4 the
sentences come from real conversations already reviewed for personal data; if you still see
something that looks like real personal data (a real name, an address or a phone number that are
not on a task card), tell the coordinator at once and do not copy it anywhere.

## Troubleshooting

| Problem | What to do |
|---|---|
| The accents look wrong when you open the file | Open it as UTF-8 (in LibreOffice: in the import dialog, Character set "Unicode (UTF-8)", separator comma) |
| The program saved it in another format | Save again as CSV, UTF-8, comma-separated, keeping the first row of column names |
| You filled the other letter's columns | Move your answers to your columns, or tell the coordinator |

## For the coordinator

**Prepare** (from `src/interaction_system/evaluation` in a prepared terminal,
[REPRODUCING.md](../REPRODUCING.md#before-anything)), once the campaign is judged with the judge
chosen in E0b (its `--tag`):

```sh
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --tag <tag> --sample 400 \
    --sample-out results/e1_bounded_vs_llm_led/annotation.csv
```

Sizes (author, 2026-10-31): 400 in E1 and in its role swap (100 per model and condition: the
sample also corrects the comparisons, JUDGE_VALIDATION.md), 200 in the E4 twin and the real
sessions. The sample is drawn with seed 7 (`--seed`), balanced across the groups of model and condition
(`--sample` divided by the number of groups, from each), among the sentences the judge labelled
without error. For the twin of E4 the folder is `results/e4_sim_es`; for the real sessions,
the folder of their campaign, `~/.ros/interaction_real/<campaign>`, and `judge_runs.py` refuses it while any transcript is not reviewed
([e4_transcript_review.md](e4_transcript_review.md)).

The file holds the columns of both annotators. To keep them blind to each other, give each one a
copy (for example `annotation_a.csv` and `annotation_b.csv` in the same folder) and, when both are
back, copy annotator B's two columns into annotator A's file, row by row by `id`, into one file.
There is no script for this split and merge.

**Compute the result:**

```sh
python3 analysis/judge_runs.py results/e1_bounded_vs_llm_led --tag <tag> \
    --agreement results/e1_bounded_vs_llm_led/annotation.csv
```

It prints, for each label (`in_frame`, `unsupported`), the number of sentences annotated by both
and Cohen's kappa of the judge with annotator A, with annotator B, and between A and B. Apply the
[decision rule of E0b](../JUDGE_VALIDATION.md#the-decision-rule) to those figures by hand.

**Where the files go** (private): the sample and the filled copies in the campaign's results
folder (`results/<name>/`, git-ignored), or in `~/.ros/interaction_real/<campaign>/` for the real sessions.

**What is missing.**

- The panel cannot open this sample: `annotate --csv` needs the columns of the E0b sheets (`id`,
  `task`, `material`, `conversation_before`, `robot_utterance`, `in_frame (1/0)`,
  `unsupported (1/0)`, `note`) and stops on this file. So there is no tutorial, no saving per
  answer, no doubt note and no lock per annotator; and the one file holds the columns of both
  annotators.
- `--agreement` gives the kappas only: no interval, no sensitivity or specificity, no minimum
  number of positives, no exclusion of the sentences of a system of the judge's own family, and
  no decision. JUDGE_VALIDATION.md says the judge is used in the campaign "only if it passes
  there too", without fixing the size of the sample or the rule on a sample this small.
- The size is not fixed consistently: [EXPERIMENTS.md](../EXPERIMENTS.md#hand-reviews) gives about
  20 dialogues per campaign, stratified by condition and persona (each campaign is in one
  language); the command of
  [REPRODUCING.md](../REPRODUCING.md#e1-and-the-role-swap) draws 200 sentences, balanced by model
  and condition only. Fix it before drawing.
- A route the tools allow, not adopted as the procedure: `analysis/judge_validation.py sheets
  <campaign folder> --n <n>` writes `e0/sheet_a.csv`, `sheet_b.csv` and `key.json` in the campaign
  folder, which the panel opens with `annotate --csv <sheet> --annotator "Name"`, and
  `judge_validation.py report <campaign folder> --a ... --b ... --order <tag>` applies the E0b rule.
  That sampler takes only the sentences labelled by every judge whose labels file is in the
  folder, stratifies by persona and condition with no judge choosing the sentences (decided
  2026-11-02; the judges' labels only size it, by the E0b size rule), and marks a label with fewer
  than 30 human positives as not validated. `judge_runs.py --sample` does not use the judge's
  labels either. Whether to use it for the
  confirmation is a decision of the authors, to be written in JUDGE_VALIDATION.md before any
  campaign is confirmed.
