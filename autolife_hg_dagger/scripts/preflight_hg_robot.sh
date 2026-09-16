#!/usr/bin/env bash
# Fail-closed readiness check before starting the HD-DAGGER control stack.

set -uo pipefail

robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
require_cameras=0
require_groot=0
groot_url="${HG_DAGGER_GROOT_URL:-http://192.168.8.179:8777}"
token_file="${HG_DAGGER_GROOT_TOKEN_FILE:-/home/ubuntu/.config/autolife_hg_dagger/groot_server.token}"

usage() {
  echo "Usage: $0 [--robot-id ID] [--require-cameras] [--require-groot]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --robot-id) robot_id="${2:?--robot-id requires a value}"; shift 2 ;;
    --require-cameras) require_cameras=1; shift ;;
    --require-groot) require_groot=1; require_cameras=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

if systemctl --user is-active --quiet arm-control-service.service; then
  pass "arm-control-service.service is active"
else
  fail "arm-control-service.service is not active"
fi

joint_topic="/topic_arm_whole_body_and_gripper_current_joints_status_0_${robot_id}"
if timeout 8s ros2 topic echo --once "$joint_topic" >/dev/null 2>&1; then
  pass "live joint feedback received from ${joint_topic}"
else
  fail "no live joint feedback on ${joint_topic} (do not enable hardware)"
fi

conflict_pattern='openarmx_teleop_vr_navigation_306.*full_vr_navigation.launch.py|openarmx_teleop_vr_306_v4.controller_node|openarmx_306_v4_mapper|hg_dagger_supervisor|openarmx_teleop_vr_306_v4.vr_web_bridge'
conflicts="$(pgrep -af "$conflict_pattern" || true)"
if [ -n "$conflicts" ]; then
  fail "another VR/HG control stack is running:"
  printf '%s\n' "$conflicts" >&2
else
  pass "no competing VR/HG controller process"
fi

if ss -H -ltn "sport = :8446" 2>/dev/null | grep -q .; then
  fail "TCP port 8446 is already in use"
else
  pass "TCP port 8446 is free"
fi

if [ "$require_cameras" -eq 1 ]; then
  for camera_name in rgbd_head_color rgbd_head_depth hand_left hand_right; do
    meta="/dev/shm/camera_metadata_struct_${camera_name}"
    buffer="/dev/shm/camera_image_buffer_${camera_name}"
    if [ -s "$meta" ] && [ -s "$buffer" ]; then
      pass "camera SHM ready: ${camera_name}"
    else
      fail "camera SHM missing/incomplete: ${camera_name}"
    fi
  done
fi

if [ "$require_groot" -eq 1 ]; then
  if [ -s "$token_file" ]; then
    token_mode="$(stat -c '%a' "$token_file" 2>/dev/null || true)"
    if [ "$token_mode" = "600" ]; then
      pass "THOR token exists with mode 0600"
    else
      fail "THOR token must have mode 0600 (current: ${token_mode:-unknown})"
    fi
  else
    fail "THOR token is missing: ${token_file}"
  fi

  if curl --fail --silent --show-error --max-time 3 "${groot_url%/}/health" >/dev/null; then
    pass "THOR health endpoint reachable: ${groot_url%/}/health"
  else
    fail "THOR health endpoint unavailable: ${groot_url%/}/health"
  fi
fi

if [ "$failures" -ne 0 ]; then
  echo "PREFLIGHT FAILED: ${failures} check(s) failed. HD-DAGGER was not started." >&2
  exit 1
fi

echo "PREFLIGHT PASSED for robot ${robot_id}."
