# Preparar un té

What the participant reads:

```text
PREPARAR UN TÉ

El robot te va a guiar para preparar un té (haz como que lo preparas).
• Haz cada paso y dile cuándo lo has hecho.
• Cuando te diga que dejes reposar el té, quédate en silencio, sin decir nada, hasta que el robot vuelva a hablarte; entonces dile que ya está.
• Tú no tomas el té con leche ni con azúcar: en el último paso, dile que te lo quieres saltar.

Además:
En algún momento, pregúntale al robot cuál es su comida favorita.

Usa los datos inventados de esta tarjeta: no digas datos personales reales (tu nombre, tu dirección, tu teléfono...).
Habla con naturalidad, como lo harías con una persona.
Si el robot no te entiende, repítelo con otras palabras.
La conversación termina cuando el robot se despide.
```

## For the experimenter

- Scenario: `pr_tea` (`e4_es.yaml`), strategy `procedure`.
- Mechanism provoked: silence during a step with a declared duration; an optional step skipped; planned ending: `completed`.
- Detour: `off_task` (off_task: an unrelated question; decline: a request the robot should decline; not_allowed: a value the task does not allow).
- The card follows the scenario's `user_goal`, so that its ground truth (`expect`) scores the conversation.
