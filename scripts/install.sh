#!/usr/bin/env bash
# One-command setup of local-image-studio on a new Mac (Apple Silicon). Safe to run again:
# every step checks first and skips what is already there.
#
#   scripts/install.sh            full setup (about 50 GB of downloads on the first run)
#   scripts/install.sh --check    only report what is missing, change nothing
#
# Options: --no-models (skip model downloads), --no-edit (skip the photo-edit model),
#          --no-shell (do not add the img-* aliases to ~/.zshrc)
#
# Never touches Ollama.app: the studio runs its own pinned Ollama on its own ports and folders.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source config/studio.env

CHECK=0 MODELS=1 EDIT=1 SHELLRC=1
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;; --no-models) MODELS=0 ;; --no-edit) EDIT=0 ;; --no-shell) SHELLRC=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
todo() { printf '  \033[33mtodo\033[0m  %s\n' "$*"; MISSING=1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
MISSING=0

step "1. System"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] && ok "macOS on Apple Silicon" \
  || { echo "  needs macOS on Apple Silicon (MLX and the pinned Ollama build)"; exit 1; }
mem=$(( $(sysctl -n hw.memsize) / 1073741824 ))
(( mem >= 32 )) && ok "${mem} GB memory" || echo "  note: ${mem} GB memory - use the smaller chat model on the Models screen"
free_gb=$(df -g "$ROOT" | awk 'NR==2 {print $4}')
(( free_gb >= 60 )) && ok "${free_gb} GB free disk" || echo "  note: only ${free_gb} GB free disk, the models need about 50 GB"

step "2. Tools"
for t in curl jq git uv; do
  if command -v "$t" >/dev/null; then ok "$t"
  else
    todo "$t is missing - install it: brew install $t"
  fi
done
(( MISSING )) && { echo; echo "Install the missing tools, then run this script again."; exit 1; }

step "3. Python environment (.venv, pinned in uv.lock)"
if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import mflux" 2>/dev/null; then ok ".venv ready"
elif (( CHECK )); then todo ".venv"
else UV_CACHE_DIR="$ROOT/.uv-cache" uv sync --python 3.13 && rm -rf "$ROOT/.uv-cache" && ok ".venv ready"; fi

step "4. Pinned Ollama $STUDIO_OLLAMA_VERSION (vendor/, SHA-256 checked)"
if [[ -x "$ROOT/$STUDIO_OLLAMA_BIN" ]]; then ok "vendor/ollama-$STUDIO_OLLAMA_VERSION"
elif (( CHECK )); then todo "pinned Ollama"
else scripts/setup-ollama.sh && ok "pinned Ollama"; fi

step "5. Private folders (git-ignored)"
for d in models outputs refs run logs data; do mkdir -p "$d"; chmod 700 "$d"; done
ok "models outputs refs run logs data (chmod 700)"

step "6. Photo-edit model ($EDIT_BASE_MODEL, 8-bit, about 15 GB download)"
if [[ -d "$ROOT/$EDIT_MODEL_PATH/transformer" ]]; then ok "$EDIT_MODEL_PATH"
elif (( CHECK || ! EDIT || ! MODELS )); then todo "edit model (scripts/setup-edit.sh)"
else scripts/setup-edit.sh && ok "$EDIT_MODEL_PATH"; fi

step "7. Chat and text-to-image models (Ollama, about 30 GB download)"
have_models() { OLLAMA_HOST="$STUDIO_HOST:$STUDIO_PORT" "$ROOT/$STUDIO_OLLAMA_BIN" list 2>/dev/null; }
if (( CHECK || ! MODELS )); then
  echo "  they download on the first img-on, or run this script without --no-models"
else
  was_on=0; curl -fsS -m 2 "http://$STUDIO_HOST:$STUDIO_PORT/api/version" >/dev/null 2>&1 && was_on=1
  bin/studio on --no-portal            # starts the private server and pulls the missing models
  have_models | grep -q "${CHAT_MODEL%%:*}" && ok "$CHAT_MODEL" || todo "$CHAT_MODEL"
  have_models | grep -q "${STUDIO_MODEL%%:*}" && ok "$STUDIO_MODEL" || todo "$STUDIO_MODEL"
  (( was_on )) || STUDIO_NO_LAUNCHER=1 bin/studio off >/dev/null  # leave a running studio alone
fi

step "8. Shell aliases (img-on, img-off, img-status, ...)"
line="[ -f \"$ROOT/shell/aliases.zsh\" ] && source \"$ROOT/shell/aliases.zsh\""
if grep -qF "$ROOT/shell/aliases.zsh" ~/.zshrc 2>/dev/null || grep -q "local-image-studio/shell/aliases.zsh" ~/.zshrc 2>/dev/null; then ok "~/.zshrc already sources the aliases"
elif (( CHECK || ! SHELLRC )); then todo "add to ~/.zshrc:  $line"
else
  cp ~/.zshrc "$ROOT/run/zshrc.backup-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
  printf '\n# local-image-studio aliases (img-on, img-off, ...). Remove this line to uninstall.\n%s\n' "$line" >> ~/.zshrc
  ok "added to ~/.zshrc (backup in run/)"
fi

step "Done"
if (( CHECK )); then
  (( MISSING )) && echo "Some parts are missing - run scripts/install.sh" || echo "Everything is in place."
else
  echo "Open a new terminal and run:  img-on"
fi
