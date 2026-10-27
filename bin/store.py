"""History (SQLite in data/) and config edits for the portal. Stdlib only."""
import json
import re
import sqlite3
import threading
import time

from studiolib import ROOT

DB_PATH = ROOT / "data" / "studio.db"
ENV_PATH = ROOT / "config" / "studio.env"
_lock = threading.Lock()
_mig_lock = threading.Lock()  # own lock: _db() runs while _lock is held
_migrated = False
# Columns added after the first release: (table, column, SQL type and default)
NEW_COLUMNS = [("chats", "kind", "TEXT DEFAULT 'director'"), ("chats", "pb_mode", "TEXT")]


def _migrate(con, existed):
    """Add new columns once per process. An existing file is backed up first (no data loss)."""
    global _migrated
    if _migrated:
        return
    missing = [(t, c, d) for t, c, d in NEW_COLUMNS
               if c not in {r["name"] for r in con.execute(f"PRAGMA table_info({t})")}]
    if missing and existed:
        bak = DB_PATH.with_name(f"{DB_PATH.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        dst = sqlite3.connect(bak)
        con.backup(dst)
        dst.close()
        bak.chmod(0o600)
    for t, c, d in missing:
        con.execute(f"ALTER TABLE {t} ADD COLUMN {c} {d}")
    con.commit()
    _migrated = True


def _db():
    DB_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    existed = DB_PATH.is_file() and DB_PATH.stat().st_size > 0
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, created REAL, finished REAL, status TEXT, mode TEXT, prompt TEXT,
          spec TEXT, models TEXT, outputs TEXT, chat_id TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS chats (
          id TEXT PRIMARY KEY, created REAL, updated REAL, title TEXT, model TEXT,
          messages TEXT, final TEXT);
    """)
    DB_PATH.chmod(0o600)
    with _mig_lock:
        _migrate(con, existed)
    return con


def save_job(job, models):
    outs = [it.get("file") for it in job["items"] if it.get("file")]
    with _lock, _db() as con:
        con.execute("""INSERT INTO jobs (id, created, finished, status, mode, prompt, spec, models, outputs, chat_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET finished=excluded.finished, status=excluded.status,
                       outputs=excluded.outputs""",
                    (job["id"], job["created"], job.get("finished"), job["status"], job["spec"]["mode"],
                     job["spec"]["prompt"], json.dumps(job["spec"]), json.dumps(models), json.dumps(outs),
                     job.get("chat_id")))


def save_chat(chat_id, model, messages, final=None):
    title = next((m["content"][:80] for m in messages if m["role"] == "user" and not m.get("hidden")), "New chat")
    now = time.time()
    with _lock, _db() as con:
        con.execute("""INSERT INTO chats (id, created, updated, title, model, messages, final) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, title=excluded.title,
                       messages=excluded.messages, final=COALESCE(excluded.final, chats.final)""",
                    (chat_id, now, now, title, model, json.dumps(messages), json.dumps(final) if final else None))


def save_prompt_chat(chat_id, model, messages, mode):
    """A Prompt builder chat: same table as the director chats, kind="prompt-builder", mode on/off."""
    title = next((m["content"][:80] for m in messages if m["role"] == "user"), "New chat")
    now = time.time()
    with _lock, _db() as con:
        con.execute("""INSERT INTO chats (id, created, updated, title, model, messages, final, kind, pb_mode)
                       VALUES (?,?,?,?,?,?,NULL,'prompt-builder',?)
                       ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, title=excluded.title,
                       model=excluded.model, messages=excluded.messages, pb_mode=excluded.pb_mode""",
                    (chat_id, now, now, title, model, json.dumps(messages), mode))


def list_history(kind, q="", limit=200):
    with _db() as con:
        if kind == "chats":
            rows = con.execute("SELECT id, created, updated, title, model, final, kind, pb_mode FROM chats "
                               "WHERE title LIKE ? OR messages LIKE ? ORDER BY updated DESC LIMIT ?",
                               (f"%{q}%", f"%{q}%", limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM jobs WHERE prompt LIKE ? OR note LIKE ? ORDER BY created DESC LIMIT ?",
                               (f"%{q}%", f"%{q}%", limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("spec", "models", "outputs", "final", "messages"):
            if d.get(k):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


def get_chat(chat_id):
    with _db() as con:
        r = con.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["messages"] = json.loads(d["messages"] or "[]")
    d["final"] = json.loads(d["final"]) if d["final"] else None
    return d


def delete_history(kind, item_id):
    table = "chats" if kind == "chats" else "jobs"
    with _lock, _db() as con:
        con.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))


def set_note(job_id, note):
    with _lock, _db() as con:
        con.execute("UPDATE jobs SET note = ? WHERE id = ?", (note[:500], job_id))


def set_env(updates):
    """Change KEY="value" lines in config/studio.env, keep every comment and other line."""
    allowed = {"CHAT_MODEL", "STUDIO_MODEL", "EDIT_BASE_MODEL", "EDIT_MODEL_PATH",
               "CHAT_MEM_GB", "GEN_MEM_GB", "EDIT_MEM_GB"}
    with _lock:
        text = ENV_PATH.read_text()
        for key, value in updates.items():
            if key not in allowed or not re.fullmatch(r"[A-Za-z0-9_./:\-]+", str(value)):
                raise ValueError(f"refused config change: {key}")
            line = f'{key}="{value}"'
            text, n = re.subn(rf'^{key}=.*$', line, text, flags=re.M)
            if n == 0:
                text += f"\n{line}\n"
        ENV_PATH.write_text(text)
