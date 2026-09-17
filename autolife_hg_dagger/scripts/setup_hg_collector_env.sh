#!/usr/bin/env bash
# Validate the isolated repository-pinned collector required by HG-DAGGER.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
collector_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"
source_file="${script_dir}/../../lerobot_data_collector/record_lerobot_official.py"
lerobot_py="${LEROBOT_PY:-/home/ubuntu/miniconda3/envs/lerobot/bin/python}"

if [ ! -f "${collector_root}/collector_control.py" ]; then
  echo "ERROR: existing collector runtime not found: ${collector_root}" >&2
  exit 2
fi
if [ ! -f "${source_file}" ]; then
  echo "ERROR: repository collector source not found: ${source_file}" >&2
  exit 3
fi

if [ ! -x "${lerobot_py}" ]; then
  echo "ERROR: LeRobot Python is unavailable: ${lerobot_py}" >&2
  exit 4
fi

"${lerobot_py}" -m py_compile "${source_file}"
echo "HG-DAGGER isolated collector is ready: ${source_file}"
echo "Existing collector remains untouched: ${collector_root}/record_lerobot_official.py"
