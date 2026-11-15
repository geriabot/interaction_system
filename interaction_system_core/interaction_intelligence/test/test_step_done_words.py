# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""In a procedure, a reply that is exactly one of the language's step_done_words ("Done.",
"Hecho.") reports the step done: it is never read as a request to stop, and the cancel detector is
not asked (DialogueMode.done_words_are_answers). Given the robot's last line ("Let me know when
you're done"), a small model read a bare "done" as a wish to stop in about one turn in ten of a
scenario, and the robot asked whether to end the conversation. A longer reply still goes to the
detector; the other strategies are unchanged."""

import json
import os

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.languages import language_files, load_language
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill
from interaction_intelligence.utils import local_step_done, normalize_text

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
STEPS = [{"name": "steps", "content": json.dumps(
    [{"step": "Touch the screen and choose English"}, {"step": "Pick your destination"}])}]
DETECT = "Decide whether the user wants to STOP or CANCEL"


def client(language="English"):
    """A real client whose model reads every utterance as a request to stop."""
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    pb = PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"],
                       language=language)
    calls = []

    def infer(session, prompt, max_tokens, schema):
        if DETECT in prompt:
            calls.append(prompt)
            return '{"about": "conversation", "request": "cancel"}'
        return "[line]"

    return LLMDialogClient("nlu", "nlg", infer, pb, language=language), calls


def begin(llm, mode="procedure"):
    env = SkillEnv(llm=llm, ctx=DialogContext())
    state = {}
    get_skill("begin")(env, state, SkillInput(args={
        "dialog_mode": mode, "task_description": "Buy a ticket", "resources_json": json.dumps(STEPS)}))
    return env, state


@pytest.mark.parametrize("language,said", [
    ("English", "done"), ("English", "Done."), ("English", "Ready!"), ("English", "All done."),
    ("Spanish", "Hecho."), ("Spanish", "Ya está."), ("Spanish", "listo"),
])
def test_a_bare_done_in_a_procedure_is_never_a_request_to_stop(language, said):
    llm, calls = client(language)
    env, state = begin(llm)
    result = get_skill("cancel_check")(env, state, SkillInput(
        user_text=said, option="Touch the screen and choose English. Let me know when you're done."))
    assert result.label == "none"
    assert not state.get("awaiting_cancel")
    assert not calls  # decided without the model


@pytest.mark.parametrize("said", ["I'm done, I want to stop.", "Done. Now stop, please."])
def test_a_longer_reply_still_goes_to_the_detector(said):
    llm, calls = client()
    env, state = begin(llm)
    result = get_skill("cancel_check")(env, state, SkillInput(user_text=said, option="Step 1"))
    assert result.label == "ask" and len(calls) == 1


def test_only_the_procedure_takes_the_done_words_as_answers():
    takes = {name: get_mode(name).done_words_are_answers
             for name in ("procedure", "quiz", "questionnaire", "slot_filling", "grounded_qa",
                          "explanation", "roleplay")}
    assert takes == {"procedure": True, "quiz": False, "questionnaire": False,
                     "slot_filling": False, "grounded_qa": False, "explanation": False,
                     "roleplay": False}


def test_local_step_done_takes_only_whole_replies():
    assert local_step_done("Done!") and local_step_done("hecho", "Spanish")
    assert not local_step_done("Done, but I want to stop")
    assert not local_step_done("I'm done")
    assert not local_step_done("")


def test_no_step_done_word_is_a_stop_command_or_a_no():
    for name in language_files():
        lang = load_language(name)
        no = {normalize_text(w) for w in lang.no_words}
        assert not lang.step_done_words & lang.cancel_commands, name
        assert not lang.step_done_words & no, name
