# At the café

*English translation for readers; the participants read the Spanish card ([es/sf_coffee.md](../es/sf_coffee.md)).*

What the participant reads:

```text
AT THE CAFÉ

The robot is the waiter of a café. Order:
• a medium latte, with oat milk,
• under the name Laura.
• Halfway through the order, tell it «Stop, forget it». When it asks whether you want to finish, say neither yes nor no: give it the detail it had asked you for and carry on with the order.
When the robot reads the order back, tell it whether it is right. If something is wrong, correct it.

Also:
At some point, ask the robot what the weather will be like tomorrow.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `sf_coffee` (`e4_es.yaml`), strategy `slot_filling`.
- Mechanism provoked: a cancel confirmation answered with the slot asked: only a yes stops, so the robot resumes and asks again; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
