# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The interpretation server's parameters written from the system's parameter files: the routes
send the request the former gateway sent for the model binding, the routed questions keep their
reasoning variant, the LLM-led baseline adds its question, the prompts of every parameter file
are templates, and a test double needs no server."""

import os

import yaml

from interaction_intelligence.interpretation_config import server_parameters

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")
_QUESTIONS = os.path.join(_CONFIG, "interpretation", "questions.yaml")
_PROMPTS = os.path.join(_CONFIG, "prompts.yaml")
_APP = os.path.join(os.path.dirname(__file__), "..", "..", "interaction_system", "config",
                    "app_params.yaml")
_ABLATION = os.path.join(os.path.dirname(__file__), "..", "..", "..", "evaluation", "ablations",
                         "interaction_ablations", "config", "interpretation", "questions.yaml")


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data))
    return str(path)


def params(tmp_path, *overrides):
    files = [_APP] + [write(tmp_path, f"p{i}.yaml", o) for i, o in enumerate(overrides)]
    return server_parameters(files, _QUESTIONS, _PROMPTS, _ABLATION, out_dir=str(tmp_path))


def routes(parameters):
    out = {}
    for key, value in parameters.items():
        if key.startswith("routes."):
            _, route, option = key.split(".", 2)
            out.setdefault(route, {})[option] = value
    return out


def test_the_default_binding_is_the_gateway_ollama_request(tmp_path):
    parameters, warnings = params(tmp_path)
    assert warnings == []
    r = routes(parameters)
    assert r["dialogue"] == {"backend": "ollama", "dialect": "llm_manager",
                             "url": "http://127.0.0.1:11434", "model": "qwen2.5:7b",
                             "timeout_s": 120.0, "keep_alive": -1, "num_ctx": 8192,
                             "think": False}
    assert r["reasoning"] == dict(r["dialogue"], think=True)
    assert parameters["questions_file"] == _QUESTIONS
    assert parameters["templates_file"] == _PROMPTS
    assert parameters["question_modules"] == ["interaction_intelligence.interpretation_questions"]
    assert parameters["default_route"] == "dialogue"


def test_an_experiment_binding_as_run_experiment_writes_it(tmp_path):
    gateway = {"llm_manager_node": {"ros__parameters": {
        "default_provider": "ollama",
        "ollama": {"enable": True, "host": "10.0.0.2", "port": 11500, "model": "qwen3:8b",
                   "timeout": 90.0, "think": "low", "num_ctx": 16384}}}}
    r = routes(params(tmp_path, gateway)[0])
    assert r["dialogue"]["url"] == "http://10.0.0.2:11500"
    assert r["dialogue"]["model"] == "qwen3:8b" and r["dialogue"]["think"] == "low"
    assert r["dialogue"]["num_ctx"] == 16384 and r["reasoning"]["think"] is True


def test_a_cloud_binding(tmp_path):
    gateway = {"llm_manager_node": {"ros__parameters": {
        "default_provider": "openai", "ollama": {"enable": False},
        "openai": {"enable": True, "model": "gpt-x", "timeout": 30.0, "api_key": "sk-1"}}}}
    parameters, warnings = params(tmp_path, gateway)
    r = routes(parameters)
    assert r["dialogue"] == {"backend": "openai", "dialect": "llm_manager", "model": "gpt-x",
                             "timeout_s": 30.0, "reasoning_effort": "none"}
    from interaction_intelligence.interpretation_config import server_environment
    files = [_APP, write(tmp_path, "cloud.yaml", gateway)]
    assert server_environment(files) == {"OPENAI_API_KEY": "sk-1"}
    assert server_environment([_APP]) == {}
    claude = {"llm_manager_node": {"ros__parameters": {"default_provider": "claude"}}}
    assert routes(params(tmp_path, claude)[0])["dialogue"]["backend"] == "anthropic"


def test_the_intelligence_model_and_its_routes(tmp_path):
    intelligence = {"interaction_intelligence": {"ros__parameters": {
        "llm_model": "big:14b",
        "routes": ["classify_intent=other:8b", "extract_slots=other:8b", "_roleplay_line=x:1b",
                   ""]}}}
    parameters, _ = params(tmp_path, intelligence)
    r = routes(parameters)
    assert r["dialogue"]["model"] == "big:14b"
    assert r["model_1"]["model"] == "other:8b" and r["model_2"]["model"] == "x:1b"
    assert r["model_1_reasoning"]["think"] is True
    with open(parameters["questions_file"], encoding="utf-8") as f:
        questions = yaml.safe_load(f)["questions"]
    assert questions["classify_intent"]["route"] == "model_1"
    assert questions["classify_intent"]["variants"]["reasoning"]["route"] == "model_1_reasoning"
    assert questions["extract_turn"]["route"] == "model_1"
    assert questions["roleplay_reply"]["route"] == "model_2"
    assert "route" not in questions["get_opening"]


def test_the_llm_led_baseline_adds_its_question(tmp_path):
    intelligence = {"interaction_intelligence": {"ros__parameters": {
        "skill_modules": ["interaction_ablations.skills"]}}}
    parameters, _ = params(tmp_path, intelligence)
    assert parameters["question_modules"][-1] == "interaction_ablations.interpretation_questions"
    with open(parameters["questions_file"], encoding="utf-8") as f:
        assert "monolithic_turn" in yaml.safe_load(f)["questions"]


def test_the_prompts_of_the_parameter_files_are_templates(tmp_path):
    editor = {"interaction_intelligence": {"ros__parameters": {"prompts": {"opening": "Hi"}}}}
    parameters, _ = params(tmp_path, editor)
    assert parameters["templates_files"] == [str(tmp_path / "p0.yaml")]
    assert params(tmp_path)[0]["templates_files"] == [""]


def test_a_test_double_or_no_provider_needs_no_server(tmp_path):
    double = {"interaction_intelligence": {"ros__parameters": {
        "llm_client_factory": "interaction_sim.scripted_llm:make"}}}
    assert params(tmp_path, double)[0] is None
    files = [write(tmp_path, "only.yaml", {"interaction_intelligence": {"ros__parameters": {}}})]
    parameters, warnings = server_parameters(files, _QUESTIONS, _PROMPTS)
    assert parameters is None and "no provider" in warnings[0]


def test_the_model_loaded_at_the_start_is_the_gateway_one(tmp_path):
    from interaction_intelligence.interpretation_config import warmup_of
    files = [_APP]
    assert warmup_of(files) == {"url": "http://127.0.0.1:11434", "model": "qwen2.5:7b",
                                "num_ctx": 8192}
    off = write(tmp_path, "off.yaml", {"llm_manager_node": {"ros__parameters": {
        "ollama": {"enable": False}}}})
    assert warmup_of(files + [off]) is None
