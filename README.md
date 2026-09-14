# Autolife HD-DAgger

HD-DAgger integration for GR00T N1.7 VLA inference and VR expert takeover on
the Autolife robot platform.

The repository contains:

- `autolife_hg_dagger/`: ROS 2 authority arbitration, failure handling,
  two-stage VR re-anchoring, THOR baseline bridge, timing protocol and sidecar
  recording.
- `lerobot_data_collector/`: the RGBD LeRobot recorder integration with a
  synchronized failure pre-roll buffer.
- `openarmx_teleop_vr_306_v4/`: the VR web UI integration file used to display
  takeover prompts and request controller haptics.

Runtime tokens, passwords, datasets, logs and machine-local backups are not
tracked. See `autolife_hg_dagger/README.md` for deployment and test commands.
