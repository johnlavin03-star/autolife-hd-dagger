#!/usr/bin/env bash
# Source this file in every shell used to inspect/control HG-DAGGER nodes.

# ROS/colcon hooks and common interactive prompt scripts legitimately inspect
# optional variables. Keep nounset disabled in the calling interactive shell;
# executable HG-DAGGER scripts still enforce their own ``set -euo pipefail``.
set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash

export ROS_DOMAIN_ID=0
export ROBOT_ID="${HG_DAGGER_ROBOT_ID:-${ROBOT_ID:-328}}"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="lo"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>200</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>'

echo "HG-DAGGER ROS environment loaded (robot ${ROBOT_ID}, CycloneDDS loopback; nounset off)."
