import os
from glob import glob

from setuptools import setup

package_name = "interaction_perception"

setup(
    name=package_name,
    version="0.0.0",
    packages=[package_name],
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            ["resource/" + package_name],
        ),
        (
            "share/" + package_name,
            ["package.xml"],
        ),
        (
            os.path.join("share", package_name, "config"),
            glob("config/*.yaml"),
        ),
        (
            os.path.join("share", package_name, "launch"),
            glob("launch/*.py"),
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="aquintan4",
    maintainer_email="aquintana.camacho@proton.me",
    description="The perception subsystem of the interaction system: the state of the user (facial emotion, lip activity) from a camera, served to the interaction system.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "emotion_detector = interaction_perception.emotion_detector:main",
            "user_state_server = interaction_perception.user_state_server:main",
            "lip_activity_detector = interaction_perception.lip_activity_detector:main",
        ],
    },
)
