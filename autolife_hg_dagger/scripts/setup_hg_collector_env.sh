#!/usr/bin/env bash
# Install the repository-pinned crash-safe collector required by HG-DAGGER.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
collector_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"
target="${collector_root}/record_lerobot_official.py"
source_file="${script_dir}/../../lerobot_data_collector/record_lerobot_official.py"

if [ ! -f "${target}" ]; then
  echo "ERROR: collector recorder not found: ${target}" >&2
  exit 2
fi
if [ ! -f "${source_file}" ]; then
  echo "ERROR: repository collector source not found: ${source_file}" >&2
  exit 3
fi

if cmp -s "${source_file}" "${target}"; then
  echo "HG-DAGGER atomic collector is already installed."
else
  backup="${target}.pre_atomic_$(date +%Y%m%d_%H%M%S)"
  cp "${target}" "${backup}"
  install -m 0644 "${source_file}" "${target}"
  echo "Installed repository-pinned HG-DAGGER atomic collector; backup: ${backup}"
fi

python3 -m py_compile "${target}"
echo "HG-DAGGER collector environment is ready."
