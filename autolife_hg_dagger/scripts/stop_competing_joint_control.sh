#!/usr/bin/env bash
# Deliberately stop only Control Center's whole-body joint launch for one robot.

set -euo pipefail

robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
confirmed=0

usage() {
  echo "Usage: $0 [--robot-id ID] --confirm"
  echo "Run only after confirming nobody is using Control Center or the robot arms."
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --robot-id) robot_id="${2:?--robot-id requires a value}"; shift 2 ;;
    --confirm) confirmed=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! "$robot_id" =~ ^[0-9]+$ ]]; then
  echo "ERROR: invalid robot ID: ${robot_id}" >&2
  exit 2
fi
if [ "$confirmed" -ne 1 ]; then
  echo "REFUSED: explicit --confirm is required." >&2
  usage >&2
  exit 2
fi

if pgrep -af 'hg_dagger_supervisor|openarmx_teleop_vr_306_v4.controller_node|openarmx_306_v4_mapper' >/dev/null; then
  echo "ERROR: an HD-DAGGER/VR controller is already running; no process was stopped." >&2
  pgrep -af 'hg_dagger_supervisor|openarmx_teleop_vr_306_v4.controller_node|openarmx_306_v4_mapper' >&2 || true
  exit 3
fi

pattern="/opt/ros/jazzy/bin/ros2 launch control_center_joint_control whole_body_joint_control.launch.py.*robot_id:=${robot_id}([^0-9]|$)"
mapfile -t launch_pids < <(pgrep -f "$pattern" || true)
if [ "${#launch_pids[@]}" -eq 0 ]; then
  echo "No competing Control Center whole-body launch found for robot ${robot_id}."
  exit 0
fi

echo "Stopping only these Control Center launch processes with SIGINT:"
for pid in "${launch_pids[@]}"; do
  ps -o user,pid,ppid,lstart,cmd -p "$pid"
done
kill -INT "${launch_pids[@]}"

deadline=$((SECONDS + 12))
while [ "$SECONDS" -lt "$deadline" ]; do
  remaining=0
  for pid in "${launch_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      remaining=1
    fi
  done
  [ "$remaining" -eq 0 ] && break
  sleep 0.25
done

for pid in "${launch_pids[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: PID ${pid} did not exit. Close Whole Body Joint Control in the GUI; SIGKILL was not used." >&2
    exit 4
  fi
done

sleep 0.5
if pgrep -f "$pattern" >/dev/null; then
  echo "ERROR: Control Center restarted the launch. Close it in the GUI before HD-DAGGER." >&2
  pgrep -af "$pattern" >&2 || true
  exit 5
fi

echo "Competing Control Center launch stopped cleanly for robot ${robot_id}."
