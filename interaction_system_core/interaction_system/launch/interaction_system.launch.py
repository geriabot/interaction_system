# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The interaction system: the orchestrator and the dialogue behaviors (one process,
behavior_architecture's mission_executor) and the interaction intelligence.

The speech capability (simple_hri) and the LLM gateway
(llm_manager_node) are started apart: main.launch.py starts everything.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


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
    return [mission_executor, intelligence]


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
            'language', default_value='',
            description='Language of the conversation for the LLM, e.g. English, Spanish '
                        '(empty: language of the parameter files)'),
        OpaqueFunction(function=launch_setup),
    ])
