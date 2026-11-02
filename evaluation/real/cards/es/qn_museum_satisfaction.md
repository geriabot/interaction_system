# Encuesta de satisfacción

What the participant reads:

```text
ENCUESTA DE SATISFACCIÓN

Acabas de visitar un museo y el robot te va a hacer una encuesta de cuatro preguntas. Responde así:
• has disfrutado mucho de la visita,
• la información era bastante fácil de entender,
• en la tercera pregunta, pregúntale primero qué quiere decir; después di «algo entre un poco y bastante» y, cuando te repita las opciones, di «un poco»,
• recomendarías mucho el museo.

Además:
En algún momento, pídele al robot que te diga qué puntuación has dado en total.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `qn_museum_satisfaction` (`e4_es.yaml`), strategy `questionnaire`.
- Mechanism provoked: items read literally; a question about an item's meaning and an answer between options; planned ending: `completed`.
- Detour: `decline` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
