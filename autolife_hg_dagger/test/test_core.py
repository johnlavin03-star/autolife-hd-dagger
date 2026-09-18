import pytest

from autolife_hg_dagger.core import (
    AuthorityStateMachine,
    Mode,
    both_grips_released,
    controller_hold_confirmed,
    expert_timeout_requires_estop,
    grip_snapshot,
    left_x_snapshot,
    left_y_snapshot,
    policy_takeover_timing,
    policy_to_controller,
    right_b_snapshot,
    xa_snapshot,
)


def test_hg_dagger_recovery_cycle():
    machine = AuthorityStateMachine()
    assert machine.enable().new == Mode.POLICY_STOPPED
    assert machine.start_policy().new == Mode.POLICY_WARMUP
    assert machine.warmup_complete().new == Mode.POLICY_ACTIVE
    assert machine.failure().new == Mode.FAILURE_HOLD
    assert machine.hold_confirmed().new == Mode.EXPERT_RELEASE_REQUIRED
    assert machine.expert_release_confirmed().new == Mode.EXPERT_READY
    assert machine.expert_first_command().new == Mode.EXPERT_ACTIVE
    assert machine.resume().new == Mode.POLICY_WARMUP
    assert machine.warmup_complete().new == Mode.POLICY_ACTIVE
    assert machine.authority_epoch == 9


def test_policy_can_be_stopped_and_restarted_by_operator():
    machine = AuthorityStateMachine()
    machine.enable()
    machine.start_policy()
    assert machine.stop_policy().new == Mode.POLICY_STOPPED
    assert machine.start_policy().new == Mode.POLICY_WARMUP


def test_expert_cannot_become_ready_before_release_confirmation():
    machine = AuthorityStateMachine()
    machine.enable()
    machine.start_policy()
    machine.warmup_complete()
    machine.failure()
    machine.hold_confirmed()
    with pytest.raises(ValueError):
        machine.expert_first_command()
    assert machine.expert_release_confirmed().new == Mode.EXPERT_READY


def test_invalid_takeover_is_rejected():
    machine = AuthorityStateMachine()
    with pytest.raises(ValueError):
        machine.failure()


def test_policy_action_contract():
    controller, action = policy_to_controller({"action": list(range(16)), "units": "degrees"})
    assert controller["left_arm_target_joints_position"] == list(range(7))
    assert controller["right_arm_target_joints_position"] == list(range(7, 14))
    assert action[14:] == [14.0, 15.0]


def test_groot_21d_policy_action_contract_preserves_unmodelled_lower_body():
    controller, action = policy_to_controller({
        "action": list(range(21)),
        "units": "degrees",
        "measured_leg_waist": [101, 102, 103, 104],
    })
    assert controller["leg_waist_target_joints_position"] == [101.0, 102.0, 19.0, 20.0]
    assert controller["neck_target_joints_position"] == [16.0, 17.0, 18.0]
    assert len(action) == 21


@pytest.mark.parametrize("action", [[0.0] * 15, [0.0] * 15 + [float("nan")]])
def test_invalid_policy_action(action):
    with pytest.raises(ValueError):
        policy_to_controller({"action": action})


def test_vr_gestures_from_existing_web_payload():
    packet = {
        "leftController": {"gripActive": True, "xButton": 1, "yButton": 1},
        "rightController": {"gripActive": False, "aButton": 1},
    }
    assert grip_snapshot(packet) == (True, False)
    assert left_y_snapshot(packet)
    assert left_x_snapshot(packet)
    assert not right_b_snapshot(packet)
    assert not both_grips_released(packet)
    assert not xa_snapshot(packet)


def test_xa_reset_gesture_requires_both_buttons():
    packet = {
        "leftController": {"xButton": 1},
        "rightController": {"aButton": 1},
    }
    assert xa_snapshot(packet)


def test_policy_takeover_requires_six_active_seconds():
    started = 10_000_000_000
    assert policy_takeover_timing(started, started + 5_999_000_000, 6.0)[0] is False
    ready, elapsed, remaining = policy_takeover_timing(
        started, started + 6_000_000_000, 6.0
    )
    assert ready
    assert elapsed == pytest.approx(6.0)
    assert remaining == 0.0


def test_reset_returns_expert_pause_to_reanchor_gate():
    machine = AuthorityStateMachine()
    machine.enable()
    machine.start_policy()
    machine.warmup_complete()
    machine.failure()
    machine.hold_confirmed()
    machine.expert_release_confirmed()
    machine.expert_first_command()
    assert machine.reset_to_expert_ready().new == Mode.EXPERT_READY


def test_explicit_controller_holding_state_confirms_barrier():
    assert controller_hold_confirmed({"state": "HOLDING"})


def test_robot_300_measured_hold_confirms_barrier():
    assert controller_hold_confirmed({
        "state": "ARMED",
        "hardware_ready": True,
        "target_source": "measured_hold",
        "grip_release_held_sides": ["left", "right"],
    })


@pytest.mark.parametrize("status", [
    {"state": "ARMED", "hardware_ready": True},
    {
        "state": "ARMED",
        "hardware_ready": True,
        "target_source": "measured_hold",
        "grip_release_held_sides": ["left"],
    },
    {
        "state": "ARMED",
        "hardware_ready": False,
        "target_source": "measured_hold",
        "grip_release_held_sides": ["left", "right"],
    },
])
def test_ordinary_or_partial_armed_state_does_not_confirm_barrier(status):
    assert not controller_hold_confirmed(status)


def test_expert_pause_with_both_grips_released_is_not_estop():
    assert not expert_timeout_requires_estop((False, False))


@pytest.mark.parametrize("grips", [(True, False), (False, True), (True, True)])
def test_expert_target_timeout_while_gripping_requires_estop(grips):
    assert expert_timeout_requires_estop(grips)
