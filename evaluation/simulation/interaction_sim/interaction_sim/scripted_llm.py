# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
A deterministic stand-in for the LLM, to test the control of the dialogue behaviors.

It has the methods of LLMDialogClient, answered by fixed rules on the user's words instead of
a model, so that a scripted user steers every branch of the trees on purpose. The replies are
tags in brackets ("[opening]", "[confirm] ...") that the tests check.

Rules on what the user says:
  slots          "name=value" pairs separated by ";" ("+items=a,b" adds to a list,
                 "-items=a" removes from it); "skip" or "don't know" skips the slot asked;
                 "repeat" asks to repeat; a question mark asks; else nothing understood
  confirmation   "yes" confirms; a question mark asks; "name=value" corrects; else unclear
  explanation    teach-back: "restate all" says every key point back; "right 1", "wrong 2" say
                 those points back right or wrong (numbered among the points still to
                 verify, the only ones the skill sends); a question mark asks; "understood" or
                 "got it" alone declines; "do not understand" or "again" did not understand;
                 else other. Without key points in the request there is one ("the main point")
  quiz           "repeat" or "pardon" is not an answer; else correct if the expected answer
                 is in what the user said
  procedure      the first of done, skip, problem, repeat, back in what the user said; a
                 question mark asks; else unclear
  grounded_qa    "ask <word>": answered with "[<word>]" if the word is in the material, else not
                 in it ("ask unsupported" is answered but fails the verification); "off topic"
                 is outside the task; "shame" is a comment; "repeat" repeats; "no more" or
                 "bye" ends; else other
  questionnaire  "option N" chooses option N; "between" is ambiguous; "meaning" asks what it
                 means; "pass" declines; "repeat" repeats; else other
  roleplay       "stop practice" ends, "the end" ends the scene, "help me" asks for a hint,
                 "repeat" asks to hear the last line again
  cancel         an exact stop command of the dialogue's language asks to cancel (as the real
                 fast path: "cancel", "stop"; "Para." in Spanish); "please cancel" too,
                 except while a closed-choice slot is asked (strict mode); "forget it" asks to
                 cancel also in strict mode; "yes" (or "sí") confirms it, "no" resumes,
                 anything else is "other" (the robot resumes too)
  phrases        the fixed phrases are their key in brackets ("[close_breakdown]")
  slow           "slow N" (N seconds, e.g. "slow 3" or "slow 2.5") makes the answer take N
                 seconds, as a slow model would (to test the conversational fillers): in a user
                 turn, the cancel check (the first question of every turn, in every strategy)
                 waits; in the task, the opening of slot filling waits
  behavior       a task that says "explain", "quiz", "guide" or "practise" chooses the
                 explanation, quiz, procedure or roleplay; otherwise slot filling (-1 if the
                 chosen one is not offered); a task that says "choice fails" makes the choice
                 raise, as a model that fails
  LLM-led        (the monolithic ablation, which sends its whole prompt) "end success",
                 "end failed" or "end cancelled" closes with that outcome and the
                 "name=value" pairs as the data; "garble" answers no JSON; else it goes on.
                 The opening is "[mono_open]" ("[mono_open_direct]" without the greeting),
                 the other replies "[mono] <what the user said>"

