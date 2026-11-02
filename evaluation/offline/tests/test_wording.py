# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Tests of bench_wording without any LLM: the automatic checks of a spoken line, the whole
pipeline through the production client and prompt builder with fake transports, that it is
reported and never part of the safety gate, and that its situations are invented (no content of
a scenario, of a phase A case or of an example of the prompts).

    cd evaluation/offline && python3 -m pytest -q tests
"""

import json

import pytest

from benchmarks import bench_wording as bw
from core import dataset_check
from core.gate import PHASE_A_BENCHMARKS
from core.phase_a import CRITICAL_CATEGORIES
from core.path_setup import resolve_path
from run_eval import BENCHMARKS

CFG = {
    "prompts_yaml": "../../interaction_system_core/interaction_intelligence/config/prompts.yaml",
    "llm": {"provider": "fake", "model": "fake"},
    "languages": ["en", "es"],
}
DATASET = resolve_path(BENCHMARKS["wording"][1])


def cases():
    with open(DATASET, encoding="utf-8") as f:
        return json.load(f)["cases"]


def case(**kw):
    base = {"id": "x", "language": "es", "description": "d", "category": "c", "prompt": "opening",
            "asks": None, "max_words": None}
    base.update(kw)
    return base


def check(line, **kw):
    return bw.checks(case(**kw), line)["checks"]


# ==== The checks of one line ====


def test_the_live_test_defects_are_caught():
    # The three lines heard in the live Spanish voice test
    sf = {"slot_schema": {"slots": [{"name": "nombre_cliente", "type": "str"}]},
          "slot_phrases": ["nombre del cliente"], "third_person": ["el cliente"]}
    c = check("¿Qué leche quieres? Puedes decirme varias opciones si lo deseas.",
              asks=True, slot_kind="single")
    assert c["single_value"] is False and c["question_form"] is False
    c = check("¿Cuál es el nombre del cliente?", asks=True, slot_kind="single", **sf)
    assert c["no_slot_name"] is False and c["second_person"] is False
    c = check("¿Confirma que desea cancelar el pedido? Sí para confirmar.", asks=True)
    assert c["informal"] is False and c["question_form"] is False


def test_good_lines_pass_every_check():
    sf = {"slot_schema": {"slots": [{"name": "nombre_cliente", "type": "str"}]},
          "slot_phrases": ["nombre del cliente"], "third_person": ["el cliente"]}
    c = check("¿Cómo te llamas?", asks=True, slot_kind="single", max_words=20, **sf)
    assert all(v is not False for v in c.values()) and c["informal"] is True
    c = check("¿Quieres dejar el pedido aquí?", asks=True, max_words=15)
    assert c["informal"] is True and c["question_form"] is True and c["max_length"] is True
    c = check("What's your name?", language="en", asks=True, slot_kind="single")
    assert c["informal"] is None and c["single_value"] is True


@pytest.mark.parametrize("line", [
    "¿Desea algo más?", "¿Usted prefiere la mañana?", "¿Me puede decir su nombre?",
    "Dígame qué arreglos necesita.", "¿Le parece bien el martes?", "¿Seguro que quiere cancelar?",
    "¿Puede repetirlo?", "Indíqueme su elección, por favor.",
])
def test_usted_forms_are_flagged(line):
    assert bw.formal_es(line), line


@pytest.mark.parametrize("line", [
    "¿Quieres cancelar el alquiler?", "Eso puede tardar un poco.", "Eso quiere decir que sí.",
    "¿Me dices tu nombre?", "El bonsái y su maceta están listos.", "¿Te parece bien el martes?",
    "¿Puedes repetirlo?", "Elige una opción, por favor.",
])
def test_informal_and_impersonal_forms_are_not_flagged(line):
    assert not bw.formal_es(line), line


def test_closings_ask_nothing_and_lists_invite_more():
    assert check("¡Gracias, hasta pronto!", asks=False)["question_form"] is True
    assert check("Listo. ¿Algo más?", asks=False)["question_form"] is False
    assert check("¿Qué arreglos quieres? Puedes decirme varios.", asks=True,
                 slot_kind="list")["list_invitation"] is True
    assert check("¿Qué arreglos quieres?", asks=True, slot_kind="list")["list_invitation"] is False


def test_one_word_slot_names_are_ordinary_words():
    sf = {"slot_schema": {"slots": [{"name": "garment", "type": "str"},
                                    {"name": "customer_name", "type": "str"}]}}
    assert check("Which garment is it?", language="en", **sf)["no_slot_name"] is True
    assert check("What is the customer name?", language="en", **sf)["no_slot_name"] is False


def test_the_length_limit():
    assert check("uno dos tres", max_words=3)["max_length"] is True
    assert check("uno dos tres cuatro", max_words=3)["max_length"] is False
    assert check("uno dos tres cuatro")["max_length"] is None


# ==== The pipeline, through the production client and prompt builder ====


def fake(answer_es, answer_en, prompts=None):
    """A transport that answers every spoken line with a fixed line of the prompt's language."""
    def infer(session, prompt, max_tokens, schema):
        if prompts is not None:
            prompts.append(prompt)
        # The address rule names Spanish in every language: the language is the prompt's other
        # mentions
        line = answer_es if "Spanish" in prompt.replace('"tú" in Spanish', "") else answer_en
        if schema:
            return json.dumps({"kind": "answer", "correct": False, "feedback": line})
        return line
    return infer


