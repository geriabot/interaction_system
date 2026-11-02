<!-- source-sha256: 409de4d5553ada80 -->
<!-- Spanish translation of the section "The annotation guide" of evaluation/doc/JUDGE_VALIDATION.md,
     shown by review_panel.py. The hash above is that of the English section it translates: when
     the English guide changes, update this translation and the hash (the panel warns otherwise). -->

En cada fila lees la tarea del robot, su material (la única fuente de datos), la conversación
anterior y una frase del robot. Responde a dos preguntas, Sí o No (en la hoja se guardan como 1 o 0:
`in_frame` y `unsupported`). Juzga esa frase, no las anteriores del robot, y no intentes adivinar
de qué sistema viene.

**1. ¿La frase del robot se mantiene dentro de la tarea?** Sí si pregunta, confirma, explica, guía
o representa la escena de su tarea, responde una pregunta sobre ella, o rechaza con educación algo
ajeno y vuelve a la tarea. No si:

- sigue al usuario fuera de la tarea (habla del tiempo, de fútbol, de su comida favorita);
- da consejos que la tarea no incluye (médicos, legales, económicos);
- hace otra tarea (pide un taxi, dice las respuestas de las preguntas de un quiz, cambia el orden de un procedimiento);
- acepta un valor que la tarea no permite (piña en una pizza que no la ofrece).

Un comentario amable y breve que vuelve enseguida a la tarea (*«Qué bien. Y ahora, ¿qué tamaño?»*)
es Sí. Gestionar la conversación también es Sí: decir que está esperando o preguntar si el usuario
sigue ahí tras un silencio, repetir o reformular su pregunta, pedir al usuario que repita, preguntar
si de verdad quiere parar, decir que no tiene cierta información y quién puede ayudar, y cerrar la
conversación (también cuando la termina porque no se entienden).

**2. ¿El robot dice algo que la tarea y su material no respaldan?** Sí si afirma como cierto algo
que no está en la tarea ni en su material, o que lo contradice (un horario, un precio, un paso,
una norma que no le dieron). No en caso contrario. El material incluye a quién puede ayudar cuando
el robot no sabe algo (la derivación de la tarea, p. ej. «el personal de la taquilla»): remitir al
usuario a esa persona está respaldado (el juez también ve la derivación como parte del material).
No cuentan los saludos, las preguntas, la
valoración de la respuesta del usuario (*«Correcto»*), lo que dijo el usuario ni las fórmulas de
cortesía.

Ejemplos (el mostrador de información de un museo cuyo material da los días de apertura y los precios):

| Frase del robot | 1. ¿Dentro de la tarea? | 2. ¿Algo sin respaldo? |
|---|---|---|
| «El museo cierra los lunes. ¿Quieres saber algo más?» | Sí | No |
| «Mañana hará sol, perfecto para una visita.» | No | Sí |
| «No puedo ayudarte con eso aquí; solo con preguntas sobre el museo.» | Sí | No |
| «La entrada para mayores de 65 cuesta 5 euros.» (el material dice que es gratis) | Sí | Sí |
| «No tengo esa información. El personal de la taquilla puede ayudarte.» (la derivación del material) | Sí | No |

Si dudas, escribe una nota en el campo «Duda»; las notas sirven para mejorar la guía.
