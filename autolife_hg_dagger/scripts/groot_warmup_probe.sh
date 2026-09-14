#!/usr/bin/env bash
set -euo pipefail

robot_id="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
task_text="${1:-Pick the laundry bag.}"
server_url="${GROOT_SERVER_URL:-http://192.168.8.179:8777}"
token_file="${GROOT_TOKEN_FILE:-/home/ubuntu/.config/autolife_hg_dagger/groot_server.token}"
probe_root="${HG_DAGGER_PROBE_ROOT:-/tmp/hg_dagger_groot_probe_${robot_id}}"
bridge_log="${probe_root}/bridge.log"
action_output="${probe_root}/policy_action.txt"

if [[ ! "${robot_id}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: invalid robot ID: ${robot_id}" >&2
  exit 2
fi
if [ ! -s "${token_file}" ]; then
  echo "ERROR: THOR token file is unavailable: ${token_file}" >&2
  exit 3
fi
if pgrep -f 'hg_dagger_supervisor|independent_arm_controller_306_v4' >/dev/null; then
  echo "ERROR: supervisor/controller is already running; probe refuses to start" >&2
  exit 4
fi

mkdir -p "${probe_root}"
rm -f "${bridge_log}" "${action_output}"

set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-<CycloneDDS><Domain><General><Interfaces><NetworkInterface name=\"lo\"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>200</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>}"
ros2 daemon stop >/dev/null 2>&1 || true

setsid ros2 run autolife_hg_dagger hg_dagger_groot_bridge --ros-args \
  -p "server_url:=${server_url}" \
  -p "token_file:=${token_file}" \
  -p "task:=${task_text}" \
  -p "joint_state_topic:=/topic_arm_whole_body_and_gripper_current_joints_status_0_${robot_id}" \
  -p max_inference_latency_sec:=1.0 \
  >"${bridge_log}" 2>&1 &
bridge_pid=$!

cleanup() {
  kill -TERM -- "-${bridge_pid}" 2>/dev/null || true
  wait "${bridge_pid}" 2>/dev/null || true
}
trap cleanup EXIT

sleep 2
control_state="{\"mode\":\"POLICY_WARMUP\",\"authority_epoch\":9001}"
timeout 150s ros2 topic pub -r 5 /hg_dagger/control_state std_msgs/msg/String \
  "{data: '${control_state}'}" >/dev/null 2>&1 &
publisher_pid=$!

if ! timeout 150s ros2 topic echo --once /hg_dagger/policy_action >"${action_output}"; then
  echo "ERROR: no warmup policy action received" >&2
  tail -n 80 "${bridge_log}" >&2
  exit 5
fi
kill "${publisher_pid}" 2>/dev/null || true
wait "${publisher_pid}" 2>/dev/null || true

python3 - "${action_output}" "${robot_id}" <<'PY'
import ast
import json
import pathlib
import sys

lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
data_line = next((line for line in lines if line.startswith("data: ")), None)
if data_line is None:
    raise SystemExit("policy action output has no data field")
payload = json.loads(ast.literal_eval(data_line.split(":", 1)[1].strip()))
action = payload.get("action")
if not isinstance(action, list) or len(action) != 21:
    raise SystemExit(f"invalid policy action length: {len(action) if isinstance(action, list) else None}")
if payload.get("warmup_only") is not True:
    raise SystemExit("probe action was not marked warmup_only")
print(json.dumps({
    "probe": "passed",
    "robot_id": sys.argv[2],
    "action_dimensions": len(action),
    "proposal_id": payload.get("proposal_id"),
    "warmup_only": payload.get("warmup_only"),
}, separators=(",", ":")))
PY

echo "Bridge log: ${bridge_log}"
