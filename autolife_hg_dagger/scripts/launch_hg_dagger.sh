#!/usr/bin/env bash
# Run preflight and start the launch file only when the robot is safe and ready.

set -euo pipefail

robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
start_groot="${HG_DAGGER_START_GROOT:-false}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
dry_run="true"
task_name="hg_dagger_task"
data_root="/home/ubuntu/hg_dagger_data"

for launch_arg in "$@"; do
  case "$launch_arg" in
    dry_run:=*) dry_run="${launch_arg#dry_run:=}" ;;
    task_name:=*) task_name="${launch_arg#task_name:=}" ;;
    data_root:=*) data_root="${launch_arg#data_root:=}" ;;
  esac
done

if [[ ! "$task_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "ERROR: invalid task_name: ${task_name}" >&2
  exit 2
fi
if [[ "$data_root" != /* ]]; then
  echo "ERROR: data_root must be absolute: ${data_root}" >&2
  exit 2
fi

preflight_args=(--robot-id "$robot_id" --require-cameras)
if [ "$start_groot" = "true" ]; then
  preflight_args+=(--require-groot)
fi

bash "${script_dir}/preflight_hg_robot.sh" "${preflight_args[@]}"

if [ "$dry_run" = "false" ]; then
  collector_dir="${data_root}/${task_name}/hg_dagger_rgbd"
  collector_fifo="${collector_dir}/.official_recording_control"
  collector_pid_file="${collector_dir}/.hg_dagger_recorder.pid"
  if [ ! -p "$collector_fifo" ]; then
    echo "FAIL  RGBD collector FIFO is missing for this task: ${collector_fifo}" >&2
    echo "INFO  Start collectors with the same TASK_NAME and HG_DATA_ROOT before real hardware launch." >&2
    exit 1
  fi
  if [ ! -s "$collector_pid_file" ]; then
    echo "FAIL  RGBD collector PID file is missing: ${collector_pid_file}" >&2
    exit 1
  fi
  collector_pid="$(cat "$collector_pid_file")"
  if [[ ! "$collector_pid" =~ ^[0-9]+$ ]] || ! kill -0 "$collector_pid" 2>/dev/null; then
    echo "FAIL  RGBD collector is not running for task ${task_name} (stale PID: ${collector_pid:-missing})" >&2
    exit 1
  fi
  collector_cmd="$(ps -o args= -p "$collector_pid" 2>/dev/null || true)"
  if [[ "$collector_cmd" != *"${collector_dir}/atomic_dataset_v1"* ]]; then
    echo "FAIL  PID ${collector_pid} does not belong to the expected task dataset." >&2
    echo "INFO  Expected dataset: ${collector_dir}/atomic_dataset_v1" >&2
    exit 1
  fi
  echo "PASS  task-matched RGBD collector is running: PID=${collector_pid}"
  echo "PASS  collector FIFO ready: ${collector_fifo}"
fi

if [ "$start_groot" = "true" ]; then
  echo "INFO  GR00T bridge will start IDLE behind HG-DAGGER authority."
  echo "INFO  Enabling the VR session leaves VLA stopped and the robot holding."
  echo "INFO  Inference begins only after the operator releases both Grips, long-presses X, and releases X."
fi

exec ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  robot_id:="$robot_id" start_groot_bridge:="$start_groot" "$@"
