# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The deterministic parts of the wording of the spoken lines: the code, not the model, decides
when a question invites several values (only for a list slot), every prompt that writes a spoken
line carries the same rule on how to address the user (directly, in the informal second person),
and the cancellation question carries no instruction on how to answer it."""

import os

import pytest
import yaml

from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.prompt_builder import ADDRESS_RULE, LIST_SLOT_RULE, PromptBuilder

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")

# Every template that writes a line the user hears (or a field of it: the quiz feedback, the
# grounded answer, the parts of the role play feedback)
SPOKEN = {
    "opening", "resume_opening", "opening_direct", "generate_response", "ask_confirmation",
    "response_to_intent", "answer_confirmation_question",
    "quiz_opening", "evaluate_quiz_answer", "quiz_next_question", "quiz_rephrase_question",
    "quiz_summary",
    "explanation_opening", "explanation_opening_direct", "repair_explanation",
    "targeted_question", "ask_missing_points", "answer_explanation_question",
    "rephrase_explanation",
    "understanding_confirmed",
    "qa_opening", "answer_from_material",
    "procedure_opening", "procedure_step", "procedure_answer_question", "procedure_help_step",
    "procedure_remind", "procedure_summary",
    "roleplay_intro", "roleplay_reply", "roleplay_coach", "roleplay_feedback",
    "ask_cancel_confirmation", "cancel_confirmed_response",
}


def templates() -> dict:
    with open(_PROMPTS) as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def builder(language: str = "English") -> PromptBuilder:
    return PromptBuilder(templates(), language=language)


def frame(first_type: str) -> DialogFrame:
    """A form whose first slot (the one asked next) is of the given type."""
    return DialogFrame.from_slots_json(
        '{"slots": [{"name": "first", "type": "%s"}, {"name": "second", "type": "str"}]}'
        % first_type)


SCENE = {"scenario": "A kayak rental desk by a lake", "role": "the desk clerk"}
MATERIAL = [{"name": "hours", "content": "The desk opens at nine."}]
HISTORY = [{"role": "assistant", "content": "Hi."}, {"role": "user", "content": "Hello."}]
EVALUATION = {"scores": [{"criterion": "asks a question", "score": 4, "comment": "Good."}]}


def rendered(pb: PromptBuilder) -> dict:
    """Every spoken-line prompt, rendered through the builder as the client asks it."""
    f = frame("str")
    return {
        "opening": pb.opening("rent a kayak", f),
        "resume_opening": pb.resume_opening("rent a kayak", f, "first"),
        "opening_direct": pb.opening_direct("rent a kayak", f),
        "generate_response": pb.generate_response("rent a kayak", f, "hi", [], []),
        "ask_confirmation": pb.ask_confirmation("rent a kayak", f),
        "response_to_intent": pb.response_to_intent("rent a kayak", "confirms", {}, f),
        "answer_confirmation_question": pb.answer_confirmation_question("why?", "rent", f),
        "quiz_opening": pb.quiz_opening("lakes", 3),
        "evaluate_quiz_answer": pb.evaluate_quiz_answer("Deepest lake?", "Baikal", "Ness")[0],
        "quiz_next_question": pb.quiz_next_question("Right!", 2, 3, "Largest lake?"),
        "quiz_rephrase_question": pb.quiz_rephrase_question("Deepest lake?", "Baikal"),
        "quiz_summary": pb.quiz_summary("lakes", 2, 3, "Largest lake?"),
        "explanation_opening": pb.explanation_opening("paddling"),
        "explanation_opening_direct": pb.explanation_opening_direct("paddling"),
        "repair_explanation": pb.repair_explanation("paddling", [{"point": "Wear a vest"}], []),
        "targeted_question": pb.targeted_question("Wear a vest"),
        "ask_missing_points": pb.ask_missing_points("paddling", ["Wear a vest"], ["Stay close"]),
        "answer_explanation_question": pb.answer_explanation_question("why?", "paddling", []),
        "rephrase_explanation": pb.rephrase_explanation("paddling", [], 2),
        "understanding_confirmed": pb.understanding_confirmed("paddling"),
        "qa_opening": pb.qa_opening("the rental desk"),
        "answer_from_material": pb.answer_from_material("when?", "desk", MATERIAL, HISTORY)[0],
        "procedure_opening": pb.procedure_opening("launch a kayak", 3),
        "procedure_step": pb.procedure_step("Hold the paddle", 1, 3, "first"),
        "procedure_answer_question": pb.procedure_answer_question("how?", "launch", "Hold", []),
        "procedure_help_step": pb.procedure_help_step("launch", "Hold the paddle", [], 2),
        "procedure_remind": pb.procedure_remind("Hold the paddle"),
        "procedure_summary": pb.procedure_summary("launch", 3, 3, ""),
        "roleplay_intro": pb.roleplay_intro("practise renting", SCENE),
        "roleplay_reply": pb.roleplay_reply(SCENE, HISTORY),
        "roleplay_coach": pb.roleplay_coach("help me", SCENE, HISTORY + HISTORY),
        "roleplay_feedback": pb.roleplay_feedback(SCENE, EVALUATION, "user")[0],
        "ask_cancel_confirmation": pb.ask_cancel_confirmation("rent a kayak"),
        "cancel_confirmed_response": pb.cancel_confirmed_response("rent a kayak"),
    }


def one_line(text: str) -> str:
    return " ".join(text.split())


# ==== The list instruction: only when the slot asked for is a list ====


def test_the_list_instruction_is_present_only_when_the_next_slot_is_a_list():
    pb = builder()
    rule = one_line(templates()[LIST_SLOT_RULE])
    assert rule
    with_list = pb.generate_response("order", frame("list_str"), "hi", [], [])
    assert one_line(with_list).count(rule) == 1
    for slot_type in ("str", "int", "float", "bool"):
        single = pb.generate_response("order", frame(slot_type), "hi", [], [])
        assert rule not in one_line(single), slot_type
        assert "several values" not in single and "multiple values" not in single


def test_no_list_instruction_once_the_list_has_items_and_a_single_value_slot_is_asked():
    f = frame("list_str")
    f.add_to_list("first", ["a"])
    assert f.next_slot() == "second"
    p = builder().generate_response("order", f, "a", [], [])
    assert one_line(templates()[LIST_SLOT_RULE]) not in one_line(p)


def test_the_generate_response_template_has_no_always_on_list_rule():
    tpl = templates()["generate_response"]
    assert "{list_slot_rule}" in tpl
    assert "multiple values" not in tpl and "add more items" not in tpl


def test_the_list_instruction_does_not_break_the_cached_prefix():
    # The rule goes with the per-turn data: the instructions are the same for both turns
    pb = builder()
    a = pb.generate_response("order", frame("list_str"), "hi", [], [])
    b = pb.generate_response("order", frame("str"), "hi", [], [])
    assert a.cache_prefix_len > 0
    assert a[: a.cache_prefix_len] == b[: b.cache_prefix_len]


# ==== How the user is addressed: the same rule in every spoken-line prompt ====


def test_the_address_rule_is_informal_second_person_and_language_agnostic():
    rule = builder().address
    assert rule and "second person" in rule and "informal" in rule and "familiar" in rule
    assert "third person" in rule
    # A name the user gives is theirs: without it a read-back made it a third party's
    assert "their own name" in rule
    # The same rule in every language: the register is named, never translated
    assert builder("Spanish").address == rule


def test_the_address_rule_gives_no_phrase_or_word_to_copy():
    # Models copied its old examples literally: a name question in tasks that ask for no
    # name, and a Spanish pronoun in English lines. It describes, it does not quote
    rule = builder().address
    assert '"' not in rule and "'" not in rule
    assert rule.isascii()


def test_every_spoken_line_template_carries_the_address_rule():
    tpl = templates()
    marked = {k for k, v in tpl.items() if "{address}" in v}
    assert marked == SPOKEN


@pytest.mark.parametrize("language", ["English", "Spanish"])
def test_every_spoken_line_prompt_renders_the_address_rule(language):
    pb = builder(language)
    prompts = rendered(pb)
    assert set(prompts) == SPOKEN
    for name, prompt in prompts.items():
        assert pb.address in prompt, name
        assert "{address}" not in prompt and "{list_slot_rule}" not in prompt, name


def test_the_address_rule_joins_the_cached_prefix_like_the_language():
    pb = builder()
    p = pb.generate_response("order", frame("str"), "hi", [], [])
    assert pb.address in p[: p.cache_prefix_len]


def test_the_shared_rules_are_not_prompts():
    tpl = templates()
    for key in (ADDRESS_RULE, LIST_SLOT_RULE):
        assert "{" not in tpl[key], key


def test_templates_without_the_shared_rules_still_render():
    pb = PromptBuilder({"t": "Rules in {language}.\n- {address}\n\nUSER: {user_text}\n"})
    assert pb._t("t", user_text="hi").startswith("Rules in English.\n- \n")


# ==== The cancellation question ====


def test_the_cancel_question_does_not_explain_how_to_answer_it():
    tpl = templates()["ask_cancel_confirmation"]
    assert "positive answer" not in tpl and "confirms cancellation" not in tpl
    # Still asked so that "yes" means stopping (classify_cancel_reply reads it that way)
    assert "whether they want to stop" in one_line(tpl)
    assert "whether they want to stop" in templates()["classify_cancel_reply"]


# ==== Grounded questions: a wrong check of the last answer is not a reaction ====


def test_a_check_with_another_value_is_a_request_to_repeat():
    # A check that repeats the last answer with another value means the person misunderstood:
    # answering it with only the invitation to ask more let the mistake pass
    tpl = templates()["answer_from_material"]
    assert "compare its value with the value of the last answer" in tpl
    assert 'is "asks" with kind "repeat"' in tpl
    assert "value different from the one the last answer gave" in tpl
