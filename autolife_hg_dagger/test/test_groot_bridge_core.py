import numpy as np

from autolife_hg_dagger.groot_bridge_core import (
    EXPECTED_FEATURES,
    EXPECTED_IMAGES,
    GrootContract,
    array_digest_float32,
    parse_q23,
    policy_state_from_q23,
)


def _state_packet():
    return {
        "leg_waist_joint_state": {"position": [0, 1, 2, 3]},
        "left_arm_joint_state": {"position": list(range(4, 11))},
        "right_arm_joint_state": {"position": list(range(11, 18))},
        "left_gripper_state": {"position": [18]},
        "right_gripper_state": {"position": [19]},
        "neck_joint_state": {"position": [20, 21, 22]},
    }


def test_q23_to_groot_21_order():
    q23 = parse_q23(_state_packet())
    assert q23 == list(range(23))
    assert policy_state_from_q23(q23) == (
        list(range(4, 20)) + list(range(20, 23)) + [2.0, 3.0]
    )


def test_current_server_contract_is_accepted():
    contract = GrootContract.from_mapping({
        "state_feature_names": EXPECTED_FEATURES,
        "action_feature_names": EXPECTED_FEATURES,
        "image_features": {key: [3, 480, 640] for key in EXPECTED_IMAGES},
        "chunk_size": 40,
        "n_action_steps": 40,
    })
    assert contract.n_action_steps == 40


def test_execution_prefix_digest_matches_numpy_protocol():
    rows = [[1.25, -2.5], [3.0, 4.125]]
    array = np.asarray(rows, dtype=np.float32)
    import hashlib
    import json

    header = json.dumps(
        {"shape": list(array.shape), "dtype": array.dtype.str},
        sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    expected = "sha256:" + hashlib.sha256(header + b"\0" + array.tobytes()).hexdigest()
    assert array_digest_float32(rows) == expected
