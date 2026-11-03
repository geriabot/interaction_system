# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The questions of the interaction system for the interpretation server: every question of the
client is declared, its context carries the arguments of PromptBuilder without loss, and the
server, with the system's questions file, templates and question module, writes the prompt and
the schema that PromptBuilder writes in the client."""

import json
import os
import time

import pytest
import yaml

from interaction_intelligence.dialog_context import DialogContext, Resource, UserState
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.interpretation_context import (
    CLIENT_NAMES, QUESTIONS, ContextError, InterpretationPromptBuilder, answer_text, ask_as,
    asked_question, build_prompt, decode_context, encode_context, interpret_request,
    max_tokens_of, prompt_templates)
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")
_PROMPTS = os.path.join(_CONFIG, "prompts.yaml")
_QUESTIONS = os.path.join(_CONFIG, "interpretation", "questions.yaml")


def templates() -> dict:
    with open(_PROMPTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def definitions() -> dict:
    with open(_QUESTIONS, encoding="utf-8") as f:
        return yaml.safe_load(f)["questions"]


def frame() -> DialogFrame:
    f = DialogFrame.from_slots_json(
        '{"slots": [{"name": "drink", "type": "str", "canonical_values": ["latte", "té"]},'
        ' {"name": "milk", "type": "str", "condition_slot": "drink", "condition_value": "latte"},'
        ' {"name": "extras", "type": "list_str"},'
        ' {"name": "age", "type": "int", "minimum": 18}]}')
    f.add_turn("assistant", "¿Qué quieres tomar?")
    f.add_turn("user", "Un latte con canela")
    f.try_update({"drink": "latte", "extras": ["canela"]})
    f.increment_attempts("milk")
    return f


def context(notable: bool = True) -> DialogContext:
    ctx = DialogContext(resources=[Resource("menu", "Today", "Latte 2,50 €")], domain="café")
    ctx.update_user_state(UserState("frustrated", 0.9 if notable else 0.1, "vision",
                                    time.monotonic()))
    return ctx


def test_every_question_of_the_client_is_declared():
    client = {CLIENT_NAMES.get(q, q) for q in QUESTIONS}
    routable = {next((q for q, c in CLIENT_NAMES.items() if c == name), name)
                for name in LLMDialogClient.questions()}
    assert routable == set(QUESTIONS)
    assert client == set(LLMDialogClient.questions())
    assert set(definitions()) == set(QUESTIONS)


def test_the_definitions_use_the_module_and_the_templates():
    prompts = templates()
    for name, spec in definitions().items():
        method, template, kind = QUESTIONS[name]
        assert spec["class"] == "DialogueQuestion"
        assert spec["template"] == template and template in prompts, name
        assert spec["max_tokens"] > 0
        assert (spec.get("validate") == "object") == (kind == "json"), name
        assert (spec.get("parse") == "text") == (kind == "text"), name
        assert spec.get("allow_empty", False) == (kind == "text"), name
        reasoning = "reasoning" in (spec.get("variants") or {})
        assert reasoning == (name in LLMDialogClient.REASONING_QUESTIONS), name
        assert hasattr(PromptBuilder, method), name


def test_judge_teach_back_has_room_for_every_key_point():
    assert max_tokens_of("judge_teach_back", {"key_points": [1, 2, 3]}, 120) == 300
    assert max_tokens_of("verify_answer", {}, 200) == 200


def test_the_context_carries_frames_contexts_and_tuples():
    f = frame()
    sent = encode_context("generate_response", "Spanish", {
        "task": "pedir", "frame": f, "user_text": "sí", "extracted_slots": [],
        "attempts_exceeded": ("milk",), "ctx": context(), "rejected": {"age": 7}})
    decoded = decode_context(json.loads(sent))
    args = decoded["args"]
    assert decoded["language"] == "Spanish"
    assert args["frame"].to_state() == f.to_state()
    assert args["attempts_exceeded"] == ("milk",)
    assert args["ctx"].to_prompt_block() == context().to_prompt_block()
    assert "frustrated" in args["ctx"].to_prompt_block()


def test_a_state_of_the_user_that_is_not_notable_is_not_sent():
    sent = json.loads(encode_context("qa_opening", "English",
                                     {"task": "t", "ctx": context(notable=False)}))
    assert sent["args"]["ctx"]["__context__"]["user_state"] is None


def test_a_value_that_json_cannot_carry_is_refused():
    with pytest.raises(ContextError):
        encode_context("qa_opening", "English", {"task": object()})
    with pytest.raises(ContextError):
        encode_context("not_a_question", "English", {})


@pytest.mark.parametrize("language", ["English", "Spanish"])
def test_the_prompt_is_the_one_of_prompt_builder(language):
    pb = PromptBuilder(templates(), language=language)
    calls = {
        "extract_turn": {"user_text": "oat milk", "frame": frame(), "ctx": context()},
        "generate_response": {"task": "order", "frame": frame(), "user_text": "x",
                              "extracted_slots": [{"slot": "drink", "value": "latte"}],
                              "attempts_exceeded": ["milk"], "ctx": context(),
                              "rejected": {"age": 3}},
        "roleplay_reply": {"scene": {"scenario": "s", "role": "r"},
                           "history": [{"role": "assistant", "content": "Hi?"}],
                           "ctx": None, "avoid": "Hi?"},
    }
    for question, args in calls.items():
        method = QUESTIONS[question][0]
        expected = getattr(pb, method)(**args)
        decoded = decode_context(json.loads(encode_context(question, language, args)))
        prompt, schema = build_prompt(question, templates(), decoded)
        if QUESTIONS[question][2] == "json":
            assert (prompt, schema) == expected
        else:
            assert prompt == expected and schema is None


def test_the_templates_are_found_in_the_parameters_file():
    from_file = {"interaction_intelligence.ros__parameters.prompts.opening": "a",
                 "opening": "a", "interaction_intelligence.ros__parameters.language": "x"}
    assert prompt_templates(from_file) == {"opening": "a"}
    assert prompt_templates({"opening": "b", "x.y": "c"}) == {"opening": "b"}


def test_the_server_asks_with_the_prompt_of_prompt_builder(tmp_path):
    server_module = pytest.importorskip("behavior_interpretation")
    route = {"backend": "fake", "answers": ['{"intent": "confirms"}']}
    server = server_module.InterpretationServer(
        questions_file=_QUESTIONS, templates_file=_PROMPTS,
        question_modules=["interaction_intelligence.interpretation_questions"],
        routes={"dialogue": route, "reasoning": route}, default_route="dialogue",
        log_dir=str(tmp_path))
    f, ctx = frame(), context()
    for question, args, variant in (
            ("classify_intent", {"user_text": "yes", "frame": f, "ctx": ctx}, "reasoning"),
            ("get_opening", {"task": "order", "frame": f, "ctx": ctx}, ""),
            ("judge_teach_back", {"user_text": "x", "key_points": [{"point": "a"}] * 2,
                                  "asked": "?", "ctx": None}, "")):
        result = server.interpret({"question": question, "variant": variant,
                                   "context": encode_context(question, "Spanish", args)})
        assert result.success, result.message
    calls = [json.loads(line) for line in open(tmp_path / "calls.jsonl", encoding="utf-8")]
    pb = PromptBuilder(templates(), language="Spanish")
    assert (calls[0]["prompt"], calls[0]["schema"]) == pb.classify_intent("yes", f, ctx)
    assert calls[0]["route"] == "reasoning" and calls[0]["source"] == "llm"
    assert calls[1]["prompt"] == pb.opening("order", f, ctx)
    assert calls[1]["schema"] is None
    assert calls[2]["prompt"] == pb.judge_teach_back("x", [{"point": "a"}] * 2, "?")[0]


def test_a_malformed_context_is_a_malformed_request():
    server_module = pytest.importorskip("behavior_interpretation")
    server = server_module.InterpretationServer(
        questions_file=_QUESTIONS, templates_file=_PROMPTS,
        question_modules=["interaction_intelligence.interpretation_questions"],
        routes={"dialogue": {"backend": "fake"}, "reasoning": {"backend": "fake"}},
        default_route="dialogue")
    result = server.interpret({"question": "get_opening",
                               "context": json.dumps({"args": {"nothing": 1}})})
    assert not result.success and "get_opening" in result.message


def test_the_client_marks_each_prompt_with_its_question_and_context():
    plain = PromptBuilder(templates(), language="Spanish")
    marked = InterpretationPromptBuilder(templates(), language="Spanish")
    f, ctx = frame(), context()
    prompt, schema = marked.extract_slots("con avena", f, ctx)
    assert (prompt, schema) == plain.extract_slots("con avena", f, ctx)
    assert prompt.cache_prefix_len == plain.extract_slots("con avena", f, ctx)[0].cache_prefix_len
    question, sent = asked_question(prompt)
    assert question == "extract_turn" and sent["language"] == "Spanish"
    assert build_prompt(question, templates(), decode_context(sent)) == (prompt, schema)
    line = marked.roleplay_reply({"scenario": "s", "role": "r"}, [], None, avoid="¿Qué?")
    assert asked_question(line)[0] == "roleplay_reply"
    assert asked_question(line)[1]["args"]["avoid"] == "¿Qué?"
    assert asked_question(plain.qa_opening("t")) is None


def test_the_request_carries_the_call_of_the_client():
    prompt = ask_as("Hi", "classify_intent", {"language": "English", "args": {}})
    fields = interpret_request(prompt, "dialogue-1", 200, True, 7.5)
    assert fields == {"question": "classify_intent", "request_id": "dialogue-1",
                      "context": '{"language": "English", "args": {}}',
                      "variant": "reasoning", "budget_s": 7.5, "max_tokens": 200}
    no_limit = interpret_request(prompt, "", 0, False, None)
    assert no_limit["budget_s"] == 0.0 and no_limit["variant"] == ""
    with pytest.raises(ContextError):
        interpret_request("a prompt without its question", "d", 10, False, 1.0)


def test_the_answer_is_the_model_text_or_nothing():
    assert answer_text(True, '{"text": " {\\"a\\": 1} "}', "llm") == ' {"a": 1} '
    assert answer_text(True, '{"text": ""}', "llm") == ""
    assert answer_text(True, "", "invalid") is None
    assert answer_text(False, "", "") is None
