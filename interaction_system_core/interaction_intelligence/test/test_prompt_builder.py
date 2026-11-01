# Copyright 2026 Álvaro Quintana Camacho
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""PromptBuilder cache prefix: stable instructions first, per-turn data last."""

import os

import yaml

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.prompt_builder import PromptBuilder

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def production_builder() -> PromptBuilder:
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    return PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"])


def frame() -> DialogFrame:
    return DialogFrame.from_slots_json('{"slots": [{"name": "drink", "type": "str"}]}')


def test_prefix_ends_at_paragraph_before_first_data_placeholder():
    pb = PromptBuilder({"t": "Rules in {language}.\n\nMore rules.\n\nUSER: {user_text}\n"})
    p = pb._t("t", user_text="hi")
    assert p[: p.cache_prefix_len] == "Rules in English.\n\nMore rules.\n\n"


def test_template_opening_with_data_has_no_prefix():
    pb = PromptBuilder({"t": 'Task: "{task}"\n\nRules.'})
    assert pb._t("t", task="x").cache_prefix_len == 0


def test_prefix_is_identical_across_turns():
    pb = production_builder()
    f = frame()
    a, _ = pb.extract_slots("a coffee", f)
    b, _ = pb.extract_slots("make it a tea instead", f)
    assert a.cache_prefix_len > 1000
    assert a[: a.cache_prefix_len] == b[: b.cache_prefix_len]
    assert "a coffee" not in a[: a.cache_prefix_len]


def test_context_block_joins_the_cached_prefix():
    pb = production_builder()
    ctx = DialogContext(domain="coffee shop")
    p = pb.generate_response("order a drink", frame(), "hi", [], [], ctx)
    head = p[: p.cache_prefix_len]
    assert head.rstrip().endswith("[/CONTEXT]")
    assert "order a drink" not in head
    assert p[p.cache_prefix_len :].lstrip().startswith("CURRENT TURN")


def test_context_still_leads_templates_without_prefix():
    pb = PromptBuilder({"t": 'Task: "{task}"'})
    p = pb._inject(pb._t("t", task="x"), DialogContext(domain="clinic"))
    assert p.startswith("[CONTEXT]") and p.cache_prefix_len == 0


def test_extraction_lists_the_conditional_slots_that_do_not_apply_yet():
    # The user may give a conditional slot with its parent in one turn ("a latte with oat milk")
    f = DialogFrame.from_slots_json(
        '{"slots": [{"name": "drink", "type": "str", "canonical_values": ["latte", "tea"]},'
        ' {"name": "milk", "type": "str", "canonical_values": ["oat", "soy"],'
        '  "condition_slot": "drink", "condition_value": "latte"}]}')
    prompt, _ = production_builder().extract_slots("A latte with oat milk", f)
    line = next(ln for ln in prompt.splitlines() if ln.strip().startswith("- milk"))
    assert "ONLY WHEN drink is 'latte'" in line and "'oat'" in line


def test_the_opening_asks_the_slot_the_frame_asks_next():
    # The code chooses the slot of the first question, as in every later turn: the reply is
    # read against that slot (a skip leaves it, not another one, unanswered)
    f = DialogFrame.from_slots_json(
        '{"slots": [{"name": "problem", "type": "str"}, {"name": "contact_name", "type": "str"}]}')
    pb = production_builder()
    for prompt in (pb.opening("Book a plumber", f), pb.opening_direct("Book a plumber", f)):
        assert "SLOT TO ASK NOW: problem" in prompt
