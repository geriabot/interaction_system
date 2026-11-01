# En la entrada del museo

What the participant reads:

```text
EN LA ENTRADA DEL MUSEO

Estás en la entrada de un museo de ciencias y el robot atiende a los visitantes. Pregúntale:
• si el museo abre los lunes; cuando te responda, comenta «vaya, qué pena»,
• cuánto cuesta la entrada para una persona de 70 años, y pídele que te repita la respuesta,
• si hay cafetería.
Cuando no tengas más preguntas, díselo.

Además:
En algún momento, pregúntale al robot qué opina del último partido de fútbol.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `qa_museum` (`e4_es.yaml`), strategy `grounded_qa`.
- Mechanism provoked: abstention, a comment and a request to repeat; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