Used by the interaction intelligence when its parameter llm_client_factory is
"interaction_sim.scripted_llm:make". LocalizedScriptedLLM (make_localized, or
interaction_sim.llm_faults:make_scripted_localized behind the injector of model failures) answers
the same, but says the real fixed phrases of the dialogue's language file instead of their tags,
so that a test checks the lines the robot says in English and in Spanish; "sí" confirms like
"yes" in both.
"""

import json
import re
import time

from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.utils import local_cancel_check

_PAIR = re.compile(r"([+-]?)(\w+)\s*=\s*([^;]+)")
_SLOW = re.compile(r"\bslow (\d+(?:\.\d+)?)\b")


def _slow(text: str) -> None:
    """Take the seconds that "slow N" asks for, as a slow model would."""
    match = _SLOW.search(text or "")
    if match:
        time.sleep(float(match.group(1)))


def make():
    return ScriptedLLM()


def make_localized():
    return LocalizedScriptedLLM()


def _pairs(text: str) -> list[tuple[str, str, str]]:
    return [(op, slot, value.strip()) for op, slot, value in _PAIR.findall(text)]


class ScriptedLLM:

    history_max_turns = 200
    # The language of the dialogue (set by the intelligence): its exact stop commands
    language = "English"

    def phrase(self, key):
        return f"[{key}]"

    # ==== TASK LEVEL ====

    _MODE_WORDS = (
        ("explain", "explanation"),
        ("quiz", "quiz"),
        ("guide", "procedure"),
        ("practise", "roleplay"),
    )

    def choose_dialogue_behavior(self, task, behaviors, ctx=None):
        if "choice fails" in task.lower():
            raise RuntimeError("scripted failure of the choice")
        wanted = next((m for w, m in self._MODE_WORDS if w in task.lower()), "slot_filling")
        for b in behaviors:
            if b.get("mode") == wanted:
                return b["behavior_id"]
        return -1

    def create_frame(self, task, ctx=None):
        match = re.search(r"slots:\s*([\w, ]+)", task)
        names = [n.strip() for n in match.group(1).split(",")] if match else ["name"]
        return DialogFrame({n: None for n in names}, {n: "str" for n in names})

    def audit_frame(self, frame, task, ctx=None):
        return 0

    # ==== SLOT FILLING ====

    def get_opening(self, task, frame, ctx=None):
        _slow(task)
        return f"[opening] {frame.next_slot()}?"

    def get_opening_direct(self, task, frame, ctx=None):
        _slow(task)
        return f"[opening_direct] {frame.next_slot()}?"

    def get_resume_opening(self, task, frame, next_slot, ctx=None):
        return f"[resume] {next_slot}?"

    def extract_turn(self, user_text, frame, ctx=None):
        values = self.extract_slots(user_text, frame, ctx)
        text = user_text.lower()
        if values:
            return "values", values
        if "skip" in text or "don't know" in text:
            return "skip", []
        if "repeat" in text:
            return "repeat", []
        return ("question" if "?" in text else "other"), []

    def extract_slots(self, user_text, frame, ctx=None):
        out = []
        for op, slot, value in _pairs(user_text):
            if slot not in frame.all_keys():
                continue
            if frame.slot_type(slot) == "list_str":
                items = [i.strip() for i in value.split(",") if i.strip()]
                entry = {"slot": slot, "value": items, "source_span": value}
                if op == "-":
                    entry["_remove"] = True
                elif op == "+":
                    entry["_add"] = True
                out.append(entry)
            else:
                out.append({"slot": slot, "value": value, "source_span": value})
        return out

    def generate_response(
        self, task, frame, user_text, extracted_slots, attempts_exceeded, ctx=None, rejected=None
    ):
        parts = [f"[ask] {frame.next_slot()}?"]
        if rejected:
            parts.append(f"rejected={sorted(rejected)}")
        if attempts_exceeded:
            parts.append(f"rephrased={sorted(attempts_exceeded)}")
        return " ".join(parts)

    def get_confirmation_question(self, task, frame, ctx=None):
        return f"[confirm] {frame.to_dict()}"

    def classify_intent(self, user_text, frame, ctx=None):
        text = user_text.lower()
        pairs = _pairs(user_text)
        if pairs:
            return "corrects", {slot: value for _, slot, value in pairs if slot in frame.all_keys()}
        if "?" in text:
            return "question", {}
        if re.search(r"\b(yes|sí)\b", text):
            return "confirms", {}
        return "unclear", {}

    def answer_confirmation_question(self, user_text, task, frame, ctx=None):
        return "[answer]"

    def response_to_intent(self, task, intent, corrections, frame, ctx=None):
        return f"[{intent}]"

    # ==== EXPLANATION ====

    def generate_explanation(self, task, ctx=None):
        return "[explanation]"

    def generate_explanation_direct(self, task, ctx=None):
        return "[explanation_direct]"

    def extract_key_points(self, task, resources):
        return [{"point": "the main point", "critical": True}]

    def judge_teach_back(self, user_text, key_points, asked, ctx=None):
        text = user_text.lower()
        if "restate all" in text:
            return {"kind": "restates",
                    "points": [(i, "correct", "") for i in range(len(key_points))]}
        restated = re.findall(r"\b(right|wrong) (\d+)", text)
        if restated:
            return {"kind": "restates", "points": [
                (int(n) - 1, "correct" if what == "right" else "wrong", "x")
                for what, n in restated if 0 < int(n) <= len(key_points)]}
        if "?" in text:
            return {"kind": "question", "points": []}
        if "understood" in text or "got it" in text:
            return {"kind": "declines", "points": []}
        if "do not understand" in text or "again" in text:
            return {"kind": "not_understood", "points": []}
        return {"kind": "other", "points": []}

    def repair_explanation(self, task, points, correct, ctx=None):
        return "[repair " + ",".join(p["point"] for p in points) + "]"

    def targeted_question(self, point, ctx=None):
        return f"[targeted] {point}?"

    def ask_missing_points(self, task, correct, missing, ctx=None):
        return "[ask_missing " + ",".join(missing) + "]"

    def answer_explanation_question(self, user_text, task, history, ctx=None):
        return "[answer]"

    def rephrase_explanation(self, task, history, attempt_number, ctx=None):
        return f"[rephrase {attempt_number}]"

    def understanding_confirmed_response(self, task, ctx=None):
        return "[understood]"

    # ==== QUIZ ====

    def quiz_opening(self, task, total):
        return f"[quiz {total}]"

    def evaluate_quiz_answer(self, question, expected, given):
        if re.search(r"\b(repeat|pardon)\b", given.lower()):
            return {"kind": "repeat", "correct": False, "feedback": ""}
        correct = expected.lower().strip() in given.lower()
        return {"kind": "answer", "correct": correct,
                "feedback": "[right]" if correct else "[wrong]"}

    def quiz_rephrase_question(self, question, expected):
        return f"[rephrased] {question}"

    def quiz_next_question(self, feedback, n, total, question):
        return f"{feedback} [question {n}/{total}] {question}"

    def quiz_summary(self, task, correct, total, wrongs):
        return f"[score {correct}/{total}]"

    # ==== PROCEDURE ====

    def procedure_opening(self, task, total, ctx=None):
        return f"[procedure {total}]"

    def procedure_step(self, step, n, total, situation, ctx=None):
        return f"[{situation} {n}/{total}] {step}"

    def classify_step_turn(self, user_text, step, check, history, ctx=None):
        text = user_text.lower()
        for intent in ("done", "skip", "problem", "repeat", "back"):
            if re.search(rf"\b{intent}\b", text):
                return intent
        return "question" if "?" in text else "unclear"

    def procedure_answer_question(self, user_text, task, step, history, ctx=None):
        return "[answer]"

    def procedure_help_step(self, task, step, history, attempt_number, ctx=None):
        return f"[help {attempt_number}]"

    def procedure_remind(self, step, ctx=None):
        return "[remind]"

    def procedure_summary(self, task, completed, total, not_done, ctx=None):
        return f"[summary {completed}/{total}]"

    # ==== GROUNDED QUESTION ANSWERING ====

    def qa_opening(self, task, ctx=None):
        return "[qa_open]"

    def answer_from_material(self, user_text, task, material, history, ctx=None):
        # As the client returns it: what the person does (act) first; finished is "done" and
        # reacts is "comment"
        text = user_text.lower()
        if "no more" in text or "bye" in text:
            return {"act": "finished", "kind": "done", "answer": "", "evidence": []}
        if "shame" in text:
            return {"act": "reacts", "kind": "comment", "answer": "", "evidence": []}
        if "repeat" in text:
            return {"act": "asks", "kind": "repeat", "answer": "", "evidence": []}
        if "off topic" in text:
            return {"act": "asks", "kind": "off_task", "answer": "", "evidence": []}
        asked = re.search(r"\bask (\w+)", text)
        if not asked:
            return {"act": "asks", "kind": "other", "answer": "", "evidence": []}
        word = asked.group(1)
        known = " ".join(r["content"].lower() for r in material)
        if word in known or word == "unsupported":
            return {"act": "asks", "kind": "answerable", "answer": f"[{word}]",
                    "evidence": [word]}
        return {"act": "asks", "kind": "not_in_material", "answer": "", "evidence": []}

    def verify_answer(self, answer, material, ctx=None):
        return "unsupported" not in answer

    # ==== QUESTIONNAIRE ====

    def map_scale_answer(self, user_text, item, options, ctx=None):
        text = user_text.lower()
        chosen = re.search(r"\boption (\d+)", text)
        if chosen and 0 < int(chosen.group(1)) <= len(options):
            return {"kind": "option", "option": int(chosen.group(1)) - 1}
        for word, kind in (("between", "ambiguous"), ("meaning", "meaning"),
                           ("pass", "refuses"), ("repeat", "repeat")):
            if word in text:
                return {"kind": kind, "option": -1}
        return {"kind": "other", "option": -1}

    # ==== ROLEPLAY ====

    def roleplay_intro(self, task, scene, ctx=None):
        return "[intro]"

    def roleplay_reply(self, scene, history, ctx=None):
        lines = sum(1 for t in history if t["role"] == "assistant")
        return f"[character {lines + 1}]"

    def classify_roleplay_turn(self, user_text, scene, history, ctx=None):
        text = user_text.lower()
        if "stop practice" in text:
            return "end"
        if "the end" in text:
            return "scene_end"
        if "help me" in text:
            return "coach"
        if "repeat" in text:
            return "repeat"
        return "continue"

    def roleplay_coach(self, user_text, scene, history, ctx=None):
        return "[hint]"

    def evaluate_roleplay(self, scene, history, ctx=None):
        return {
            "scores": [
                {"criterion": c, "score": 3, "evidence": "", "comment": ""}
                for c in scene["criteria"]
            ]
        }

    def roleplay_feedback(self, scene, evaluation, ended_by, ctx=None):
        return {"text": f"[feedback {ended_by}]", "highlight": "h", "improve": "i"}

    # ==== CANCEL ====

    def verify_finished(self, user_text, robot=""):
        return True  # the scripted closing is always a closing

    def detect_cancel_intent(self, user_text, ctx=None, strict=False, task=""):
        _slow(user_text)
        if local_cancel_check(user_text, self.language):
            return True
        # Strict mode (a closed-choice question): only an explicit request, not a cancel-like word
        text = user_text.lower()
        return "forget it" in text or (not strict and "cancel" in text)

    def classify_cancel_reply(self, user_text, ctx=None):
        _slow(user_text)
        text = user_text.lower()
        if "no more" in text:
            return "finished"  # nothing more to ask or add: not abandoning
        if re.search(r"\b(yes|sí)\b", text):
            return "yes"
        if re.search(r"\bno\b", text):
            return "no"
        return "other"

    def ask_cancel_confirmation(self, task, ctx=None):
        return "[cancel?]"

    def cancel_confirmed_response(self, task, frame=None, ctx=None):
        return "[cancelled]"

    # ==== LLM-LED (the monolithic ablation) ====

    _USER_SAID = re.compile(r'THE USER JUST SAID\n"(.*)"$', re.M)
    _END = re.compile(r"\bend (success|failed|cancelled)\b")

    def _call_nlu(self, prompt, schema, max_tokens):
        said = self._USER_SAID.search(prompt)
        if said is None:
            direct = "without greeting" in prompt
            return json.dumps({"reply": "[mono_open_direct]" if direct else "[mono_open]",
                               "done": False, "outcome": "", "result": {}})
        text = said.group(1)
        if "garble" in text:
            return "no JSON here"
        end = self._END.search(text.lower())
        if end:
            data = {slot: value for _, slot, value in _pairs(text)}
            return json.dumps({"reply": f"[mono_end] {end.group(1)}", "done": True,
                               "outcome": end.group(1), "result": {"data": data}})
        return json.dumps({"reply": f"[mono] {text}", "done": False, "outcome": "",
                           "result": {}})


class LocalizedScriptedLLM(ScriptedLLM):
    """The scripted LLM with the fixed phrases of the dialogue's language file
    (config/languages/<language>.yaml) instead of their tags: "[close_breakdown]" becomes the
    closing line the robot really says, in English or Spanish."""

    def phrase(self, key):
        from interaction_intelligence.languages import load_language

        return load_language(self.language).phrases[key]
