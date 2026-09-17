from glob import glob
from setuptools import find_packages, setup


package_name = "autolife_hg_dagger"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml", "README.md"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        (
            "share/" + package_name + "/scripts",
            glob("scripts/*.sh") + glob("scripts/*.py"),
        ),
        ("share/" + package_name + "/patches", glob("patches/*.patch")),
    ],
    install_requires=["setuptools"],
    tests_require=["pytest"],
    zip_safe=False,
    maintainer="Autolife Robotics",
    maintainer_email="ubuntu@example.com",
    description="HG-DAgger authority, timing and recording adapter.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "hg_dagger_supervisor = autolife_hg_dagger.supervisor_node:main",
            "hg_dagger_groot_bridge = autolife_hg_dagger.groot_policy_bridge:main",
        ],
    },
)
