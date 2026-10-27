#!/usr/bin/env bash
# One-time: download the pinned Ollama (official GitHub release), check its
# SHA-256, and unpack it into vendor/. Used only by this project's private server.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/config/studio.env"

DEST="$ROOT/vendor/ollama-$STUDIO_OLLAMA_VERSION"
TGZ="$ROOT/vendor/ollama-$STUDIO_OLLAMA_VERSION-darwin.tgz"
mkdir -p "$ROOT/vendor"

[[ -f "$TGZ" ]] || curl -fL --proto '=https' -o "$TGZ" \
  "https://github.com/ollama/ollama/releases/download/v$STUDIO_OLLAMA_VERSION/ollama-darwin.tgz"
echo "$STUDIO_OLLAMA_SHA256  $TGZ" | shasum -a 256 -c -

mkdir -p "$DEST"
tar -xzf "$TGZ" -C "$DEST"
codesign --verify --strict "$DEST/ollama" && echo "signature ok"
"$DEST/ollama" --version
