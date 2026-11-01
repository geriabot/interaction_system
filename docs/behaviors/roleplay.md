# Role play (`roleplay`)

The robot plays a character of a scene so that the user can practice a situation (a job
interview, a conversation in another language), gives hints out of character on request, and at
the end steps out of the role and scores the user from 1 to 5 on each criterion of a rubric,
citing the user's own lines as evidence, with feedback. Tree `behavior_tree_xml/roleplay.xml`,
skills `interaction_intelligence/skills/roleplay.py`. [Index](README.md).

## What it needs and returns

- **Needs:** the scene: the first resource that is a JSON object
  `{scenario, role, criteria: [...]}`, with optional `user_goal` (what the user practices) and
  `max_exchanges` (user turns in the scene, 10 by default and at least 6, `MIN_EXCHANGES`, so
  that the user has room to show every criterion; only lines of the scene count, not hints or
  requests to repeat). A criterion is a string or
  `{name}`; at least one is required.
- **Returns:** `{scenario, role, exchanges, max_exchanges, ended_by, scores, average, highlight,
  improve}`: each score with its criterion, the score, the evidence (the user's own lines) and a
  comment; who ended the scene (`ended_by`: `user`, `scene` or `limit`); and the feedback.
- **Outcome:** `completed` once the scene was evaluated on every criterion, however it ended;
  `partial` when the evaluation could not score some criterion (the model failed), with
  `Criteria not scored: ...` as the reason and the scores it did give, as the other strategies
  end with an element left unfinished.

The scene is hidden from the context of the other questions, so the character never sees the
rubric.

## How each turn is understood

One question to the LLM (`classify_roleplay_turn`):

| The user | Label | The robot |
|---|---|---|
| A line of the scene | `continue` | The character replies (a line that repeats an earlier question is asked again once) |
| Did not hear or understand the character's last line, or asks to hear it again | `repeat` | The first time, the character's last line literally (no LLM, the request kept out of the scene's transcript); after that, the character says it again in other words. A turn without progress |
| Asks for help | `coach` | A hint out of character, kept out of the scene's transcript |
| Wants to stop the practice | `end` | Steps out of the role and evaluates |
| The scene reached its natural end | `scene_end` | Steps out of the role and evaluates |
| — (the last exchange allowed: `max_exchanges`, capped by `max_turns`) | `limit` | Steps out of the role and evaluates |
| — (the model gives no valid reading: no answer, or not what was asked) | `unclear` | Asks to repeat with a fixed phrase ("Could you repeat that?"), out of character; the line is kept out of the scene's transcript. A turn without progress |

Only `continue` turns count as exchanges: a hint, a request to repeat or a turn not understood
does not use up the scene. Every line of the scene makes progress; requests to repeat and turns
not understood do not, so `max_stalled` of them in a row end the dialogue with a
[breakdown](no_progress.md): a model that is down breaks a role play down as it does every other
strategy (until 2026-10-30 a turn it could not read was taken as a line of the scene, so the
scene ran to its limit and closed without scores). The exchange limit is capped by
the request's turn limit so that the user always gets the evaluation.

Before the scene, the robot introduces it out of character (`roleplay_intro`): the situation, the
role it plays (named only: not how the character behaves or what it will ask, which would give
away the rubric) and that the user plays themself, never the other way round; then how to ask for
a hint and to finish.

## Tree

![The tree of the role play](../../media/bt/roleplay.svg)

## Eventualities

| Situation | What happens |
|---|---|
| Silence | [Silence](silence.md) |
| `max_stalled` requests to repeat or turns not understood in a row | [Breakdown](no_progress.md): closing line, outcome `breakdown`, no evaluation |
| "Stop" (spoken cancellation) | [Spoken cancellation](cancellation.md), and once confirmed (or at once with the model down), stopping and finishing lead to the same place: when the user said at least one line of the scene, the robot steps out of the role and gives the evaluation of the exchanges so far (the feedback and the scores, `ended_by` `user`, in `final_frame_json`); outcome `cancelled` by the user. Before the first line there is nothing to score: the cancel closing. If no criterion can be scored (the model fails), the cancel closing too. "I want to stop the practice" inside the scene is `end`: evaluated, outcome as the scene's (decided by the author, 2026-10-30; until then a cancellation ended without evaluation, and the English introduction told the user to say "stop", an exact stop command: it now says "finish") |
| The LLM fails classifying a turn | `unclear`: the robot asks to repeat, a turn without progress; a sustained failure ends as a breakdown |
| The LLM fails evaluating the scene (some or every criterion) | The feedback says the scores obtained (or only the fixed closing phrase); outcome `partial`, `Criteria not scored: ...` |
| The LLM fails generating a line | A fixed phrase of the language (the character's: "Go on, I'm listening.") |
| A scene without criteria | `error` before the robot speaks |
| Turn limit, failure of the system | [Endings](endings.md) |

## Parameters

`max_exchanges` is a field of the scene ([above](#what-it-needs-and-returns)). The limits are
parameters of the orchestrator ([CONFIGURATION.md](../CONFIGURATION.md#interaction_orchestrator));
`max_turns`, `max_silence` and `max_stalled` can also be set per request
([ROS_INTERFACES.md](../ROS_INTERFACES.md#dialogtask-goal)).
