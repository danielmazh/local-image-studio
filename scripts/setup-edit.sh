#!/usr/bin/env bash
# One-time setup of the reference-image engine (mflux + FLUX.2 klein 4B, 8-bit).
# Downloads ~15GB from Hugging Face into models/hf, saves an 8-bit copy into
# models/flux2-klein-4b-q8, then deletes the full-size download.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source config/studio.env

UV_CACHE_DIR="$ROOT/.uv-cache" uv sync
HF_HOME="$ROOT/models/hf" HF_HUB_DISABLE_TELEMETRY=1 \
  .venv/bin/mflux-save --model "$EDIT_BASE_MODEL" --quantize 8 --path "$ROOT/$EDIT_MODEL_PATH"
rm -rf "$ROOT/models/hf/hub" "$ROOT/models/hf/xet"  # keep models/hf/token
echo "edit engine ready: $ROOT/$EDIT_MODEL_PATH"
