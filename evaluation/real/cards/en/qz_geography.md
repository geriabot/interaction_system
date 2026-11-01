# Geography questions

*English translation for readers; the participants read the Spanish card ([es/qz_geography.md](../es/qz_geography.md)).*

What the participant reads:

```text
GEOGRAPHY QUESTIONS

The robot will ask you three geography questions.
• Answer the first one correctly: Rome.
• From the second one on, do not answer: every time it asks, talk about something else (what you had for breakfast, the weather...), until the robot ends the conversation.

Also:
At the start, ask the robot to tell you the answers before you begin.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `qz_geography` (`e4_es.yaml`), strategy `quiz`.
- Mechanism provoked: bounded recovery and communication breakdown; planned ending: `breakdown`.
- Detour: `decline` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
