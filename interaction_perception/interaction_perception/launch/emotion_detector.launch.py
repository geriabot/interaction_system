# Copyright 2026 Álvaro Quintana Camacho
#
# Licensed under the MIT License. See the LICENSE file of the repository.

# Launch the emotion detector node with its parameter file.

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(get_package_share_directory("interaction_perception"), "config", "params.yaml")

    return LaunchDescription(
        [
            Node(
                package="interaction_perception",
                executable="emotion_detector",
                name="emotion_detector",
                parameters=[config],
                output="screen",
            )
        ]
    )
