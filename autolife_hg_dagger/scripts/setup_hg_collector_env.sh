#!/usr/bin/env bash
# Install the small, version-checked collector fix required by HG-DAGGER.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
collector_root="${HG_DAGGER_COLLECTOR_ROOT:-/home/ubuntu/lerobot_data_collector}"
target="${collector_root}/record_lerobot_official.py"
patch_file="${script_dir}/../patches/collector_pre_roll_boundary.patch"

if [ ! -f "${target}" ]; then
  echo "ERROR: collector recorder not found: ${target}" >&2
  exit 2
fi
if [ ! -f "${patch_file}" ]; then
  echo "ERROR: HG-DAGGER collector patch not found: ${patch_file}" >&2
  exit 3
fi

if grep -q "HG-DAGGER pre-roll/live boundary" "${target}"; then
  echo "HG-DAGGER collector pre-roll boundary fix is already installed."
else
  backup="${target}.pre_hg_dagger_boundary"
  cp --update=none "${target}" "${backup}"
  patch --batch --forward -d "${collector_root}" -p0 < "${patch_file}"
  echo "Installed HG-DAGGER collector fix; original backup: ${backup}"
fi

python3 -m py_compile "${target}"
echo "HG-DAGGER collector environment is ready."
