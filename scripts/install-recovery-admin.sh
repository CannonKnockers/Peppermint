#!/usr/bin/env bash
# Install only the standalone process helper. The GTK app always runs as you.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="$project_dir/peppermint/recovery/processes.py"
target_file=/usr/local/libexec/peppermint-recovery-helper
/usr/bin/python3 -I "$source_file" --check
echo "Installing $target_file as root:root, mode 0755."
echo "Mint will ask for administrator authentication. No password is stored."
/usr/bin/pkexec --disable-internal-agent /usr/bin/install -D -o root -g root -m 0755 "$source_file" "$target_file"
cmp "$source_file" "$target_file"
stat -c '%U:%G %a %n' "$target_file"
echo "Administrator recovery helper installed. Protected actions use Mint authentication."
