# Recovery and communication breakdown

When the robot does not understand a turn, or the user asks for something again, it tries to
repair the exchange, but the repairs are bounded: a person who cannot follow is not kept in an
endless loop of "could you repeat that?". There are two levels.

1. **Per element** (a slot, a question, a step), inside each behavior: repeat, then simplify, then
   leave that element unfinished and go on.
2. **For the whole conversation**, shared: after `max_stalled` user turns in a row without
   progress, the robot says that it seems they are not understanding each other and closes the
   conversation with the outcome `breakdown`, so that the requesting behavior knows why and can
   act (call a person, try later).

The LLM only classifies each turn; the tree counts and decides. Subtrees `NoProgress` and
`CountProgress` of `common.xml`. [Index](README.md).

## Per element: the recovery ladder

| Behavior | Turn that needs a repair (not always a [turn without progress](#for-the-whole-conversation-the-breakdown-exit)) | 1st repair | 2nd repair | Then |
|---|---|---|---|---|
| [Slot filling](slot_filling.md) | No value at all, no question, no "I don't know" | Asks again | The last asking, different (a closed question) | At `max_attempts` failed attempts: the slot is left unanswered |
| [Quiz](quiz.md) | Not an answer (asks to repeat or clarify, did not hear) | The question again, literally | The question in simpler words, without revealing the answer | At `max_attempts`: the question is left unanswered (not graded wrong) |
| [Procedure](procedure.md) | "I'm stuck" (a problem) | Help with the step | More help | At the `max_attempts`-th problem on the step: it is marked stuck and the procedure goes on |
| [Explanation](explanation.md) | A teach-back with a critical point missing or wrong, or not understood | Explains again only the points missing or wrong (or the whole topic) | Again, in different words | After `max_attempts` failed checks: closes by reminding the user of the critical points, outcome `failed` |
| [Questionnaire](questionnaire.md) | An answer between options | The options again, without suggesting any | The options again | At `max_attempts`: the item is left without an answer |
| [Role play](roleplay.md) | Did not hear or understand the character's last line | The line again, literally | The character says it again in different words | `max_stalled` such requests in a row: [breakdown](#for-the-whole-conversation-the-breakdown-exit), with the evaluation of the scene once the user said a line of it |

Every behavior gives up on an element at its `max_attempts`-th failure, so an element is offered
at most `max_attempts` times. `max_attempts` must be smaller than `max_stalled` (the defaults are
3 and 4), so that a person who fails every time still meets the whole ladder of an element before
the conversation ends as a breakdown.

An element left unfinished makes the outcome `partial` (a procedure step only if it is not
optional), and the result says which.

The user can also skip an element on purpose: "I don't know" or "skip it" for a slot (left
unanswered), "skip" for a step. "I don't know" in a quiz is an answer (wrong). Skipping on
purpose is an answer, a turn with progress; an element left unfinished because its ladder ran
out is not.

## For the whole conversation: the breakdown exit

`ListenToUser` sets `progress := true` on every user turn. The behavior's branches for a turn
without progress call `NoProgress` (`stalled += 1; progress := false`), as the condition of an
if / then / else: *then* the behavior's repair, *else* (`stalled` reached `max_stalled`)
`EndDialogue` with the reason `breakdown`. After the behavior handled the turn, `CountProgress`
resets `stalled` if the turn had progress.

The count goes on across elements: giving up an element is itself a turn without progress, so a
person who fails every element reaches the breakdown instead of being taken through every
element of the task. But the element presented after one was given up always gets its first
repair: when a behavior gives up an element and presents the next, it sets `new_element`, and
the first turn without progress on the new element never ends the conversation (it is counted,
and the repair follows). For example, with the defaults (`max_attempts` 3, `max_stalled` 4), a
quiz question not understood three times is left unanswered (`stalled` 3); a "Sorry?" to the
next question is counted (4) and the question is asked again; the next turn without progress
ends the conversation as a breakdown. Without this rule that "Sorry?" would have ended it before
the new question was ever repeated.

Turns that count as without progress:

| Behavior | Turns |
|---|---|
| Slot filling | Gathering: nothing understood (no value, no question, no skip), the turn that leaves the slot asked unanswered at its `max_attempts`-th failed attempt, and a request to hear the question again (with its own ladder per slot: literal, simpler, the options; never a failed attempt). Confirming: an unclear answer, and a request to hear the data again |
| Quiz | A reply that is not an answer (also the one that leaves the question unanswered) |
| Procedure | Unclear (a reminder is given), and a request to repeat the step |
| Explanation | A reply that is none of the expected ones (off-topic, unintelligible): the check is asked again; and a request to hear it again (the last line again, then in other words), which is not a failed check |
| Role play | A request to repeat the character's last line, a line that makes no sense, and a turn the LLM could not read (every other line of the user is part of the scene) |
| Grounded question answering | A request to repeat the last answer, and a turn not understood (also a word salad that would otherwise get an abstention); a reaction ("Okay") is neutral: it neither resets nor increases the count |
| Questionnaire | An answer between options (with its own limit per item: the options again, then the item without an answer), a request to repeat the item, and a turn not understood |

A request to repeat counts because a person who asks for everything again and again is not
following; one repetition is harmless, since any turn with progress resets the count.

## Trees

![NoProgress](../../media/bt/common/NoProgress.svg)

![CountProgress](../../media/bt/common/CountProgress.svg)

## Edge cases

| Situation | What happens |
|---|---|
| `max_stalled` turns in a row without progress | The closing line "It seems we are not understanding each other well. Let's leave it here and pick it up another time.", outcome `breakdown`, reason "Communication breakdown: max_stalled turns in a row without progress" |
| `max_stalled` is 0 | No breakdown exit: only the per-element ladders and the turn limit bound the conversation |
| The LLM fails while classifying a turn | The turn is read as not understood (the safe default of each question), so a sustained LLM failure ends as a breakdown, not in an endless loop |
| A filler word from the user ("hmm") | Dropped as noise: a silence, not a turn ([Silence](silence.md)) |

## Parameters

`max_stalled` and `max_attempts` are parameters of the orchestrator
([CONFIGURATION.md](../CONFIGURATION.md#interaction_orchestrator)); a request can set its own
`max_stalled` and `max_attempts` ([ROS_INTERFACES.md](../ROS_INTERFACES.md#dialogtask-goal)). What
`max_attempts` counts in each behavior is in the table of the
[recovery ladder](#per-element-the-recovery-ladder).
