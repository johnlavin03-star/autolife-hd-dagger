#!/usr/bin/env bash
set -euo pipefail

task_name="${1:-}"
data_root="${2:-${HG_DAGGER_DATA_ROOT:-/home/ubuntu/hg_dagger_data}}"
collector_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"

pid_cmdline_contains() {
  local pid="$1"
  local required="$2"
  [ -r "/proc/${pid}/cmdline" ] \
    && tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq -- "${required}"
}

if [ -z "${task_name}" ]; then
  echo "Usage: $0 TASK_NAME [DATA_ROOT]" >&2
  exit 2
fi

for variant in rgb rgbd; do
  session_dir="${data_root}/${task_name}/hg_dagger_${variant}"
  pid_file="${session_dir}/.hg_dagger_recorder.pid"
  if [ ! -f "${pid_file}" ]; then
    echo "${variant}: no PID file"
    continue
  fi
  recorder_pid="$(cat "${pid_file}")"
  if kill -0 "${recorder_pid}" 2>/dev/null; then
    if ! pid_cmdline_contains "${recorder_pid}" "${session_dir}/atomic_dataset_v1"; then
      echo "${variant}: stale/foreign PID ${recorder_pid}; not sending signals" >&2
      rm -f "${pid_file}"
      continue
    fi
    python3 "${collector_root}/collector_control.py" command \
      --base-dir "${session_dir}" quit >/dev/null 2>&1 || true
    for _ in $(seq 1 80); do
      kill -0 "${recorder_pid}" 2>/dev/null || break
      sleep 0.25
    done
  fi
  if kill -0 "${recorder_pid}" 2>/dev/null; then
    kill -TERM "${recorder_pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}" "${session_dir}/.official_recording_control"
  echo "${variant}: stopped"
done

hand_session_dir="${data_root}/${task_name}/hg_dagger_rgbd"
hand_pid_file="${hand_session_dir}/.hg_dagger_hand_producer.pid"
if [ -f "${hand_pid_file}" ]; then
  hand_pid="$(cat "${hand_pid_file}")"
  if [ -n "${hand_pid}" ] && kill -0 "${hand_pid}" 2>/dev/null; then
    if pid_cmdline_contains "${hand_pid}" "hand_camera_producer.py"; then
      kill -TERM "${hand_pid}" 2>/dev/null || true
      for _ in $(seq 1 40); do
        kill -0 "${hand_pid}" 2>/dev/null || break
        sleep 0.25
      done
    else
      echo "hand producer: stale/foreign PID ${hand_pid}; not sending signals" >&2
    fi
  fi
  rm -f "${hand_pid_file}"
  echo "hand producer: stopped"
fi
