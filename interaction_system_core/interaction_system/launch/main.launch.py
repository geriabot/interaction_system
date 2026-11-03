# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The whole stack: the speech capability and the interaction system (with its interpretation
server, which asks the model), with the shared app_params.yaml. This is what the GUI starts.

  ros2 launch interaction_system main.launch.py                     # simple_hri, English
  ros2 launch interaction_system main.launch.py language:=es        # Spanish
  ros2 launch interaction_system main.launch.py speech:=none        # simulation (interaction_sim)

The speech capability is simple_hri: actions that listen and speak when the behaviors ask
(faster-whisper with Silero VAD and Piper by default, or Whisper and MMS-TTS).
"""

import os

import yaml

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# The languages are the language files of the interaction intelligence (config/languages/*.yaml):
# each gives the name the intelligence uses and the speech settings of simple_hri in that
# language (recognition language, Piper voice and speaker, MMS-TTS voice)
from interaction_intelligence.languages import (
    LanguageFileError, UnknownLanguageError, load_language)


def configured_language(params_files) -> str:
    """The intelligence's `language` in the last parameter file setting it (English when none
    does)."""
    language = 'English'
    for path in params_files:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        name = ((data.get('interaction_intelligence') or {}).get('ros__parameters') or {}).get(
            'language')
        if name:
            language = name
    return language


def launch_setup(context):
    pkg_dir = get_package_share_directory('interaction_system')
    app_params = LaunchConfiguration('params_file').perform(context) or os.path.join(
        pkg_dir, 'config', 'app_params.yaml')
    if not os.path.isfile(app_params):
        raise FileNotFoundError(f'Config not found: {app_params}')

    # When launched from the GUI, DIALOSTACK_GUI_PARAMS points to a temporary YAML file
    # generated from the Config Editor. It is applied after app_params so its values take
    # precedence (ROS 2 applies params files in order).
    gui_override = os.environ.get('DIALOSTACK_GUI_PARAMS', '')
    extra = [gui_override] if gui_override and os.path.isfile(gui_override) else []

    speech = LaunchConfiguration('speech').perform(context)
    # The language: the launch argument, or else the one of the parameter files (the GUI's last)
    language = LaunchConfiguration('language').perform(context)
    if not language:
        language = configured_language([app_params] + extra)
    try:
        lang = load_language(language)
    except (UnknownLanguageError, LanguageFileError) as exc:
        raise RuntimeError(str(exc)) from None
    language_name, speech_settings = lang.name, lang.speech
    stt_language = speech_settings['stt_language']

    if speech == 'simple_hri':
        stt = LaunchConfiguration('stt').perform(context)
        tts = LaunchConfiguration('tts').perform(context)
        if stt not in ('faster_whisper', 'whisper') or tts not in ('piper', 'mms'):
            raise RuntimeError('stt: faster_whisper or whisper; tts: piper or mms')
        if tts == 'piper':
            tts_params = {'lang_code': stt_language, 'voice': speech_settings['piper_voice'],
                          'play_sound': True}
            if speech_settings['piper_speaker'] >= 0:
                tts_params['speaker'] = speech_settings['piper_speaker']
        else:
            tts_params = {'lang_code': speech_settings['mms_voice'], 'use_gpu': True,
                          'play_sound': True}
        stt_params = {
            'model': LaunchConfiguration('whisper_model').perform(context),
            'language': stt_language,
        }
        input_device = LaunchConfiguration('input_device').perform(context)
        if input_device:
            stt_params['input_device'] = input_device
        speech_nodes = [
            Node(
                package='simple_hri',
                executable=f'stt_{stt}',
                name='stt_service_node',
                output='screen',
                parameters=[stt_params],
            ),
            Node(
                package='simple_hri',
                executable=f'tts_{tts}',
                name='tts_service_node',
                output='screen',
                parameters=[tts_params],
            ),
        ]
    elif speech == 'none':
        # No speech nodes: another node provides the capability (e.g. the simulated user of
        # interaction_sim, which serves simple_hri's actions)
        speech_nodes = []
    else:
        raise RuntimeError(
            f"Unknown speech capability '{speech}': simple_hri or none")

    # The model binding (llm_manager_node's parameters of app_params.yaml) configures the
    # interpretation server that interaction_system.launch.py starts; the former gateway
    # (ros2_llm_manager) is not started
    return speech_nodes + [
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_dir, 'launch', 'interaction_system.launch.py')),
            launch_arguments={
                'params_file': app_params,
                'config_file': LaunchConfiguration('config_file').perform(context) or os.path.join(
                    pkg_dir, 'config', 'interaction_behaviors_config.yaml'),
                'catalog_file': LaunchConfiguration('catalog_file').perform(context) or os.path.join(
                    pkg_dir, 'config', 'interaction_orchestrator_params.yaml'),
                'language': language_name,
            }.items(),
        ),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'speech', default_value='simple_hri',
            description='The speech capability: simple_hri, or none (provided by '
                        'another node, e.g. the simulation)'),
        DeclareLaunchArgument(
            'language', default_value='',
            description='Language of the conversation: a language file of the interaction '
                        'intelligence, by name or code (en, es, English, Spanish...; empty: the '
                        'language of the parameter files, English by default)'),
        DeclareLaunchArgument(
            'params_file', default_value='',
            description='Parameters of the nodes (empty: config/app_params.yaml)'),
        DeclareLaunchArgument(
            'config_file', default_value='',
            description='behavior_architecture configuration: the orchestrator and the runners '
                        '(empty: config/interaction_behaviors_config.yaml)'),
        DeclareLaunchArgument(
            'catalog_file', default_value='',
            description='The catalog of dialogue behaviors '
                        '(empty: config/interaction_orchestrator_params.yaml)'),
        DeclareLaunchArgument(
            'stt', default_value='faster_whisper',
            description="simple_hri's speech-to-text: faster_whisper (faster-whisper + Silero VAD) "
                        'or whisper (Whisper + webrtcvad)'),
        DeclareLaunchArgument(
            'tts', default_value='piper',
            description="simple_hri's text-to-speech: piper (Piper voices) or mms (MMS-TTS)"),
        DeclareLaunchArgument(
            'whisper_model', default_value='small',
            description='Whisper model of simple_hri: tiny, base, small, medium, large(-v3)'),
        DeclareLaunchArgument(
            'input_device', default_value='',
            description="The microphone of simple_hri's faster_whisper (a sounddevice name or "
                        "index; empty: PortAudio's default). 'default' goes through the system's "
                        'sound server, which converts the sample rate'),
        OpaqueFunction(function=launch_setup),
    ])