def test_the_pipeline_reports_rates_per_check_and_never_a_critical_error():
    res = bw.run(dict(CFG), DATASET, infer_fn=fake("¿Confirma que desea seguir?",
                                                   "Shall we go on?"))
    g = res["global"]
    assert g["cases"] == len(cases()) and g["critical_total"] == 0 and g["no_answer"] == 0
    assert set(g["checks"]) == set(bw.CHECKS)
    # Every line is formal but the cancel closing: a closing with a question mark is replaced by
    # the fixed one, which is what the user hears (decided by the author, 2026-11-02)
    es = [c for c in cases() if c["language"] == "es"]
    fixed = sum(c["prompt"] == "cancel_confirmed_response" for c in es) / len(es)
    assert res["by_language"]["es"]["checks"]["informal"]["rate"] == fixed > 0
    assert res["by_language"]["en"]["checks"]["informal"]["lines"] == 0
    assert g["rate_informal"] == fixed
    # The closings asked something: question_form fails on them, not on the questions
    assert 0 < g["rate_question_form"] < 1


def test_the_lines_are_asked_through_the_real_prompts():
    prompts = []
    bw.run(dict(CFG), DATASET, infer_fn=fake("¿Cómo te llamas?", "What's your name?", prompts))
    list_rule = "The slot to ask for is a LIST"
    responses = [p for p in prompts if "Generate the NEXT assistant message" in p]
    by_kind = {c["slot_kind"] for c in cases() if c["prompt"] == "generate_response"}
    assert by_kind == {"single", "list"}
    n_list = sum(1 for c in cases() if c["prompt"] == "generate_response"
                 and c["slot_kind"] == "list")
    assert sum(1 for p in responses if list_rule in p) == n_list
    assert all("informal second person" in p for p in prompts)


def test_a_model_that_does_not_answer_is_counted_not_checked():
    res = bw.run(dict(CFG), DATASET, infer_fn=lambda *a: None)
    g = res["global"]
    assert g["critical_total"] == 0 and g["flags"]["no_answer"] == len(cases())
    assert all(v["lines"] == 0 for v in g["checks"].values())


def test_every_kind_of_line_of_the_dataset_is_asked():
    assert set(bw.ASK) == set(dataset_check.WORDING_PROMPTS)
    assert {c["prompt"] for c in cases()} == set(bw.ASK)
    for lang in ("en", "es"):
        assert {c["prompt"] for c in cases() if c["language"] == lang} == set(bw.ASK)


# ==== Reported in E0a, never a selection criterion ====


def test_wording_is_reported_not_gated():
    import run_models

    assert "wording" not in PHASE_A_BENCHMARKS
    assert not any("wording" in v for v in CRITICAL_CATEGORIES.values())
    assert "wording" in run_models.REPORTED_BENCHMARKS
    assert all(f"rate_{c}" in run_models.SUMMARY_KEYS["wording"] for c in bw.CHECKS)


# ==== The situations are invented ====


def test_the_situations_pass_their_checks():
    assert dataset_check.check_wording() == []


def test_the_checker_finds_scenario_and_phase_a_text_and_prompt_examples(tmp_path):
    good = cases()[0]
    with open(resolve_path("datasets/phase_a/turn_act.json"), encoding="utf-8") as f:
        phase_a_text = next(c["user_text"] for c in json.load(f)["cases"]
                            if len(c["user_text"].split()) >= 8)
    path = tmp_path / "wording.json"
    path.write_text(json.dumps({"_instructions": [], "question": "q", "cases": [
        dict(good, id="w1", user_text="well one two three four five six"),
        dict(good, id="w2", user_text=phase_a_text),
        dict(good, id="w3", user_text="What is your name?"),
    ]}))
    scen = dataset_check.shingles("one two three four five six seven")
    problems = dataset_check.check_wording(str(path), scen=scen)
    assert any("w1" in p and "shares text with a scenario" in p for p in problems)
    assert any("w2" in p and "phase A case" in p for p in problems)
    assert any("w3" in p and "example of the prompts" in p for p in problems)
