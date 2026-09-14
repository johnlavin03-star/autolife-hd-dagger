#!/usr/bin/env bash
set -euo pipefail

task_name="${1:-}"
task_text="${2:-${task_name}}"
data_root="${3:-${HG_DAGGER_DATA_ROOT:-/home/ubuntu/hg_dagger_data}}"
collector_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"
lerobot_py="${LEROBOT_PY:-/home/ubuntu/miniconda3/envs/lerobot/bin/python}"
robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
topic_suffix="${HG_DAGGER_TOPIC_SUFFIX:-0_${robot_id}}"

if [ -z "${task_name}" ]; then
  echo "Usage: $0 TASK_NAME [TASK_TEXT] [DATA_ROOT]" >&2
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
recorder="${collector_root}/record_lerobot_official.py"
control="${collector_root}/collector_control.py"
if [ ! -f "${recorder}" ] || [ ! -f "${control}" ]; then
  echo "ERROR: collector checkout is incomplete: ${collector_root}" >&2
  exit 5
fi

set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

ros_ld_library_path="${LD_LIBRARY_PATH:-}"
ros_pythonpath="${PYTHONPATH:-}"
lerobot_env_lib="/home/ubuntu/miniconda3/envs/lerobot/lib"
cyclonedds_uri='<CycloneDDS><Domain><General><NetworkInterfaceAddress>127.0.0.1</NetworkInterfaceAddress></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>120</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>'

start_one() {
  local variant="$1"
  local with_depth="$2"
  local min_cameras="$3"
  local session_dir="${data_root}/${task_name}/hg_dagger_${variant}"
  local dataset_root="${session_dir}/dataset"
  local fifo="${session_dir}/.official_recording_control"
  local status_file="${session_dir}/.official_recording_status.json"
  local event_file="${session_dir}/.official_episode_event.json"
  local pid_file="${session_dir}/.hg_dagger_recorder.pid"
  local log_dir="${session_dir}/logs"
  local log_file="${log_dir}/hg_dagger_recorder_$(date +%Y%m%d_%H%M%S).log"

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
  mkfifo "${fifo}"

  local args=(
    "${recorder}"
    --output-dir "${dataset_root}"
    --repo-id "local/${task_name}_hg_dagger_${variant}"
    --task-name "${task_text}"
    --motion-lock-file "/tmp/lerobot_robot_${topic_suffix}.motion.lock"
    --fps 30
    --image-source shm
    --image-poll-fps 120
    --min-cameras "${min_cameras}"
    --max-sync-delta-sec 0.03
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
    --action-arm-topic /hg_dagger/collector/arm_action
    --action-gripper-topic /hg_dagger/collector/gripper_action
    --control-fifo "${fifo}"
    --status-file "${status_file}"
    --episode-event-file "${event_file}"
  )
  if [ "${with_depth}" = "1" ]; then
    args+=(--with-depth)
  fi

  nohup env \
    ROS_DOMAIN_ID=0 \
    ROBOT_ID="${robot_id}" \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    CYCLONEDDS_URI="${cyclonedds_uri}" \
    LD_LIBRARY_PATH="${lerobot_env_lib}:${ros_ld_library_path}" \
    PYTHONPATH="${ros_pythonpath}" \
    "${lerobot_py}" "${args[@]}" >"${log_file}" 2>&1 &
  local recorder_pid=$!
  echo "${recorder_pid}" > "${pid_file}"
  echo "Started ${variant}: PID=${recorder_pid}, FIFO=${fifo}, log=${log_file}"
}

start_one rgbd 1 4
echo "RGBD collector is continuously buffering synchronized pre-roll frames in paused mode."
echo "It creates an episode only after an HG-DAGGER 'start' command."
echo "Use the same path in launch: data_root:=${data_root} task_name:=${task_name}"
echo "Robot ID=${robot_id}, topic suffix=${topic_suffix}"
