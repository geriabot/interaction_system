# Satisfaction survey

*English translation for readers; the participants read the Spanish card ([es/qn_museum_satisfaction.md](../es/qn_museum_satisfaction.md)).*

What the participant reads:

```text
SATISFACTION SURVEY

You have just visited a museum and the robot will give you a survey of four statements. Answer like this:
• you enjoyed the visit very much,
• the information was quite easy to understand,
• for the third statement, first ask it what it means; then say «something between a little and quite a lot» and, when it repeats the options, say «a little»,
• you would recommend the museum very much.

Also:
At some point, ask the robot to tell you your total score.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `qn_museum_satisfaction` (`e4_es.yaml`), strategy `questionnaire`.
- Mechanism provoked: items read literally; a question about an item's meaning and an answer between options; planned ending: `completed`.
- Detour: `decline` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
