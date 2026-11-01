# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The perception subsystem of the interaction system: the detectors on the camera image and
the user-state server.

  ros2 launch interaction_perception interaction_perception.launch.py
  ros2 launch interaction_perception interaction_perception.launch.py image_topic:=/camera/color/image_raw
  ros2 launch interaction_perception interaction_perception.launch.py lips:=false
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(get_package_share_directory("interaction_perception"), "config")
    image_topic = LaunchConfiguration("image_topic")
    return LaunchDescription([
        DeclareLaunchArgument("image_topic", default_value="/camera/image_raw",
                              description="The camera image the detectors read"),
        DeclareLaunchArgument("emotion", default_value="true",
                              description="Run the facial emotion detector"),
        DeclareLaunchArgument("lips", default_value="true",
                              description="Run the lip activity detector (visual barge-in)"),
        DeclareLaunchArgument("visualization", default_value="false",
                              description="Show the detectors' debug windows"),
        Node(
            package="interaction_perception",
            executable="user_state_server",
            name="user_state_server",
            output="screen",
        ),
        Node(
            package="interaction_perception",
            executable="emotion_detector",
            name="emotion_detector",
            output="screen",
            parameters=[os.path.join(config, "params.yaml"), {
                "input_topic": image_topic,
                "show_visualization": LaunchConfiguration("visualization"),
            }],
            condition=IfCondition(LaunchConfiguration("emotion")),
        ),
        Node(
            package="interaction_perception",
            executable="lip_activity_detector",
            name="lip_activity_detector_node",
            output="screen",
            parameters=[os.path.join(config, "lip_activity_params.yaml"), {
                "image_topic": image_topic,
                "show_debug": LaunchConfiguration("visualization"),
            }],
            condition=IfCondition(LaunchConfiguration("lips")),
        ),
    ])
