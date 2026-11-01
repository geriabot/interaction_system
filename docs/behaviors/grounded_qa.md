# Grounded question answering (`grounded_qa`)

The user asks and the robot answers **only from the information it was given**: it never answers
from the general knowledge of the language model, says so when it does not have the information,
and declines what is outside the task. Typical uses: the reception desk of a hospital or a hotel, a
museum, a station, a library. Tree `behavior_tree_xml/grounded_qa.xml`, skills
`interaction_intelligence/skills/grounded_qa.py`. [Index](README.md).

## Why it is built this way

An information-seeking dialogue is where a robot with a language model is most tempted to invent:
a question about the topic whose answer is not in the information still gets a fluent answer. So
the strategy separates three cases (in the material, in the topic but not in the material, outside
the task), abstains in the second instead of guessing, and verifies every answer against the
material before saying it.

## What it needs and returns

- **Needs:** the **material**: the resources of the request with plain text (a structured
  resource, such as a question bank, is not material). Optionally a resource named `handover`,
  whose content is the sentence that offers a person when the answer is not in the material
  ("The staff at the ticket desk can help you with that"). Without material the request fails
  before the robot speaks.
- **Returns:** `{questions, answered, abstained, declined}`; each question with its resolution
  (`answered`, `abstained` or `declined`) and the answer given.
- **Outcome:** `completed` when the user says they have no more questions.

## How each turn is understood

One question to the LLM (`answer_from_material`) reads the turn against the material and, when
the answer is there, drafts it with only facts of the material. A second question
(`verify_answer`) checks that every claim of the draft is supported by the material; only then is
it said. Advice of the robot's own is a claim the material does not support, even when it is
reasonable ("boarding closes 15 minutes before departure, so arrive half an hour early"): the
robot gives only what the material says, because the verification cannot tell sensible advice
from invented advice (decided by the author, 2026-11-01, in the review of the trap cases).

The first thing that question decides is **what the person does**: asks for something (a
question or a request, with or without a question mark: "I wanted to know the price", "No, but
what time does it close?"), **has finished** (wants nothing more: "No", "No, thanks", "That will
be all", "I have no more questions", "Goodbye") or **only reacts** (without asking anything and
without saying they have finished: "Thanks, that was helpful", "Okay", "What a shame", "30
euros, perfect"). Only a turn that asks can be answered from the material, and the code enforces
it whatever the rest of the model's answer says: the robot never volunteers information nobody
asked for, a property of the system like the verification's. Why (decided by the author,
2026-10-31; possibly reversible): in the second debugging round Llama 3.1 8B recognised 2 of 47
closings (Mistral NeMo 10 of 10) and answered 103 turns that asked nothing ("Thank you, that was
helpful" got a ticket price, which the verification passes because it is true); the rule "if
nothing is asked, it cannot be answered" was already in the question and was ignored. The former
rule "a 'no' alone is a comment, a 'no' with thanks the end" was dropped: after "anything else?"
a "no" is the end, and a thanks alone is a reaction (every answer ends with "anything else?", so
taking a thanks for the end would close too early: in the debugging, a user thanked and then
asked another question). The change is kept only if Qwen 3 8B and Mistral NeMo 12B do not get
worse in the next debugging round; if Llama still chooses badly, Llama is not fit (a limit of
the model).

| The user | The robot | Progress |
|---|---|---|
| Asks something whose answer is in the material | The answer, verified, then "Is there anything else you'd like to know?" | Yes |
| Asks something within the topic whose answer is not in the material | "I don't have that information." and the handover, if given (fixed phrases) | Yes |
| Asks something whose drafted answer is not supported by the material | The same abstention: an answer that does not pass the verification is never said | Yes |
| Asks something outside the task (also help with their own matters: what to buy or choose), or asks for an opinion or a service the robot does not give | "That is outside what we are talking about here, so I can't help you with it; I can only answer questions about the information I have." | Yes |
| Only reacts, without asking and without saying they have finished ("Thanks, that was helpful", "what a shame", the answer repeated back) | "Is there anything else you'd like to know?" (not recorded as a question) | Yes |
| Has finished: wants nothing more ("No", "No, thanks", "That will be all", "I have no more questions", "Goodbye"). Never a cancellation: the cancellation detector answers "finished" and leaves the turn to the strategy; if the robot did ask "do you want to stop?" and the user answers that they have no more questions, the same closing ([Spoken cancellation](cancellation.md)) | "Alright. Have a good day!" and the conversation ends (`completed`) | — |
| Asks to repeat the last answer | The last answer again (no LLM) | No |
| Anything else (off-topic noise, unintelligible) | "Could you repeat that?" | No |

The abstention, the declining and the closing are fixed phrases, never generated, so the robot
cannot add facts in them.

**Requests outside the task (decided by the author, 2026-11-01).** The tree counts the requests
in a row that are declined as outside the task; a question within the task (answered or abstained)
starts the count again, a reaction does not. After `max_off_task` (default 6; per request) the
robot closes with `close_off_task` (outcome `breakdown`, reason `Off-task: …`), so that the
requesting behavior knows the user wanted something else. **Sense first.** The understanding of a
turn first says whether the words make sense as something a person could say here; words that do
not (a word salad) are a turn not understood, whatever the rest of the answer says.

**Decided by the author, 2026-11-01.** What the person does has a fourth value, `unclear`
(unintelligible speech, a word salad, filler): a turn without progress, so that a sustained one ends
as a breakdown. A closing is verified before the dialogue ends (`verify_finished`: did the person
say they want nothing more? a question of any kind is not), as an answer is verified before it is
said; an unverified closing is a turn not understood ("Could you repeat that?").

## Tree

![The tree of grounded question answering](../../media/bt/grounded_qa.svg)

## Eventualities

| Situation | What happens |
|---|---|
| Silence | [Silence](silence.md) |
| "Stop" | [Spoken cancellation](cancellation.md); once confirmed the outcome is `cancelled` even if some questions were answered (`completed` means the whole task; the questions with their resolution travel in `final_frame_json`, and the requesting behavior decides what follows) |
| `max_stalled` turns in a row without progress | [Breakdown](no_progress.md) |
| The LLM fails reading a turn | `other`: the robot asks to repeat; a sustained failure ends as a breakdown |
| The LLM fails verifying an answer | The answer is not said: the robot abstains |
| No material in the request | `error` before the robot speaks |
| Turn limit, failure of the system | [Endings](endings.md) |

## Parameters

The limits are parameters of the orchestrator
([CONFIGURATION.md](../CONFIGURATION.md#interaction_orchestrator)); `max_turns`, `max_silence` and
`max_stalled` can also be set per request
([ROS_INTERFACES.md](../ROS_INTERFACES.md#dialogtask-goal)). The fixed phrases are
`phrases.qa_invite`, `phrases.qa_more`, `phrases.qa_abstain`, `phrases.qa_decline` and
`phrases.qa_bye` ([CONFIGURATION.md](../CONFIGURATION.md#interaction_intelligence)).
