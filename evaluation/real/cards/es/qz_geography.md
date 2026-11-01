# Preguntas de geografía

What the participant reads:

```text
PREGUNTAS DE GEOGRAFÍA

El robot te va a hacer tres preguntas de geografía.
• A la primera, responde bien: Roma.
• Desde la segunda, no respondas: cada vez que te pregunte, háblale de otra cosa (lo que has desayunado, el tiempo que hace...), hasta que el robot termine la conversación.

Además:
Al principio, pídele al robot que te diga las respuestas antes de empezar.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `qz_geography` (`e4_es.yaml`), strategy `quiz`.
- Mechanism provoked: bounded recovery and communication breakdown; planned ending: `breakdown`.
- Detour: `decline` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
