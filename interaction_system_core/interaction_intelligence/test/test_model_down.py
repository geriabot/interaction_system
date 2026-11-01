# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""What the system does when the model is down, and the noise filter (decided by the author,
2026-10-30):

  - the reply to "do you want to stop?" is read by the model; only when the model gives no
    usable answer, a fallback reads it: the whole utterance, normalised, exactly one of the
    language's yes_words or no_words (utils.local_yes_no); anything else is "other";
  - the client records whether its last call to the model gave a usable answer (model_status),
    which the intelligence keeps in the state of the dialogue (model_down); with the model known
    to be down, an exact stop command ends the conversation at once, without the confirmation
    question, with the fixed cancel closing;
  - in the role play, a confirmed cancellation closes the practice with the evaluation of what
    was done when the user said at least one line of the scene (outcome cancelled, the scores in
    the data); with less, the cancel closing;
  - a transcription is noise only when the whole utterance is noise phrases."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.languages import load_language
from interaction_intelligence.llm_client import CallLimits, LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill
from interaction_intelligence.utils import clean_user_input, local_yes_no

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def builder(language="English"):
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    return PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"],
                         language=language)


def client(answers, language="English", limits=None):
    """A real client whose model gives these answers in turn (None: no answer; an Exception
    instance: the call raises)."""
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(prompt)
        answer = answers[min(len(calls), len(answers)) - 1] if answers else None
        if isinstance(answer, Exception):
            raise answer
        return answer

    return LLMDialogClient("nlu", "nlg", infer, builder(language), language=language,
                           limits=limits), calls


# ==== THE FALLBACK READING OF A YES/NO ANSWER ====


@pytest.mark.parametrize("language,text,expected", [
    ("English", "Yes.", "yes"), ("English", "yes!", "yes"), ("English", "OK", "yes"),
    ("English", "Correct.", "yes"), ("English", "No.", "no"), ("English", "Nope", "no"),
    ("Spanish", "Sí.", "yes"), ("Spanish", "¡Sí!", "yes"), ("Spanish", "si", "yes"),
    ("Spanish", "Vale.", "yes"), ("Spanish", "Correcto.", "yes"), ("Spanish", "No.", "no"),
])
def test_an_exact_yes_or_no_word_of_the_language(language, text, expected):
    assert local_yes_no(text, language) == expected


@pytest.mark.parametrize("language,text", [
    ("English", "Yes, but first the size"), ("English", "I think so"), ("English", "no idea"),
    ("English", "Sí."),          # a word of another language: not of this dialogue
    ("Spanish", "Sí, pero espera"), ("Spanish", "Si quieres"), ("Spanish", "No sé"),
    ("Spanish", "Yes."), ("Spanish", ""), ("Spanish", "..."),
])
def test_anything_else_is_not_read(language, text):
    assert local_yes_no(text, language) is None


def test_the_model_decides_the_reply_whenever_it_answers():
    # The model reads "Sí." as "no" (whatever the reason): its answer stands, no word list
    c, calls = client(['{"answer": "no"}'], "Spanish")
    assert c.classify_cancel_reply("Sí.") == "no"
    c, calls = client(['{"answer": "other"}'], "English")
    assert c.classify_cancel_reply("yes") == "other"
    assert c.model_status is True


@pytest.mark.parametrize("answers", [
    [None],                                 # no answer (an error or a timeout)
    [RuntimeError("down")],                 # the call raises
    ["Sorry, I cannot help with that."],    # prose instead of the JSON asked for
    ['{"answer": "maybe"}'],                # JSON, not one of the answers
])
def test_when_the_model_fails_the_fallback_reads_the_whole_utterance(answers):
    for language, yes, no in (("English", "Yes.", "No."), ("Spanish", "Sí.", "No.")):
        c, _ = client(answers, language)
        assert c.classify_cancel_reply(yes) == "yes"
        assert c.model_status is False
        c, _ = client(answers, language)
        assert c.classify_cancel_reply(no) == "no"
        c, _ = client(answers, language)
        assert c.classify_cancel_reply("drink=tea, please") == "other"


def test_a_call_not_made_falls_back_too():
    limits = CallLimits(15.0, 0.1)  # the budget is spent: no call is made
    c, calls = client(['{"answer": "no"}'], "Spanish", limits)
    assert c.classify_cancel_reply("Vale.") == "yes"
    assert calls == [] and c.model_status is False


