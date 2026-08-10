#!/usr/bin/env bash
# Install Ollama in the home directory and pull the model Peppermint uses.
# This script needs no root.
set -euo pipefail

PREFIX="$HOME/.local/ollama"
DIST="$HOME/.local/share/ollama-dist"
MODEL="${PEPPERMINT_MODEL:-qwen3:8b}"
UNIT="$HOME/.config/systemd/user/ollama.service"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1. Find the newest release"
TAG=$(curl -sSL https://api.github.com/repos/ollama/ollama/releases/latest | grep -m1 '"tag_name"' | cut -d'"' -f4)
[ -n "$TAG" ] || { echo "Peppermint could not read the release list."; exit 1; }
URL="https://github.com/ollama/ollama/releases/download/$TAG/ollama-linux-amd64.tar.zst"
echo "Release $TAG"

say "2. Download and unpack"
mkdir -p "$DIST" "$PREFIX"
curl -fSL --progress-bar -o "$DIST/ollama.tar.zst" "$URL"
tar --use-compress-program=unzstd -xf "$DIST/ollama.tar.zst" -C "$PREFIX"
rm -f "$DIST/ollama.tar.zst"
"$PREFIX/bin/ollama" --version || true

say "3. Make the user service"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=Ollama server (user install for Peppermint)
After=network-online.target

[Service]
Type=simple
ExecStart=$PREFIX/bin/ollama serve
Restart=on-failure
RestartSec=3
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=10m"

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now ollama.service
sleep 3
curl -sf --max-time 10 http://127.0.0.1:11434/api/version || { echo "Ollama does not answer."; exit 1; }
echo

say "4. Pull the model ($MODEL, about 5 GB)"
OLLAMA_HOST=127.0.0.1:11434 "$PREFIX/bin/ollama" pull "$MODEL"

say "Ollama is ready."
nvidia-smi --query-gpu=memory.used,memory.total --format=csv 2>/dev/null || true
echo "Next: run scripts/install.sh"
