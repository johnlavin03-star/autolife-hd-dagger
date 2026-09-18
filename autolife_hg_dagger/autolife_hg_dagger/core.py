"""ROS-independent state machine and payload helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Dict, Optional, Tuple


class Mode(str, Enum):
    DISARMED = "DISARMED"
    POLICY_STOPPED = "POLICY_STOPPED"
    POLICY_ACTIVE = "POLICY_ACTIVE"
    FAILURE_HOLD = "FAILURE_HOLD"
    EXPERT_RELEASE_REQUIRED = "EXPERT_RELEASE_REQUIRED"
    EXPERT_READY = "EXPERT_READY"
    EXPERT_ACTIVE = "EXPERT_ACTIVE"
    POLICY_WARMUP = "POLICY_WARMUP"
    ESTOP = "ESTOP"


@dataclass(frozen=True)
class Transition:
    old: Mode
    new: Mode
    reason: str
    authority_epoch: int


class AuthorityStateMachine:
    """Deterministic authority transitions with a monotonically increasing epoch."""

    def __init__(self) -> None:
        self.mode = Mode.DISARMED
        self.authority_epoch = 0

    def _move(self, new: Mode, reason: str) -> Transition:
        old = self.mode
        if new != old:
            self.mode = new
            self.authority_epoch += 1
        return Transition(old, self.mode, reason, self.authority_epoch)

    def enable(self) -> Transition:
        if self.mode == Mode.ESTOP:
            raise ValueError("cannot enable while ESTOP is latched")
        return self._move(
            Mode.POLICY_STOPPED,
            "session enabled; waiting for operator long-A policy start",
        )

    def start_policy(self) -> Transition:
        if self.mode != Mode.POLICY_STOPPED:
            raise ValueError(f"policy start is invalid in {self.mode.value}")
        return self._move(
            Mode.POLICY_WARMUP,
            "operator long-A requested policy start",
        )

    def stop_policy(self) -> Transition:
        if self.mode not in (Mode.POLICY_ACTIVE, Mode.POLICY_WARMUP):
            raise ValueError(f"policy stop is invalid in {self.mode.value}")
        return self._move(
            Mode.POLICY_STOPPED,
            "operator long-B stopped policy inference",
        )

    def disable(self) -> Transition:
        return self._move(Mode.DISARMED, "session disabled")

    def failure(self, reason: str = "VLA failure") -> Transition:
        if self.mode not in (Mode.POLICY_ACTIVE, Mode.POLICY_WARMUP):
            raise ValueError(f"failure is invalid in {self.mode.value}")
        return self._move(Mode.FAILURE_HOLD, reason)

    def hold_confirmed(self) -> Transition:
        if self.mode != Mode.FAILURE_HOLD:
            raise ValueError(f"hold confirmation is invalid in {self.mode.value}")
        return self._move(
            Mode.EXPERT_RELEASE_REQUIRED,
            "controller hold confirmed; release both Grips",
        )

    def expert_release_confirmed(self) -> Transition:
        if self.mode != Mode.EXPERT_RELEASE_REQUIRED:
            raise ValueError(
                f"expert release confirmation is invalid in {self.mode.value}"
            )
        return self._move(
            Mode.EXPERT_READY,
            "both Grips released; press Grip again to re-anchor",
        )

    def grip_takeover(self) -> Transition:
        if self.mode == Mode.POLICY_ACTIVE:
            return self._move(Mode.FAILURE_HOLD, "operator Grip requested takeover")
        if self.mode == Mode.EXPERT_READY:
            return self._move(Mode.EXPERT_ACTIVE, "expert re-anchor requested")
        raise ValueError(f"Grip takeover is invalid in {self.mode.value}")

    def expert_first_command(self) -> Transition:
        if self.mode != Mode.EXPERT_READY:
            raise ValueError(f"expert command is invalid in {self.mode.value}")
        return self._move(Mode.EXPERT_ACTIVE, "first expert command forwarded")

    def resume(self) -> Transition:
        if self.mode not in (
            Mode.EXPERT_ACTIVE,
            Mode.EXPERT_READY,
            Mode.EXPERT_RELEASE_REQUIRED,
        ):
            raise ValueError(f"resume is invalid in {self.mode.value}")
        return self._move(Mode.POLICY_WARMUP, "operator returned authority to policy")

    def warmup_complete(self) -> Transition:
        if self.mode != Mode.POLICY_WARMUP:
            raise ValueError(f"warmup completion is invalid in {self.mode.value}")
        return self._move(Mode.POLICY_ACTIVE, "fresh policy action accepted")

    def reset_to_expert_ready(self) -> Transition:
        if self.mode not in (
            Mode.EXPERT_RELEASE_REQUIRED,
            Mode.EXPERT_READY,
            Mode.EXPERT_ACTIVE,
        ):
            raise ValueError(f"expert reset is invalid in {self.mode.value}")
        return self._move(
            Mode.EXPERT_READY,
            "quick reset completed; press Grip to re-anchor",
        )

    def estop(self, reason: str) -> Transition:
        return self._move(Mode.ESTOP, reason)


def finite_vector(value: Any, length: int) -> Optional[list[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def policy_to_controller(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], list[float]]:
    """Validate a 16-D legacy or 21-D GR00T action and build controller JSON.

    Policy order: left arm 7, right arm 7, left/right gripper.
    Controller joint positions use degrees, matching the existing V4 controller.
    """
    action = finite_vector(payload.get("action"), 21)
    if action is None:
        action = finite_vector(payload.get("action"), 16)
    if action is None:
        raise ValueError("policy action must contain 16 or 21 finite values")
    units = str(payload.get("units", "degrees")).lower()
    if units not in ("degree", "degrees", "deg"):
        raise ValueError("policy action units must be degrees")
    controller = {
        "left_arm_target_joints_position": action[0:7],
        "right_arm_target_joints_position": action[7:14],
        "source": "hg_dagger_policy",
    }
    if len(action) == 21:
        measured_waist = finite_vector(payload.get("measured_leg_waist"), 4)
        if measured_waist is None:
            raise ValueError("21-D policy action requires measured_leg_waist q4")
        controller.update({
            "leg_waist_target_joints_position": [
                measured_waist[0], measured_waist[1], action[19], action[20]
            ],
            "neck_target_joints_position": action[16:19],
        })
    return controller, action


def both_grips_released(packet: Dict[str, Any]) -> bool:
    return not any(grip_snapshot(packet))


def grip_snapshot(packet: Dict[str, Any]) -> Tuple[bool, bool]:
    def active(key: str) -> bool:
        controller = packet.get(key)
        return bool(controller.get("gripActive", False)) if isinstance(controller, dict) else False

    return active("leftController"), active("rightController")


def left_y_snapshot(packet: Dict[str, Any]) -> bool:
    controller = packet.get("leftController")
    return bool(controller.get("yButton", False)) if isinstance(controller, dict) else False


def right_a_snapshot(packet: Dict[str, Any]) -> bool:
    controller = packet.get("rightController")
    return bool(controller.get("aButton", False)) if isinstance(controller, dict) else False


def right_b_snapshot(packet: Dict[str, Any]) -> bool:
    controller = packet.get("rightController")
    return bool(controller.get("bButton", False)) if isinstance(controller, dict) else False


def xa_snapshot(packet: Dict[str, Any]) -> bool:
    left = packet.get("leftController")
    right = packet.get("rightController")
    return bool(
        isinstance(left, dict)
        and isinstance(right, dict)
        and left.get("xButton", False)
        and right.get("aButton", False)
    )


def controller_hold_confirmed(status: Dict[str, Any]) -> bool:
    """Accept both controller hold status formats used by deployed V4 stacks.

    Newer controllers expose an explicit HOLDING state.  Robot 300 keeps the
    top-level state ARMED while reporting the equivalent fail-safe barrier as
    a measured-pose target with both clutch sides held.  Require all of those
    fields so an ordinary ARMED state can never satisfy the handover barrier.
    """
    if status.get("state") == "HOLDING":
        return True
    held_sides = status.get("grip_release_held_sides")
    return bool(
        status.get("state") == "ARMED"
        and status.get("hardware_ready") is True
        and status.get("target_source") == "measured_hold"
        and isinstance(held_sides, (list, tuple, set))
        and {"left", "right"}.issubset(set(held_sides))
    )


def expert_timeout_requires_estop(grips: Tuple[bool, bool]) -> bool:
    """A missing expert target is a fault only while an operator is gripping.

    Releasing both Grips is the normal pause gesture: the controller holds its
    measured pose and the expert may re-grip later.  If a Grip remains pressed,
    loss of the target stream means the commanded controller path disappeared
    while motion was requested and must fail closed.
    """
    return any(bool(value) for value in grips)


def stamped_envelope(
    *, source: str, sequence: int, source_timestamp_ns: Optional[int],
    receive_wall_ns: int, receive_monotonic_ns: int, authority_epoch: int,
    valid: bool = True, reason: str = "", payload: Any = None,
) -> Dict[str, Any]:
    return {
        "source": source,
        "source_sequence": int(sequence),
        "source_timestamp_ns": source_timestamp_ns,
        "robot_receive_timestamp_ns": int(receive_wall_ns),
        "robot_receive_monotonic_ns": int(receive_monotonic_ns),
        "authority_epoch": int(authority_epoch),
        "valid": bool(valid),
        "reason": str(reason),
        "payload": payload,
    }