# ==== THE MODEL STATUS OF THE CLIENT ====


def test_the_status_of_the_last_call():
    c, _ = client([])
    assert c.model_status is None                         # no call yet
    assert c.detect_cancel_intent("Stop.") is True
    assert c.model_status is None                         # decided locally: no call
    c, _ = client(['{"cancel": false}'])
    c.detect_cancel_intent("I would rather leave it")
    assert c.model_status is True
    c, _ = client([None, '{"cancel": false}'])
    c.detect_cancel_intent("I would rather leave it")
    assert c.model_status is False
    c.detect_cancel_intent("I would rather leave it")     # a later call answers: up again
    assert c.model_status is True


def test_a_generated_line_too_short_or_missing_is_a_failure():
    c, _ = client(["", ""])
    assert c.ask_cancel_confirmation("Book a taxi") == c.phrase("cancel_ask")
    assert c.model_status is False
    c, _ = client(["Do you want to stop?"])
    c.ask_cancel_confirmation("Book a taxi")
    assert c.model_status is True


# ==== AN EXACT STOP COMMAND WITH THE MODEL DOWN ====

DRINKS = {"slots": [{"name": "drink", "type": "str", "canonical_values": ["coffee", "tea"]},
                    {"name": "size", "type": "str"}]}


def begin(llm, goal):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def slot_filling(llm):
    return begin(llm, {"dialog_mode": "slot_filling", "task_description": "Take a drink order",
                       "frame_schema_json": json.dumps(DRINKS)})


@pytest.mark.parametrize("language,command", [("English", "Stop."), ("Spanish", "Para."),
                                              ("Spanish", "¡Para, para!")])
def test_with_the_model_down_an_exact_command_ends_at_once(language, command):
    llm, calls = client(['{"cancel": false}'], language)
    env, state = slot_filling(llm)
    state["model_down"] = True
    result = get_skill("cancel_check")(env, state, SkillInput(user_text=command, option="q?"))
    assert result.label == "confirmed"
    assert result.reply == load_language(language).phrases["cancel_ok"]
    assert state["user_cancelled"] is True and not state["awaiting_cancel"]
    assert calls == []                                    # no question to the model
    assert get_skill("result")(env, state, SkillInput()).label == "cancelled"


@pytest.mark.parametrize("language,command", [("English", "Stop."), ("Spanish", "Para.")])
def test_with_the_model_up_an_exact_command_asks_for_confirmation(language, command):
    llm, _ = client(["Do you want to stop?"], language)
    env, state = slot_filling(llm)
    state["model_down"] = False
    result = get_skill("cancel_check")(env, state, SkillInput(user_text=command, option="q?"))
    assert (result.label, result.reply) == ("ask", "Do you want to stop?")
    assert state["awaiting_cancel"] is True and not state["user_cancelled"]


def test_with_the_model_down_anything_else_still_goes_to_the_model():
    llm, calls = client(['{"cancel": false}'], "Spanish")
    env, state = slot_filling(llm)
    state["model_down"] = True
    result = get_skill("cancel_check")(env, state,
                                       SkillInput(user_text="Para mí un café.", option="q?"))
    assert result.label == "none" and len(calls) == 1


# ==== THE ROLE PLAY: STOPPING AND FINISHING LEAD TO THE SAME PLACE ====

SCENE = {"scenario": "The returns desk", "role": "The shop assistant",
         "criteria": ["Explains the fault", "Asks for a refund"], "max_exchanges": 4}


class Roleplay:
    """A test double of the client for the role play (its evaluation: 3 on every criterion,
    or nothing when the model fails)."""

    language = "English"
    model_status = None

    def __init__(self, scores=True, reply="yes"):
        self.scores, self.reply = scores, reply

    def phrase(self, key):
        return f"[{key}]"

    def classify_roleplay_turn(self, user_text, scene, history, ctx=None):
        return "continue"

    def roleplay_reply(self, scene, history, ctx=None):
        return "[character]"

    def evaluate_roleplay(self, scene, history, ctx=None):
        if not self.scores:
            return {}
        return {"scores": [{"criterion": c, "score": 3, "evidence": "", "comment": ""}
                           for c in scene["criteria"]]}

    def roleplay_feedback(self, scene, evaluation, ended_by, ctx=None):
        return {"text": f"[feedback {ended_by}]", "highlight": "h", "improve": "i"}

    def classify_cancel_reply(self, user_text, ctx=None):
        return self.reply

    def detect_cancel_intent(self, user_text, ctx=None, strict=False, task=""):
        return True

    def ask_cancel_confirmation(self, task, ctx=None):
        return "[cancel?]"

    def cancel_confirmed_response(self, task, frame=None, ctx=None):
        return "[cancelled]"


