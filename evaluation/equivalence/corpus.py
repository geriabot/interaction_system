# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The calls of the comparison: every question of LLMDialogClient, with arguments that exercise
the parts of its prompt (a frame with filled, empty, conditional, list and bounded slots and a
history; a context with a domain, resources and a notable state of the user, or none; the
material, key points, histories and scenes of the other strategies), in English and Spanish.

Each case is (name, question, function of the client). The function calls one method of the
client with its arguments; the calls it makes to the model are what is compared. Only the
public API of LLMDialogClient, DialogFrame and DialogContext is used, so that the corpus runs on
any version of the interaction intelligence that has them.
"""

import time

from interaction_intelligence.dialog_context import DialogContext, Resource, UserState
from interaction_intelligence.dialog_frame import DialogFrame

# The questions on which the model may reason when the dialogue's reasoning is "critical"
REASONING = frozenset({
    "classify_intent", "evaluate_quiz_answer", "judge_teach_back", "answer_from_material",
    "verify_answer", "classify_cancel_reply", "map_scale_answer", "classify_step_turn",
    "classify_roleplay_turn",
})

TEXT = {
    "English": {
        "task": "Take a coffee order: the drink, the milk for a latte, the size and extras.",
        "user": "A latte with oat milk, please, and make it large.",
        "question": "How long does it take?",
        "domain": "A café in the university's library.",
        "menu": "Latte 2.50 €, tea 1.80 €. Oat or soy milk. Sizes: small, large.",
        "point1": "Take the tablet after breakfast.",
        "point2": "Never with grapefruit juice.",
        "step": "Unplug the printer and open the front cover.",
        "check": "The cover is open.",
        "scene": "A customer returns a faulty kettle to a shop.",
        "role": "The shop assistant, polite but firm.",
        "goal": "Get a refund instead of a voucher.",
        "item": "In the last week, how often did you feel tired?",
        "options": ["Never", "Sometimes", "Often", "Always"],
    },
    "Spanish": {
        "task": "Tomar un pedido de café: la bebida, la leche si es un latte, el tamaño y extras.",
        "user": "Un latte con leche de avena, por favor, y que sea grande.",
        "question": "¿Cuánto tarda?",
        "domain": "Una cafetería en la biblioteca de la universidad.",
        "menu": "Latte 2,50 €, té 1,80 €. Leche de avena o de soja. Tamaños: pequeño, grande.",
        "point1": "Tomar la pastilla después del desayuno.",
        "point2": "Nunca con zumo de pomelo.",
        "step": "Desenchufa la impresora y abre la tapa frontal.",
        "check": "La tapa está abierta.",
        "scene": "Un cliente devuelve un hervidor estropeado en una tienda.",
        "role": "El dependiente, educado pero firme.",
        "goal": "Conseguir el reembolso en vez de un vale.",
        "item": "En la última semana, ¿con qué frecuencia se sintió cansado?",
        "options": ["Nunca", "A veces", "A menudo", "Siempre"],
    },
}

FRAME_SCHEMA = """{"slots": [
  {"name": "drink", "type": "str", "canonical_values": ["latte", "tea", "café solo"]},
  {"name": "milk", "type": "str", "canonical_values": ["oat", "soy"],
   "condition_slot": "drink", "condition_value": "latte"},
  {"name": "size", "type": "str", "canonical_values": ["small", "large"]},
  {"name": "extras", "type": "list_str"},
  {"name": "age", "type": "int", "minimum": 18, "maximum": 99},
  {"name": "member", "type": "bool", "condition_slot": "age", "condition_minimum": 65},
  {"name": "notes", "type": "str"}
]}"""


def frames(t: dict) -> dict:
    """Frames at three moments of a slot filling: empty, partly filled, complete."""
    empty = DialogFrame.from_slots_json(FRAME_SCHEMA)
    partial = DialogFrame.from_slots_json(FRAME_SCHEMA)
    partial.add_turn("assistant", "Hello! What would you like to drink?")
    partial.add_turn("user", t["user"])
    partial.try_update({"drink": "latte", "milk": "oat", "extras": ["cinnamon", "canela"]})
    partial.increment_attempts("size")
    partial.add_turn("assistant", "Great. Which size?")
    full = DialogFrame.from_slots_json(FRAME_SCHEMA)
    full.try_update({"drink": "latte", "milk": "soy", "size": "large", "extras": ["sugar"],
                     "age": 70, "member": True, "notes": "sin lactosa, por favor"})
    for i in range(7):
        full.add_turn("assistant" if i % 2 == 0 else "user", f"turn {i}: «{t['user']}»")
    full.mark_unanswered("notes")
    return {"empty": empty, "partial": partial, "full": full}


def contexts(t: dict) -> dict:
    with_material = DialogContext(
        resources=[Resource(name="menu", description="The menu of the day", content=t["menu"]),
                   Resource(name="hours", description="", content="8:00–20:00")],
        domain=t["domain"])
    upset = DialogContext(domain=t["domain"])
    upset.update_user_state(UserState(emotion="frustrated", confidence=0.83, source="vision",
                                      timestamp=time.monotonic()))
    calm = DialogContext()
    calm.update_user_state(UserState(emotion="happy", confidence=0.2, source="audio"))
    return {"none": None, "material": with_material, "upset": upset, "calm": calm}


def cases(language: str) -> list[tuple[str, str, callable]]:
    """The calls of the comparison in one language: (name, question, call(client))."""
    t = TEXT[language]
    out: list[tuple[str, str, callable]] = []

    def add(question: str, variant: str, call):
        out.append((f"{question}/{variant}", question, call))

    history = [{"role": "assistant", "content": "Hi! Shall we start?"},
               {"role": "user", "content": t["question"]},
               {"role": "assistant", "content": t["point1"]},
               {"role": "user", "content": "ok"}] * 3
    material = [{"name": "leaflet", "content": f"{t['point1']} {t['point2']}"},
                {"name": "faq", "content": t["menu"]}]
    key_points = [{"point": t["point1"], "critical": True},
                  {"point": t["point2"], "critical": False}]
    scene = {"scenario": t["scene"], "role": t["role"], "user_goal": t["goal"],
             "criteria": ["States the problem clearly", "Asks for a refund"]}
    scene_no_goal = {"scenario": t["scene"], "role": t["role"], "criteria": ["Is polite"]}
    play = [{"role": "assistant", "content": "What would you like to drink?"},
            {"role": "user", "content": "This kettle does not work."},
            {"role": "assistant", "content": "I see. Do you have the receipt?"},
            {"role": "user", "content": "Yes, here it is. I want my money back."}]
    behaviors = [{"behavior_id": 0, "name": "slot_filling", "mode": "slot_filling",
                  "explanation": "  Collect the data of a form.  "},
                 {"behavior_id": 3, "name": "explanation", "mode": "explanation",
                  "explanation": "Explain something and check it was understood."}]

    for c in ("none", "material", "upset", "calm"):
        add("classify_task_mode", c, lambda llm, c=c: llm.classify_task_mode(
            t["task"], contexts(t)[c]))
        add("choose_dialogue_behavior", c, lambda llm, c=c: llm.choose_dialogue_behavior(
            t["task"], behaviors, contexts(t)[c]))
        add("generate_explanation", c, lambda llm, c=c: llm.generate_explanation(
            t["task"], contexts(t)[c]))
        add("generate_explanation_direct", c, lambda llm, c=c: llm.generate_explanation_direct(
            t["task"], contexts(t)[c]))
        add("qa_opening", c, lambda llm, c=c: llm.qa_opening(t["task"], contexts(t)[c]))
        add("ask_cancel_confirmation", c, lambda llm, c=c: llm.ask_cancel_confirmation(
            t["task"], contexts(t)[c]))
        add("understanding_confirmed_response", c,
            lambda llm, c=c: llm.understanding_confirmed_response(t["task"], contexts(t)[c]))
        add("create_frame", c, lambda llm, c=c: llm.create_frame(t["task"], contexts(t)[c]))

    for f in ("empty", "partial", "full"):
        for c in ("none", "material", "upset"):
            v = f"{f}-{c}"
            add("get_opening", v, lambda llm, f=f, c=c: llm.get_opening(
                t["task"], frames(t)[f], contexts(t)[c]))
            add("get_opening_direct", v, lambda llm, f=f, c=c: llm.get_opening_direct(
                t["task"], frames(t)[f], contexts(t)[c]))
            add("get_resume_opening", v, lambda llm, f=f, c=c: llm.get_resume_opening(
                t["task"], frames(t)[f], frames(t)[f].next_slot(), contexts(t)[c]))
            add("extract_turn", v, lambda llm, f=f, c=c: llm.extract_turn(
                t["user"], frames(t)[f], contexts(t)[c]))
            add("generate_response", v, lambda llm, f=f, c=c: llm.generate_response(
                t["task"], frames(t)[f], t["user"],
                [{"slot": "drink", "value": "latte"}, {"slot": "extras", "value": ["canela"]}],
                ["size"], contexts(t)[c], {"age": 7}))
            add("get_confirmation_question", v, lambda llm, f=f, c=c:
                llm.get_confirmation_question(t["task"], frames(t)[f], contexts(t)[c]))
            add("classify_intent", v, lambda llm, f=f, c=c: llm.classify_intent(
                "No, soy milk. " + t["user"], frames(t)[f], contexts(t)[c]))
            add("answer_confirmation_question", v, lambda llm, f=f, c=c:
                llm.answer_confirmation_question(t["question"], t["task"], frames(t)[f],
                                                 contexts(t)[c]))
            add("response_to_intent", v, lambda llm, f=f, c=c: llm.response_to_intent(
                t["task"], "corrects", {"milk": "soy", "extras": ["miel"]}, frames(t)[f],
                contexts(t)[c]))
            add("cancel_confirmed_response", v, lambda llm, f=f, c=c:
                llm.cancel_confirmed_response(t["task"], frames(t)[f], contexts(t)[c]))
        add("audit_frame", f, lambda llm, f=f: llm.audit_frame(
            frames(t)[f], t["task"], contexts(t)["material"]))
    add("generate_response", "no-extractions", lambda llm: llm.generate_response(
        t["task"], frames(t)["partial"], "mm", [], [], None))
    add("cancel_confirmed_response", "no-frame", lambda llm: llm.cancel_confirmed_response(
        t["task"], None, contexts(t)["material"]))

    for c in ("none", "material"):
        ctx = (lambda c=c: contexts(t)[c])
        add("judge_teach_back", c, lambda llm, ctx=ctx: llm.judge_teach_back(
            t["point1"] + " y " + t["point2"], key_points, "¿Qué tienes que hacer?", ctx()))
        add("repair_explanation", c, lambda llm, ctx=ctx: llm.repair_explanation(
            t["task"], [{"point": t["point1"], "status": "wrong", "said": "before breakfast"},
                        {"point": t["point2"], "status": "incomplete", "said": "zumo"},
                        {"point": "x", "status": "missing", "said": ""}],
            [t["point2"]], ctx()))
        add("targeted_question", c, lambda llm, ctx=ctx: llm.targeted_question(
            t["point1"], ctx()))
        add("ask_missing_points", c, lambda llm, ctx=ctx: llm.ask_missing_points(
            t["task"], [t["point1"]], [t["point2"], "Drink water"], ctx()))
        add("answer_explanation_question", c, lambda llm, ctx=ctx:
            llm.answer_explanation_question(t["question"], t["task"], history, ctx()))
        add("rephrase_explanation", c, lambda llm, ctx=ctx: llm.rephrase_explanation(
            t["task"], history, 2, ctx()))
        add("answer_from_material", c, lambda llm, ctx=ctx: llm.answer_from_material(
            t["question"], t["task"], material, history, ctx()))
        add("verify_answer", c, lambda llm, ctx=ctx: llm.verify_answer(
            t["point1"], material, ctx()))
        add("map_scale_answer", c, lambda llm, ctx=ctx: llm.map_scale_answer(
            "more or less often", t["item"], t["options"], ctx()))
        add("procedure_opening", c, lambda llm, ctx=ctx: llm.procedure_opening(
            t["task"], 4, ctx()))
        add("procedure_step", c, lambda llm, ctx=ctx: llm.procedure_step(
            t["step"], 2, 4, "after a problem", ctx()))
        add("classify_step_turn", c, lambda llm, ctx=ctx: llm.classify_step_turn(
            "hecho", t["step"], t["check"], history, ctx()))
        add("procedure_answer_question", c, lambda llm, ctx=ctx: llm.procedure_answer_question(
            t["question"], t["task"], t["step"], history, ctx()))
        add("procedure_help_step", c, lambda llm, ctx=ctx: llm.procedure_help_step(
            t["task"], t["step"], history, 3, ctx()))
        add("procedure_remind", c, lambda llm, ctx=ctx: llm.procedure_remind(t["step"], ctx()))
        add("procedure_summary", c, lambda llm, ctx=ctx: llm.procedure_summary(
            t["task"], 3, 4, [t["step"]], ctx()))
        add("roleplay_intro", c, lambda llm, ctx=ctx: llm.roleplay_intro(
            t["task"], scene, ctx()))
        add("roleplay_reply", c, lambda llm, ctx=ctx: llm.roleplay_reply(scene, play, ctx()))
        add("classify_roleplay_turn", c, lambda llm, ctx=ctx: llm.classify_roleplay_turn(
            "Can you help me?", scene, play, ctx()))
        add("roleplay_coach", c, lambda llm, ctx=ctx: llm.roleplay_coach(
            "Can you help me?", scene_no_goal, play, ctx()))
        add("evaluate_roleplay", c, lambda llm, ctx=ctx: llm.evaluate_roleplay(
            scene, play, ctx()))
        add("roleplay_feedback", c, lambda llm, ctx=ctx: llm.roleplay_feedback(
            scene, {"scores": [{"criterion": "States the problem clearly", "score": 4,
                                "comment": "Clear.", "evidence": "This kettle does not work."},
                               {"criterion": "Asks for a refund", "score": 2, "comment": "",
                                "evidence": ""}]}, "scene", ctx()))
        add("detect_cancel_intent", c, lambda llm, ctx=ctx: llm.detect_cancel_intent(
            "I think I would rather stop here", ctx(), strict=False, task=t["task"]))
        add("detect_cancel_intent", c + "-strict", lambda llm, ctx=ctx:
            llm.detect_cancel_intent("please cancel the milk", ctx(), strict=True))
        add("classify_cancel_reply", c, lambda llm, ctx=ctx: llm.classify_cancel_reply(
            "bueno, vale", ctx()))

    add("roleplay_reply", "no-goal", lambda llm: llm.roleplay_reply(scene_no_goal, [], None))
    add("extract_key_points", "material", lambda llm: llm.extract_key_points(
        t["task"], material))
    add("extract_key_points", "no-material", lambda llm: llm.extract_key_points(t["task"], []))
    add("judge_teach_back", "one-point", lambda llm: llm.judge_teach_back(
        "no sé", key_points[:1], "Tell me", None))
    add("verify_finished", "plain", lambda llm: llm.verify_finished("nothing else, thanks"))
    add("verify_finished", "robot", lambda llm: llm.verify_finished(
        "no, gracias", "¿Algo más?"))
    add("detect_wrong_check", "plain", lambda llm: llm.detect_wrong_check(
        "Nine euros? Fine.", "It costs 19 euros."))
    add("detect_wrong_check", "spanish", lambda llm: llm.detect_wrong_check(
        "¿A las ocho? Vale, gracias.", "Abre a las 10:00."))
    add("quiz_opening", "plain", lambda llm: llm.quiz_opening(t["task"], 5))
    add("evaluate_quiz_answer", "plain", lambda llm: llm.evaluate_quiz_answer(
        "¿Cuál es el planeta más cercano al Sol?", "Mercurio", "Mercury... no, lead"))
    add("quiz_rephrase_question", "plain", lambda llm: llm.quiz_rephrase_question(
        "What is H2O?", "water"))
    add("quiz_next_question", "plain", lambda llm: llm.quiz_next_question(
        "¡Correcto!", 2, 5, "What is NaCl?"))
    add("quiz_summary", "wrongs", lambda llm: llm.quiz_summary(
        t["task"], 3, 5, ["What is NaCl?", "Q4"]))
    add("quiz_summary", "none-wrong", lambda llm: llm.quiz_summary(t["task"], 5, 5, []))
    return out


def ablation_cases(language: str) -> list[tuple[str, str, callable]]:
    """The calls of the LLM-led baseline (interaction_ablations: the whole strategy in one
    prompt), one per strategy; [] when the ablations are not importable."""
    try:
        from interaction_ablations import skills as ablation
    except ImportError:
        return []
    from interaction_intelligence.skills import SkillEnv

    t = TEXT[language]
    base = {"task": t["task"], "domain": t["domain"],
            "mono_history": [{"role": "assistant", "content": "Hello!"},
                             {"role": "user", "content": t["user"]}]}
    states = {
        "slot_filling": {"form": DialogFrame.from_slots_json(FRAME_SCHEMA).to_state()},
        "slot_filling-no-form": {},
        "quiz": {"questions": [{"question": "What is H2O?", "answer": "water"},
                               {"question": "¿Capital de Francia?", "answer": "París"}]},
        "procedure": {"steps": [{"step": t["step"], "check": t["check"]},
                                {"step": "Close it.", "check": ""}]},
        "grounded_qa": {"material": [{"name": "leaflet", "content": t["point1"]}],
                        "handover": "Ask at the desk."},
        "questionnaire": {"instrument": {
            "name": "PHQ", "instructions": "Answer for the last week.",
            "items": [{"text": t["item"], "clarification": "",
                       "options": [{"label": o} for o in t["options"]]},
                      {"text": "Sleep?", "clarification": "Hours of sleep.",
                       "options": [{"label": "Yes"}, {"label": "No"}]}]}},
        "roleplay": {"scene": {"scenario": t["scene"], "role": t["role"], "user_goal": t["goal"],
                               "criteria": ["Is polite", "Asks for a refund"]},
                     "max_exchanges": 8},
        "explanation": {"resources": [{"name": "leaflet", "content": t["point1"]}]},
        "explanation-no-resources": {"resources": [], "domain": ""},
    }
    out = []
    for name, extra in states.items():
        strategy = name.split("-")[0]

        def call(llm, extra=extra, strategy=strategy):
            state = {**base, "strategy": strategy, **extra}
            ablation._ask(SkillEnv(llm=llm, ctx=DialogContext()), state,
                          'THE USER JUST SAID\n"' + t["user"] + '"')

        out.append((f"monolithic_turn/{name}", "monolithic_turn", call))
    return out
