# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The Tests tab of the control panel (scripts/control_panel.py; doc/TEST_PANEL.md): any scenario of
the experiments tried with a person as the user: you choose a scenario (any strategy, in English
or Spanish), the condition (the method or the LLM-led conversation) and the system model, read
what the user is asked to do, and talk with the robot through the speech capability (simple_hri:
the microphone and speaker of this computer, or the robot's). Nothing is stored; it is not the
evaluation of the paper.

Ready examples (test_panel_examples.yaml): one scenario per strategy and one per mechanism that a
scenario provokes on purpose. For the chosen scenario the page shows a tester card: the role
(user_goal), what to do turn by turn (the scenario's events, at the turns of the simulated user:
the reply to the robot's k-th line is turn k), the expected ending, what should happen
(expect.mechanisms in plain words) and the timing, in the language of the scenario file.

    python3 evaluation/scripts/control_panel.py    # then open http://localhost:8095/#tests

Until 2026-10-30 this was a page of its own (port 8097); it is now a tab of the control panel, so
that both share one server and its jobs (one at a time: they share the GPU, the microphone and
the ROS domains). Running this file only says so.

It calls real/real_sessions.py try-scenario (a conversation as a trial --try) and its checks
(speak, listen, llm), with the same options as in a terminal, inside pixi with the workspace
sourced. The conditions, models and limits are those of real/sessions_es.yaml.

How the robot sounds, for a test only (nothing is stored, nothing is changed): a configuration of
the real sessions (real/configs/; any, drafts included: --config), or ad-hoc values: the Piper
voice (campaigns.VOICES) and its speed, expressiveness and rhythm variation (campaigns.SETTINGS,
PRESETS; empty: the voice's own), which the speak check uses to say its phrase, so voice
configurations can be compared without any model, and the test conversation passes to
try-scenario (--voice, --length-scale, --noise-scale, --noise-w-scale); and the seconds before a
conversational filler (try-scenario --filler-after; empty: the scenario's or the system's).
"""

import glob
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import control_panel  # noqa: E402  (its jobs: start, state, stop, answer)
from experiment_files import scenario_files  # noqa: E402
sys.path.insert(0, os.path.join(control_panel.EVAL, "real"))
import campaigns  # noqa: E402  (the configurations, the voices and their settings)
import real_sessions  # noqa: E402  (the Piper settings and their ranges)
from campaigns import PRESETS, SETTINGS, VOICES  # noqa: E402,F401  (as before: test_panel.VOICES)

SCENARIOS = os.path.join(control_panel.EXPERIMENTS, "scenarios")
REAL = os.path.join(control_panel.EVAL, "real", "real_sessions.py")
EXAMPLES = os.path.join(HERE, "test_panel_examples.yaml")

# Phrases for the speak check: a question with several options, where the intonation shows
QUESTIONS = {"es": "¿Quieres el café con leche entera, desnatada, de avena o de soja?",
             "en": "Would you like your coffee with whole milk, skimmed milk, oat milk or soy milk?"}


def voice_settings(body):
    """The settings of the voice given in the page ({key: value}, the empty ones left out);
    ValueError (with what to fix) if one is not a number in its range."""
    out = {}
    for key, (label, _) in SETTINGS.items():
        text = str(body.get(key) if body.get(key) is not None else "").strip()
        if not text:
            continue
        low, high = real_sessions.PIPER_SETTINGS[key]
        try:
            value = float(text)
            real_sessions.piper_parameters({key: value})
        except ValueError:
            raise ValueError(f"{label}: a number from {low:g} to {high:g}, or empty for the "
                             "voice's own") from None
        out[key] = value
    return out


def settings_arguments(settings):
    """--length-scale 1.1 ... for real_sessions.py."""
    return [a for key, value in settings.items() for a in (f"--{key.replace('_', '-')}", f"{value:g}")]


def voice_language(voice):
    """The language of a voice of VOICES (es, en), or None if it is not one of them."""
    return next((lang for value, lang, _, _ in VOICES if value == voice), None)


def filler_after(text):
    """--filler-after of the panel's field: None (empty: the default) or seconds >= 0."""
    text = str(text if text is not None else "").strip()
    if not text:
        return None
    value = float(text)
    if value < 0 or value != value:
        raise ValueError("Filler after (s): 0 or more seconds, or empty for the default")
    return value

# ==== The tester card: what the person does, turn by turn, from the scenario's events ====
#
# The turns are counted as the simulated user's (simulation/interaction_sim: llm_user.py and
# harness.py): the user's turn k is the reply to the robot's k-th line of the dialogue (each time
# the robot finishes speaking, its prompts after a silence included; its fillers are not lines), so
# a silence uses up a turn and a quiet wait, in which the robot says nothing, does not.

CARD = {
    "es": {
        "heading": "Tarjeta del probador",
        "role": "Tu papel",
        "turns": "Turno a turno",
        "counting": "Cómo se cuentan los turnos: cada vez que el robot termina de hablar te toca a "
                    "ti; tu turno 1 es tu respuesta a lo primero que dice el robot. Si te quedas "
                    "callado, ese turno también cuenta (el robot volverá a hablarte y te tocará el "
                    "siguiente); los «Mmm…» del robot mientras piensa no cuentan.",
        "none": "Ningún turno tiene una acción fijada: lo que se prueba lo provoca tu papel; "
                "hazlo cuando tu papel lo dice.",
        "rest": "En los demás turnos, sigue tu papel.",
        "text": "En tu {n} turno di: «{text}»",
        "cancel": "En tu {n} turno pide terminar, diciendo: «{text}»",
        "silence": "En tu {n} turno quédate en silencio, sin decir nada, hasta que el robot vuelva "
                   "a hablar (o se despida)",
        "detour": "En tu {n} turno, desvío ({kind}): di «{text}»",
        "barge_in": "En tu {n} turno interrumpe al robot mientras habla (a los {s} s); el barge-in "
                    "está desactivado en este panel: el robot no se parará",
        "other": "En tu {n} turno: acción «{kind}» {text}",
        "detours": {"off_task": "una pregunta ajena a la tarea",
                    "decline": "algo que el robot debe rechazar",
                    "not_allowed": "un valor que la tarea no admite"},
        "e4": "En las sesiones reales el participante elige el momento de estas acciones («en "
              "algún momento» de su tarjeta); aquí van en el turno en que las dice su gemelo "
              "simulado.",
        "ending": "Final esperado",
        "endings": {
            "completed": "completada: el robot termina la tarea y se despide",
            "partial": "parcial: algo queda sin hacer o sin respuesta, y el robot se despide",
            "success": "con éxito (completada, o parcial si algo queda sin hacer)",
            "failed": "fallida: la tarea no se consigue",
            "cancelled": "cancelada: el robot se despide sin terminar la tarea",
            "breakdown": "ruptura de la comunicación: el robot termina porque no avanzáis",
            "user_silent": "silencio: el robot termina porque no respondes",
            "turn_limit": "límite de turnos: el robot termina al llegar a su límite"},
        "unanswered": "Queda sin respuesta: {slots}",
        "show": "Qué debería verse",
        "mechanism": "El mecanismo del escenario (en inglés en el fichero): {m}",
        "provokes": "Lo que provoca, según la lista de ejemplos (en inglés): {m}",
        "limits": "Tiempos",
        "limits_line": "El robot espera {wait} s de silencio antes de hablarte; si pasan {max_silence} "
                       "s seguidos sin que digas nada, termina la conversación. Límite: {max_turns} "
                       "turnos.",
        "limits_differ": "Algunos experimentos con este fichero esperan {values} s antes de hablar (aquí "
                         "{wait} s): una pausa que allí no llega a ser un silencio aquí puede serlo.",
    },
    "en": {
        "heading": "Tester card",
        "role": "Your role",
        "turns": "Turn by turn",
        "counting": "How turns are counted: each time the robot finishes speaking it is your turn; "
                    "your turn 1 is your reply to the first thing the robot says. If you stay "
                    "silent, that turn counts too (the robot will speak to you again and the next "
                    "turn is yours); the robot's \"Hmm…\" while it thinks does not count.",
        "none": "No turn has a planned action: what is tried is provoked by your role; do it "
                "when your role says.",
        "rest": "On the other turns, play your role.",
        "text": "On your {n} turn say: \"{text}\"",
        "cancel": "On your {n} turn ask to stop, saying: \"{text}\"",
        "silence": "On your {n} turn stay silent, without saying anything, until the robot speaks "
                   "again (or says goodbye)",
        "detour": "On your {n} turn, detour ({kind}): say \"{text}\"",
        "barge_in": "On your {n} turn interrupt the robot while it speaks (after {s} s); barge-in "
                    "is off in this panel: the robot will not stop",
        "other": "On your {n} turn: action \"{kind}\" {text}",
        "detours": {"off_task": "a question outside the task",
                    "decline": "something the robot must decline",
                    "not_allowed": "a value the task does not allow"},
        "e4": "In the real sessions the participant chooses when to do these (\"at some point\" on "
              "the card); here they come at the turn of the simulated twin.",
        "ending": "Expected ending",
        "endings": {
            "completed": "completed: the robot finishes the task and says goodbye",
            "partial": "partial: something is left undone or unanswered, and the robot says goodbye",
            "success": "success (completed, or partial when something is left undone)",
            "failed": "failed: the task is not achieved",
            "cancelled": "cancelled: the robot says goodbye without finishing the task",
            "breakdown": "breakdown: the robot ends it because the conversation makes no progress",
            "user_silent": "silence: the robot ends it because you do not answer",
            "turn_limit": "turn limit: the robot ends it when it reaches its limit"},
        "unanswered": "Left unanswered: {slots}",
        "show": "What should happen",
        "mechanism": "The scenario's mechanism: {m}",
        "provokes": "What it provokes, by the list of examples: {m}",
        "limits": "Timing",
        "limits_line": "The robot waits {wait} s of silence before speaking to you; after "
                       "{max_silence} s in a row without you saying anything, it ends the "
                       "conversation. Limit: {max_turns} turns.",
        "limits_differ": "Some experiments with this file wait {values} s before speaking (here "
                         "{wait} s): a pause that is not a silence there may be one here.",
    },
}


def ordinal(n, lang):
    if lang == "es":
        return f"{n}.{'er' if n in (1, 3) else 'º'}"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def num(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def times(n, lang):
    if lang == "es":
        return "1 vez" if n == 1 else f"{n} veces"
    return "once" if n == 1 else f"{n} times"


def event_line(event, lang):
    """One line of the card per event (every kind of llm_user.UserEvent; an unknown kind is shown
    as such, never dropped)."""
    t = CARD[lang]
    n, kind, text = ordinal(int(event.get("turn", 0)), lang), event.get("kind", ""), event.get("text", "")
    if kind in ("text", "cancel", "silence"):
        return t[kind].format(n=n, text=text)
    if kind == "detour":
        return t["detour"].format(n=n, kind=t["detours"].get(event.get("detour"), event.get("detour", "")),
                                  text=text)
    if kind == "barge_in":
        return t["barge_in"].format(n=n, s=num(event.get("barge_in_after", 0.5)))
    return t["other"].format(n=n, kind=kind, text=f"«{text}»" if text else "").strip()


# The strategies' own checks (analysis/strategy_checks.py), as (Spanish, English); {n}: a count
STRATEGY_CHECKS = {
    "range_rejected": ("Cuando dices un número fuera del rango permitido, el robot no lo apunta, te "
                       "dice el rango y te lo vuelve a preguntar",
                       "When you say a number out of the allowed range, the robot does not record "
                       "it, says the range and asks again"),
    "range_said": ("El robot dice los dos límites del rango", "The robot says both bounds of the range"),
    "unanswered_ok": ("Lo que no contestas queda sin respuesta, y lo que depende de ello no se pregunta",
                      "What you do not answer is left unanswered, and what depends on it is not asked"),
    "key_points_from_llm": ("La petición no trae los puntos clave: el robot los saca del material y "
                            "comprueba con ellos lo que cuentas",
                            "The request has no key points: the robot draws them from the material "
                            "and checks what you tell it against them"),
    "key_points": ("Puntos clave de la explicación: {n}", "Key points of the explanation: {n}"),
    "tb_marked_wrong": ("El robot detecta los puntos que cuentas mal",
                        "The robot spots the points you tell it wrong"),
    "tb_others_correct": ("Los puntos que cuentas bien cuentan como bien",
                          "The points you tell it right count as right"),
    "tb_repair_only_wrong": ("Al corregirte, el robot explica de nuevo solo lo que dijiste mal, no lo "
                             "que dijiste bien",
                             "When it corrects you, the robot explains again only what you said "
                             "wrong, not what you said right"),
    "tb_repair_review": ("Puntos dichos bien que la corrección medio repite (para revisarlos a mano): {n}",
                         "Points said right that the correction half repeats (to review by hand): {n}"),
    "tb_closed_consistent": ("El robot no da la explicación por entendida mientras un punto clave siga mal",
                             "The robot does not close the explanation as understood while a key "
                             "point is still wrong"),
    "contradiction_not_done": ("Cuando dices que está hecho pero cuentas algo que contradice la "
                               "comprobación del paso, el robot no lo da por hecho: te ayuda",
                               "When you say it is done but report something that contradicts the "
                               "step's check, the robot does not mark it done: it helps you"),
    "repeat_literal": ("A tu primera petición de repetir, el robot repite su frase palabra por palabra",
                       "At your first request to repeat, the robot says its line again word for word"),
    "repeat_rephrased": ("A la segunda, te lo dice con otras palabras",
                         "At the second, it says it in other words"),
    "rp_exchanges": ("Intercambios de la escena: {n} (las peticiones de repetir no cuentan)",
                     "Exchanges of the scene: {n} (the requests to repeat do not count)"),
    "rp_ended_by": ("Quién termina la práctica: {n}", "Who ends the practice: {n}"),
    "rubric_in_intro": ("Criterios de la rúbrica que la presentación dice tal cual: {n}",
                        "Criteria of the rubric the intro says word for word: {n}"),
    "rubric_in_intro_words": ("Criterios de la rúbrica con todas sus palabras en la presentación (para "
                              "leerlos a mano): {n}",
                              "Criteria of the rubric with all their words in the intro (to read by "
                              "hand): {n}"),
}


def mechanism_lines(planned, lang):
    """expect.mechanisms (analysis/mechanisms.py and strategy_checks.py) in plain words, one line
    per key."""
    es = lang == "es"
    lines = []
    for key, value in (planned or {}).items():
        n = value if isinstance(value, int) else 0
        if key == "cancel_asked":
            lines.append(("El robot no te pregunta si quieres terminar: lo que dices es parte de la tarea"
                          if es else "The robot does not ask whether you want to stop: what you say "
                          "belongs to the task") if n == 0 else
                         (f"El robot te pregunta si quieres terminar ({times(n, lang)})" if es else
                          f"The robot asks whether you want to stop ({times(n, lang)})"))
        elif key == "cancel_confirmed":
            lines.append("Cuando confirmas, la conversación termina como cancelada" if es else
                         "When you confirm, the conversation ends as cancelled")
        elif key == "cancel_denied":
            lines.append("Cuando dices que no, el robot sigue con lo que te había preguntado" if es
                         else "When you say no, the robot resumes with what it had asked")
        elif key == "cancel_withdrawn":
            lines.append("Cuando respondes con el dato pedido, el robot no cancela y te repite su "
                         "pregunta" if es else "When you answer with the value asked, the robot "
                         "does not cancel and asks its question again")
        elif key == "silence_prompts":
            lines.append(f"El robot te habla en tus silencios {times(n, lang)} (te dice que te espera, "
                         "te repite lo último o «cuando quieras»)" if es else
                         f"The robot speaks to you in your silences {times(n, lang)} (it says it is "
                         "waiting, repeats its last line or says \"whenever you're ready\")")
        elif key == "silence_quiet":
            lines.append(f"Esperas en silencio en las que el robot no dice nada: {n}" if es else
                         f"Silent waits in which the robot says nothing: {n}")
        elif key == "silence_grace":
            lines.append(f"Mientras dura el paso con tiempo, el robot no te mete prisa ({n} esperas "
                         "sin aviso, que no cuentan como silencio)" if es else
                         f"While the timed step lasts, the robot does not hurry you ({n} waits "
                         "without a prompt, not counted as silence)")
        elif key in ("events_applied", "events_planned"):
            lines.append(f"Todas las acciones fijadas ({n}) llegan a ocurrir antes del final" if es
                         else f"Every planned action ({n}) happens before the end")
        elif key == "closing_said":
            lines.append(("El robot se despide con una frase de cierre después de tu última frase"
                          if es else "The robot says a closing line after your last line")
                         if value else ("Sin frase de cierre" if es else "No closing line"))
        elif key in STRATEGY_CHECKS:
            line = STRATEGY_CHECKS[key][0 if es else 1]
            if "{n}" in line:
                if isinstance(value, list):
                    value = ", ".join(map(str, value)) or ("ninguno" if es else "none")
                lines.append(line.format(n=value))
            else:
                lines.append(line if value else ("No se espera: " if es else "Not expected: ") + line)
        else:
            lines.append(f"{key}: {value}")
    return lines


def tester_card(scenario, spanish, limits, waits=(), e4=False, provokes=()):
    """The card of a scenario for the person who tries it: the role, what to do turn by turn (the
    events), the expected ending, what should happen and the timing. In the file's language."""
    lang = "es" if spanish else "en"
    t = CARD[lang]
    expect = scenario.get("expect") or {}
    events = sorted(scenario.get("events") or [], key=lambda e: int(e.get("turn", 0)))
    turns = [t["counting"]] + ([event_line(e, lang) for e in events] + [t["rest"]] if events
                               else [t["none"]])
    if events and e4:
        turns.append(t["e4"])
    ending = expect.get("ending") or expect.get("outcome") or "success"
    ending_lines = [t["endings"].get(ending, ending)]
    if expect.get("unanswered"):
        ending_lines.append(t["unanswered"].format(slots=", ".join(map(str, expect["unanswered"]))))
    show = mechanism_lines(expect.get("mechanisms"), lang)
    if scenario.get("mechanism"):
        show.append(t["mechanism"].format(m=scenario["mechanism"]))
    if provokes:
        show.append(t["provokes"].format(m="; ".join(provokes)))
    goal = scenario.get("goal") or {}
    wait = limits.get("wait_timeout", "?")
    timing = [t["limits_line"].format(wait=num(wait), max_silence=num(limits.get("max_silence", "?")),
                                      max_turns=goal.get("max_turns", limits.get("max_turns", "?")))]
    other = sorted({w for w in waits if w != wait})
    if other:
        timing.append(t["limits_differ"].format(values=", ".join(num(w) for w in other), wait=num(wait)))
    return {"heading": t["heading"], "sections": [
        {"title": t["role"], "lines": [str(scenario.get("user_goal", "")).strip()]},
        {"title": t["turns"], "lines": turns},
        {"title": t["ending"], "lines": ending_lines},
        {"title": t["show"], "lines": show},
        {"title": t["limits"], "lines": timing}]}


def panel_limits(sessions):
    """The limits of a test (those of the sessions file, as real_sessions.py try-scenario)."""
    return {**dict(sessions.get("limits") or {}), "max_turns": sessions.get("max_turns", "?")}


def experiment_waits():
    """The wait_timeout of the experiments, per scenario file: {file: {seconds}}."""
    out = {}
    for path in glob.glob(os.path.join(control_panel.EXPERIMENTS, "*.yaml")):
        exp = control_panel.load(path) or {}
        if isinstance(exp, dict) and exp.get("scenarios"):
            wait = (exp.get("limits") or {}).get("wait_timeout")
            if wait is not None:
                for f, _ in scenario_files(exp):
                    out.setdefault(os.path.basename(f), set()).add(float(wait))
    return out


def examples():
    """The ready examples (test_panel_examples.yaml), each with its strategy and the mechanisms
    its scenario provokes: {by_strategy: [...], by_mechanism: [...]}."""
    data = control_panel.load(EXAMPLES)
    names = data["mechanisms"]
    provokes = data.get("provokes", {})
    strategies = {}
    for name in {ex["file"] for group in ("by_strategy", "by_mechanism") for ex in data[group]}:
        for s in control_panel.load(os.path.join(SCENARIOS, name))["scenarios"]:
            strategies[(name, s["id"])] = s.get("strategy", "?")
    out = {"by_strategy": [], "by_mechanism": []}
    for group in ("by_strategy", "by_mechanism"):
        for ex in data[group]:
            ids = provokes.get(ex["file"], {}).get(ex["scenario"], [])
            item = {"file": ex["file"], "scenario": ex["scenario"],
                    "strategy": strategies[(ex["file"], ex["scenario"])],
                    "condition": ex.get("condition", "bounded"), "note": ex.get("note", ""),
                    "provokes": [{"id": i, "name": names.get(i, "")} for i in ids]}
            if group == "by_strategy":
                item.update(title=f"{ex['strategy']}: {ex['title']}")
            else:
                item.update(mechanism=ex["mechanism"],
                            title=f"{ex['mechanism']} · {names.get(ex['mechanism'], '')}")
            out[group].append(item)
    return out


def catalogue():
    """The scenario files (each with its language) and their scenarios by strategy (each with its
    tester card), the conditions of the sessions file and the ready examples."""
    sessions = control_panel.load(control_panel.sessions_common.SESSIONS)
    limits, waits = panel_limits(sessions), experiment_waits()
    listed = control_panel.load(EXAMPLES)
    names, provokes = listed["mechanisms"], listed.get("provokes", {})
    e4 = os.path.basename(sessions.get("scenarios_file", ""))
    files = []
    for path in sorted(glob.glob(os.path.join(SCENARIOS, "*.yaml"))):
        data = control_panel.load(path) or {}
        if not isinstance(data, dict) or not data.get("scenarios"):
            continue
        name, spanish = os.path.basename(path), path.endswith("_es.yaml")
        strategies = {}
        for s in data["scenarios"]:
            expect = s.get("expect", {})
            strategies.setdefault(s.get("strategy", "?"), []).append({
                "id": s["id"], "task": s.get("goal", {}).get("task_description", ""),
                "user_goal": str(s.get("user_goal", "")).strip(),
                "outcome": expect.get("ending") or expect.get("outcome", "success"),
                "mechanism": s.get("mechanism", ""),
                "card": tester_card(s, spanish, limits, waits.get(name, ()), e4=name == e4,
                                    provokes=[f"{i} ({names.get(i, '')})"
                                              for i in provokes.get(name, {}).get(s["id"], [])])})
        files.append({"file": name, "language": "Spanish" if spanish else "English",
                      "strategies": strategies})
    controls = {}
    for c in sessions.get("conditions", {}).values():
        controls.setdefault(c["control"], []).append(c["model"])
    return {"files": files, "controls": controls, "check_phrase": sessions.get("check_phrase", ""),
            "questions": QUESTIONS, "examples": examples(),
            # Any configuration of the real sessions, drafts included (a test changes nothing)
            "configs": [{k: c[k] for k in ("name", "state", "used_by", "values", "error")}
                        for c in campaigns.configurations()],
            "voices": [{"value": v, "language": lang, "gender": gender, "label": label}
                       for v, lang, gender, label in VOICES],
            "settings": [{"key": k, "label": label, "hint": hint,
                          "low": real_sessions.PIPER_SETTINGS[k][0],
                          "high": real_sessions.PIPER_SETTINGS[k][1]}
                         for k, (label, hint) in SETTINGS.items()],
            "presets": [{"label": label, "values": values} for label, values in PRESETS]}


def is_cloud(model):
    return "/" in model


def models():
    """The models to choose from: those of the Ollama server of the sessions file (/api/tags)
    and the cloud models of its conditions (not those still written <pin>)."""
    sessions = control_panel.load(control_panel.sessions_common.SESSIONS)
    llm = sessions.get("ollama", {})
    base = f"http://{llm.get('host', '127.0.0.1')}:{llm.get('port', 11434)}"
    out = {"server": base, "ollama": [], "error": "",
           "cloud": sorted({c["model"] for c in sessions.get("conditions", {}).values()
                            if is_cloud(c["model"]) and "<pin>" not in c["model"]})}
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as r:
            out["ollama"] = sorted(m["name"] for m in json.load(r)["models"])
    except (OSError, ValueError, KeyError) as exc:
        out["error"] = f"the Ollama server at {base} does not answer ({exc})"
    return out


def chosen_configuration(body):
    """The configuration chosen for a test (real/configs/, any, drafts included), or None for
    ad-hoc values; ValueError when it cannot be read."""
    name = (body.get("config") or "").strip()
    return campaigns.load_config(name) if name else None


def run(body):
    """real_sessions.py try-scenario <scenario> --scenarios <file> --control --model, with a
    configuration (--config) or ad-hoc values (over it, when both)."""
    names = {f["file"]: f for f in catalogue()["files"]}
    name = body.get("file", "")
    if name not in names:
        return "Unknown scenario file"
    ids = {s["id"] for ss in names[name]["strategies"].values() for s in ss}
    scenario = body.get("scenario", "")
    if scenario not in ids:
        return "Choose a scenario"
    control = body.get("control", "bounded")
    if control not in ("bounded", "llm_led"):
        return "Unknown condition"
    model = (body.get("model") or "").strip()
    if not model:
        return "Write the system model"
    if "<pin>" in model:
        return "Write the dated version of the cloud model (it says <pin>)"
    if is_cloud(model) and not body.get("allow_cloud"):
        return f"{model} is a cloud model: what you say, transcribed, goes to its provider. Confirm it first"
    try:
        config = chosen_configuration(body)
    except ValueError as exc:
        return str(exc)
    voice = (body.get("voice") or "").strip()
    if voice and voice_language(voice) is None:
        return f"Unknown voice {voice}"
    heard = voice or (config["values"]["voice"] if config else "")
    if heard and campaigns.voice_language(heard) != ("es" if name.endswith("_es.yaml") else "en"):
        return f"The voice {heard} is not of the language of {name}"
    try:
        filler = filler_after(body.get("filler_after"))
    except ValueError:
        return "Filler after (s): 0 or more seconds, or empty for the default"
    try:
        settings = voice_settings(body)
    except ValueError as exc:
        return str(exc)
    command = ["python3", REAL, "try-scenario", scenario, "--scenarios", os.path.join(SCENARIOS, name),
               "--control", control, "--model", model, "--barge-in", "on" if body.get("barge_in") else "off"]
    command += ["--allow-cloud"] if is_cloud(model) else []
    command += ["--config", config["name"]] if config else []
    command += ["--voice", voice] if voice else []
    command += settings_arguments(settings)
    command += ["--filler-after", f"{filler:g}"] if filler is not None else []
    try:
        env = control_panel.robot_env(body)
    except ValueError as exc:
        return str(exc)
    extra = "".join([f", configuration {config['name']}" if config else "",
                     f", voice {real_sessions.piper_text({'voice': voice, **settings})}"
                     if voice or settings else "",
                     f", filler after {filler:g} s" if filler is not None else ""])
    return control_panel.start_job(
        "real", f"Test: {scenario} ({name}), {control} with {model}{extra} (nothing stored)",
        command, env, stdin=True)


def check(body):
    """real_sessions.py check speak | listen | llm (nothing stored); speak and listen with the
    configuration chosen (its voice; its microphone and speech-recognition model), if any."""
    what = body.get("what", "")
    if what not in ("speak", "listen", "llm"):
        return "Unknown check"
    command = ["python3", REAL, "check", what]
    try:
        config = chosen_configuration(body) if what in ("speak", "listen") else None
    except ValueError as exc:
        return str(exc)
    command += ["--config", config["name"]] if config else []
    if what == "speak" and (body.get("phrase") or "").strip():
        command += ["--phrase", body["phrase"].strip()]
    voice = (body.get("voice") or "").strip() if what == "speak" else ""
    if voice:
        if voice_language(voice) is None:
            return f"Unknown voice {voice}"
        command += ["--voice", voice]
    try:
        settings = voice_settings(body) if what == "speak" else {}
    except ValueError as exc:
        return str(exc)
    command += settings_arguments(settings)
    try:
        env = control_panel.robot_env(body)
    except ValueError as exc:
        return str(exc)
    with_voice = (f" with {real_sessions.piper_text({'voice': voice, **settings})}"
                  if voice or settings else "") + (f" (configuration {config['name']})" if config else "")
    return control_panel.start_job("check", f"Check: {what}{with_voice} "
                                   "(nothing stored)", command, env, stdin=True, meta={"check": what})


# ==== The Tests tab of the control panel's page (its markup and its script; the page has the
# shared helpers: $, val, esc, opt, post, and the voice helpers fillVoice, settingsHtml...) ====

TAB_HTML = r"""
<section class="page" id="page-tests"><h2>Tests</h2>
<div class="small">Choose a scenario of the experiments, read what the user is asked to do, and talk with the robot yourself
(simple_hri: the microphone and speaker of this computer). Nothing is stored and nothing is changed: it is not the evaluation of the paper.
The conditions, models and limits are those of real/sessions_es.yaml; how the robot sounds is a configuration of the real sessions
(any, drafts included) or ad-hoc values; the language follows the scenario file.</div>
<div class="row"><div><label>Ready example (one per strategy, one per mechanism: fills the file, strategy and scenario)</label><select id="t_example" onchange="tUseExample()"></select></div>
<div><label>Example language</label><select id="t_exlang" onchange="tUseExample()"><option value="es">Spanish</option><option value="en">English twin, where there is one</option></select></div></div>
<div class="small" id="t_exinfo"></div>
<div class="row"><div><label>Scenario file</label><select id="t_file" onchange="$('t_example').value='';tFillStrategies();tVoiceChanged()"></select></div>
<div><label>Strategy</label><select id="t_strategy" onchange="$('t_example').value='';tFillList()"></select></div></div>
<label>Scenario</label><div id="t_list"></div>
<div id="t_role"></div>
<div class="row"><div><label>Condition</label><select id="t_control" onchange="tFillModel()">
<option value="bounded">bounded (the method)</option><option value="llm_led">llm_led (the LLM leads)</option></select></div>
<div><label>System model (on the Ollama server, or a cloud model of the sessions file) <button class="small" onclick="tLoadModels()" title="Ask the Ollama server again">&#8635;</button></label><select id="t_model"></select>
<div class="small" id="t_models_why"></div></div></div>
<div class="row"><div><label>ROS_DOMAIN_ID</label><input type="number" id="t_domain" value="0"></div>
<div><label>DDS (RMW_IMPLEMENTATION)</label><select id="t_rmw"><option value="">as in this terminal</option><option>rmw_fastrtps_cpp</option><option>rmw_cyclonedds_cpp</option></select></div></div>
<label class="inline" title="Future work: the environment has no camera driver nor the perception's dependencies (MediaPipe, OpenCV, cv_bridge), so barge-in cannot run yet (doc/REAL_SESSIONS.md#barge-in)"><input type="checkbox" id="t_barge" disabled> barge-in by lip activity (camera): future work, off</label>
<h3>How the robot sounds (for this test only)</h3>
<div class="row"><div><label>Configuration of the real sessions (real/configs/; any, drafts included): fills the fields below and fixes them</label><select id="t_config" onchange="tConfigChanged()"></select></div></div>
<div class="small" id="t_confinfo"></div>
<div class="row"><div><label>Voice of (filters the list below)</label><select id="t_gender" onchange="fillVoice('t_',true);tVoiceChanged()"><option value="">a man or a woman</option><option value="woman">a woman</option><option value="man">a man</option></select></div>
<div><label>Voice (Piper; for the speak check and the test, never stored)</label><select id="t_voice" onchange="tVoiceChanged()"></select></div></div>
<div class="row">
<div><label>Filler after (s): the robot says "Mmm…" when its answer takes longer (empty: the default; 0: none)</label><input type="number" id="t_filler" min="0" step="0.1" placeholder="default"></div></div>
<div class="row"><div><label>Voice configuration (fills the three fields below)</label><select id="t_preset" onchange="usePreset('t_')"></select></div></div>
<div class="row" id="t_settings"></div>
<div class="small">To compare voices and configurations, choose them and press Check: speak (only the voice starts: no model, no microphone; the first time, the voice is downloaded). The test passes them to try-scenario; the voice must be of the scenario file's language. Empty fields: the voice's own values.</div>
<div class="row"><div><label>Phrase the robot says in Check: speak (filled by the choice beside it; you can edit it)</label><input type="text" id="t_phrase" oninput="$('t_phrasepick').value=''"></div>
<div><label>What the robot says</label><select id="t_phrasepick" onchange="tPickPhrase()"><option value="q">a question with options, to hear its intonation (in the voice's language)</option><option value="check">the loudspeaker test greeting of the real sessions (check_phrase)</option><option value="">my own text: write it in the box</option></select></div></div>
<div><button class="test ck" onclick="tCheck('speak')">&#9654; Check: speak</button><button class="test ck" onclick="tCheck('listen')">&#9654; Check: listen</button>
<button class="test ck" onclick="tCheck('llm')">&#9654; Check: LLM</button><button class="run rt" id="t_run" onclick="tRun()">&#9654; Start the test</button></div>
<div class="small why" id="w_tests"></div></section>
"""

TAB_JS = r"""
// ---- tests (scripts/test_panel.py): any scenario by voice, nothing stored
let tcat={files:[],controls:{},examples:{by_strategy:[],by_mechanism:[]},configs:[]},tsel=null,tmods={ollama:[],cloud:[]};
const tfile=()=>tcat.files.find(f=>f.file==val('t_file'))||{strategies:{}};
function tFillStrategies(){$('t_strategy').innerHTML=Object.keys(tfile().strategies).map(s=>opt(s)).join('');tFillList();}
function tFillList(){const l=tfile().strategies[val('t_strategy')]||[];tsel=null;
 $('t_list').innerHTML=l.map((s,i)=>`<div class="scen" onclick="tPick(${i})" id="t_sc${i}"><code>${esc(s.id)}</code><span class="small">${esc(s.task)}</span></div>`).join('');tPick(0);}
function tPick(i){const l=tfile().strategies[val('t_strategy')]||[];tsel=l[i]||null;document.querySelectorAll('#t_list .scen').forEach((d,k)=>d.classList.toggle('sel',k==i));
 $('t_role').innerHTML=tsel?tCardHtml(tsel.card):'';}
function tCardHtml(c){return `<div class="box card"><b>${esc(c.heading)} (${esc(tfile().language)})</b>`+c.sections.filter(s=>s.lines.length).map(s=>
  `<div class="sec"><i>${esc(s.title)}</i>`+(s.lines.length==1?esc(s.lines[0]):s.lines.map(l=>'• '+esc(l)).join('\n'))+'</div>').join('')+'</div>';}
const tAllExamples=()=>[...tcat.examples.by_strategy,...tcat.examples.by_mechanism];
function tFillExamples(){const g=(label,l,off)=>`<optgroup label="${label}">`+l.map((e,i)=>opt(off+i,`${e.title} — ${e.scenario} (${e.file})`)).join('')+'</optgroup>';
 const s=tcat.examples.by_strategy;$('t_example').innerHTML=opt('','(none: choose the scenario below)')+g('One per strategy',s,0)+g('One per mechanism',tcat.examples.by_mechanism,s.length);}
function tUseExample(){const e=tAllExamples()[val('t_example')];if(!e){$('t_exinfo').textContent='';return;}let f=e.file,why='';
 if(val('t_exlang')=='en'&&f.endsWith('_es.yaml')){const twin=f.replace(/_es\.yaml$/,'.yaml');const t=tcat.files.find(x=>x.file==twin);
  if(t&&Object.values(t.strategies).some(l=>l.some(s=>s.id==e.scenario)))f=twin;else why=`No English twin of ${e.file}: in Spanish. `;}
 $('t_file').value=f;tFillStrategies();$('t_strategy').value=e.strategy||Object.keys(tfile().strategies).find(k=>tfile().strategies[k].some(s=>s.id==e.scenario));tFillList();
 const l=tfile().strategies[val('t_strategy')]||[];tPick(Math.max(0,l.findIndex(s=>s.id==e.scenario)));$('t_sc'+l.findIndex(s=>s.id==e.scenario))?.scrollIntoView({block:'nearest'});
 $('t_control').value=e.condition||'bounded';tFillModel();
 $('t_exinfo').textContent=why+(e.note?e.note+'. ':'')+(e.provokes.length?'This scenario provokes: '+e.provokes.map(p=>`${p.id} (${p.name})`).join('; '):'');}
async function tLoadModels(){tmods=await (await fetch('/api/test/models')).json();const name=m=>m.endsWith(':latest')?m.slice(0,-7):m;
 $('t_model').innerHTML=(tmods.ollama.length?`<optgroup label="Ollama (${esc(tmods.server)})">`+tmods.ollama.map(m=>opt(name(m))).join('')+'</optgroup>':'')+
  (tmods.cloud.length?'<optgroup label="Cloud (sessions file)">'+tmods.cloud.map(m=>opt(m)).join('')+'</optgroup>':'');
 $('t_models_why').textContent=tmods.error||(tmods.ollama.length?'':'The Ollama server has no models');tFillModel();}
function tFillModel(){const want=(tcat.controls[val('t_control')]||[]).filter(m=>!m.includes('<pin>'));const have=[...$('t_model').options].map(o=>o.value);
 const m=want.find(w=>have.includes(w));if(m)$('t_model').value=m;}
const tFields=()=>['t_gender','t_voice','t_preset','t_filler',...voiceCat.settings.map(s=>'t_'+s.key)];
function tConfigChanged(){const c=tcat.configs.find(x=>x.name==val('t_config'));tFields().forEach(id=>{if($(id))$(id).disabled=!!c;});
 if(c){const v=c.values;$('t_gender').value='';fillVoice('t_',true);$('t_voice').value=v.voice;voiceCat.settings.forEach(s=>$('t_'+s.key).value=v[s.key]??'');
  $('t_preset').value=presetOf(v);$('t_filler').value=v.filler_after_s??'';}
 $('t_confinfo').textContent=c?`${c.name}: ${c.state}${c.used_by.length?' (used by '+c.used_by.join(', ')+')':''}; microphone ${c.values.input_device}, speech recognition ${c.values.whisper_model}. The checks and the test pass --config ${c.name}.`:
  'Ad-hoc values: the fields below (the microphone and the speech-recognition model are the defaults: default, small).';tVoiceChanged();}
// What the test sends of how the robot sounds: a configuration, or the ad-hoc fields
function tSound(){return val('t_config')?{config:val('t_config')}:{voice:val('t_voice'),filler_after:val('t_filler'),...settingsOf('t_')};}
function tRun(){if(!tsel)return;const m=val('t_model').trim();let allow=false;if(!m){$('msg').textContent='Choose the system model';return;}
 if(m.includes('/')){allow=confirm(`${m} is a cloud model: what you say, transcribed on this computer (never the audio), goes to its provider. Go on?`);if(!allow)return;}
 post('/api/test/run',{file:val('t_file'),scenario:tsel.id,control:val('t_control'),model:m,allow_cloud:allow,domain:val('t_domain'),rmw:val('t_rmw'),barge_in:false,...tSound()});}
function tCheck(what){post('/api/test/check',{what,domain:val('t_domain'),rmw:val('t_rmw'),phrase:val('t_phrase'),...tSound()});}
function tPickPhrase(){pickPhrase('t_',tfile().language=='English'?'en':'es');}
function tVoiceChanged(){if(val('t_phrasepick')=='q')tPickPhrase();}
async function tLoad(){tcat=await (await fetch('/api/test/catalogue')).json();setVoiceCat(tcat);
 $('t_file').innerHTML=tcat.files.map(f=>opt(f.file,`${f.file} (${f.language})`)).join('');
 if(tcat.files.find(f=>f.file=='e4_es.yaml'))$('t_file').value='e4_es.yaml';
 const keep=val('t_config');$('t_config').innerHTML=opt('','(ad-hoc: the values below)')+tcat.configs.filter(c=>!c.error).map(c=>opt(c.name,`${c.name} (${c.state}${c.used_by.length?': '+c.used_by.join(', '):''})`)).join('');
 if(keep&&tcat.configs.some(c=>c.name==keep))$('t_config').value=keep;
 $('t_settings').innerHTML=settingsHtml('t_');$('t_preset').innerHTML=presetOptions();fillVoice('t_',true);
 tFillExamples();tFillStrategies();tConfigChanged();tPickPhrase();tLoadModels();}
"""


def main():
    """Retired (2026-10-30): the test panel is the Tests tab of the control panel, one server
    with one job at a time (two servers would each run a job, sharing the GPU, the microphone
    and the ROS domains)."""
    print("The test panel is now the Tests tab of the control panel:\n"
          "    python3 evaluation/scripts/control_panel.py\n"
          "then open http://localhost:8095/#tests (doc/TEST_PANEL.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
