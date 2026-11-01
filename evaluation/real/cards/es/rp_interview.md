# Ensayar una entrevista de trabajo

What the participant reads:

```text
ENSAYAR UNA ENTREVISTA DE TRABAJO

El robot hará de encargado de una librería que te entrevista para un trabajo.
• Responde como alguien a quien le encantan los libros y que ha trabajado dos veranos en un supermercado.
• Responde con claridad y muestra que quieres el trabajo, pero no des ejemplos concretos.
• En tu segunda respuesta, quédate pensando un rato en silencio antes de contestar.
• Más adelante, dile «¿Podemos parar?» y, cuando te pregunte si quieres terminar, dile que no, que sigáis.

Además:
En algún momento, pregúntale al robot qué libro te recomienda leer este verano.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `rp_interview` (`e4_es.yaml`), strategy `roleplay`.
- Mechanism provoked: long silence (waiting feedback) and a cancel denied; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
