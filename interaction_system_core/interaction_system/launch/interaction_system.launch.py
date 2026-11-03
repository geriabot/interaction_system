# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The interaction system: the orchestrator and the dialogue behaviors (one process,
behavior_architecture's mission_executor), the interaction intelligence and the interpretation
server that asks the model its questions (behavior_architecture's interpretation_server, in the
namespace `interaction`: /interaction/interpretation/interpret).

The server is configured from the same parameter files (interaction_intelligence's
interpretation_config.py): its routes from the model binding (llm_manager_node's parameters)
and the intelligence's llm_provider, llm_model and routes, its questions and question module
from interaction_intelligence (and interaction_ablations, for the LLM-led baseline), its
templates from prompts.yaml. interpretation:=false starts no server (another one serves the
questions, or the intelligence uses a test double: llm_client_factory).

The speech capability (simple_hri) is started apart: main.launch.py starts everything.
"""

import os

from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from interaction_intelligence.interpretation_config import (server_environment,
                                                            server_parameters, warmup_of)


def interpretation_server(context, params_files: list[str]) -> list:
    """The interpretation server of the interaction system, with its parameters written from
    the parameter files (and a log line per warning)."""
    intelligence_dir = get_package_share_directory('interaction_intelligence')
    try:
        ablation = os.path.join(get_package_share_directory('interaction_ablations'),
                                'config', 'interpretation', 'questions.yaml')
    except PackageNotFoundError:
        ablation = ''
    parameters, warnings = server_parameters(
        params_files,
        questions_file=os.path.join(intelligence_dir, 'config', 'interpretation',
                                    'questions.yaml'),
        prompts_file=LaunchConfiguration('prompts_file').perform(context),
        ablation_questions_file=ablation,
        log_dir=LaunchConfiguration('interpretation_log_dir').perform(context))
    messages = [LogInfo(msg=f'[interpretation] {w}') for w in warnings]
    if parameters is None:
        return messages
    server = Node(
        package='behavior_architecture',
        executable='interpretation_server',
        name='interpretation_server',
        namespace='interaction',
        output='screen',
        emulate_tty=True,
        parameters=[parameters],
        # An API key given in the parameter files, in the variable that its backend reads
        additional_env=server_environment(params_files),
    )
    nodes = messages + [server]
    # The model loaded into the Ollama server's memory at the start, as the former gateway did
    warmup = warmup_of(params_files)
    if warmup:
        nodes.append(Node(
            package='interaction_intelligence',
            executable='interpretation_warmup',
            name='interpretation_warmup',
            output='screen',
            arguments=['--url', warmup['url'], '--model', warmup['model'],
                       '--num-ctx', str(warmup['num_ctx'])],
        ))
    return nodes


def launch_setup(context):
    params = [LaunchConfiguration('params_file').perform(context)]
    # When launched from the GUI, DIALOSTACK_GUI_PARAMS points to a YAML generated from the
    # Config Editor, applied last so that its values take precedence
    gui_override = os.environ.get('DIALOSTACK_GUI_PARAMS', '')
    if gui_override and os.path.isfile(gui_override):
        params.append(gui_override)

    # Overrides given by the launch arguments (empty: the value of the parameter files)
    language = LaunchConfiguration('language').perform(context)
    intelligence_override = [{'language': language}] if language else []

    mission_executor = Node(
        package='behavior_architecture',
        executable='mission_executor',
        output='screen',
        emulate_tty=True,
        arguments=[LaunchConfiguration('config_file')],
        parameters=[LaunchConfiguration('catalog_file').perform(context)] + params,
    )
    intelligence = Node(
        package='interaction_intelligence',
        executable='interaction_intelligence',
        name='interaction_intelligence',
        output='screen',
        parameters=[LaunchConfiguration('prompts_file').perform(context)] + params
        + intelligence_override,
    )
    nodes = [mission_executor, intelligence]
    if LaunchConfiguration('interpretation').perform(context).lower() in ('true', '1', 'yes'):
        nodes += interpretation_server(context, params)
    return nodes


def generate_launch_description():
    pkg_dir = get_package_share_directory('interaction_system')
    intelligence_dir = get_package_share_directory('interaction_intelligence')
    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file',
            default_value=os.path.join(pkg_dir, 'config', 'app_params.yaml'),
            description='Parameters of the nodes (limits, language, LLM binding...)'),
        DeclareLaunchArgument(
            'config_file',
            default_value=os.path.join(pkg_dir, 'config', 'interaction_behaviors_config.yaml'),
            description='behavior_architecture configuration: the orchestrator and the runners'),
        DeclareLaunchArgument(
            'catalog_file',
            default_value=os.path.join(pkg_dir, 'config', 'interaction_orchestrator_params.yaml'),
            description='The catalog of dialogue behaviors and the default limits'),
        DeclareLaunchArgument(
            'prompts_file',
            default_value=os.path.join(intelligence_dir, 'config', 'prompts.yaml'),
            description='Templates of the questions to the LLM (prompts.*)'),
        DeclareLaunchArgument(
            'interpretation', default_value='true',
            description='Start the interpretation server that asks the model the questions of '
                        'the intelligence (false: another one serves them)'),
        DeclareLaunchArgument(
            'interpretation_log_dir', default_value='',
            description="Directory of the interpretation server's call log (empty: none)"),
        DeclareLaunchArgument(
            'language', default_value='',
            description='Language of the conversation for the LLM, e.g. English, Spanish '
                        '(empty: language of the parameter files)'),
        OpaqueFunction(function=launch_setup),
    ])
