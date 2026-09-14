#!/usr/bin/env bash
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

log_file=/tmp/hg_dagger_dry_run.log
setsid ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  dry_run:=true start_web:=false >"${log_file}" 2>&1 &
launch_pid=$!

cleanup() {
  kill -TERM -- "-${launch_pid}" 2>/dev/null || true
  for _ in $(seq 1 30); do
    kill -0 "${launch_pid}" 2>/dev/null || break
    sleep 0.1
  done
  kill -KILL -- "-${launch_pid}" 2>/dev/null || true
  wait "${launch_pid}" 2>/dev/null || true
}
trap cleanup EXIT

sleep 6
echo NODES
timeout 8s ros2 node list
echo SERVICES
timeout 8s ros2 service list | grep hg_dagger || true
echo SELECTED_JOINT
timeout 8s ros2 topic info /hg_dagger/selected/joint_target --verbose
echo VENDOR_COMMAND
timeout 8s ros2 topic info /topic_arm_whole_body_target_joints_position_0_328 --verbose
echo ENABLE_RESULT
timeout 20s ros2 service call /hg_dagger/set_session_enabled std_srvs/srv/SetBool '{data: true}' || true

cleanup
trap - EXIT
echo LOG_TAIL
tail -n 35 "${log_file}"
