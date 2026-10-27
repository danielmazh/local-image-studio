"""Studio tabs: one generation session per tab, each with its own memory file.

run/tabs/tab-<n>.json      state of the tab (UI state + server iteration log)
run/tabs/tab-mem-<n>.md    readable memory, rewritten after every change / iteration
On Done the session moves to outputs/sessions/<date>-<slug>/.
"""
import json
import re
import shutil
import threading
import time
import uuid

from studiolib import ROOT

TAB_DIR = ROOT / "run" / "tabs"
SESSIONS = ROOT / "outputs" / "sessions"
_lock = threading.Lock()
_backend = None  # optional storage instead of the files above (set_backend)


def set_backend(b):
    """Use another tab storage. b has all() -> list, put(t), drop(tab_id), done(tab_id, title)."""
    global _backend
    _backend = b


def _path(n):
    return TAB_DIR / f"tab-{n}.json"


def _mem(n):
    return TAB_DIR / f"tab-mem-{n}.md"


def _all():
    if _backend is not None:
        return sorted(_backend.all(), key=lambda t: t["n"])
    TAB_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    tabs = []
    for f in TAB_DIR.glob("tab-*.json"):
        try:
            tabs.append(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    return sorted(tabs, key=lambda t: t["n"])


def _get(tab_id):
    t = next((t for t in _all() if t["id"] == tab_id), None)
    if not t:
        raise ValueError("no such tab")
    return t


def _write(t):
    t["updated"] = time.time()
    if _backend is not None:
        return _backend.put(t)
    f = _path(t["n"])
    f.write_text(json.dumps(t))
    f.chmod(0o600)
    m = _mem(t["n"])
    m.write_text(memory_md(t))  # overwritten on every change: always the latest state
    m.chmod(0o600)


def listing():
    return [{"id": t["id"], "n": t["n"], "title": t["title"], "updated": t["updated"]} for t in _all()]


def new():
    with _lock:
        used = {t["n"] for t in _all()}
        n = next(i for i in range(1, 1000) if i not in used)
        t = {"id": uuid.uuid4().hex[:12], "n": n, "title": f"Tab {n}", "created": time.time(),
             "state": {}, "iterations": []}
        _write(t)
    return {"id": t["id"], "n": n, "title": t["title"]}


def get(tab_id):
    t = _get(tab_id)
    return {k: t[k] for k in ("id", "n", "title", "state", "iterations", "updated")}


def save(tab_id, state, title=None):
    with _lock:
        t = _get(tab_id)
        t["state"] = state
        if title:
            t["title"] = str(title)[:60]
        _write(t)


def add_iteration(tab_id, spec, files):
    """Server side log of what was generated, kept even if the UI state is replaced."""
    with _lock:
        try:
            t = _get(tab_id)
        except ValueError:
            return
        t["iterations"].append({"n": len(t["iterations"]) + 1, "time": time.time(), "mode": spec["mode"],
                                "prompt": spec["prompt"], "refs": spec["refs"], "size": f"{spec['width']}x{spec['height']}",
                                "steps": spec["steps"], "seeds": spec["seeds"], "files": files})
        _write(t)


def context_for_chat(tab_id):
    """Compact session memory for the chat model, so it stays smart over many iterations."""
    try:
        t = _get(tab_id)
    except ValueError:
        return ""
    if not t["iterations"]:
        return ""
    lines = ["Session memory (earlier iterations in this tab, newest last):"]
    for it in t["iterations"][-6:]:
        lines.append(f"- Iteration {it['n']} ({it['mode']}, {it['size']}, {len(it['files'])} images): {it['prompt']}")
        if it["refs"]:
            lines.append(f"  references: {', '.join(it['refs'])}")
    lines.append("When the user gives feedback, change only what they ask and keep what worked. "
                 "Then show the new prompt and ask for approval as usual.")
    return "\n".join(lines)


def memory_md(t):
    st = t.get("state") or {}
    out = [f"# {t['title']}", "", f"Tab {t['n']} - updated {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if st.get("prompt"):
        out += ["## Current prompt", "", st["prompt"], ""]
    photos = st.get("photos") or st.get("refs") or []
    if photos:
        out += ["## Photos", ""] + [f"- {p.get('path', p) if isinstance(p, dict) else p}" for p in photos] + [""]
    if t["iterations"]:
        out += ["## Iterations", ""]
        for it in t["iterations"]:
            out += [f"### Iteration {it['n']} - {it['mode']} {it['size']}, steps {it['steps']}, seeds {it['seeds']}",
                    "", it["prompt"], ""] + [f"- outputs/{f}" for f in it["files"]] + [""]
    msgs = st.get("messages") or []
    if msgs:
        out += ["## Chat", ""]
        for m in msgs:
            if m.get("hidden") and not str(m.get("content", "")).startswith("[Iteration"):
                continue
            who = "Me" if m.get("role") == "user" else "Director"
            text = re.sub(r"<<<FINAL>>>.*?<<<END>>>", "[final prompt block]", str(m.get("content", "")), flags=re.S)
            out += [f"**{who}:** {text.strip()}", ""]
    return "\n".join(out)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "session"


def done(tab_id, title=None):
    if _backend is not None:
        return _backend.done(tab_id, title)
    with _lock:
        t = _get(tab_id)
        if title:
            t["title"] = str(title)[:60]
        folder = SESSIONS / f"{time.strftime('%Y%m%d-%H%M')}-{_slug(t['title'])}"
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        # Every result image this tab produced or references
        names = {f for it in t["iterations"] for f in it["files"]}
        names |= set(re.findall(r"[\w.-]+\.png", json.dumps(t.get("state", {}))))
        copied = []
        for name in sorted(names):
            src = ROOT / "outputs" / name
            if src.is_file():
                shutil.copy2(src, folder / name)
                copied.append(name)
        (folder / "memory.md").write_text(memory_md(t))
        (folder / "session.json").write_text(json.dumps(t, indent=1))
        last = t["iterations"][-1]["prompt"] if t["iterations"] else (t.get("state") or {}).get("prompt", "")
        (folder / "prompt.txt").write_text(last or "")
        for f in folder.iterdir():
            f.chmod(0o600)
        _path(t["n"]).unlink(missing_ok=True)
        _mem(t["n"]).unlink(missing_ok=True)
    return {"folder": str(folder), "files": copied}


def close(tab_id):
    with _lock:
        t = _get(tab_id)
        if _backend is not None:
            return _backend.drop(t["id"])
        _path(t["n"]).unlink(missing_ok=True)
        _mem(t["n"]).unlink(missing_ok=True)
