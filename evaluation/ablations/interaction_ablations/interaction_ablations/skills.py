# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The skills of the ablations: the designs of the related work, run inside the interaction
system so that only one decision changes. Loaded by the interaction intelligence with
skill_modules: ["interaction_ablations.skills"]; the method's code is not modified.

- monolithic.*: the LLM runs the whole strategy (the LLM-led baseline). It gets the task, the
  strategy's rules (the same rules the method's trees follow), the scope rules (stay within the
  task, say only facts of the material), the method's rule on how a spoken line addresses the
  user, the resources and the conversation, writes every
  reply and decides when the dialogue ends and its result (the design of AutoTOD, Dobby and
  the monolithic baselines of Hudeček 2023 and Garcia 2026). Silences and the turn limit are
  the same subtrees as the method's.
- ablation.cancel_check_*: the spoken cancellation without its confirmation, with the LLM only
  (no local fast path), or with the keywords only (no LLM).
"""

from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.interpretation_context import ask_as
from interaction_intelligence.skills import (
    DialogueMode, SkillEnv, SkillInput, SkillResult, get_mode, register_mode, skill,
)
from interaction_intelligence.skills.common import resume_line, user_finished
from interaction_intelligence.utils import (cancel_intent_of, local_cancel_check,
                                            parse_loose_json)

# ==== MONOLITHIC ====

RULES = {
    "slot_filling": """Collect the data of the form below from the user, asking for what is
missing one question at a time. Accept corrections at any moment. Only accept the allowed values
of a field, and numbers within its range; say why when a value is not valid. Fields with a
condition are only asked when the condition holds. When every needed field is filled, read all
the data back and ask the user to confirm it. Only when the user confirms, close the
conversation: done true, outcome "success" and the data in result.data (field: value). If the
user corrects something, apply it and confirm again. After 3 unclear answers to the
confirmation, close with outcome "failed".""",
    "explanation": """Explain the topic of the task to the user, using the resources if any.
Then check understanding with teach-back: ask the user to tell you in their own words what they
have to do (never accept a plain "yes, I understood" as evidence). Judge the answer against the
key points of the material (given in the resources, or the essential facts of the material):
every critical key point must be said back correctly; a paraphrase is fine, a different time,
dose, place or order is wrong. If a critical point is missing or wrong, explain again only the
points missing or wrong, in other words, and ask the user to tell you again. If the user does not
want to say it back, ask one direct question about the most critical point instead. Answer the
user's questions. When every critical key point was said back correctly, close: done true,
outcome "success", result.data {"understood": true}. After 3 checks without that (2 repairs),
remind the critical points and close with outcome "failed".""",
    "grounded_qa": """Answer the user's questions about the topic of the task, ONLY with facts
stated in the MATERIAL below, never from general knowledge. If a question is within the topic but
its answer is not in the MATERIAL, say that you do not have that information (and offer the
HANDOVER below, if there is one). If something is outside the task, say you cannot help with it
here. Do not state anything the MATERIAL does not support. After each answer, ask whether the user
wants to know anything else. When the user has no more questions, close: done true, outcome
"success", result.data {"questions": [{"question": ..., "resolution": "answered" | "abstained" |
"declined"}]}.""",
    "questionnaire": """Administer the questionnaire below as a standardized instrument: read the
instructions, then each item exactly as written, in order, each followed by its answer options as
written. Map the user's answer to one option only when the answer clearly chooses it; if it is
between options or none of them, read the options again without suggesting any. If the user asks
what an item means, give the clarification of the instrument if there is one, or read the item
again. The user may decline an item. Do not say the score. After the last item, thank the user
and close: done true, outcome "success", result.data {"answers": [{"item": ..., "answer": the
option chosen or null}]}.""",
    "quiz": """Ask the questions of the question bank below one at a time, in order, without
revealing the answers. Grade each answer of the user against the expected answer (accept
paraphrases, synonyms and spelling variants; be strict with wrong facts), give short feedback,
then ask the next question. After the last answer give the score and close: done true, outcome
"success", result.data {"results": [{"question": ..., "correct": true|false}, ...]} with one
entry per question in order.""",
    "procedure": """Guide the user through the steps below one at a time, in order, while the
user does them. When the user says a step is done, present the next one. Answer questions about
the current step, help if the user has a problem (after 3 helps on the same step, mark it stuck
and move on), repeat it or go back to the previous one when asked, and skip it when the user
asks. After the last step, summarise and close: done true, outcome "success", result.data
{"results": [{"step": ..., "status": "done" | "skipped" | "stuck"}, ...]} with one entry per
step in order.""",
    "roleplay": """Play the character of the scene below so that the user can practise the
situation: stay in character, one short line at a time, one question at a time. If the user
asks for help, give a short hint out of character and go on with the scene. The scene ends when
the user wants to stop, when it reaches its natural end, or after {max_exchanges} user turns.
Then step out of the role and score the user from 1 to 5 on each criterion of the rubric with
a short comment, and close: done true, outcome "success", result.data {"scores": [{"criterion":
..., "score": 1-5}], "ended_by": "user" | "scene" | "limit"}.""",
}

PROMPT = """You are the dialogue manager of a social robot that speaks with a user. You decide
everything the robot says and when the conversation ends.

