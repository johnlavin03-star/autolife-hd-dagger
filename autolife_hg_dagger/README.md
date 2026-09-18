# Autolife HG-DAgger

这是一个独立的 ROS 2 Python 仲裁包。运动链只使用 `openarmx_teleop_vr_306_v4` 公开的 topic、service 和参数，不直接发布机器人厂商控制 topic。为显示 HD-DAGGER 中文 HUD 与区分 B 短按/长按，网页前端有小范围集成；原 VR mapper/controller 的运动逻辑不变。

## 第一阶段能力

- VLA joint action 与 VR expert action 的单点控制权仲裁。
- `POLICY_STOPPED → POLICY_WARMUP → POLICY_ACTIVE → FAILURE_HOLD → EXPERT_RELEASE_REQUIRED → EXPERT_READY → EXPERT_ACTIVE → POLICY_WARMUP` 状态机。
- 网页使能后默认保持 `POLICY_STOPPED`，不会请求 THOR 推理。双 Grip 松开时长按右 A 1.2 秒进入 VLA warmup；在 VLA 预热或控制期间长按右 B 1.2 秒立即保持机器人并返回 `POLICY_STOPPED`。短按 B 仍保留原有视角切换。
- 长按 Y 交还后，VLA warmup 与 RGBD 视频编码并行；warmup 连续输出稳定后恢复策略，采集器在后台完成 manifest ACK，避免长片段编码阻塞控制状态。
- 在 `EXPERT_RELEASE_REQUIRED`、`EXPERT_READY`，或双 Grip 已松开的 `EXPERT_ACTIVE` 中，长按左 X + 右 A 1 秒可请求原控制器的碰撞检查快速复位。复位全程仍经过 HG-DAgger 仲裁，完成后必须重新按 Grip 锚定；含复位运动的 intervention 会被标记为不可训练并丢弃。
- 接管前向 controller 发双臂 `release_hold`，等待 controller 报告 `HOLDING` 后才允许 VR 目标通过。
- controller 确认保持后必须先松开双 Grip，再次按 Grip 才能从最新实测 FK 重新锚定；网页会提示并尝试触发手柄震动。
- 硬件使能由 `/hg_dagger/set_session_enabled` 代理，controller 启动参数强制 `quick_reset_after_hardware_enable=false`，因此使能/接管不触发复位。
- mapper 的 X+A quick reset 在 HG launch 中强制关闭，防止动作绕过 supervisor 的单点仲裁。
- 机器人侧 wall clock 与 monotonic clock 时间戳；浏览器时间戳仅保留为诊断字段。
- Grip 请求接管；双 Grip 松开时长按 Y 返回 policy warmup。当前固定为 RGBD，Y 短按切换已锁定。
- 初版 policy 恢复预热：连续 6 个动作、至少 0.2 秒，并检查与当前 applied action 的跳变。
- 单条 episode 包含失败前默认 5 秒同步 RGBD 前缀和接管后的 expert 段；manifest/trace 记录失败边界，start/save/discard 均要求 collector 的 request ID 确认。
- collector 后台原子提交成功后，VR 顶部 HUD 显示“当前纠正片段已保存”、episode index 和实际帧数；在 manifest/Parquet/视频完整落盘前不会提前报成功。
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
bash src/autolife-hd-dagger/autolife_hg_dagger/scripts/setup_hg_web_env.sh
ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  dry_run:=true robot_id:=328 task_name:=my_task data_root:=/path/to/hg_data
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
  dry_run:=true robot_id:=328 start_groot_bridge:=true \
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
source /home/ubuntu/ros2_ws/src/autolife-hd-dagger/autolife_hg_dagger/scripts/source_hg_ros_env.sh
ros2 topic echo /hg_dagger/control_state
ros2 service call /hg_dagger/set_session_enabled std_srvs/srv/SetBool "{data: true}"
```

完成 dry-run、topic 单发布者检查和低速人工监督测试之前，不要以 `dry_run:=false` 启动。

## 实机启动前检查

实机上优先使用 fail-closed wrapper。它会检查底层手臂服务与实时关节反馈、四路
RGBD/手部相机 SHM、8446 端口，以及是否存在桌面 VR 导航程序启动的旧 controller。
任一项失败都不会启动 HD-DAGGER：

```bash
export HG_DAGGER_ROBOT_ID=300
export HG_DAGGER_START_GROOT=false
bash /home/ubuntu/ros2_ws/src/autolife-hd-dagger/autolife_hg_dagger/scripts/launch_hg_dagger.sh \
  dry_run:=true task_name:=hd300_check data_root:=/home/ubuntu/hg_dagger_data_300 \
  policy_timeout_sec:=30.0
