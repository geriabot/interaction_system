"""ament_python package definition of interaction_ablations."""

import os
from glob import glob

from setuptools import find_packages, setup

package_name = "interaction_ablations"


def tree(directory):
    """(install dir, files) for every subdirectory of `directory`."""
    entries = []
    for root, _, files in os.walk(directory):
        if files:
            entries.append((os.path.join("share", package_name, root),
                            [os.path.join(root, f) for f in files]))
    return entries


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ] + tree("behavior_tree_xml") + tree("config"),
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Rodrigo Pérez-Rodríguez",
    maintainer_email="rodrigo.perez@urjc.es",
    description="Ablations of the interaction system for the experiments",
    license="MIT",
)
