# E4: reviewing the transcripts of the real sessions

[All the hand tasks](../../HUMAN_REVIEW.md) · Who: one member of the team · When: after each
session, and always before the judge reads the sessions · How long: every stored conversation
(64 in E4: 16 participants × 4), a few minutes each.

This review is not done in the review panel: it runs in the terminal, with
`real/real_sessions.py review`.

## What and why

In E4 real participants talk with the robot, following task cards with fictitious names and data.
Someone may still say real personal data by mistake (their own name, an address, a phone number
that is not on the card). The ethics protocol says that a person reads every stored conversation
and replaces any such data before anything is sent to the judge or shown to the annotators. The
script replaces each text you give it in the whole record of the conversation and in its logs,
does not keep what was replaced, and marks the record as reviewed (by whom, when). The judge's
script refuses the sessions while any record is not reviewed.
More: [REAL_SESSIONS.md](../REAL_SESSIONS.md#scoring-the-sessions).

## Before you start

- The participant's session has been run and stored, in
  `~/.ros/interaction_real/<campaign>/<participant>/` on the computer of the sessions (the campaign
  of real sessions, e.g. `pilot`, and the participant's code, e.g. `P07`).
- You know the participant's code and have that participant's task cards at hand, to tell the
  fictitious data of the cards from real data.
- You need the workspace installed with pixi on that computer. ROS is not needed for this command.

## Running the review

From `src/interaction_system/evaluation` in a pixi shell (from the workspace root: `pixi shell`,
then `cd src/interaction_system/evaluation`):

```sh
python3 real/real_sessions.py --campaign <campaign> review P07 --by "Your Name"
```

Options: `--k 2` reviews only trial 2; `--again` reviews trials already reviewed (they are skipped
otherwise); `--out <folder>` if the sessions are not in `~/.ros/interaction_real` (the campaign's
folder is `<folder>/<campaign>`).

For each trial not yet reviewed, the terminal shows:

1. A header, `=== P07 trial 1: <scenario> ===`.
2. The transcript, one numbered line per turn: `ROBOT` or `USER` (a robot filler is marked
   `(filler)`), and the text.
3. "What the speech recognition heard": every transcription, also those not taken as turns. Read
   it too: it may hold more than the turns.
4. The prompt: "Personal data the participant said that is not on the card (a name, an address, a
   phone...): write 'real text => replacement' (or the real text alone: [DATO]); an empty line
   ends."

Then type one line per piece of personal data, and press Enter on an empty line to finish the
trial:

```text
> Juan Real Surname => Ana García
> 600 000 000
>
```

The first line replaces the name with another one; the second replaces the number with `[DATO]`.
If the trial holds no personal data, just press Enter on the empty line: the trial is marked as
reviewed with no replacement. The terminal confirms
`Trial 1: reviewed by Your Name; <n> replacements in the record, <m> in its logs`.

## Rules

- Replace only real personal data that is not on the card. The fictitious data of the cards stay.
- Write the whole text to replace, as it appears (a full name, a whole number). The
  replacement ignores capitals and runs over the whole record, its technical fields included, and
  over the logs: a short text (a first name of three letters, a common word) would also be
  replaced inside other words. Never replace a common word.
- Do not copy the personal data anywhere else (notes, messages).
- A review cannot be undone (what was replaced is not kept). If you made a mistake, tell the
  coordinator; `--again` lets you review the trial once more, for instance to replace something
  you missed.

## Troubleshooting

| What you see | What to do |
|---|---|
| `P07 has no stored trial` | Check the participant's code, and `--out` if the sessions are in another folder |
| `Trial 1: already reviewed by ...` | It was reviewed before. `--again` to review it again |
| Fewer replacements than the lines you typed | A text did not appear as you wrote it: check spelling and spaces, and run with `--k <trial> --again` |

## For the coordinator

### Prepare

There is nothing to generate: the trials are stored by `real_sessions.py trial`. Review each
participant after their session (or all of them at the end), and always before
`analysis/judge_runs.py` runs on the campaign's folder, `~/.ros/interaction_real/<campaign>`.

### Check that everything is reviewed

`judge_runs.py` stops and lists the records not reviewed
yet; the field `reviewed` (`by`, `time`, `replacements`) of each record says who reviewed it.
After the review, run the judge on the sessions and, once it has labeled them, its confirmation by
two annotators ([judge_confirmation.md](judge_confirmation.md)).

### Where the files go

The reviewed records and logs replace the originals in
`~/.ros/interaction_real/<campaign>/<participant>/` (outside the repository, never published).
Nothing else is written.
