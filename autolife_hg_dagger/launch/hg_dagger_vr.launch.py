"""Launch the immutable V4 VR stack behind the HG-DAgger authority layer."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


VR_PREFIX = "/openarmx_teleop_vr_306_v4"
HG_PREFIX = "/hg_dagger"


def generate_launch_description():
    hg_share = get_package_share_directory("autolife_hg_dagger")
    vr_share = get_package_share_directory("openarmx_teleop_vr_306_v4")
    controller_config = os.path.join(vr_share, "config", "controller.yaml")
    teleop_config = os.path.join(vr_share, "config", "teleop.yaml")
    hg_config = os.path.join(hg_share, "config", "hg_dagger.yaml")
    urdf = os.path.join(vr_share, "urdf", "robot_v2_2_simplified.urdf")
    srdf = os.path.join(vr_share, "urdf", "robot_v2_2.srdf")

    dry_run = LaunchConfiguration("dry_run")
    start_web = LaunchConfiguration("start_web")
    web_host = LaunchConfiguration("web_host")
    web_port = LaunchConfiguration("web_port")
    robot_env_python = LaunchConfiguration("robot_env_python")
    ros_domain_id = LaunchConfiguration("ros_domain_id")
    rmw_implementation = LaunchConfiguration("rmw_implementation")
    cyclonedds_uri = LaunchConfiguration("cyclonedds_uri")
    data_root = LaunchConfiguration("data_root")
    task_name = LaunchConfiguration("task_name")
    robot_id = LaunchConfiguration("robot_id")
    start_groot_bridge = LaunchConfiguration("start_groot_bridge")
    groot_server_url = LaunchConfiguration("groot_server_url")
    groot_token_file = LaunchConfiguration("groot_token_file")
    groot_task = LaunchConfiguration("groot_task")
    groot_max_inference_latency_sec = LaunchConfiguration(
        "groot_max_inference_latency_sec")
    policy_timeout_sec = LaunchConfiguration("policy_timeout_sec")

    supervisor = Node(
        package="autolife_hg_dagger",
        executable="hg_dagger_supervisor",
        name="hg_dagger_supervisor",
        output="screen",
        parameters=[hg_config, {
            "trace_root": PathJoinSubstitution([data_root, task_name, "hg_dagger_sidecar"]),
            "rgb_collector_fifo": PathJoinSubstitution([
                data_root, task_name, "hg_dagger_rgb", ".official_recording_control"]),
            "rgbd_collector_fifo": PathJoinSubstitution([
                data_root, task_name, "hg_dagger_rgbd", ".official_recording_control"]),
            "vendor_joint_command_topic": ParameterValue([
                "/topic_arm_whole_body_target_joints_position_0_", robot_id
            ], value_type=str),
            "vendor_gripper_command_topic": ParameterValue([
                "/topic_arm_gripper_target_joints_position_0_", robot_id
            ], value_type=str),
            "policy_timeout_sec": ParameterValue(
                policy_timeout_sec, value_type=float),
        }],
    )

    controller = Node(
        package="openarmx_teleop_vr_306_v4",
        executable="openarmx_306_v4_arm_controller_robot_env.sh",
        name="independent_arm_controller_306_v4",
        output="screen",
        parameters=[controller_config, {
            "topic_suffix": ParameterValue(["0_", robot_id], value_type=str),
            "dry_run": ParameterValue(dry_run, value_type=bool),
            "reset_before_hardware_enable": False,
            "quick_reset_after_hardware_enable": False,
            "body_height_control_enabled": False,
            "allow_waist_in_ik": False,
            "head_follow_enabled": False,
            "require_teleop_heartbeat": True,
            "input_target_topic": f"{HG_PREFIX}/selected/eef_target",
            "input_gripper_topic": f"{HG_PREFIX}/selected/gripper_target",
            "input_release_topic": f"{HG_PREFIX}/selected/release_hold",
            "input_joint_target_topic": f"{HG_PREFIX}/selected/joint_target",
            "input_body_height_topic": f"{HG_PREFIX}/disabled/body_height_command",
            "input_head_target_topic": f"{HG_PREFIX}/disabled/head_target",
            "urdf_path": urdf,
            "srdf_path": srdf,
        }],
        remappings=[
            (f"{VR_PREFIX}/set_hardware_enabled", f"{HG_PREFIX}/controller/set_hardware_enabled"),
        ],
    )

    mapper = Node(
        package="openarmx_teleop_vr_306_v4",
        executable="openarmx_306_v4_mapper",
        name="independent_vr_mapper_306_v4",
        output="screen",
        parameters=[teleop_config, {
            "topic_suffix": ParameterValue(["0_", robot_id], value_type=str),
            "dry_run": ParameterValue(dry_run, value_type=bool),
            # X+A quick reset calls the controller directly in the stock mapper.
            # Disable it so every motion request remains behind the HG authority.
            "quick_reset_enabled": False,
        }],
        remappings=[
            (f"{VR_PREFIX}/eef_target", f"{HG_PREFIX}/expert/eef_target"),
            (f"{VR_PREFIX}/gripper_target", f"{HG_PREFIX}/expert/gripper_target"),
            (f"{VR_PREFIX}/release_hold", f"{HG_PREFIX}/expert/release_hold"),
            (f"{VR_PREFIX}/head_target", f"{HG_PREFIX}/disabled/head_target"),
            (f"{VR_PREFIX}/set_hardware_enabled", f"{HG_PREFIX}/set_session_enabled"),
        ],
    )

    web_bridge = ExecuteProcess(
        condition=IfCondition(start_web),
        cmd=[
            robot_env_python,
            "-m", "openarmx_teleop_vr_306_v4.vr_web_bridge",
            "--ros-args",
            "-p", ["host:=", web_host],
            "-p", ["https_port:=", web_port],
            "-p", "rgbd_camera_enabled:=true",
            "-p", "status_topic:=/hg_dagger/web_teleop_status",
            "-r", f"{VR_PREFIX}/set_hardware_enabled:={HG_PREFIX}/set_session_enabled",
        ],
        output="screen",
    )

    groot_bridge = Node(
        condition=IfCondition(start_groot_bridge),
        package="autolife_hg_dagger",
        executable="hg_dagger_groot_bridge",
        name="hg_dagger_groot_policy_bridge",
        output="screen",
        parameters=[{
            "server_url": groot_server_url,
            "token_file": groot_token_file,
            "task": groot_task,
            "joint_state_topic": ParameterValue([
                "/topic_arm_whole_body_and_gripper_current_joints_status_0_",
                robot_id,
            ], value_type=str),
            "max_inference_latency_sec": ParameterValue(
                groot_max_inference_latency_sec, value_type=float),
        }],
    )

    return LaunchDescription([
        DeclareLaunchArgument("dry_run", default_value="true"),
        DeclareLaunchArgument("start_web", default_value="true"),
        DeclareLaunchArgument("web_host", default_value="0.0.0.0"),
        DeclareLaunchArgument("web_port", default_value="8446"),
        DeclareLaunchArgument(
            "robot_env_python",
            default_value="/home/ubuntu/ros2_ws/venvs/hg_dagger_web/bin/python",
        ),
        DeclareLaunchArgument("ros_domain_id", default_value="0"),
        DeclareLaunchArgument(
            "rmw_implementation", default_value="rmw_cyclonedds_cpp"),
        DeclareLaunchArgument(
            "cyclonedds_uri",
            default_value=(
                '<CycloneDDS><Domain><General><Interfaces>'
                '<NetworkInterface name="lo"/>'
                '</Interfaces><AllowMulticast>false</AllowMulticast>'
                '</General><Discovery><ParticipantIndex>auto</ParticipantIndex>'
                '<MaxAutoParticipantIndex>200</MaxAutoParticipantIndex>'
                '</Discovery></Domain></CycloneDDS>'
            ),
        ),
        DeclareLaunchArgument(
            "data_root",
            default_value="/home/ubuntu/hg_dagger_data",
            description="Explicit writable root for sidecar and LeRobot datasets.",
        ),
        DeclareLaunchArgument(
            "robot_id", default_value="328",
            description="Robot numeric ID used to build vendor topic suffixes.",
        ),
        DeclareLaunchArgument("task_name", default_value="hg_dagger_task"),
        DeclareLaunchArgument(
            "start_groot_bridge", default_value="false",
            description="Start the authenticated THOR GR00T N1.7 policy bridge.",
        ),
        DeclareLaunchArgument(
            "groot_server_url", default_value="http://192.168.8.179:8777"),
        DeclareLaunchArgument(
            "groot_token_file",
            default_value="/home/ubuntu/.config/autolife_hg_dagger/groot_server.token",
        ),
        DeclareLaunchArgument("groot_task", default_value="Pick the laundry bag."),
        DeclareLaunchArgument(
            "groot_max_inference_latency_sec", default_value="1.0",
            description="Fail over to VR when one GR00T start/infer/retry reaches this latency; <=0 disables.",
        ),
        DeclareLaunchArgument(
            "policy_timeout_sec", default_value="0.25",
            description="Supervisor policy heartbeat timeout; use a larger value for VR-only commissioning.",
        ),
        SetEnvironmentVariable("ROS_DOMAIN_ID", ros_domain_id),
        SetEnvironmentVariable("ROBOT_ID", robot_id),
        SetEnvironmentVariable("ROS_AUTOMATIC_DISCOVERY_RANGE", "SUBNET"),
        SetEnvironmentVariable("RMW_IMPLEMENTATION", rmw_implementation),
        SetEnvironmentVariable("CYCLONEDDS_URI", cyclonedds_uri),
        supervisor,
        controller,
        mapper,
        groot_bridge,
        web_bridge,
    ])
