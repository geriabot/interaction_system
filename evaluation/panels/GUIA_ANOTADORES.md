# Guía para anotar frases del robot (movida)

La guía de las personas que anotan está ahora, en inglés, en
[doc/human_review/e0b_annotation.md](../doc/human_review/e0b_annotation.md): cómo abrir el panel,
qué se ve en cada pantalla, qué hacer si algo va mal. El panel tiene un botón arriba a la derecha
que lo pasa al español; la guía de anotación en español que muestra el panel sigue en
[annotation_guide_es.md](annotation_guide_es.md). Todas las tareas de revisión a mano:
[HUMAN_REVIEW.md](../HUMAN_REVIEW.md).

## Anotar desde tu ordenador, a través del repositorio

Resumen en español de la sección
[Annotating through the repository](../doc/human_review/e0b_annotation.md#annotating-through-the-repository):

1. Clona el repositorio con sus submódulos: `git clone --recurse-submodules git@github.com:geriabot/interaction_system.git`
   y después `cd interaction_system/evaluation && git switch main && cd ..`. Si ya lo tienes:
   `git pull && git submodule update --init`, y `cd evaluation && git switch main && git pull && cd ..`.
   `evaluation/` es un submódulo privado (`geriabot/interaction_system_eval`): tu cuenta de GitHub
   necesita acceso. Basta con Python 3.10 o más reciente: ni ROS ni pixi.
2. Desde la raíz del repositorio, abre la hoja en inglés:
   `python3 evaluation/panels/review_panel.py annotate --annotator "Annotator B" --csv evaluation/annotation/e0b/sheet_b_en.csv`
   (como «Annotator B» y no con tu nombre: la evaluación se publicará con el trabajo y el nombre quedaría en el
   fichero de progreso) y abre en el navegador la dirección que muestra la terminal (normalmente `http://localhost:8098`).
   Haz la práctica corta y luego la hoja, en tantas sesiones como quieras: el panel guarda lo hecho
   y sigue donde lo dejaste al repetir la orden.
3. Al terminar, lo mismo con la hoja en castellano (`--csv evaluation/annotation/e0b/sheet_b_es.csv`).
4. Devuelve solo tus ficheros, desde el submódulo: `cd evaluation`, `git add annotation/e0b/sheet_b_*`,
   `git commit -m "E0b: annotation of sheet B"` y `git push` (si lo rechaza, `git pull --rebase` y
   otra vez `git push`).

Trabaja a solas, no abras ningún otro fichero de la evaluación y no edites la hoja a mano.

*The annotators' guide moved to [doc/human_review/e0b_annotation.md](../doc/human_review/e0b_annotation.md)
(English, the single source). This file is kept so that old links still work.*
