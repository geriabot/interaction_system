# Making tea

*English translation for readers; the participants read the Spanish card ([es/pr_tea.md](../es/pr_tea.md)).*

What the participant reads:

```text
MAKING TEA

The robot will guide you to make tea (pretend you are making it).
• Do each step and tell it when you have done it.
• When it tells you to let the tea steep, stay silent, without saying anything, until the robot speaks to you again; then tell it you are done.
• You do not take your tea with milk or sugar: at the last step, tell it you want to skip it.

Also:
At some point, ask the robot what its favorite food is.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `pr_tea` (`e4_es.yaml`), strategy `procedure`.
- Mechanism provoked: silence during a step with a declared duration; an optional step skipped; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
