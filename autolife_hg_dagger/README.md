# Autolife HG-DAgger

这是一个独立于 `openarmx_teleop_vr_306_v4` 的 ROS 2 Python 包。它只使用原 VR 包公开的 topic、service 和参数，不修改原包源码，也不直接发布机器人厂商控制 topic。

## 第一阶段能力

- VLA joint action 与 VR expert action 的单点控制权仲裁。
- `POLICY_ACTIVE → FAILURE_HOLD → EXPERT_RELEASE_REQUIRED → EXPERT_READY → EXPERT_ACTIVE → POLICY_WARMUP` 状态机。
- 接管前向 controller 发双臂 `release_hold`，等待 controller 报告 `HOLDING` 后才允许 VR 目标通过。
- controller 确认保持后必须先松开双 Grip，再次按 Grip 才能从最新实测 FK 重新锚定；网页会提示并尝试触发手柄震动。
- 硬件使能由 `/hg_dagger/set_session_enabled` 代理，controller 启动参数强制 `quick_reset_after_hardware_enable=false`，因此使能/接管不触发复位。
- 机器人侧 wall clock 与 monotonic clock 时间戳；浏览器时间戳仅保留为诊断字段。
- Grip 请求接管；双 Grip 松开时长按 Y 返回 policy warmup。当前固定为 RGBD，Y 短按切换已锁定。
- 初版 policy 恢复预热：连续 6 个动作、至少 0.2 秒，并检查与当前 applied action 的跳变。
- 单条 episode 包含失败前默认 5 秒同步 RGBD 前缀和接管后的 expert 段；manifest/trace 记录失败边界，start/save/discard 均要求 collector 的 request ID 确认。
- 30 Hz 发布 `/hg_dagger/collector/action`，动作顺序与 GR00T N1.7 严格一致：
  左臂 7 + 右臂 7 + 左右夹爪 + 颈部 3 + 上腰 pitch/yaw，共 21 维。

## 输入接口

- `/hg_dagger/policy_action` (`std_msgs/String`):

```json
{"action":[0,0,0,0,0,0,0,0,0,0,0,0,0,0,10,10],"units":"degrees","sequence":1,"timestamp_ns":0}
```

- `/hg_dagger/request_failure` (`std_srvs/Trigger`): VLA 失败触发。
- `/hg_dagger/request_takeover` (`std_srvs/Trigger`): 外部显式请求接管。
- `/hg_dagger/request_resume` (`std_srvs/Trigger`): 非 VR 的显式交还请求。
- `/hg_dagger/set_session_enabled` (`std_srvs/SetBool`): 代理现有 VR 页面/mapper 的硬件使能。

## 输出与诊断

- `/hg_dagger/control_state`：当前模式、epoch、session/intervention id、深度模式。
- `/hg_dagger/events`：状态切换与数据有效性事件。
- `/hg_dagger/timing/vr_input`、`/hg_dagger/timing/expert_eef`：机器人侧时间戳信封。
- `/hg_dagger/collector/action`：完整、持续的 21D 已仲裁动作镜像（接管前为 policy/hold，接管后为 expert）。
- `/hg_dagger/collector/arm_action`、`/hg_dagger/collector/gripper_action`：供原 collector 通过命令行参数订阅的 30 Hz 完整 joint label；不会重发到厂商 topic。

## 构建与 dry-run

```bash
cd /home/ubuntu/ros2_ws
colcon build --packages-select autolife_hg_dagger --symlink-install
source install/setup.bash
bash src/autolife_hg_dagger/scripts/setup_hg_web_env.sh
ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  dry_run:=true task_name:=my_task data_root:=/path/to/hg_data
```

### THOR GR00T N1.7 bridge