def roleplay(llm, lines=0):
    env, state = begin(llm, {
        "dialog_mode": "roleplay", "task_description": "Practise a return",
        "resources_json": json.dumps([{"name": "scene", "content": json.dumps(SCENE)}])})
    for i in range(lines):
        get_skill("roleplay.classify")(env, state, SkillInput(user_text=f"line {i}"))
    return env, state


def confirm_cancel(env, state):
    ask = get_skill("cancel_check")(env, state, SkillInput(user_text="stop", option="q?"))
    assert ask.label == "ask"
    return get_skill("cancel_check")(env, state, SkillInput(user_text="yes"))


def test_a_confirmed_cancel_after_a_line_of_the_scene_gives_the_evaluation():
    env, state = roleplay(Roleplay(), lines=1)
    result = confirm_cancel(env, state)
    assert (result.label, result.reply) == ("confirmed", "[feedback user]")
    data = get_mode("roleplay").data(state)
    assert data["ended_by"] == "user" and len(data["scores"]) == 2 and data["average"] == 3
    assert get_skill("result")(env, state, SkillInput()).label == "cancelled"


def test_a_confirmed_cancel_before_any_line_is_a_plain_cancel():
    env, state = roleplay(Roleplay(), lines=0)
    result = confirm_cancel(env, state)
    assert (result.label, result.reply) == ("confirmed", "[cancelled]")
    assert get_mode("roleplay").data(state)["scores"] == []
    assert get_skill("result")(env, state, SkillInput()).label == "cancelled"


def test_a_cancel_whose_evaluation_fails_says_the_cancel_closing():
    env, state = roleplay(Roleplay(scores=False), lines=2)
    assert confirm_cancel(env, state).reply == "[cancelled]"


def test_with_the_model_down_a_command_in_the_scene_also_gives_the_evaluation():
    env, state = roleplay(Roleplay(), lines=1)
    state["model_down"] = True
    result = get_skill("cancel_check")(env, state, SkillInput(user_text="Stop!", option="q?"))
    assert (result.label, result.reply) == ("confirmed", "[feedback user]")
    env, state = roleplay(Roleplay(), lines=0)
    state["model_down"] = True
    result = get_skill("cancel_check")(env, state, SkillInput(user_text="Stop!", option="q?"))
    assert (result.label, result.reply) == ("confirmed", "[cancel_ok]")


def test_the_intro_word_to_finish_is_not_a_stop_command():
    # Either way leads to the evaluation now, but the word of the intro ends the practice
    # without the confirmation question
    from interaction_intelligence.utils import local_cancel_check
    assert local_cancel_check("Finish.", "English") is None
    assert local_cancel_check("Terminar.", "Spanish") is None
    assert '"finish"' in load_language("English").phrases["roleplay_intro"]


# ==== THE NOISE FILTER: ONLY A WHOLE UTTERANCE OF NOISE ====


@pytest.mark.parametrize("text", [
    "Eh.", "Mm.", "Hmm", "mm hmm", "Eh, eh.", "Thanks for watching!", "Thank you for watching.",
    "Gracias por ver.", "Gracias por ver el vídeo.", "Subtítulos por la comunidad de Amara.org",
    "Subtítulos realizados por la comunidad de Amara.org", "Música.", "Suscríbete",
])
def test_a_whole_utterance_of_noise_is_discarded(text):
    assert clean_user_input(text) is None


@pytest.mark.parametrize("text", [
    "Eh, para.", "Hmm, stop.", "No olvides la leche.", "La próxima semana.", "Próxima semana.",
    "Pon música.", "See you next time!", "Hasta la próxima semana.", "Mm, a latte please.",
    "Thanks, I was watching the video.",
])
def test_a_noise_phrase_inside_a_real_utterance_keeps_it_unchanged(text):
    assert clean_user_input(text) == text
