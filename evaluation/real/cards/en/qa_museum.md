# At the museum entrance

*English translation for readers; the participants read the Spanish card ([es/qa_museum.md](../es/qa_museum.md)).*

What the participant reads:

```text
AT THE MUSEUM ENTRANCE

You are at the entrance of a science museum and the robot attends to the visitors. Ask it:
• whether the museum opens on Mondays; when it answers, comment «oh, what a shame»,
• how much a ticket costs for a 70-year-old person, and ask it to repeat the answer,
• whether there is a café.
When you have no more questions, tell it.

Also:
At some point, ask the robot what it thinks of the last football match.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `qa_museum` (`e4_es.yaml`), strategy `grounded_qa`.
- Mechanism provoked: abstention, a comment and a request to repeat; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
