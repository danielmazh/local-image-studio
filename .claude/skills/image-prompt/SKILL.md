---
name: image-prompt
description: Interview the user about an image they want, check their reference photos, and hand back one exact, ready-to-run img-gen or img-edit command for local-image-studio. Use when the user asks for an image prompt, wants to plan an image, or says "/image-prompt".
---

# Image prompt interview (local-image-studio)

Read `prompts/interviewer.md` in this project and follow it fully. It holds the engines,
the defaults, the tested face rules and the prompt templates. It is the single source of truth;
the local chat model (`img-chat`) uses the same file.

Differences when you run it inside Claude Code:

1. Ask the questions with AskUserQuestion when the options are clear (2-4 options, the
   recommended one first). Use plain chat for open questions.
2. When the user names reference photos, read each image file and judge it as the guide says.
   Copy approved photos into `refs/` (chmod 600) and use absolute paths.
3. Do not print the `<<<FINAL>>>` block. Instead end with:
   - a 2-3 line summary in the user's language,
   - the English prompt in a ```text block,
   - one ```bash block per command, ready to copy:
     `img-on` first if needed, then the `img-gen` line (one per seed) or the `img-edit` line
     (all seeds in one `-S 1,2,3`, one model load).
4. Do not run the commands unless the user asks you to.
