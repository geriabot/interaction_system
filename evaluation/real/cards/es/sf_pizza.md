# Pedir una pizza

What the participant reads:

```text
PEDIR UNA PIZZA

El robot toma pedidos en una pizzería. Pide:
• una pizza grande con champiñones y aceitunas,
• a domicilio, en la Calle Mayor 5.
• Cuando te pida un teléfono de contacto, dile que prefieres no darlo.
Cuando el robot te repita el pedido, dile si está bien. Si algo está mal, corrígelo.

Además:
Intenta añadir piña a la pizza (no está en la carta). Si el robot no te deja, sigue con tu pedido.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `sf_pizza` (`e4_es.yaml`), strategy `slot_filling`.
- Mechanism provoked: a slot the user does not give (left unanswered); planned ending: `partial`.
- Detour: `not_allowed` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
