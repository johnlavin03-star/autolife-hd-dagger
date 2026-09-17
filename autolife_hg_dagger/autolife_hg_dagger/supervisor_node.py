"""ROS 2 HG-DAgger supervisor.

This node is the sole authority selector upstream of the existing V4 controller.
It never publishes vendor hardware topics.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict, Optional

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

from .core import (
    AuthorityStateMachine,
    Mode,
    controller_hold_confirmed,
    expert_timeout_requires_estop,
    grip_snapshot,
    left_y_snapshot,
    policy_to_controller,
    stamped_envelope,
    xa_snapshot,
)
from .recorder import CollectorFifo, TraceWriter


def compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class HgDaggerSupervisor(Node):
    def __init__(self) -> None:
        super().__init__("hg_dagger_supervisor")
        self._declare_parameters()
        self._lock = threading.RLock()
        self._machine = AuthorityStateMachine()
        self._session_id = ""
        self._intervention_id = ""
        self._sequence: Dict[str, int] = {}
        self._controller_status: Dict[str, Any] = {}
        self._mapper_status: Dict[str, Any] = {}
        self._policy_status: Dict[str, Any] = {}
        self._last_policy_status_monotonic_ns = 0
        self._latest_grippers = [0.0, 0.0]
        self._latest_action: Optional[list[float]] = None
        self._last_policy_monotonic_ns = 0
        self._last_expert_monotonic_ns = 0
        self._last_vendor_command_monotonic_ns = 0
        self._last_vendor_command_wall_ns = 0
        self._hold_sent_monotonic_ns = 0
        self._depth_next = bool(self.get_parameter("default_depth_enabled").value)
        self._active_depth = False
        self._y_pressed = False
        self._y_press_ns = 0
        self._grips = (False, False)
        self._expert_command_count = 0
        self._intervention_reset_used = False
        self._intervention_failure_context_valid = True
        self._recorder_active = False
        self._release_gate_started_ns = 0
        self._expert_command_pending = False
        self._expert_forwarded_monotonic_ns = 0
        self._policy_warmup_started_ns = 0
        self._policy_warmup_count = 0
        self._policy_warmup_last_action: Optional[list[float]] = None
        self._policy_warmup_reference_jump_deg = 0.0
        self._policy_warmup_rejection = ""
        self._last_failure_reason = ""
        # Atomic collector episodes can encode concurrently.  Keep a count for
        # shutdown/re-enable visibility, but never block another intervention
        # inside the same enabled session merely because an earlier episode is
        # still being packed.
        self._collector_finalize_pending = 0
        self._reset_chord_started_ns = 0
        self._reset_chord_consumed = False
        self._dagger_reset_pending = False
        self._dagger_reset_active = False
        self._dagger_reset_seen_active = False
        self._dagger_reset_started_ns = 0

        latest = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
        )
        reliable = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )

        self._selected_joint_pub = self.create_publisher(
            String, "/hg_dagger/selected/joint_target", reliable)
        self._selected_eef_pub = self.create_publisher(
            String, "/hg_dagger/selected/eef_target", latest)
        self._selected_gripper_pub = self.create_publisher(
            String, "/hg_dagger/selected/gripper_target", latest)
        self._selected_release_pub = self.create_publisher(
            String, "/hg_dagger/selected/release_hold", reliable)
        self._state_pub = self.create_publisher(String, "/hg_dagger/control_state", reliable)
        self._web_status_pub = self.create_publisher(
            String, "/hg_dagger/web_teleop_status", reliable)
        self._event_pub = self.create_publisher(String, "/hg_dagger/events", reliable)
        self._timed_vr_pub = self.create_publisher(String, "/hg_dagger/timing/vr_input", latest)
        self._timed_eef_pub = self.create_publisher(String, "/hg_dagger/timing/expert_eef", latest)
        self._timed_action_pub = self.create_publisher(String, "/hg_dagger/collector/action", reliable)
        self._collector_arm_pub = self.create_publisher(
            String, "/hg_dagger/collector/arm_action", reliable)
        self._collector_gripper_pub = self.create_publisher(
            String, "/hg_dagger/collector/gripper_action", reliable)
        self._policy_forward_ack_pub = self.create_publisher(
            String, "/hg_dagger/policy_forward_ack", reliable)

        self.create_subscription(
            String, "/openarmx_teleop_vr_306_v4/vr_input", self._on_vr_input, latest)
        self.create_subscription(
            String, "/hg_dagger/policy_action", self._on_policy_action, latest)
        self.create_subscription(
            String, "/hg_dagger/policy_status", self._on_policy_status, reliable)
        self.create_subscription(
            String, "/hg_dagger/expert/eef_target", self._on_expert_eef, latest)
        self.create_subscription(
            String, "/hg_dagger/expert/gripper_target", self._on_expert_gripper, latest)
        self.create_subscription(
            String, "/hg_dagger/expert/release_hold", self._on_expert_release, reliable)
        self.create_subscription(
            String, "/openarmx_teleop_vr_306_v4/status", self._on_controller_status, reliable)
        self.create_subscription(
            String,
            "/openarmx_teleop_vr_306_v4/teleop_status",
            self._on_mapper_status,
            reliable,
        )
        self.create_subscription(
            String,
            str(self.get_parameter("vendor_joint_command_topic").value),
            self._on_vendor_joint_command,
            latest,
        )
        self.create_subscription(
            String,
            str(self.get_parameter("vendor_gripper_command_topic").value),
            self._on_vendor_gripper_command,
            latest,
        )

        service_group = ReentrantCallbackGroup()
        self._controller_enable = self.create_client(
            SetBool,
            "/hg_dagger/controller/set_hardware_enabled",
            callback_group=service_group,
        )
        self._controller_quick_reset = self.create_client(
            Trigger,
            "/hg_dagger/controller/quick_reset",
            callback_group=service_group,
        )
        self.create_service(
            SetBool,
            "/hg_dagger/set_session_enabled",
            self._on_set_session_enabled,
            callback_group=service_group,
        )
        self.create_service(Trigger, "/hg_dagger/request_failure", self._on_request_failure)
        self.create_service(Trigger, "/hg_dagger/request_takeover", self._on_request_takeover)
        self.create_service(Trigger, "/hg_dagger/request_resume", self._on_request_resume)

        self._trace = TraceWriter(
            str(self.get_parameter("trace_root").value),
            float(self.get_parameter("pre_failure_ring_seconds").value),
        )
        self._collector = CollectorFifo(
            str(self.get_parameter("rgb_collector_fifo").value),
            str(self.get_parameter("rgbd_collector_fifo").value),
        )
        self.create_timer(0.02, self._control_tick)
        self.create_timer(1.0 / float(self.get_parameter("collector_action_rate_hz").value), self._publish_collector_action)
        self.create_timer(0.2, self._publish_state)
        self.get_logger().info(
            "HG-DAgger supervisor ready; controller vendor topics are read-only")

    def _declare_parameters(self) -> None:
        defaults = {
            "default_depth_enabled": True,
            "rgbd_only": True,
            "y_long_press_seconds": 1.2,
            "xa_reset_hold_seconds": 1.0,
            "xa_reset_timeout_sec": 25.0,
            "hold_confirmation_timeout_sec": 1.0,
            "policy_timeout_sec": 0.25,
            "policy_status_timeout_sec": 1.0,
            "expert_timeout_sec": 0.35,
            "policy_warmup_min_actions": 6,
            "policy_warmup_min_duration_sec": 0.20,
            "policy_warmup_timeout_sec": 12.0,
            "policy_resume_max_arm_jump_deg": 8.0,
            "policy_resume_max_gripper_jump_deg": 80.0,
            "minimum_episode_frames": 15,
            "collector_action_rate_hz": 30.0,
            "pre_failure_ring_seconds": 5.0,
            "trace_root": "/home/ubuntu/nas14/hg_dagger_traces",
            "rgb_collector_fifo": "/tmp/hg_dagger_rgb_collector.fifo",
            "rgbd_collector_fifo": "/tmp/hg_dagger_rgbd_collector.fifo",
            "collector_start_command": "start",
            "collector_save_command": "save",
            "collector_discard_command": "discard",
            "collector_command_timeout_sec": 15.0,
            "collector_finalize_timeout_sec": 600.0,
            "vendor_joint_command_topic": "/topic_arm_whole_body_target_joints_position_0_328",
            "vendor_gripper_command_topic": "/topic_arm_gripper_target_joints_position_0_328",
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)

    def _next_sequence(self, source: str) -> int:
        value = self._sequence.get(source, 0) + 1
        self._sequence[source] = value
        return value

    @staticmethod
    def _parse(message: String) -> Dict[str, Any]:
        data = json.loads(message.data)
        if not isinstance(data, dict):
            raise ValueError("payload must be a JSON object")
        return data

    def _timestamp(self, source: str, payload: Dict[str, Any], valid: bool = True, reason: str = "") -> Dict[str, Any]:
        source_timestamp_ns = payload.get("timestamp_ns")
        if source_timestamp_ns is None and isinstance(payload.get("timestamp"), (int, float)):
            # Browser Date.now() is milliseconds since epoch.
            source_timestamp_ns = int(float(payload["timestamp"]) * 1_000_000)
        try:
            source_timestamp_ns = None if source_timestamp_ns is None else int(source_timestamp_ns)
        except (TypeError, ValueError):
            source_timestamp_ns = None
        return stamped_envelope(
            source=source,
            sequence=int(payload.get("sequence", self._next_sequence(source))),
            source_timestamp_ns=source_timestamp_ns,
            receive_wall_ns=time.time_ns(),
            receive_monotonic_ns=time.monotonic_ns(),
            authority_epoch=self._machine.authority_epoch,
            valid=valid,
            reason=reason,
            payload=payload,
        )

    def _event(self, kind: str, reason: str, **extra: Any) -> None:
        record = {
            "kind": kind,
            "reason": reason,
            "wall_ns": time.time_ns(),
            "monotonic_ns": time.monotonic_ns(),
            "mode": self._machine.mode.value,
            "authority_epoch": self._machine.authority_epoch,
            "session_id": self._session_id,
            "intervention_id": self._intervention_id,
            **extra,
        }
        self._event_pub.publish(String(data=compact(record)))
        self._trace.append_ring(record)
        self._trace.write("events", record)

    def _publish_release_hold(self, reason: str) -> None:
        payload = {
            "sides": ["left", "right"],
            "source": "hg_dagger_authority_barrier",
            "authority_epoch": self._machine.authority_epoch,
            "reason": reason,
        }
        self._selected_release_pub.publish(String(data=compact(payload)))
        self._hold_sent_monotonic_ns = time.monotonic_ns()

    def _begin_failure_hold(self, reason: str) -> None:
        self._last_failure_reason = str(reason)
        transition = self._machine.failure(reason)
        self._release_gate_started_ns = 0
        self._expert_command_pending = False
        self._publish_release_hold(reason)
        self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)
        # Freeze the rolling pre-failure window at the failure boundary. Waiting
        # until the operator completes release/re-grip would let that window roll
        # forward and could evict the actual failure context.
        try:
            self._start_intervention_locked()
        except Exception as exc:
            transition = self._machine.estop(
                f"trace/collector start failed: {exc}"
            )
            self._publish_release_hold("recording setup failed")
            self._event(
                "recording_setup_failed",
                str(exc),
                old=transition.old.value,
                new=transition.new.value,
            )

    def _update_reset_gesture_locked(self, chord: bool, now_ns: int) -> None:
        if not chord:
            self._reset_chord_started_ns = 0
            self._reset_chord_consumed = False
            return
        if (
            self._reset_chord_consumed
            or self._dagger_reset_pending
            or self._dagger_reset_active
        ):
            return
        allowed = (
            not any(self._grips)
            and self._machine.mode in (
                Mode.EXPERT_RELEASE_REQUIRED,
                Mode.EXPERT_READY,
                Mode.EXPERT_ACTIVE,
            )
        )
        if not allowed:
            self._reset_chord_started_ns = 0
            return
        if not self._reset_chord_started_ns:
            self._reset_chord_started_ns = now_ns
            return
        duration = (now_ns - self._reset_chord_started_ns) / 1e9
        if duration < float(self.get_parameter("xa_reset_hold_seconds").value):
            return
        self._reset_chord_consumed = True
        self._reset_chord_started_ns = 0
        self._request_quick_reset_locked()

    def _request_quick_reset_locked(self) -> None:
        if not self._controller_quick_reset.service_is_ready():
            self._event(
                "quick_reset_rejected", "controller quick-reset service is unavailable"
            )
            return
        self._publish_release_hold("X+A quick reset authority barrier")
        if self._intervention_id:
            self._intervention_reset_used = True
            self._event(
                "intervention_invalidated",
                "quick reset occurred inside intervention; episode will be discarded",
            )
        self._dagger_reset_pending = True
        future = self._controller_quick_reset.call_async(Trigger.Request())

        def finished(done: Any) -> None:
            with self._lock:
                self._dagger_reset_pending = False
                try:
                    response = done.result()
                except Exception as exc:
                    self._event("quick_reset_rejected", f"quick reset call failed: {exc}")
                    return
                if not bool(response.success):
                    self._event("quick_reset_rejected", str(response.message))
                    return
                self._dagger_reset_active = True
                self._dagger_reset_seen_active = bool(
                    self._controller_status.get("quick_reset_active", False)
                )
                self._dagger_reset_started_ns = time.monotonic_ns()
                self._expert_command_pending = False
                self._last_expert_monotonic_ns = 0
                self._event("quick_reset_started", str(response.message))

        future.add_done_callback(finished)

    def _complete_quick_reset_locked(self) -> None:
        self._dagger_reset_active = False
        self._dagger_reset_seen_active = False
        self._dagger_reset_started_ns = 0
        self._expert_command_pending = False
        self._last_expert_monotonic_ns = 0
        try:
            transition = self._machine.reset_to_expert_ready()
        except ValueError as exc:
            self._event("quick_reset_completed", str(exc))
            return
        self._publish_release_hold("quick reset completed; Grip re-anchor required")
        self._event(
            "transition", transition.reason,
            old=transition.old.value, new=transition.new.value,
        )

    def _on_vr_input(self, message: String) -> None:
        try:
            packet = self._parse(message)
        except Exception as exc:
            self._event("invalid_vr_input", str(exc))
            return
        envelope = self._timestamp("vr_input", packet)
        self._timed_vr_pub.publish(String(data=compact(envelope)))
        self._trace.append_ring({"wall_ns": envelope["robot_receive_timestamp_ns"], "stream": "vr", "data": envelope})
        self._trace.write("vr", envelope)

        with self._lock:
            previous_grips = self._grips
            if "leftController" in packet or "rightController" in packet:
                self._grips = grip_snapshot(packet)
            grip_rising = any(now and not old for now, old in zip(self._grips, previous_grips))
            if grip_rising and self._machine.mode == Mode.POLICY_ACTIVE:
                self._begin_failure_hold("operator Grip requested takeover")
            elif (
                self._machine.mode == Mode.EXPERT_RELEASE_REQUIRED
                and not any(self._grips)
                and envelope["robot_receive_monotonic_ns"]
                > self._release_gate_started_ns
            ):
                transition = self._machine.expert_release_confirmed()
                self._event(
                    "transition",
                    transition.reason,
                    old=transition.old.value,
                    new=transition.new.value,
                )
            elif grip_rising and self._machine.mode == Mode.EXPERT_READY:
                self._event(
                    "expert_regrip_detected",
                    "second Grip press accepted; waiting for re-anchored expert target",
                )
            elif (
                grip_rising
                and self._machine.mode == Mode.EXPERT_ACTIVE
                and self._last_expert_monotonic_ns <= 0
            ):
                # Arm the watchdog while the mapper re-anchors.  A fresh EEF
                # target replaces this timestamp immediately; no target while
                # Grip remains held fails closed after expert_timeout_sec.
                self._last_expert_monotonic_ns = envelope[
                    "robot_receive_monotonic_ns"
                ]
                self._event(
                    "expert_regrip_detected",
                    "Grip pressed after expert pause; waiting for fresh target",
                )

            self._update_reset_gesture_locked(
                xa_snapshot(packet), envelope["robot_receive_monotonic_ns"]
            )

            event_type = packet.get("type")
            if str(packet.get("hand", "")).lower() == "left" and str(packet.get("button", "")).upper() == "Y" and event_type in ("button_press", "button_release"):
                now_y = bool(packet.get("pressed", event_type == "button_press"))
            else:
                now_y = left_y_snapshot(packet)
            now_ns = time.monotonic_ns()
            if now_y and not self._y_pressed:
                self._y_pressed = True
                self._y_press_ns = now_ns
            elif not now_y and self._y_pressed:
                duration = (now_ns - self._y_press_ns) / 1e9
                self._y_pressed = False
                if duration >= float(self.get_parameter("y_long_press_seconds").value):
                    if not any(self._grips) and self._machine.mode in (
                        Mode.EXPERT_ACTIVE,
                        Mode.EXPERT_READY,
                        Mode.EXPERT_RELEASE_REQUIRED,
                    ):
                        self._resume_locked("long Y with both Grips released")
                    else:
                        self._event("gesture_rejected", "long Y requires expert mode and both Grips released")
                elif self._machine.mode not in (Mode.EXPERT_ACTIVE, Mode.EXPERT_READY):
                    if bool(self.get_parameter("rgbd_only").value):
                        self._depth_next = True
                        self._event(
                            "depth_mode_locked",
                            "RGBD-only collection is configured; short Y ignored",
                            depth_next=True,
                        )
                    else:
                        self._depth_next = not self._depth_next
                        self._event("depth_mode_changed", "short Y toggled next intervention", depth_next=self._depth_next)

    def _on_policy_action(self, message: String) -> None:
        try:
            payload = self._parse(message)
            controller, action = policy_to_controller(payload)
        except Exception as exc:
            self._event("policy_action_rejected", str(exc))
            return
        with self._lock:
            envelope = self._timestamp("policy", payload)
            self._trace.append_ring({"wall_ns": envelope["robot_receive_timestamp_ns"], "stream": "policy", "data": envelope})
            self._trace.write("policy", envelope)
            self._last_policy_monotonic_ns = time.monotonic_ns()
            if self._machine.mode == Mode.POLICY_WARMUP:
                reference = self._latest_action
                if reference is None:
                    self._policy_warmup_count = 0
                    self._policy_warmup_rejection = "waiting for controller reference"
                    return
                arm_jump = max(
                    abs(candidate - current)
                    for candidate, current in zip(action[:14], reference[:14])
                )
                gripper_jump = max(
                    abs(candidate - current)
                    for candidate, current in zip(action[14:16], reference[14:16])
                )
                self._policy_warmup_reference_jump_deg = max(
                    arm_jump, gripper_jump
                )
                if self._policy_warmup_last_action is not None:
                    consecutive_jump = max(
                        abs(candidate - previous)
                        for candidate, previous in zip(
                            action[:14], self._policy_warmup_last_action[:14]
                        )
                    )
                    if consecutive_jump > float(self.get_parameter(
                            "policy_resume_max_arm_jump_deg").value):
                        self._policy_warmup_count = 0
                        self._policy_warmup_last_action = action
                        self._policy_warmup_rejection = (
                            f"unstable policy stream: {consecutive_jump:.2f} deg step"
                        )
                        return
                self._policy_warmup_last_action = action
                self._policy_warmup_count += 1
                self._policy_warmup_rejection = ""
                elapsed = (
                    time.monotonic_ns() - self._policy_warmup_started_ns
                ) / 1e9
                if (
                    self._policy_warmup_count < int(self.get_parameter(
                        "policy_warmup_min_actions").value)
                    or elapsed < float(self.get_parameter(
                        "policy_warmup_min_duration_sec").value)
                ):
                    return
                transition = self._machine.warmup_complete()
                self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)
                # The warmup proposal was explicitly marked unexecuted. Force
                # the bridge to discard it and open a fresh remote session from
                # the latest measured state before any policy command is sent.
                return
            if self._machine.mode != Mode.POLICY_ACTIVE:
                return
            controller.update({
                "authority_epoch": self._machine.authority_epoch,
                "source_sequence": envelope["source_sequence"],
                "source_timestamp_ns": envelope["source_timestamp_ns"],
                "robot_receive_timestamp_ns": envelope["robot_receive_timestamp_ns"],
            })
            self._selected_joint_pub.publish(String(data=compact(controller)))
            self._selected_gripper_pub.publish(String(data=compact({
                "left_gripper_target_joints_position": [action[14]],
                "right_gripper_target_joints_position": [action[15]],
                "source": "hg_dagger_policy",
                "authority_epoch": self._machine.authority_epoch,
            })))
            self._latest_grippers = action[14:16]
            if all(key in payload for key in (
                "proposal_id", "chunk_step", "bridge_generation"
            )):
                self._policy_forward_ack_pub.publish(String(data=compact({
                    "proposal_id": str(payload["proposal_id"]),
                    "chunk_step": int(payload["chunk_step"]),
                    "bridge_generation": int(payload["bridge_generation"]),
                    "authority_epoch": self._machine.authority_epoch,
                    "robot_forward_timestamp_ns": time.time_ns(),
                })))

    def _on_policy_status(self, message: String) -> None:
        try:
            status = self._parse(message)
        except Exception as exc:
            self._event("invalid_policy_status", str(exc))
            return
        with self._lock:
            self._policy_status = status
            self._last_policy_status_monotonic_ns = time.monotonic_ns()
            if (
                str(status.get("phase")) == "failed"
                and self._machine.mode in (Mode.POLICY_ACTIVE, Mode.POLICY_WARMUP)
            ):
                self._begin_failure_hold(str(status.get("detail", "VLA bridge failure")))

    def _on_expert_eef(self, message: String) -> None:
        try:
            payload = self._parse(message)
        except Exception as exc:
            self._event("expert_eef_rejected", str(exc))
            return
        with self._lock:
            envelope = self._timestamp("expert_eef", payload)
            self._timed_eef_pub.publish(String(data=compact(envelope)))
            self._trace.write("vr", envelope)
            has_pose = any(
                f"pos_{side}_in_robot" in payload or f"quat_{side}_in_robot" in payload
                for side in ("left", "right")
            )
            if (
                self._dagger_reset_pending
                or self._dagger_reset_active
                or self._machine.mode not in (Mode.EXPERT_READY, Mode.EXPERT_ACTIVE)
                or not has_pose
            ):
                return
            if self._machine.mode == Mode.EXPERT_READY:
                if not any(self._grips):
                    return
                if not self._intervention_id or not self._recorder_active:
                    transition = self._machine.estop(
                        "expert command rejected because recorder is not active"
                    )
                    self._publish_release_hold("recording setup missing")
                    self._event(
                        "recording_setup_missing",
                        transition.reason,
                        old=transition.old.value,
                        new=transition.new.value,
                    )
                    return
            payload.update({
                "authority_epoch": self._machine.authority_epoch,
                "robot_receive_timestamp_ns": envelope["robot_receive_timestamp_ns"],
            })
            self._selected_eef_pub.publish(String(data=compact(payload)))
            self._last_expert_monotonic_ns = time.monotonic_ns()
            self._expert_command_pending = True
            self._expert_forwarded_monotonic_ns = self._last_expert_monotonic_ns

    def _on_expert_gripper(self, message: String) -> None:
        try:
            payload = self._parse(message)
        except Exception as exc:
            self._event("expert_gripper_rejected", str(exc))
            return
        with self._lock:
            if (
                self._dagger_reset_pending
                or self._dagger_reset_active
                or self._machine.mode != Mode.EXPERT_ACTIVE
            ):
                return
            self._selected_gripper_pub.publish(message)
            for index, side in enumerate(("left", "right")):
                value = payload.get(f"{side}_gripper_target_joints_position")
                if isinstance(value, list) and value:
                    try:
                        self._latest_grippers[index] = float(value[0])
                    except (TypeError, ValueError):
                        pass

    def _on_expert_release(self, message: String) -> None:
        with self._lock:
            if self._machine.mode in (Mode.EXPERT_ACTIVE, Mode.EXPERT_READY):
                self._selected_release_pub.publish(message)

    def _on_controller_status(self, message: String) -> None:
        try:
            status = self._parse(message)
        except Exception:
            return
        with self._lock:
            self._controller_status = status
            if self._dagger_reset_active:
                backend_active = bool(status.get("quick_reset_active", False))
                if backend_active:
                    self._dagger_reset_seen_active = True
                elif self._dagger_reset_seen_active:
                    self._complete_quick_reset_locked()
            if bool(status.get("emergency_stop_latched")) or status.get("state") == "FAULT":
                if self._machine.mode != Mode.ESTOP:
                    transition = self._machine.estop(str(status.get("reason", "controller fault")))
                    self._finish_intervention_locked(False, transition.reason)
                    self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)
                return
            if (
                self._machine.mode == Mode.FAILURE_HOLD
                and self._recorder_active
                and controller_hold_confirmed(status)
            ):
                transition = self._machine.hold_confirmed()
                self._release_gate_started_ns = time.monotonic_ns()
                self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)
            elif (
                self._machine.mode == Mode.EXPERT_READY
                and self._expert_command_pending
                and status.get("state") == "ARMED"
                and status.get("target_source") == "cartesian_ik"
            ):
                transition = self._machine.expert_first_command()
                self._expert_command_pending = False
                self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)

    def _on_mapper_status(self, message: String) -> None:
        try:
            status = self._parse(message)
        except Exception:
            return
        with self._lock:
            self._mapper_status = status

    def _on_vendor_joint_command(self, message: String) -> None:
        # Read-only observation used to reconstruct the exact command label.
        try:
            payload = self._parse(message)
        except Exception:
            return
        with self._lock:
            left = payload.get("left_arm_target_joints_position")
            right = payload.get("right_arm_target_joints_position")
            neck = payload.get("neck_target_joints_position")
            waist = payload.get("leg_waist_target_joints_position")
            if (
                isinstance(left, list) and len(left) == 7
                and isinstance(right, list) and len(right) == 7
                and isinstance(neck, list) and len(neck) == 3
                and isinstance(waist, list) and len(waist) == 4
            ):
                try:
                    self._latest_action = [float(v) for v in (
                        left + right + list(self._latest_grippers) + neck + waist[2:4]
                    )]
                    self._last_vendor_command_monotonic_ns = time.monotonic_ns()
                    self._last_vendor_command_wall_ns = time.time_ns()
                except (TypeError, ValueError):
                    pass

    def _on_vendor_gripper_command(self, message: String) -> None:
        try:
            payload = self._parse(message)
        except Exception:
            return
        with self._lock:
            for index, side in enumerate(("left", "right")):
                value = payload.get(f"{side}_gripper_target_joints_position")
                if isinstance(value, list) and value:
                    try:
                        self._latest_grippers[index] = float(value[0])
                    except (TypeError, ValueError):
                        pass
            if self._latest_action is not None:
                self._latest_action[14:16] = self._latest_grippers
                self._last_vendor_command_monotonic_ns = time.monotonic_ns()
                self._last_vendor_command_wall_ns = time.time_ns()

    def _start_intervention_locked(
        self, *, failure_context_valid: bool = True
    ) -> None:
        self._intervention_id = f"int-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self._active_depth = self._depth_next
        self._expert_command_count = 0
        self._intervention_reset_used = False
        self._intervention_failure_context_valid = bool(failure_context_valid)
        self._trace.start(
            self._session_id,
            self._intervention_id,
            self._active_depth,
            {
                "schema": "groot-n1.7-21",
                "action_order": [
                    "left_arm_7", "right_arm_7", "left_gripper", "right_gripper",
                    "neck_roll_pitch_yaw", "waist_pitch_yaw",
                ],
                "collector_fifo": self._collector.paths[self._active_depth],
                "failure_context_valid": self._intervention_failure_context_valid,
            },
        )
        command = str(self.get_parameter("collector_start_command").value)
        result = self._collector.command_and_wait(
            self._active_depth,
            command,
            float(self.get_parameter("collector_command_timeout_sec").value),
        )
        if not result.acknowledged or not result.success:
            self._trace.finish(
                "collector_start_failed",
                0,
                result.message,
                result.as_dict(),
            )
            self._intervention_id = ""
            raise RuntimeError(result.message)
        self._recorder_active = True
        self._event(
            "intervention_started",
            "collector acknowledged start before first expert command",
            depth=self._active_depth,
            collector_connected=True,
            collector_episode_index=result.episode_index,
            collector_request_id=result.request_id,
            pre_failure_frames=result.frames,
        )

    def _finish_intervention_locked(self, requested_save: bool, reason: str) -> None:
        if not self._intervention_id:
            return
        minimum = int(self.get_parameter("minimum_episode_frames").value)
        save = (
            requested_save
            and self._expert_command_count >= minimum
            and not self._intervention_reset_used
            and self._intervention_failure_context_valid
        )
        command_name = "collector_save_command" if save else "collector_discard_command"
        result = None
        if save and self._recorder_active:
            request_id = uuid.uuid4().hex
            command = str(self.get_parameter(command_name).value)
            if self._collector.command(self._active_depth, command, request_id):
                pending_result = {
                    "acknowledged": False,
                    "success": False,
                    "event": command,
                    "request_id": request_id,
                    "message": "collector save is finalizing in background",
                    "episode_index": None,
                    "frames": 0,
                }
                manifest_path = self._trace.finish(
                    "collector_save_pending",
                    self._expert_command_count,
                    reason,
                    pending_result,
                )
                self._collector_finalize_pending += 1
                self._intervention_id = ""
                self._recorder_active = False
                threading.Thread(
                    target=self._finalize_collector_save,
                    args=(
                        self._active_depth, command, request_id, manifest_path,
                        self._expert_command_count, reason,
                    ),
                    name="hg-dagger-collector-finalize",
                    daemon=True,
                ).start()
                self._event(
                    "intervention_finalize_started",
                    "policy recovery may continue while RGBD encoding finishes",
                    collector_request_id=request_id,
                )
                return
            result = self._collector.wait_for_result(
                self._active_depth, command, request_id, 0.1
            )
        elif self._recorder_active:
            result = self._collector.command_and_wait(
                self._active_depth,
                str(self.get_parameter(command_name).value),
                float(self.get_parameter("collector_command_timeout_sec").value),
            )
        collector_ok = bool(
            result is not None and result.acknowledged and result.success
        )
        if save and collector_ok and result.event == "save":
            status = "saved"
        elif not save and collector_ok:
            status = "discarded"
        elif save and collector_ok and result.event == "discard":
            status = "discarded_invalid"
        else:
            status = "collector_command_failed"
        self._trace.finish(
            status,
            self._expert_command_count,
            reason,
            None if result is None else result.as_dict(),
        )
        self._event(
            "intervention_finished",
            reason,
            status=status,
            frame_count=self._expert_command_count,
            collector_acknowledged=bool(result and result.acknowledged),
            collector_success=collector_ok,
            collector_episode_index=(
                None if result is None else result.episode_index
            ),
        )
        self._intervention_id = ""
        self._recorder_active = False

    def _finalize_collector_save(
        self, depth: bool, command: str, request_id: str, manifest_path: Any,
        frame_count: int, reason: str,
    ) -> None:
        result = self._collector.wait_for_result(
            depth,
            command,
            request_id,
            float(self.get_parameter("collector_finalize_timeout_sec").value),
        )
        collector_ok = result.acknowledged and result.success
        if collector_ok and result.event == "save":
            status = "saved"
        elif collector_ok and result.event == "discard":
            status = "discarded_invalid"
        else:
            status = "collector_command_failed"
        if manifest_path is not None:
            try:
                TraceWriter.update_finished_manifest(
                    manifest_path, status, result.as_dict()
                )
            except Exception as exc:
                self.get_logger().error(
                    f"failed to update finalized intervention manifest: {exc}"
                )
        with self._lock:
            self._collector_finalize_pending = max(
                0, self._collector_finalize_pending - 1
            )
            self._event(
                "intervention_finished",
                reason,
                status=status,
                frame_count=frame_count,
                collector_acknowledged=result.acknowledged,
                collector_success=collector_ok,
                collector_episode_index=result.episode_index,
                collector_request_id=request_id,
            )
            if (
                self._machine.mode == Mode.FAILURE_HOLD
                and not self._intervention_id
            ):
                try:
                    self._start_intervention_locked(failure_context_valid=False)
                    self._event(
                        "intervention_invalidated",
                        "takeover occurred while the prior episode was encoding; "
                        "the original pre-failure camera window is unavailable",
                    )
                except Exception as exc:
                    transition = self._machine.estop(
                        f"deferred trace/collector start failed: {exc}"
                    )
                    self._publish_release_hold("recording setup failed")
                    self._event(
                        "recording_setup_failed", str(exc),
                        old=transition.old.value, new=transition.new.value,
                    )
                    return
                if controller_hold_confirmed(self._controller_status):
                    transition = self._machine.hold_confirmed()
                    self._release_gate_started_ns = time.monotonic_ns()
                    self._event(
                        "transition", transition.reason,
                        old=transition.old.value, new=transition.new.value,
                    )

    def _resume_locked(self, reason: str) -> None:
        transition = self._machine.resume()
        self._finish_intervention_locked(True, reason)
        self._publish_release_hold("policy warmup barrier")
        self._policy_warmup_started_ns = time.monotonic_ns()
        self._policy_warmup_count = 0
        self._policy_warmup_last_action = None
        self._policy_warmup_reference_jump_deg = 0.0
        self._policy_warmup_rejection = ""
        self._last_policy_monotonic_ns = self._policy_warmup_started_ns
        self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)

    def _control_tick(self) -> None:
        with self._lock:
            now_ns = time.monotonic_ns()
            if self._dagger_reset_active and self._dagger_reset_started_ns:
                timeout = float(self.get_parameter("xa_reset_timeout_sec").value)
                if (now_ns - self._dagger_reset_started_ns) / 1e9 > timeout:
                    self._dagger_reset_active = False
                    self._publish_release_hold("quick reset timed out")
                    transition = self._machine.estop("quick reset timed out")
                    self._event(
                        "transition", transition.reason,
                        old=transition.old.value, new=transition.new.value,
                    )
                    return
            if self._machine.mode == Mode.POLICY_WARMUP:
                warmup_timeout = float(self.get_parameter(
                    "policy_warmup_timeout_sec").value)
                if (
                    self._policy_warmup_started_ns
                    and (now_ns - self._policy_warmup_started_ns) / 1e9
                    > warmup_timeout
                ):
                    detail = self._policy_warmup_rejection or (
                        "no stable policy action stream"
                    )
                    self._begin_failure_hold(
                        f"policy warmup timed out: {detail}"
                    )
                    return
            if self._machine.mode == Mode.FAILURE_HOLD:
                timeout = float(self.get_parameter("hold_confirmation_timeout_sec").value)
                if self._hold_sent_monotonic_ns and (now_ns - self._hold_sent_monotonic_ns) / 1e9 > timeout:
                    self._publish_release_hold("repeat hold barrier until controller confirms")
            elif self._machine.mode == Mode.POLICY_ACTIVE and self._last_policy_monotonic_ns:
                timeout = float(self.get_parameter("policy_timeout_sec").value)
                if (now_ns - self._last_policy_monotonic_ns) / 1e9 > timeout:
                    status_age = (
                        float("inf") if not self._last_policy_status_monotonic_ns
                        else (now_ns - self._last_policy_status_monotonic_ns) / 1e9
                    )
                    waiting_phases = {
                        "connecting", "capturing", "inference", "retry", "holding",
                        "executing",
                    }
                    if (
                        status_age <= float(self.get_parameter(
                            "policy_status_timeout_sec").value)
                        and str(self._policy_status.get("phase")) in waiting_phases
                    ):
                        return
                    self._begin_failure_hold("policy action watchdog timeout")
            elif self._machine.mode == Mode.EXPERT_ACTIVE and self._last_expert_monotonic_ns:
                timeout = float(self.get_parameter("expert_timeout_sec").value)
                if (now_ns - self._last_expert_monotonic_ns) / 1e9 > timeout:
                    if expert_timeout_requires_estop(self._grips):
                        self._publish_release_hold("expert input watchdog timeout")
                        transition = self._machine.estop(
                            "expert input watchdog timeout while Grip held"
                        )
                        self._finish_intervention_locked(False, transition.reason)
                        self._event(
                            "transition",
                            transition.reason,
                            old=transition.old.value,
                            new=transition.new.value,
                        )
                    else:
                        self._publish_release_hold(
                            "expert paused with both Grips released"
                        )
                        self._last_expert_monotonic_ns = 0
                        self._event(
                            "expert_paused",
                            "both Grips released; holding measured pose until re-grip",
                        )

    def _publish_collector_action(self) -> None:
        with self._lock:
            if (
                self._machine.mode in (Mode.DISARMED, Mode.ESTOP)
                or self._latest_action is None
                or not self._session_id
            ):
                return
            maximum_age = float(self.get_parameter("expert_timeout_sec").value)
            if (
                self._last_vendor_command_monotonic_ns <= 0
                or (
                    time.monotonic_ns() - self._last_vendor_command_monotonic_ns
                ) / 1e9 > maximum_age
            ):
                return
            payload = {
                "action": list(self._latest_action),
                "units": "degrees",
                "source": "controller_vendor_command_observation",
                "source_timestamp_ns": self._last_vendor_command_wall_ns,
                "authority_epoch": self._machine.authority_epoch,
                "intervention_id": self._intervention_id,
                "control_mode": self._machine.mode.value,
            }
            self._timed_action_pub.publish(String(data=compact(payload)))
            self._collector_arm_pub.publish(String(data=compact({
                "left_arm_target_joints_position": list(self._latest_action[0:7]),
                "right_arm_target_joints_position": list(self._latest_action[7:14]),
                "neck_target_joints_position": list(self._latest_action[16:19]),
                # Collector upper-waist schema selects indices 2/3.
                "leg_waist_target_joints_position": [
                    0.0, 0.0, self._latest_action[19], self._latest_action[20]
                ],
                "source": "hg_dagger_expert_label_proxy",
                "timestamp_ns": payload["source_timestamp_ns"],
                "authority_epoch": self._machine.authority_epoch,
            })))
            self._collector_gripper_pub.publish(String(data=compact({
                "left_gripper_target_joints_position": [self._latest_action[14]],
                "right_gripper_target_joints_position": [self._latest_action[15]],
                "source": "hg_dagger_expert_label_proxy",
                "timestamp_ns": payload["source_timestamp_ns"],
                "authority_epoch": self._machine.authority_epoch,
            })))
            if self._machine.mode == Mode.EXPERT_ACTIVE and self._intervention_id:
                self._expert_command_count += 1

    def _publish_state(self) -> None:
        with self._lock:
            state = {
                "mode": self._machine.mode.value,
                "authority_epoch": self._machine.authority_epoch,
                "session_id": self._session_id,
                "intervention_id": self._intervention_id,
                "depth_next": self._depth_next,
                "active_depth": self._active_depth,
                "controller_state": self._controller_status.get("state"),
                "controller_reason": self._controller_status.get("reason"),
                "grips": {"left": self._grips[0], "right": self._grips[1]},
                "expert_paused": bool(
                    self._machine.mode == Mode.EXPERT_ACTIVE
                    and not any(self._grips)
                ),
                "policy_warmup": {
                    "accepted_actions": self._policy_warmup_count,
                    "required_actions": int(self.get_parameter(
                        "policy_warmup_min_actions").value),
                    "minimum_duration_sec": float(self.get_parameter(
                        "policy_warmup_min_duration_sec").value),
                    "reference_jump_deg": round(
                        self._policy_warmup_reference_jump_deg, 3
                    ),
                    "rejection": self._policy_warmup_rejection,
                },
                "collector_finalize_pending": bool(self._collector_finalize_pending),
                "collector_finalize_pending_count": self._collector_finalize_pending,
                "quick_reset": {
                    "pending": self._dagger_reset_pending,
                    "active": self._dagger_reset_active,
                    "seen_controller_active": self._dagger_reset_seen_active,
                },
                "policy_bridge": dict(self._policy_status),
                "failure_reason": self._last_failure_reason,
            }
            self._state_pub.publish(String(data=compact(state)))
            prompts = {
                Mode.POLICY_ACTIVE: "VLA 正在控制；按 Grip 请求人工接管",
                Mode.FAILURE_HOLD: (
                    "失败已触发："
                    + (self._last_failure_reason or "VLA/操作者请求")
                    + "；正在停止 VLA 并等待机器人保持"
                ),
                Mode.EXPERT_RELEASE_REQUIRED: "机器人已保持：请松开左右 Grip",
                Mode.EXPERT_READY: "已确认松开：请重新按 Grip，从当前位置接管",
                Mode.EXPERT_ACTIVE: (
                    "VR 专家接管中"
                    if any(self._grips)
                    else "VR 专家已暂停：按 Grip 从当前位置重新锚定并继续"
                ),
                Mode.POLICY_WARMUP: "VR 已交还；正在验证 VLA 连续输出",
                Mode.ESTOP: "HG-DAgger 已停止输出，请检查故障",
                Mode.DISARMED: "HG-DAgger 会话未使能",
            }
            web_status = dict(self._mapper_status)
            web_status["hg_dagger"] = {
                **state,
                "prompt": (
                    "机器人复位中；请保持双 Grip 松开"
                    if self._dagger_reset_pending or self._dagger_reset_active
                    else prompts[self._machine.mode]
                ),
                "haptic_token": self._machine.authority_epoch,
            }
            self._web_status_pub.publish(String(data=compact(web_status)))

    def _forward_enable(self, enabled: bool) -> tuple[bool, str]:
        timeout = 12.0 if enabled else 5.0
        if not self._controller_enable.wait_for_service(timeout_sec=2.0):
            return False, "controller enable service is unavailable"
        future = self._controller_enable.call_async(SetBool.Request(data=enabled))
        event = threading.Event()
        future.add_done_callback(lambda _: event.set())
        if not event.wait(timeout):
            return False, "controller enable request timed out"
        try:
            result = future.result()
        except Exception as exc:
            return False, f"controller enable request failed: {exc}"
        return bool(result.success), str(result.message)

    def _on_set_session_enabled(self, request: SetBool.Request, response: SetBool.Response) -> SetBool.Response:
        if request.data:
            with self._lock:
                if self._collector_finalize_pending:
                    response.success = False
                    response.message = (
                        "previous RGBD episode is still being finalized"
                    )
                    return response
                if self._machine.mode != Mode.DISARMED:
                    response.success = False
                    response.message = (
                        "session enable requires DISARMED; disable the current "
                        f"session first (mode={self._machine.mode.value})"
                    )
                    return response
            try:
                self._trace.prepare_root()
            except Exception as exc:
                response.success = False
                response.message = f"data path is unavailable: {exc}"
                return response
        success, message = self._forward_enable(bool(request.data))
        with self._lock:
            if success and request.data:
                self._session_id = f"session-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
                transition = self._machine.enable()
                self._last_policy_monotonic_ns = time.monotonic_ns()
                self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)
            elif success:
                self._finish_intervention_locked(False, "session disabled")
                transition = self._machine.disable()
                self._event("transition", transition.reason, old=transition.old.value, new=transition.new.value)
                self._session_id = ""
        response.success = success
        response.message = message
        return response

    def _on_request_failure(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        with self._lock:
            try:
                self._begin_failure_hold("VLA failure service request")
                response.success = True
                response.message = "failure hold requested"
            except ValueError as exc:
                response.success = False
                response.message = str(exc)
        return response

    def _on_request_takeover(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        with self._lock:
            try:
                if self._machine.mode == Mode.POLICY_ACTIVE:
                    self._begin_failure_hold("explicit takeover service request")
                elif self._machine.mode != Mode.EXPERT_READY:
                    raise ValueError(f"takeover is invalid in {self._machine.mode.value}")
                response.success = True
                response.message = "takeover pending expert target"
            except ValueError as exc:
                response.success = False
                response.message = str(exc)
        return response

    def _on_request_resume(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        with self._lock:
            try:
                self._resume_locked("explicit resume service request")
                response.success = True
                response.message = "policy warmup entered"
            except ValueError as exc:
                response.success = False
                response.message = str(exc)
        return response

    def shutdown_recording(self) -> None:
        """Close active sidecar metadata without ever claiming a save."""

        with self._lock:
            if not self._intervention_id:
                return
            result = None
            if self._recorder_active:
                result = self._collector.command_and_wait(
                    self._active_depth,
                    str(self.get_parameter("collector_discard_command").value),
                    min(
                        2.0,
                        float(self.get_parameter("collector_command_timeout_sec").value),
                    ),
                )
            collector_ok = bool(
                result is not None and result.acknowledged and result.success
            )
            status = "aborted_shutdown" if collector_ok else "collector_unavailable"
            self._trace.finish(
                status,
                self._expert_command_count,
                "supervisor shutdown",
                None if result is None else result.as_dict(),
            )
            self._intervention_id = ""
            self._recorder_active = False


def main(args=None) -> None:
    rclpy.init(args=args)
    node = HgDaggerSupervisor()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown_recording()
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
