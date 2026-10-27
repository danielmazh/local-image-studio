# local-image-studio aliases. Sourced from ~/.zshrc (one line, added by scripts/install.sh).
# The repo root is found from this file's own location, so the clone can live anywhere.
LOCAL_IMAGE_STUDIO="${${(%):-%x}:A:h:h}"
alias img="$LOCAL_IMAGE_STUDIO/bin/studio"
alias img-on="$LOCAL_IMAGE_STUDIO/bin/studio on"
alias img-off="$LOCAL_IMAGE_STUDIO/bin/studio off"
alias img-status="$LOCAL_IMAGE_STUDIO/bin/studio status"
alias img-portal="$LOCAL_IMAGE_STUDIO/bin/studio portal"
alias img-home="$LOCAL_IMAGE_STUDIO/bin/studio launcher"
alias img-chat="$LOCAL_IMAGE_STUDIO/bin/studio chat"
alias img-gen="$LOCAL_IMAGE_STUDIO/bin/studio gen"
alias img-edit="$LOCAL_IMAGE_STUDIO/bin/studio edit"
alias img-out="open $LOCAL_IMAGE_STUDIO/outputs"
# Optional machine-only shortcuts
[ -f "$LOCAL_IMAGE_STUDIO/local/aliases.zsh" ] && source "$LOCAL_IMAGE_STUDIO/local/aliases.zsh"
