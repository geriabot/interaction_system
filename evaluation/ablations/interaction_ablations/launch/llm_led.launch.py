# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The whole stack with the LLM-led conversation (the `monolithic` ablation): the same as
interaction_system's main.launch.py, with the ablation's trees and its skills.

  ros2 launch interaction_ablations llm_led.launch.py                    # simple_hri, English
  ros2 launch interaction_ablations llm_led.launch.py language:=es       # Spanish
  ros2 launch interaction_ablations llm_led.launch.py params_file:=<my params>.yaml

It includes main.launch.py with the arguments given here (speech, stt, tts, whisper_model,
language, params_file, catalog_file), the ablation's config_file
(config/monolithic/interaction_behaviors_config.yaml) and the parameters given (params_file,
or app_params.yaml) with the ablation's skills module added to the intelligence's
`skill_modules`: a copy of them is written to a temporary file, so the user's file is not
modified.
"""

import os
import tempfile

import yaml

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

ABLATION = 'monolithic'
SKILLS = 'interaction_ablations.skills'
# The arguments passed as they are to main.launch.py (its defaults when not given)
PASSED = ('speech', 'stt', 'tts', 'whisper_model', 'input_device', 'language', 'catalog_file')


def with_skills(params_file: str) -> str:
    """A copy of the parameter file with the ablation's skills module in the intelligence's
    skill_modules (after the modules it already loads); returns its path."""
    with open(params_file, encoding='utf-8') as f:
        params = yaml.safe_load(f) or {}
    node = params.setdefault('interaction_intelligence', {}).setdefault('ros__parameters', {})
    modules = [m for m in node.get('skill_modules') or [] if m and m != SKILLS]
    node['skill_modules'] = modules + [SKILLS]
    out = tempfile.NamedTemporaryFile('w', prefix='llm_led_params_', suffix='.yaml',
                                      delete=False, encoding='utf-8')
    with out:
        yaml.safe_dump(params, out, sort_keys=False, allow_unicode=True)
    return out.name


def launch_setup(context):
    system = get_package_share_directory('interaction_system')
    ablations = get_package_share_directory('interaction_ablations')
    params_file = LaunchConfiguration('params_file').perform(context) or os.path.join(
        system, 'config', 'app_params.yaml')
    if not os.path.isfile(params_file):
        raise FileNotFoundError(f'Config not found: {params_file}')
    arguments = {name: LaunchConfiguration(name).perform(context) for name in PASSED}
    arguments = {name: value for name, value in arguments.items() if value}
    arguments['params_file'] = with_skills(params_file)
    arguments['config_file'] = os.path.join(ablations, 'config', ABLATION,
                                            'interaction_behaviors_config.yaml')
    return [IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(system, 'launch', 'main.launch.py')),
        launch_arguments=arguments.items())]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'speech', default_value='simple_hri',
            description='The speech capability: simple_hri, or none (provided by another '
                        'node, e.g. the simulation)'),
        DeclareLaunchArgument(
            'language', default_value='',
            description='Language of the conversation: a language file, by name or code (en, '
                        'es...; empty: the language of the parameter files, English by default)'),
        DeclareLaunchArgument(
            'params_file', default_value='',
            description="Parameters of the nodes (empty: interaction_system's "
                        'config/app_params.yaml); the ablation\'s skills are added to a copy'),
        DeclareLaunchArgument(
            'catalog_file', default_value='',
            description='The catalog of dialogue behaviors (empty: main.launch.py\'s default)'),
        DeclareLaunchArgument(
            'stt', default_value='faster_whisper',
            description="simple_hri's speech-to-text: faster_whisper or whisper"),
        DeclareLaunchArgument(
            'tts', default_value='piper',
            description="simple_hri's text-to-speech: piper or mms"),
        DeclareLaunchArgument(
            'whisper_model', default_value='small',
            description='Whisper model of simple_hri: tiny, base, small, medium, large(-v3)'),
        DeclareLaunchArgument(
            'input_device', default_value='',
            description="The microphone of simple_hri's faster_whisper (empty: PortAudio's "
                        'default; see main.launch.py)'),
        OpaqueFunction(function=launch_setup),
    ])
