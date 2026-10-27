"""Interview chat with the local language model. Ends with an approved prompt and runs it.

Started by `bin/studio chat` (the server must be on). Stdlib only.
Commands inside the chat: /cmd  show the last command, /reset  start over, /quit  leave.
"""
import json
import os
import readline  # noqa: F401 - line editing and history in input()
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studiolib as lib  # noqa: E402

ROOT = lib.ROOT
URL = os.environ["STUDIO_URL"]
MODEL = os.environ["CHAT_MODEL"]
KEEP_ALIVE = os.environ.get("STUDIO_KEEP_ALIVE", "10m")
SYSTEM = lib.SYSTEM_PROMPT_FILE.read_text()
IMG_EXT = lib.IMG_EXT

C_DIM, C_BOLD, C_CYAN, C_YEL, C_OFF = "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[0m"


def find_images(text):
    """Paths dragged into the terminal: quoted, or with backslash-escaped spaces."""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    found = []
    for p in parts:
        path = Path(os.path.expanduser(p))
        if path.suffix.lower() in IMG_EXT and path.is_file():
            found.append(path.resolve())
    return found


def chat(messages):
    body = {"model": MODEL, "messages": messages, "stream": True, "think": False,
            "keep_alive": KEEP_ALIVE, "options": {"temperature": 0.6, "num_ctx": 16384}}
    text = []
    print(f"{C_CYAN}", end="", flush=True)
    with lib.post(URL + "/api/chat", body) as r:
        for line in r:
            part = json.loads(line)
            if part.get("error"):
                raise RuntimeError(part["error"])
            piece = part.get("message", {}).get("content", "")
            text.append(piece)
            print(piece, end="", flush=True)
    print(C_OFF)
    return "".join(text)


def command_for(spec):
    tool = "img-gen" if spec["mode"] == "gen" else "img-edit"
    refs = " ".join(shlex.quote(r) for r in spec.get("refs", []))
    base = f"{tool} {shlex.quote(spec['prompt'])}"
    if refs:
        base += f" {refs}"
    base += f" -W {spec['width']} -H {spec['height']} -s {spec['steps']}"
    return [f"{base} -S {s}" for s in spec.get("seeds", [1])]


def run(spec, log):
    lib.unload_all(URL)
    studio = str(ROOT / "bin" / "studio")
    for seed in spec.get("seeds", [1]):
        args = [studio, spec["mode"], spec["prompt"], *spec.get("refs", []),
                "-W", str(spec["width"]), "-H", str(spec["height"]),
                "-s", str(spec["steps"]), "-S", str(seed)]
        subprocess.run(args, check=False)
    log.write("\n## Ran\n\n" + "\n".join(command_for(spec)) + "\n")


def new_messages():
    return [{"role": "system", "content": SYSTEM}]


def main():
    sessions = ROOT / "outputs" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    log = open(sessions / f"{time.strftime('%Y%m%d-%H%M%S')}.md", "a")
    messages = new_messages()
    last = None
    n_refs = 0

    print(f"{C_DIM}Chat model: {MODEL}. Drag images into the terminal to attach them.")
    print(f"Commands: /cmd  /reset  /quit{C_OFF}\n")
    messages.append({"role": "user", "content": "Start the interview. Greet me in one line and ask the first question."})
    messages.append({"role": "assistant", "content": chat(messages)})

    while True:
        try:
            text = input(f"{C_BOLD}> {C_OFF}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reset":
            messages, last, n_refs = new_messages(), None, 0
            messages.append({"role": "user", "content": "Start the interview again from the beginning."})
            messages.append({"role": "assistant", "content": chat(messages)})
            continue
        if text == "/cmd":
            print("\n".join(command_for(last)) if last else "No approved prompt yet.")
            continue

        msg = {"role": "user", "content": text}
        images = find_images(text)
        if images:
            notes = []
            for p in images:
                n_refs += 1
                notes.append(f"[attached ref {n_refs}: {p}]")
            msg["content"] = text + "\n" + "\n".join(notes)
            msg["images"] = [lib.to_llm_image(p) for p in images]
            print(f"{C_DIM}attached: {', '.join(p.name for p in images)}{C_OFF}")
        messages.append(msg)
        log.write(f"\n**me:** {msg['content']}\n")

        try:
            reply = chat(messages)
        except Exception as e:
            print(f"{C_YEL}chat error: {e}{C_OFF}")
            messages.pop()
            continue
        messages.append({"role": "assistant", "content": reply})
        log.write(f"\n**studio:** {reply}\n")

        if "<<<FINAL>>>" not in reply:
            continue
        try:
            last = lib.parse_final(reply)
        except ValueError as e:
            print(f"{C_YEL}Could not read the final block ({e}). Ask the model to write it again.{C_OFF}")
            continue

        print(f"\n{C_BOLD}Prompt:{C_OFF}\n{last['prompt']}\n\n{C_BOLD}Command:{C_OFF}")
        print("\n".join(command_for(last)))
        log.write("\n## Approved\n\n```\n" + "\n".join(command_for(last)) + "\n```\n")
        ans = input(f"\n{C_BOLD}Generate now? [Y/n] {C_OFF}").strip().lower()
        if ans in ("", "y", "yes", "כן"):
            run(last, log)
            print(f"{C_DIM}Done. Keep chatting to change things, or /quit.{C_OFF}")
        else:
            print(f"{C_DIM}Not generated. Keep chatting, or /cmd to see the command.{C_OFF}")
        log.flush()

    log.close()


if __name__ == "__main__":
    main()