328 侧 bridge 复用了 300 的内容绑定协议，但不直接发布厂商控制 topic：它读取
q23 与三路 SHM RGB，向 THOR 的 `/start`、`/infer`、`/retry` 发请求，并把
`[40,21]` chunk 逐步交给 supervisor。只有收到
`/hg_dagger/policy_forward_ack` 后，该步才会进入 `/ack` 或 `/cancel` 的真实执行前缀。
VR 接管会取消剩余 chunk；交还后的 warmup proposal 会以未执行方式 `/discard`，
随后从最新实测状态创建全新 session。

当前 bridge 对实际运行的 `policy_only_baseline/policy_only_frame` 模式做严格校验。
它不会把缺少因果视觉历史的请求发送给 SOMA/full verifier 服务。

先以权限为 `0600` 的文件提供 THOR token，然后显式启用 bridge：

```bash
mkdir -p /home/ubuntu/.config/autolife_hg_dagger
# 将现有 THOR token 安全写入 groot_server.token，不要提交到源码仓库。
chmod 600 /home/ubuntu/.config/autolife_hg_dagger/groot_server.token

ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  dry_run:=true start_groot_bridge:=true \
  groot_server_url:=http://192.168.8.179:8777 \
  groot_token_file:=/home/ubuntu/.config/autolife_hg_dagger/groot_server.token \
  groot_task:='Pick the laundry bag.' \
  task_name:=laundry_bag data_root:=/home/ubuntu/hg_dagger_data
```

`start_groot_bridge` 默认保持 `false`，避免 token 尚未配置时破坏纯 VR/dry-run
启动。也可通过进程环境变量 `GROOT_REMOTE_TOKEN` 注入，不会在状态或日志中输出。

`groot_max_inference_latency_sec` 默认是 `1.0` 秒。任一 `/start`、`/infer`
或 `/retry` 达到该阈值，即按 VLA 失败进入 VR 接管；设置为小于等于零可关闭。
当前 300 历史记录中的 baseline 往返通常约为 0.25–0.44 秒。

HG collector 现在只启动 RGBD 版本，并在暂停期间持续组装默认 5 秒的同步
pre-roll。失败触发时 supervisor 立即执行带 ACK 的 `start`，把该时刻的 pre-roll
先写入同一个 LeRobot episode，随后
继续追加 VR expert 帧。可通过 `HG_DAGGER_PRE_ROLL_SEC` 调整窗口长度。

另一个终端观察：

```bash
source /home/ubuntu/ros2_ws/src/autolife_hg_dagger/scripts/source_hg_ros_env.sh
ros2 topic echo /hg_dagger/control_state
ros2 service call /hg_dagger/set_session_enabled std_srvs/srv/SetBool "{data: true}"
```

完成 dry-run、topic 单发布者检查和低速人工监督测试之前，不要以 `dry_run:=false` 启动。

## Collector 对接边界

本包不会修改 `CB_Autolife_Data_Collector`。先在另一个终端启动固定 schema 的 RGBD recorder：

```bash
bash /home/ubuntu/ros2_ws/src/autolife_hg_dagger/scripts/start_hg_collectors.sh \
  my_task "任务文本" /path/to/hg_data
```

数据目录是 `/path/to/hg_data/my_task/hg_dagger_rgbd/dataset`；控制 FIFO 位于其父目录。collector 和 launch 必须使用同一个 `data_root`。

wrapper 直接使用原 recorder 的 `--action-arm-topic` / `--action-gripper-topic` 参数指向 HG-DAGGER 的两个 label topic。停止时运行 `stop_hg_collectors.sh my_task /path/to/hg_data`。

路径必须为绝对路径且可写；可以选择 NAS 挂载点，也可以在测试阶段显式选择本机目录。默认值为 `/home/ubuntu/hg_dagger_data`。

collector 在等待失败期间维护同步 RGBD 环形缓存；接管 `start` 成功后，缓存前缀先进入同一个 LeRobot episode。若 episode 最终保存，失败前图像、状态和已仲裁动作会和后续 VR expert 数据一起落盘；若放弃则整条 episode 一并清除。
