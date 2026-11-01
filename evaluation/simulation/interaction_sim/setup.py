"""ament_python package definition of interaction_sim."""

from setuptools import find_packages, setup

package_name = "interaction_sim"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Rodrigo Pérez-Rodríguez",
    maintainer_email="rodrigo.perez@urjc.es",
    description="Simulation of the interaction system: simulated users and speech capability",
    license="MIT",
    tests_require=["pytest"],
)
