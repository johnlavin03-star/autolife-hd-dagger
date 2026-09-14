#!/usr/bin/env bash
# Source this file in every shell used to inspect/control HG-DAGGER nodes.

set +u
source /opt/ros/jazzy/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID=0
export ROBOT_ID=328
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="lo"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>200</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>'

echo "HG-DAGGER ROS environment loaded (robot 328, CycloneDDS loopback)."
