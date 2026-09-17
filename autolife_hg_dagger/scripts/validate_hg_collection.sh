#!/usr/bin/env bash
# Validate one committed HG-DAGGER atomic collection by task name.

set -euo pipefail

task_name="${1:-}"
data_root="${2:-${HG_DAGGER_DATA_ROOT:-/home/ubuntu/hg_dagger_data}}"
lerobot_py="${LEROBOT_PY:-/home/ubuntu/miniconda3/envs/lerobot/bin/python}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${task_name}" ]; then
  echo "Usage: $0 TASK_NAME [DATA_ROOT]" >&2
  echo "Example: $0 hd300_vla_vr_01 /home/ubuntu/hg_dagger_data_300" >&2
  exit 2
fi
if [[ "${task_name}" == *"<"* || "${task_name}" == *">"* ]]; then
  echo "ERROR: TASK_NAME must be the real directory name, without < or >." >&2
  exit 2
fi

dataset_root="${data_root}/${task_name}/hg_dagger_rgbd/atomic_dataset_v1"
if [ ! -d "${dataset_root}" ]; then
  echo "ERROR: no atomic HG-DAGGER dataset exists yet: ${dataset_root}" >&2
  legacy="${data_root}/${task_name}/hg_dagger_rgbd/dataset"
  if [ -d "${legacy}" ]; then
    echo "INFO: legacy data is present and was not modified: ${legacy}" >&2
    echo "INFO: atomic_dataset_v1 is created only by a new P0 collector run." >&2
  fi
  exit 3
fi

validator_args=()
if [ -n "${HG_DAGGER_EXPECT_SAVED_EPISODES:-}" ]; then
  validator_args+=(--expect-episodes "${HG_DAGGER_EXPECT_SAVED_EPISODES}")
fi
exec "${lerobot_py}" "${script_dir}/validate_hg_atomic_dataset.py" \
  "${validator_args[@]}" "${dataset_root}"
