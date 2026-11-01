# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The interaction system running with the scripted LLM, for the dialogue tests.

The system (orchestrator, behaviors, intelligence) is started once per test module in its own
process group, with short timeouts, and stopped at the end: `system` with the method's trees,
`llm_led_system` with the LLM-led conversation (the monolithic ablation). Run the tests from a sourced
workspace, with a ROS_DOMAIN_ID that nothing else uses:

    ROS_DOMAIN_ID=77 python -m pytest evaluation/simulation/interaction_sim/test
"""

import os
import signal
import subprocess
import time

import pytest

from interaction_sim.harness import HarnessRunner

PARAMS = """
interaction_intelligence:
  ros__parameters:
    llm_client_factory: "interaction_sim.scripted_llm:make"
    conversation_log_path: "{log}"
    ack_phrases: ["Noted.", "Done."]
interaction_orchestrator:
  ros__parameters:
    wait_timeout: 1.5
    max_silence: 3.0
    max_stalled: 4
    max_attempts: 3
"""


def _launch(tmp_path_factory, name, params_text, config_file=None):
    """Start the interaction system (stopped when the generator ends) and yield the harness."""
    tmp = tmp_path_factory.mktemp(name)
    params = tmp / "params.yaml"
    params.write_text(params_text.format(log=tmp / "transcripts.log"))
    # The harness first: if it cannot start, no system is left running without a teardown
    runner = HarnessRunner(speech_time=lambda text: 0.0)
    # The system of the previous test module may take a few seconds to leave the graph
    end = time.monotonic() + 20.0
    while runner.node._dialog_client.server_is_ready() and time.monotonic() < end:
        time.sleep(0.5)
    if runner.node.wait_for_system(2):
        runner.close()
        pytest.exit("An interaction system is already running in this ROS_DOMAIN_ID: stop it, "
                    "or use another domain", returncode=1)
    log = open(tmp / "launch.log", "w")
    command = ["ros2", "launch", "interaction_system", "interaction_system.launch.py",
               f"params_file:={params}"]
    if config_file:
        command.append(f"config_file:={config_file}")
    proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        assert runner.node.wait_for_system(60), "The interaction system did not start"
        time.sleep(1.0)  # the intelligence's services
        yield runner
    finally:
        runner.close()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
        log.close()


@pytest.fixture(scope="module", params=["English", "Spanish"])
def system(request, tmp_path_factory):
    """The method: the dialogue behaviors' trees, once in each language of the system (the
    `language` of the intelligence: its language file). The scripted LLM's tags and the
    acknowledgments of PARAMS are the same in both; what the system says or reads itself (the
    silence prompt, the fillers, the exact stop commands) is the language's, so that every
    dialogue test checks the same branch in English and in Spanish (layer 1 of the debugging
    before E0a: evaluation/doc/DEBUGGING.md)."""
    params = PARAMS.replace("    ack_phrases:", f"    language: {request.param}\n    ack_phrases:")
    assert f"language: {request.param}" in params
    for runner in _launch(tmp_path_factory, f"interaction_system_{request.param}", params):
        runner.language = request.param
        yield runner


@pytest.fixture(scope="module")
def llm_led_system(tmp_path_factory):
    """The LLM-led conversation (the monolithic ablation, condition llm_led of E1): its trees
    and its skills, with the same scripted LLM and limits."""
    from ament_index_python.packages import get_package_share_directory
    config = os.path.join(get_package_share_directory("interaction_ablations"), "config",
                          "monolithic", "interaction_behaviors_config.yaml")
    params = PARAMS.replace(
        '    llm_client_factory: "interaction_sim.scripted_llm:make"\n',
        '    llm_client_factory: "interaction_sim.scripted_llm:make"\n'
        '    skill_modules: ["interaction_ablations.skills"]\n')
    assert "skill_modules" in params
    yield from _launch(tmp_path_factory, "llm_led", params, config)


@pytest.fixture(scope="module")
def degraded_system(tmp_path_factory):
    """The method with a reduced catalog and a broken tree, for the error endings that a
    request cannot reach otherwise (O12 of the coverage audit): only the quiz and the
    questionnaire are offered (both need material, so a request without it fits no behavior),
    and the questionnaire's tree is not a valid tree (its runner cannot be activated)."""
    from ament_index_python.packages import get_package_share_directory
    tmp = tmp_path_factory.mktemp("degraded_config")
    broken = tmp / "broken_questionnaire.xml"
    broken.write_text("<root BTCPP_format=\"4\"><BehaviorTree ID=\"Broken\">"
                      "<NotANode/></BehaviorTree></root>")
    default = os.path.join(get_package_share_directory("interaction_system"), "config",
                           "interaction_behaviors_config.yaml")
    with open(default, encoding="utf-8") as f:
        config_text = f.read()
    tree = 'behavior_file: "behavior_tree_xml/questionnaire.xml"'
    assert config_text.count(tree) == 1
    config = tmp / "interaction_behaviors_config.yaml"
    config.write_text(config_text.replace(tree, f'behavior_file: "{broken}"'))
    params = PARAMS + """    dialogue_behaviors:
      behaviors: ["Quiz", "Questionnaire"]
"""
    yield from _launch(tmp_path_factory, "degraded_system", params, str(config))
