# Future work

Work identified while building and evaluating the interaction system that is outside the version
evaluated in the paper. The code of that version is frozen until the paper is finished; these
items are addressed afterwards. Each item states what is missing, why it matters, and what it
touches.

## Detection of urgent situations and handover to a person

**What is missing.** The system does not recognise an urgent situation in the user's turn (a fall,
a pain, a request for help) and does not hand the conversation over to a person. The design that
was approved and not implemented:

- In every turn, before anything else, one narrow question to the language model classifies the
  user's turn as an urgency that needs a person, a request to finish the conversation, or neither.
  No keyword list is used, since the meaning of a word depends on its context. This classification
  would replace the current cancellation check (exact stop commands first, then the language
  model).
- On an urgency, in every strategy, the robot leaves the task, says a fixed line (never generated,
  with no advice), publishes a notice for the robot's architecture, which alerts a person, and the
  conversation ends with a new outcome, `escalated`. It is a handover to a person, not a clinical
  triage.

**Why.** A robot that holds conversations in a care setting has to react to an urgency instead of
continuing its task. It is stated as a limitation of the evaluated version.

**What it touches.**

- The shared subtrees of every strategy and the cancellation mechanism
  ([docs/behaviors](docs/behaviors/README.md)); the outcome table and the result returned to the
  requester ([docs/ROS_INTERFACES.md](docs/ROS_INTERFACES.md)).
- The rules of the LLM-led condition, which would handle urgencies with the same instruction.
- Evaluation: first the detector alone, on hand-annotated lines in English and Spanish (urgencies
  phrased in different ways, non-urgent health remarks, false alarms such as figurative uses or
  proper names, ordinary task lines), with its detection and false-alarm rates; then, in the
  robustness experiment, the method against the LLM-led conversation and against the method
  without detection, on urgencies injected at a fixed turn: detection rate, false alarms, turns
  until the reaction, and medical advice given (none expected). The cancellation ablations would be
  redefined on the new per-turn classification.
- With real users, a task card that provokes it, which requires an amendment of the ethics approval.

## Topic-bounded social conversation (`topic_chat`)

**What is missing.** An eighth dialogue strategy, designed and not implemented: a conversation with
nothing to collect, on a topic set by the requester ("their village in the sixties", "the photograph
of their wedding"), as in companionship or reminiscence sessions. The design:

- The request gives the topic, optional material (the description of a photograph, facts provided
  by the family or the staff, supporting questions) and limits (duration or number of turns, topics
  to avoid).
- In every turn, one narrow question classifies the user's turn: on the topic, drifting (the robot
  bridges back to the topic gently, not with an abrupt change), a topic to avoid (the robot changes
  the subject), a request to end, or an urgency (the mechanism above).
- The robot's line comments on what was said and ends with one open follow-up question on the
  topic; the robot keeps the initiative. It never states facts about the person that are not in
  the material.
- At the limit it closes with a short summary and a goodbye, not an abrupt end.
- It returns the duration, the supporting questions covered, the topics that came up, and who ended
  the conversation.

**Why.** Companionship is the most frequent use of social robots against loneliness in older
people, and reminiscence a common practice in their care. It is the one strategy without a task
result, so it is evaluated differently from the others.

**What it touches.**

- A new Behavior Tree and its skills, its document in [docs/behaviors](docs/behaviors/README.md),
  its entry in the catalog that the behavior choice reads, and its rules in the LLM-led condition.
- Evaluation in simulation, automatic only: staying on the topic, respecting the limits and the
  topics to avoid, no invented facts about the person, coverage of the supporting questions; its
  scenarios added to every experiment that runs all the strategies, and its tasks to the behavior
  choice check. How people experience it (companionship, mood) needs a study with older adults.
- The design literature is collected in the paper's notes on the strategies (reminiscence, topic
  shifts and transitions, question asking).

## Other directions

- A study with older adults and people with mild cognitive impairment in a care setting,
  co-designed with them and with clinicians.
- Turn-taking beyond silence thresholds (predictive turn-taking, listening while the robot speaks).
- New dialogue strategies.
