# Practicing a job interview

*English translation for readers; the participants read the Spanish card ([es/rp_interview.md](../es/rp_interview.md)).*

What the participant reads:

```text
PRACTICING A JOB INTERVIEW

The robot will play the manager of a bookshop who interviews you for a job.
• Answer as someone who loves books and has worked two summers in a supermarket.
• Answer clearly and show that you want the job, but do not give concrete examples.
• In your second answer, stay silent for a while, thinking, before you answer.
• Later on, ask it «Can we stop?» and, when it asks whether you want to finish, say no, let's carry on.

Also:
At some point, ask the robot which book it recommends you read this summer.

Use the made-up data on this card: do not say any real personal data (your name, your address, your phone number...).
Speak naturally, as you would with a person.
If the robot does not understand you, say it again in other words.
The conversation ends when the robot says goodbye.
```

## For the experimenter

- Scenario: `rp_interview` (`e4_es.yaml`), strategy `roleplay`.
- Mechanism provoked: long silence (waiting feedback) and a cancel denied; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
