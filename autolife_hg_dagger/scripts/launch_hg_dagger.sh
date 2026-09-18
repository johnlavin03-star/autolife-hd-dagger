#!/usr/bin/env bash
# Run preflight and start the launch file only when the robot is safe and ready.

set -euo pipefail

robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
start_groot="${HG_DAGGER_START_GROOT:-false}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

preflight_args=(--robot-id "$robot_id" --require-cameras)
if [ "$start_groot" = "true" ]; then
  preflight_args+=(--require-groot)
fi

bash "${script_dir}/preflight_hg_robot.sh" "${preflight_args[@]}"

if [ "$start_groot" = "true" ]; then
  echo "INFO  GR00T bridge will start IDLE behind HG-DAGGER authority."
  echo "INFO  Enabling the VR session leaves VLA stopped and the robot holding."
  echo "INFO  Inference begins only after the operator releases both Grips and long-presses A."
fi

exec ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  robot_id:="$robot_id" start_groot_bridge:="$start_groot" "$@"
