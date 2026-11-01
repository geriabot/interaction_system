# Ordering a pizza

*English translation for readers; the participants read the Spanish card ([es/sf_pizza.md](../es/sf_pizza.md)).*

What the participant reads:

```text
ORDERING A PIZZA

The robot takes orders at a pizzeria. Order:
• a large pizza with mushrooms and olives,
• for delivery, to Calle Mayor 5.
• When it asks you for a contact phone number, tell it you would rather not give it.
When the robot reads the order back, tell it whether it is right. If something is wrong, correct it.

Also:
Try to add pineapple to the pizza (it is not on the menu). If the robot does not let you, carry on with your order.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `sf_pizza` (`e4_es.yaml`), strategy `slot_filling`.
- Mechanism provoked: a slot the user does not give (left unanswered); planned ending: `partial`.
- Detour: `not_allowed` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