```

启用 THOR 前设置 `HG_DAGGER_START_GROOT=true`；wrapper 会额外检查 token 权限和
THOR `/health`。`policy_timeout_sec:=30.0` 只用于纯 VR 调试，正式 VLA 联调恢复为
默认 `0.25` 秒。不要同时运行桌面端 `full_vr_navigation.launch.py` 与本 launch。

## Collector 对接边界

本包不会修改 `CB_Autolife_Data_Collector`。先在另一个终端启动固定 schema 的 RGBD recorder：

```bash
bash /home/ubuntu/ros2_ws/src/autolife-hd-dagger/autolife_hg_dagger/scripts/start_hg_collectors.sh \
  my_task "任务文本" /path/to/hg_data
```

在其他机器人上同时设置 `HG_DAGGER_ROBOT_ID` 和 launch 的 `robot_id`。例如 307：

```bash
export HG_DAGGER_ROBOT_ID=307
bash /home/ubuntu/ros2_ws/src/autolife-hd-dagger/autolife_hg_dagger/scripts/start_hg_collectors.sh \
  my_task "任务文本" /path/to/hg_data

ros2 launch autolife_hg_dagger hg_dagger_vr.launch.py \
  robot_id:=307 dry_run:=true task_name:=my_task data_root:=/path/to/hg_data
```

数据目录是 `/path/to/hg_data/my_task/hg_dagger_rgbd/atomic_dataset_v1`；
控制 FIFO 位于其父目录。collector 和 launch 必须使用同一个
`data_root`。每次接管先写 `.staging/<episode_id>`，完成 Parquet footer、
四路视频编码和帧数校验后，才会原子移到 `episodes/<episode_id>` 并
fsync 追加 `episode_index.jsonl`。因此索引内的 episode 才是可供后续
导出/训练的已提交数据。

wrapper 直接使用原 recorder 的 `--action-arm-topic` / `--action-gripper-topic` 参数指向 HG-DAGGER 的两个 label topic。停止时运行 `stop_hg_collectors.sh my_task /path/to/hg_data`。

若左右手部相机 SHM 尚未存在，wrapper 默认会启动并管理
`hand_camera_producer.py`。可用 `HG_DAGGER_START_HAND_PRODUCER=0` 禁用；设备号可通过
`HG_DAGGER_HAND_LEFT_DEVICE` 和 `HG_DAGGER_HAND_RIGHT_DEVICE` 显式覆盖。
300 当前适配值分别为 `/dev/video12` 和 `/dev/video10`，首次测试仍需在 VR
画面中人工确认左右标签没有对调。

开始采集前需安装一次仓库内固定版本的 HG-DAGGER atomic
collector：

```bash
bash /home/ubuntu/ros2_ws/src/autolife-hd-dagger/autolife_hg_dagger/scripts/setup_hg_collector_env.sh
```

该脚本只校验仓库内的独立 recorder 和 LeRobot 运行环境，不会复制、
覆盖或修改 `/home/ubuntu/lerobot_data_collector/record_lerobot_official.py`。
HD-DAGGER 仅从旧 collector 目录导入共享的相机/时序工具和 control helper，
实际启动的 recorder 固定为本仓库
`lerobot_data_collector/record_lerobot_official.py`。wrapper 会 fail-closed：
仓库 recorder 不是 atomic 版就拒绝启动。

路径必须为绝对路径且可写；可以选择 NAS 挂载点，也可以在测试阶段显式选择本机目录。默认值为 `/home/ubuntu/hg_dagger_data`。
`TASK_NAME` 就是 `data_root` 下的自定义批次文件夹名，为避免路径歧义，仅允许字母、数字、`.`、`_`和 `-`。例如 `data_root=/home/ubuntu/nas`、`TASK_NAME=hd300_acceptance_p2_01`时，数据集位于 `/home/ubuntu/nas/hd300_acceptance_p2_01/hg_dagger_rgbd/atomic_dataset_v1`。

collector 在等待失败期间维护同步 RGBD 环形缓存；接管 `start` 成功后，缓存前缀先进入同一个 LeRobot episode。若 episode 最终保存，失败前图像、状态和已仲裁动作会和后续 VR expert 数据一起落盘；若放弃则整条 episode 一并清除。

每帧还写入 `metadata.control_mode`、`authority_epoch`、`train_mask`、
`pre_failure`、`failure_boundary` 和 `anchor_timestamp_ns`。`train_mask=1`
仅表示当时处于未暂停、未复位的 `EXPERT_ACTIVE`；训练导出不应把
pre-roll/HOLD 动作当成 expert label。上一条 episode 后台编码时，同一
会话仍可立即开始新接管；只有关闭后立即重新启用会话时会等待全部落盘。

停止采集器后，按真实任务名做完整性审计（不要输入
`<TASK_NAME>` 这种带尖括号的文档占位符）：

```bash
bash /home/ubuntu/ros2_ws/src/autolife-hd-dagger/autolife_hg_dagger/scripts/validate_hg_collection.sh \
  hd300_vla_vr_01 /home/ubuntu/hg_dagger_data_300
```

只有输出 `PASS` 且 `.staging` 为空，才把该批数据交给后续导出器。
