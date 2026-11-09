# Urgencies: handing the conversation over to a person

A user may tell of something that needs a person now: a fall, a strong or sudden pain, trouble
breathing, a call for help, to them or to someone with them. The robot then leaves the task at
once, says a fixed line that gives no advice ("I'm letting someone know right now, so that a
person can help you."), publishes a notice for the robot's architecture, which alerts a person,
and the conversation ends with the outcome `escalated`. It is a handover to a person, not a
clinical triage: the robot does not ask about symptoms nor suggest what to do.

Shared by every dialogue behavior: subtree `CancelCheck` of `common.xml`, skill `cancel_check`
(`interaction_intelligence/skills/common.py`), the same check as the
[spoken cancellation](cancellation.md). [Index](README.md).

## How it works

1. **Detection.** Every user turn goes first to `CancelCheck`, before the behavior, and its first
   question is whether the user tells of an urgency (`detect_urgency`, one short question with a
   yes-or-no answer). An urgency counts whatever else the words say, since it can arise from the
   task itself ("I've done the step, but I feel very dizzy"), and also when it is said calmly.
   The question names what is not an urgency: a health remark that needs no one now ("my back is
   stiff in the mornings"), a past event, a figurative use ("these numbers give me a headache"),
   and a line of the task's own scene or material (a line in character in a role play of an
   emergency call, a question about what to do after a fall), unless the user steps out of it to
   say it is happening now.
2. **A question of its own.** The urgency was first a fourth answer of the question that reads a
   request to stop ([spoken cancellation](cancellation.md)), which would have cost no extra call.
   Measured with the small local model, that lowered how well a request to stop was read (from
   92.1% to 88–89% of the cases of the phase A dataset `cancel_detect`), and it detected fewer
   urgencies (88%) than a question of its own (96%), which leaves the reading of a request to stop
   as it was. The extra call takes about 0.1 s per turn with that model.
3. **No keywords.** The meaning of "help" or "socorro" depends on its context (a hard quiz
   question, a song, a name), so the detection is the LLM's alone. An exact stop command of the
   language file asks no urgency question: it is decided without the LLM, and none of them is an
   urgency. Asked whether they want to stop, a user may answer with an urgency ("no, I've just
   hurt myself"): the urgency question comes first in that turn too.
4. **The handover.** The line is fixed (`close_escalated` in the language file): never generated,
   so it gives no advice and works whatever the model says next. The dialogue ends (`done`), and
   the outcome is `escalated` whatever else had happened, with `failure_reason`
   `Urgency, handed over to a person: <what the user said>`. The interaction intelligence publishes
   the notice on `/interaction/urgency` at once, before the closing line is said
   ([ROS_INTERFACES.md](../ROS_INTERFACES.md)).

## Limits

- With the LLM down, an urgency is not detected: the system falls back to the exact stop commands
  only, and a keyword list would raise false alarms on figurative uses and names. The notice of
  the failed question (`/llm/error`) still reaches the operators.
- The handover depends on the robot's architecture: the interaction system says the line and
  publishes the notice; who is alerted, and how, is decided outside it.
- The detection is measured on its own before it is used in a study (the phase A dataset
  `urgency_detect`: urgencies phrased in different ways, mild health remarks, past events,
  figurative uses, lines of a scene, proper names, ordinary task lines, in English and Spanish),
  with its detection and false-alarm rates. With the small local model the false alarms gather in
  two kinds of case: a line in character in a rehearsal of an emergency call, and a question about
  what to do after a fall. A false alarm ends the conversation and alerts a person; a missed
  urgency leaves the user without one.

## The LLM-led condition

The conversation led by the LLM (the evaluation's comparison) has the same rule in its
instruction: on such a situation it says only that it is letting someone know, with no advice,
and closes with outcome `escalated`.
