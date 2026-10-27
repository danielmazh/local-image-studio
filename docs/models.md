# Models: choose, install, switch

Everything here can be done on the **Models** screen of the portal. This page explains what happens.

## The three roles

| Role | Used for | Engine | Where it lives |
| --- | --- | --- | --- |
| Chat | The interview that writes the prompt. Sees your photos. | Ollama (private instance, port 11535) | `models/blobs`, `models/manifests` |
| Text to image | New images with no reference photo | Ollama (pinned 0.32.5) | `models/blobs`, `models/manifests` |
| Edit | Images from your photos (keep a face, change clothes, scenes) | mflux (Apple MLX), FLUX.2 klein family | `models/<name>-q8/` |

Only one model is in memory at a time. Before an image job the chat model is unloaded.
The image model leaves memory 2 minutes after a job, the chat model 10 minutes after the last message.
The edit model is a short process: it loads once per job (all variations) and exits.

## Switch a model

Models screen -> the role -> **Use this**. The choice is written to `config/studio.env`
(`CHAT_MODEL`, `STUDIO_MODEL`, `EDIT_BASE_MODEL` + `EDIT_MODEL_PATH`). The next chat or job uses it.
A model must be installed first.

## Install a model

Models screen -> **Install**. Progress shows on the screen.

- Ollama models are pulled into `models/` by the private server.
- Edit models download from Hugging Face into `models/hf/`, are saved 8-bit into `models/<name>-q8/`,
  and the full-size download is deleted.
- Models marked **Login needed** need a free Hugging Face account once. Today no listed model
  needs it: the FLUX.2 klein 9B edit models are parked under `_disabled` in `config/models.json`
  (move an entry back into `models` to offer it). The steps:
  1. Open the model page (link on the card) and accept the license.
  2. Create a Read token: https://huggingface.co/settings/tokens
  3. In a terminal (paste the token when asked, never in a chat):

     ```bash
     cd <project folder> && HF_HOME=models/hf .venv/bin/hf auth login --no-add-to-git-credential
     ```

     The token is saved in `models/hf/token` (inside the project, git-ignored).

## Add a new model to the list

Add an entry to `config/models.json`:

- Ollama chat or text-to-image: `{"id", "role": "chat"|"gen", "engine": "ollama", "tag": "<ollama tag>", "name", "size_gb", "mem_gb", "license", "notes"}`
  Text-to-image tags must be image models that Ollama 0.32.5 runs (the `x/` library models).
- Edit (FLUX.2 klein family only): `{"id", "role": "edit", "engine": "mflux-flux2", "base": "<mflux model name>", "dir": "models/<folder>", "download_gb", "gated", "hf_page", ...}`

Reload the Models screen. No code change is needed.

## Remove a model

Models screen -> **Remove** (not possible for the active model). Ollama models are deleted
through the private server; edit models delete their folder in `models/`.

## Load or free memory by hand

Models screen -> **Load** warms a chat or text-to-image model; **Free** unloads it.
**Free all memory** unloads every model and stops a running edit.
