#!/usr/bin/env bash
set -euo pipefail

set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

lerobot_py="${LEROBOT_PY:-/home/ubuntu/miniconda3/envs/lerobot/bin/python}"
collector_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"
ros_ld_library_path="${LD_LIBRARY_PATH:-}"
ros_pythonpath="${PYTHONPATH:-}"

env \
  LD_LIBRARY_PATH="/home/ubuntu/miniconda3/envs/lerobot/lib:${ros_ld_library_path}" \
  PYTHONPATH="${ros_pythonpath}" \
  "${lerobot_py}" "${collector_root}/record_lerobot_official.py" --help \
  >/tmp/hg_collector_help.txt

"${lerobot_py}" -m pip show lerobot
head -n 8 /tmp/hg_collector_help.txt
echo "Collector environment import check passed."
