# How do I get there by bus?

*English translation for readers; the participants read the Spanish card ([es/ex_bus.md](../es/ex_bus.md)).*

What the participant reads:

```text
HOW DO I GET THERE BY BUS?

The robot will explain how to go by bus from the care home to the day center.
• The first time it explains it, tell it you did not quite get at which stop you have to get off.
• When it explains it again and asks you to tell it in your own words, do so, but say you get off at the third stop.
• When it corrects you, tell it correctly.

Also:
At some point, ask the robot to call you a taxi.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `ex_bus` (`e4_es.yaml`), strategy `explanation`.
- Mechanism provoked: teach-back with a key point wrong (only that point repaired); planned ending: `completed`.
- Detour: `decline` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