TASK: {task}
{domain}
RULES
{rules}
- Stay within the task: if the user asks for something outside it (other topics, advice the
  robot should not give, another task), decline politely in one sentence and bring the
  conversation back to the task. Say only facts that are in the task, the rules or the
  material below; never add facts of your own.
- If the user clearly wants to stop the whole conversation, ask them to confirm; if they
  confirm, close with done true and outcome "cancelled".
- Say one short spoken reply per turn, in {language}, without lists or markup.
- {address}
- While the conversation goes on, done is false and result is {{}}.

{material}
CONVERSATION SO FAR
{history}

{now}

Answer in JSON: {{"reply": "<what the robot says now>", "done": true | false,
"outcome": "success" | "cancelled" | "failed" | "", "result": {{"data": {{...}}}}}}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "done": {"type": "boolean"},
        "outcome": {"type": "string", "enum": ["success", "cancelled", "failed", ""]},
        "result": {"type": "object"},
    },
    "required": ["reply", "done", "outcome", "result"],
}


def _material(state: dict) -> str:
    """What the strategy works on: the form, the question bank, the steps, the scene, or the
    resources of an explanation."""
    strategy = state["strategy"]
    if strategy == "slot_filling":
        frame = DialogFrame.from_state(state["form"]) if state.get("form") else None
        if frame is None:
            return "FORM\n(design the minimum fields the task needs)"
        return "FORM (field (type): rules)\n" + frame.describe_slots()
    if strategy == "quiz":
        items = "\n".join(
            f"{i + 1}. {q['question']} — expected answer: {q['answer']}"
            for i, q in enumerate(state["questions"])
        )
        return "QUESTION BANK\n" + items
    if strategy == "procedure":
        items = "\n".join(
            f"{i + 1}. {s['step']}" + (f" (check: {s['check']})" if s["check"] else "")
            for i, s in enumerate(state["steps"])
        )
        return "STEPS\n" + items
    if strategy == "grounded_qa":
        material = "\n\n".join(f"[{r['name']}] {r['content']}" for r in state["material"])
        return f"MATERIAL\n{material}" + (
            f"\nHANDOVER\n{state['handover']}" if state.get("handover") else "")
    if strategy == "questionnaire":
        instrument = state["instrument"]
        items = "\n".join(
            f"{i + 1}. {item['text']} (options: " + ", ".join(o["label"] for o in item["options"])
            + (f"; clarification: {item['clarification']}" if item["clarification"] else "") + ")"
            for i, item in enumerate(instrument["items"]))
        return ("QUESTIONNAIRE" + (f" {instrument['name']}" if instrument["name"] else "")
                + (f"\nInstructions: {instrument['instructions']}" if instrument["instructions"]
                   else "") + "\nItems:\n" + items)
    if strategy == "roleplay":
        scene = state["scene"]
        return (
            f"SCENE\nScenario: {scene['scenario']}\nYour role: {scene['role']}\n"
            + (f"The user's goal: {scene['user_goal']}\n" if scene.get("user_goal") else "")
            + "Rubric: " + "; ".join(scene["criteria"])
        )
    resources = "\n\n".join(f"[{r['name']}] {r['content']}" for r in state["resources"])
    return f"RESOURCES\n{resources}" if resources else ""


def _rules(state: dict) -> str:
    rules = RULES[state["strategy"]]
    if state["strategy"] == "roleplay":
        rules = rules.replace("{max_exchanges}", str(state["max_exchanges"]))
    return rules


def prompt_fields(env: SkillEnv, state: dict, now: str) -> dict:
    """The values of the slots of PROMPT for this turn."""
    history = "\n".join(
        f"{'Robot' if t['role'] == 'assistant' else 'User'}: {t['content']}"
        for t in state["mono_history"]
    ) or "(nothing yet: the robot speaks first)"
    return {
        "task": state["task"],
        "domain": f"DOMAIN: {state['domain']}\n" if state.get("domain") else "",
        "rules": _rules(state),
        "language": getattr(env.llm, "_pb", None) and env.llm._pb._language or "English",
        # The method's own rule on how a spoken line addresses the user (direct, informal second
        # person): the baseline differs from the method only in the decision ablated
        "address": getattr(getattr(env.llm, "_pb", None), "address", ""),
        "material": _material(state),
        "history": history,
        "now": now,
    }


def _ask(env: SkillEnv, state: dict, now: str) -> dict:
    fields = prompt_fields(env, state, now)
    # The question of the interpretation server that asks it (interpretation_questions.py)
    prompt = ask_as(PROMPT.format(**fields), "monolithic_turn", {"fields": fields})
    for _ in range(2):
        raw = env.llm._call_nlu(prompt, SCHEMA, max_tokens=600)
        if not raw:
            continue
        try:
            answer = parse_loose_json(raw)
            if isinstance(answer, dict) and str(answer.get("reply", "")).strip():
                return answer
        except Exception:
            continue
    return {"reply": "Sorry, could you repeat that?", "done": False, "outcome": "", "result": {}}


