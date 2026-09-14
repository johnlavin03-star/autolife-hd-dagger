#!/usr/bin/env bash
set -euo pipefail

source_python="${HG_DAGGER_ROBOT_PYTHON:-/home/ubuntu/miniconda3/envs/robot_env/bin/python}"
environment_root="${HG_DAGGER_WEB_ENV:-/home/ubuntu/ros2_ws/venvs/hg_dagger_web}"

if [ ! -x "${source_python}" ]; then
  echo "ERROR: robot environment Python is unavailable: ${source_python}" >&2
  exit 2
fi

mkdir -p "$(dirname "${environment_root}")"
"${source_python}" -m venv --system-site-packages "${environment_root}"

set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

"${environment_root}/bin/python" -c \
  'import aiohttp, aiortc, rclpy; print("HG-DAgger Web environment ready")'
echo "Python: ${environment_root}/bin/python"
