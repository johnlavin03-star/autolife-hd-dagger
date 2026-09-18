#!/usr/bin/env bash
set -euo pipefail

task_name="${1:-}"
task_text="${2:-${task_name}}"
data_root="${3:-${HG_DAGGER_DATA_ROOT:-/home/ubuntu/hg_dagger_data}}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/../.." && pwd)"
# Existing/official collector checkout supplies shared helper modules only.  The
# HG-DAGGER recorder itself is repository-local so this script never overwrites
# or changes the existing data-collection implementation.
collector_runtime_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"
recorder="${HG_DAGGER_RECORDER:-${repo_root}/lerobot_data_collector/record_lerobot_official.py}"
lerobot_py="${LEROBOT_PY:-/home/ubuntu/miniconda3/envs/lerobot/bin/python}"
robot_py="${ROBOT_ENV_PY:-/home/ubuntu/miniconda3/envs/robot_env/bin/python}"
robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
topic_suffix="${HG_DAGGER_TOPIC_SUFFIX:-0_${robot_id}}"

if [ -z "${task_name}" ]; then
  echo "Usage: $0 TASK_NAME [TASK_TEXT] [DATA_ROOT]" >&2
  exit 2
fi
if [[ ! "${task_name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "ERROR: TASK_NAME must use letters, digits, '.', '_' or '-' and may not contain '/': ${task_name}" >&2
  exit 2
fi
if [[ "${data_root}" != /* ]]; then
  echo "ERROR: DATA_ROOT must be an absolute path: ${data_root}" >&2
  exit 3
fi
if [[ ! "${robot_id}" =~ ^[0-9]+$ ]] || [[ ! "${topic_suffix}" =~ ^0_[0-9]+$ ]]; then
  echo "ERROR: invalid robot ID/topic suffix: ${robot_id}/${topic_suffix}" >&2
  exit 3
fi
mkdir -p "${data_root}"
if [ ! -d "${data_root}" ] || [ ! -w "${data_root}" ]; then
  echo "ERROR: data root is not writable: ${data_root}" >&2
  exit 3
fi
if [ ! -x "${lerobot_py}" ]; then
  echo "ERROR: LeRobot Python is unavailable: ${lerobot_py}" >&2
  exit 4
fi
control="${collector_runtime_root}/collector_control.py"
if [ ! -f "${recorder}" ] || [ ! -f "${control}" ]; then
  echo "ERROR: isolated recorder or collector runtime is incomplete" >&2
  echo "  HG recorder: ${recorder}" >&2
  echo "  runtime helper: ${control}" >&2
  exit 5
fi

set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

ros_ld_library_path="${LD_LIBRARY_PATH:-}"
ros_pythonpath="${PYTHONPATH:-}"
lerobot_env_lib="/home/ubuntu/miniconda3/envs/lerobot/lib"
robot_env_lib="/home/ubuntu/miniconda3/envs/robot_env/lib"
cyclonedds_uri='<CycloneDDS><Domain><General><NetworkInterfaceAddress>127.0.0.1</NetworkInterfaceAddress></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>120</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>'

start_hand_producer() {
  local requested="${HG_DAGGER_START_HAND_PRODUCER:-auto}"
  local session_dir="${data_root}/${task_name}/hg_dagger_rgbd"
  local log_dir="${session_dir}/logs"
  local pid_file="${session_dir}/.hg_dagger_hand_producer.pid"
  local producer="${collector_runtime_root}/hand_camera_producer.py"

  mkdir -p "${session_dir}" "${log_dir}"
  if [ -f "${pid_file}" ]; then
    local existing_pid
    existing_pid="$(cat "${pid_file}")"
    if [ -n "${existing_pid}" ] && kill -0 "${existing_pid}" 2>/dev/null; then
      echo "Hand-camera producer already running as PID ${existing_pid}"
      return 0
    fi
  fi
  if [ "${requested}" = "0" ]; then
    echo "Hand-camera producer disabled by HG_DAGGER_START_HAND_PRODUCER=0"
    return 0
  fi
  if [ "${requested}" = "auto" ] \
      && [ -s /dev/shm/camera_metadata_struct_hand_left ] \
      && [ -s /dev/shm/camera_metadata_struct_hand_right ]; then
    echo "Reusing existing hand-camera SHM streams"
    return 0
  fi
  if [ ! -x "${robot_py}" ] || [ ! -f "${producer}" ]; then
    echo "ERROR: hand-camera producer environment is unavailable" >&2
    return 1
  fi

  local log_file="${log_dir}/hg_dagger_hand_camera_$(date +%Y%m%d_%H%M%S).log"
  nohup env \
    LD_LIBRARY_PATH="${robot_env_lib}:${ros_ld_library_path}" \
    PYTHONPATH="${collector_runtime_root}:${ros_pythonpath}" \
    HAND_LEFT_CAMERA_DEVICE="${HG_DAGGER_HAND_LEFT_DEVICE:-/dev/video12}" \
    HAND_RIGHT_CAMERA_DEVICE="${HG_DAGGER_HAND_RIGHT_DEVICE:-/dev/video10}" \
    "${robot_py}" "${producer}" >"${log_file}" 2>&1 &
  local producer_pid=$!
  echo "${producer_pid}" > "${pid_file}"
  sleep 1
  if ! kill -0 "${producer_pid}" 2>/dev/null; then
    echo "ERROR: hand-camera producer exited during startup; see ${log_file}" >&2
    return 1
  fi
  echo "Started hand-camera producer: PID=${producer_pid}, log=${log_file}"
}

start_one() {
  local variant="$1"
  local with_depth="$2"
  local min_cameras="$3"
  local session_dir="${data_root}/${task_name}/hg_dagger_${variant}"
  local dataset_root="${session_dir}/atomic_dataset_v1"
  local fifo="${session_dir}/.official_recording_control"
  local status_file="${session_dir}/.official_recording_status.json"
  local event_file="${session_dir}/.official_episode_event.json"
  local pid_file="${session_dir}/.hg_dagger_recorder.pid"
  local log_dir="${session_dir}/logs"
  local log_file="${log_dir}/hg_dagger_recorder_$(date +%Y%m%d_%H%M%S).log"
  local default_dataset_fps=30
  local max_sync_delta_sec=0.03
  local dataset_fps="${HG_DAGGER_DATASET_FPS:-${default_dataset_fps}}"

  if ! grep -q "HG-DAGGER atomic episode store" "${recorder}"; then
    echo "ERROR: repository-local HG-DAGGER atomic recorder is invalid: ${recorder}" >&2
    return 1
  fi

  mkdir -p "${session_dir}" "${log_dir}"
  if [ -f "${pid_file}" ]; then
    local existing_pid
    existing_pid="$(cat "${pid_file}")"
    if [ -n "${existing_pid}" ] && kill -0 "${existing_pid}" 2>/dev/null; then
      echo "ERROR: ${variant} recorder is already running as PID ${existing_pid}" >&2
      return 1
    fi
  fi
  rm -f "${fifo}" "${status_file}" "${event_file}"
  mkdir -p "${status_file}.d"
  find "${status_file}.d" -maxdepth 1 -type f -mtime +7 -delete
  mkfifo "${fifo}"

  local args=(
    "${recorder}"
    --output-dir "${dataset_root}"
    --atomic-episodes
    --dagger-metadata
    --repo-id "local/${task_name}_hg_dagger_${variant}"
    --task-name "${task_text}"
    --motion-lock-file "/tmp/lerobot_robot_${topic_suffix}.motion.lock"
    --min-free-disk-gb "${HG_DAGGER_MIN_FREE_DISK_GB:-5.0}"
    --fps "${dataset_fps}"
    --image-source shm
    --image-poll-fps 120
    --min-cameras "${min_cameras}"
    --max-sync-delta-sec "${max_sync_delta_sec}"
    --max-image-age-sec 0.15
    --max-state-age-sec 0.15
    --max-state-interpolation-gap-sec 0.05
    --max-action-hold-sec 0.5
    --sync-image-buffer-size 16
    --sync-signal-buffer-size 64
    --pre-roll-sec "${HG_DAGGER_PRE_ROLL_SEC:-5.0}"
    --action-mode joint
    --with-head
    --with-upper-waist
    --state-topic "/topic_arm_whole_body_and_gripper_current_joints_status_${topic_suffix}"
    --control-state-topic /hg_dagger/control_state
    --action-arm-topic /hg_dagger/collector/arm_action
    --action-gripper-topic /hg_dagger/collector/gripper_action
    --control-fifo "${fifo}"
    --status-file "${status_file}"
    --episode-event-file "${event_file}"
  )
  if [ "${with_depth}" = "1" ]; then
    args+=(--with-depth)
  fi
  if [ "${HG_DAGGER_TEST_CAMERA_DESYNC_AFTER_FRAMES:-0}" -gt 0 ]; then
    args+=(--test-inject-camera-desync-after-frames "${HG_DAGGER_TEST_CAMERA_DESYNC_AFTER_FRAMES}")
  fi
  if [ "${HG_DAGGER_TEST_ENOSPC_ON_SAVE:-0}" = "1" ]; then
    args+=(--test-inject-enospc-on-save-once)
  fi

  nohup env \
    ROS_DOMAIN_ID=0 \
    ROBOT_ID="${robot_id}" \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    CYCLONEDDS_URI="${cyclonedds_uri}" \
    LD_LIBRARY_PATH="${lerobot_env_lib}:${ros_ld_library_path}" \
    PYTHONPATH="${collector_runtime_root}:${ros_pythonpath}" \
    "${lerobot_py}" "${args[@]}" >"${log_file}" 2>&1 &
  local recorder_pid=$!
  echo "${recorder_pid}" > "${pid_file}"
  echo "Started ${variant}: PID=${recorder_pid}, FIFO=${fifo}, log=${log_file}"
}

start_hand_producer
start_one rgbd 1 4
echo "RGBD collector is continuously buffering synchronized pre-roll frames in paused mode."
echo "It creates an episode only after an HG-DAGGER 'start' command."
echo "Use the same path in launch: data_root:=${data_root} task_name:=${task_name}"
echo "Dataset root: ${data_root}/${task_name}/hg_dagger_rgbd/atomic_dataset_v1"
echo "Sidecar root: ${data_root}/${task_name}/hg_dagger_sidecar"
echo "Robot ID=${robot_id}, topic suffix=${topic_suffix}"
