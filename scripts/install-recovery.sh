#!/usr/bin/env bash
# Configure the recovery shortcut and its restricted administrator helper.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$project_dir/.venv/bin/python" -m peppermint.recovery.processes --check
"$project_dir/.venv/bin/python" -m peppermint.recovery.shortcut --install
bash "$project_dir/scripts/install-recovery-admin.sh"