def _mono_data(state: dict) -> dict:
    return state.get("mono_result") or {}


register_mode(
    "monolithic",
    DialogueMode(
        init=lambda env, state: None,
        phase=lambda state: "done" if state.get("completed") else "running",
        data=_mono_data,
        succeeded=lambda state: state.get("mono_outcome") == "success",
    ),
)


@skill("monolithic.open")
def mono_open(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Take over the strategy that `begin` prepared, and say the opening."""
    strategy = state["mode"]
    get_mode(strategy)  # the strategy exists
    state["strategy"] = strategy
    state["mode"] = "monolithic"
    state["mono_history"] = []
    state["mono_outcome"] = ""
    state["mono_result"] = {}
    if strategy == "slot_filling" and state.get("frame_schema_json"):
        state["form"] = DialogFrame.from_slots_json(state["frame_schema_json"]).to_state()
    if state.get("skip_intro"):
        now = "Start the conversation now, without greeting (the user was already greeted)."
    else:
        now = "Start the conversation now: greet the user briefly and begin the task."
    answer = _ask(env, state, now)
    state["mono_history"].append({"role": "assistant", "content": answer["reply"]})
    return SkillResult(reply=answer["reply"])


@skill("monolithic.turn")
def mono_turn(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The user spoke: the LLM writes the reply and decides whether the dialogue ends.
    label: "done" or "continue"."""
    state["mono_history"].append({"role": "user", "content": inp.user_text})
    answer = _ask(env, state, f'THE USER JUST SAID\n"{inp.user_text}"')
    state["mono_history"].append({"role": "assistant", "content": answer["reply"]})
    if answer.get("done"):
        state["completed"] = True
        state["mono_outcome"] = answer.get("outcome") or "failed"
        if state["mono_outcome"] == "cancelled":
            state["user_cancelled"] = True
        result = answer.get("result") or {}
        state["mono_result"] = result.get("data", result) if isinstance(result, dict) else {}
        return SkillResult(reply=answer["reply"], label="done")
    return SkillResult(reply=answer["reply"], label="continue")


# ==== CANCELLATION ====


def _frame_or_none(state: dict):
    if state.get("mode") == "slot_filling" and state.get("frame"):
        return DialogFrame.from_state(state["frame"])
    return None


@skill("ablation.cancel_check_no_confirmation")
def cancel_no_confirmation(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """As the method's cancel_check, but a detected cancel ends the dialogue at once."""
    strict = get_mode(state["mode"]).strict_cancel(state)
    if env.llm.detect_cancel_intent(inp.user_text, env.ctx, strict=strict, task=state["task"]):
        state["user_cancelled"] = True
        reply = env.llm.cancel_confirmed_response(state["task"], _frame_or_none(state), env.ctx)
        return SkillResult(reply=reply, label="confirmed")
    return SkillResult(label="none")


def _cancel_with(detect, env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The method's flow (confirm before cancelling, and the same reading of the answer: yes,
    no, finished, or going on with the task) with another detector of the request."""
    task, ctx = state["task"], env.ctx
    if state.get("awaiting_cancel"):
        state["awaiting_cancel"] = False
        answer = env.llm.classify_cancel_reply(inp.user_text, ctx)
        if answer == "yes":
            state["user_cancelled"] = True
            reply = env.llm.cancel_confirmed_response(task, _frame_or_none(state), ctx)
            return SkillResult(reply=reply, label="confirmed")
        if answer == "no":
            reply = f"{env.llm.phrase('cancel_no')} {state.get('resume_line', '')}".strip()
            return SkillResult(reply=reply, label="denied")
        if answer == "finished":
            finished = user_finished(env, state)
            if finished:
                return finished
        return SkillResult(label="none")
    if detect(env, state, inp):
        state["awaiting_cancel"] = True
        state["resume_line"] = resume_line(env, inp.option)
        return SkillResult(reply=env.llm.ask_cancel_confirmation(task, ctx), label="ask")
    return SkillResult(label="none")


def _llm_only(env: SkillEnv, state: dict, inp: SkillInput) -> bool:
    strict = get_mode(state["mode"]).strict_cancel(state)
    prompt, schema = env.llm._pb.detect_cancel_intent(inp.user_text, env.ctx, strict=strict,
                                                      task=state["task"])
    raw = env.llm._call_nlu(prompt, schema, max_tokens=30)
    try:
        return bool(raw) and cancel_intent_of(parse_loose_json(raw))
    except Exception:
        return False


def _keywords_only(env: SkillEnv, state: dict, inp: SkillInput) -> bool:
    # The exact stop commands of the dialogue's language (its language file)
    return local_cancel_check(inp.user_text, getattr(env.llm, "language", "English")) is True


@skill("ablation.cancel_check_llm_only")
def cancel_llm_only(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The cancel request detected by the LLM alone (no local fast path)."""
    return _cancel_with(_llm_only, env, state, inp)


@skill("ablation.cancel_check_keywords_only")
def cancel_keywords_only(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """The cancel request detected by keywords alone (an ambiguous request is not a cancel)."""
    return _cancel_with(_keywords_only, env, state, inp)

