"""ament_python package definition of interaction_intelligence."""

import os
from glob import glob

from setuptools import find_packages, setup

package_name = "interaction_intelligence"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob(os.path.join("config", "*.yaml"))),
        # One file per language: the language-dependent resources (docs/ADDING_A_LANGUAGE.md)
        (os.path.join("share", package_name, "config", "languages"),
         glob(os.path.join("config", "languages", "*.yaml"))),
        # The questions for behavior_architecture's interpretation server
        (os.path.join("share", package_name, "config", "interpretation"),
         glob(os.path.join("config", "interpretation", "*.yaml"))),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Rodrigo Pérez-Rodríguez",
    maintainer_email="rodrigo.perez@urjc.es",
    description="The interaction intelligence: the questions of the interaction system to the LLM",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "interaction_intelligence = interaction_intelligence.intelligence_node:main",
            "interpretation_warmup = interaction_intelligence.interpretation_warmup:main",
        ],
    },
)
