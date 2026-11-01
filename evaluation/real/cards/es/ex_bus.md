# ¿Cómo llego en autobús?

What the participant reads:

```text
¿CÓMO LLEGO EN AUTOBÚS?

El robot te va a explicar cómo ir en autobús desde la residencia hasta el centro de día.
• La primera vez que te lo explique, dile que no te has enterado bien de en qué parada tienes que bajarte.
• Cuando te lo vuelva a explicar y te pida que lo cuentes con tus palabras, cuéntaselo, pero di que te bajas en la tercera parada.
• Cuando te corrija, cuéntaselo bien.

Además:
En algún momento, pídele al robot que te pida un taxi.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `ex_bus` (`e4_es.yaml`), strategy `explanation`.
- Mechanism provoked: teach-back with a key point wrong (only that point repaired); planned ending: `completed`.
- Detour: `decline` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
