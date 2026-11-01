# En la cafetería

What the participant reads:

```text
EN LA CAFETERÍA

El robot es el camarero de una cafetería. Pídele:
• un café con leche mediano, con leche de avena,
• a nombre de Laura.
• A mitad del pedido, dile «Para, déjalo». Cuando te pregunte si quieres terminar, no le digas ni sí ni no: dale el dato que te había pedido y sigue con el pedido.
Cuando el robot te repita el pedido, dile si está bien. Si algo está mal, corrígelo.

Además:
En algún momento, pregúntale al robot qué tiempo va a hacer mañana.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `sf_coffee` (`e4_es.yaml`), strategy `slot_filling`.
- Mechanism provoked: three-way cancel confirmation: the reply carries the slot asked (content kept); planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
