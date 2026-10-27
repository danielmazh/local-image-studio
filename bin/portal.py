"""local-image-studio web portal. Loopback only, token protected, stdlib only.

Started and stopped by bin/studio (on/off). Flow in the page: chat -> approve -> generate.
"""
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as mdl  # noqa: E402
import store  # noqa: E402
import tabs  # noqa: E402
import studiolib as lib  # noqa: E402

ROOT = lib.ROOT
ENV = lib.load_env()
HOST = ENV["STUDIO_HOST"]
PORT = int(ENV.get("PORTAL_PORT", "11536"))
OLLAMA = f"http://{ENV['STUDIO_HOST']}:{ENV['STUDIO_PORT']}"
# A launcher can point every folder at another place (bin/studio reads the same names)
RUN = Path(os.environ.get("STUDIO_RUN_DIR") or ROOT / "run")
TOKEN = (RUN / "portal.token").read_text().strip()
PAGE = ROOT / "portal" / "index.html"
REFS = Path(os.environ.get("STUDIO_REFS_DIR") or ROOT / "refs")
OUTPUTS = Path(os.environ.get("STUDIO_OUT_DIR") or ROOT / "outputs")
LOGS = Path(os.environ.get("STUDIO_LOG_DIR") or ROOT / "logs")
PROMPT_BUILDER_FILE = ROOT / "prompts" / "prompt-builder.md"

# Optional local overrides (a git-ignored folder named by STUDIO_LOCAL_DIR, off when unset).
# <dir>/portal_local.py may define setup(portal) and change: store, MODEL_GUARD, EXTRA_STATE,
# tabs.set_backend(...). <dir>/portal.js, <dir>/portal.css and <dir>/i18n/<lang>.json are added
# to the page when they exist (see window.studioHooks in portal/index.html).
LOCAL = Path(os.environ["STUDIO_LOCAL_DIR"]) if os.environ.get("STUDIO_LOCAL_DIR") else None
MODEL_GUARD = None   # callable(action) -> error text to refuse a model change, or None
EXTRA_STATE = {}     # extra fields for /api/state


def local_file(name):
    f = LOCAL / name if LOCAL else None
    return f if f and f.is_file() else None


def load_local():
    f = local_file("portal_local.py")
    if not f:
        return
    spec = importlib.util.spec_from_file_location("portal_local", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "setup"):
        mod.setup(sys.modules[__name__])


MAX_UPLOAD = 40 * 1024 * 1024

JOBS = {}            # job id -> state dict
JOB_LOCK = threading.Lock()  # one generation at a time (memory)


def env():
    """Config is read fresh, so a model change in the Models screen applies at once."""
    return lib.load_env()


def pick_models(body):
    """Models for this request: the tab's choice if installed, else the active ones in config."""
    e = env()
    sel = {"chat": e["CHAT_MODEL"], "gen": e["STUDIO_MODEL"],
           "edit_base": e["EDIT_BASE_MODEL"], "edit_dir": e["EDIT_MODEL_PATH"]}
    want = body.get("models") or {}
    if want:
        cat, tags = {m["id"]: m for m in mdl.catalog()}, mdl.ollama_tags()
        for role, mid in want.items():
            m = cat.get(mid)
            if not m or m["role"] != role:
                continue
            if m["engine"] == "ollama" and m["tag"] in tags:
                sel[role] = m["tag"]
            elif m["engine"] != "ollama" and (ROOT / m["dir"] / "transformer").is_dir():
                sel["edit_base"], sel["edit_dir"] = m["base"], m["dir"]
    return sel


def model_env(sel):
    return {"STUDIO_MODEL_OVERRIDE": sel["gen"], "EDIT_BASE_MODEL_OVERRIDE": sel["edit_base"],
            "EDIT_MODEL_PATH_OVERRIDE": sel["edit_dir"]}


def human_size(n):
    return f"{n / 1e9:.1f} GB"


def server_up():
    """The private Ollama server answers (the portal can run while it is off)."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def state():
    loaded = [{"name": m["name"], "size": human_size(m.get("size", 0))} for m in lib.loaded_models(OLLAMA)]
    running = [j for j in JOBS.values() if j["status"] == "running"]
    return {
        "root": str(ROOT),
        "paths": {"refs": str(REFS), "outputs": str(OUTPUTS), "config": str(ROOT / "config" / "studio.env"),
                  "interview": str(lib.SYSTEM_PROMPT_FILE), "logs": str(LOGS)},
        "models": {"chat": env()["CHAT_MODEL"], "gen": env()["STUDIO_MODEL"],
                   "edit": f"{env()['EDIT_BASE_MODEL']} ({env()['EDIT_MODEL_PATH']})"},
        "defaults": {"gen_steps": int(env().get("STUDIO_STEPS", 9)), "edit_steps": int(env().get("EDIT_STEPS", 4))},
        "loaded": loaded,
        "busy": bool(running),
        "server_up": server_up(),
        **EXTRA_STATE,
    }


def safe_name(name):
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem)[:40].strip("-") or "image"
    ext = Path(name).suffix.lower()
    return stem, ext


# Photo types the models cannot read (iPhone HEIC, TIFF, GIF, BMP, AVIF): saved as JPEG on upload
CONVERT_EXT = {".heic", ".heif", ".tif", ".tiff", ".gif", ".bmp", ".avif"}
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
            "image/heif": ".heif", "image/tiff": ".tiff", "image/gif": ".gif", "image/bmp": ".bmp", "image/avif": ".avif"}


def is_image(path):
    """True if macOS can read it as an image (sips prints a pixel width)."""
    out = subprocess.run(["sips", "-g", "pixelWidth", str(path)], capture_output=True, text=True).stdout
    return bool(re.search(r"pixelWidth: \d+", out))


def save_upload(data, name, ctype):
    """Store an uploaded photo in refs/. Returns the path, or raises ValueError (code, detail)."""
    stem, ext = safe_name(name)
    if ext not in lib.IMG_EXT | CONVERT_EXT:  # no or odd extension: trust the browser's type
        ext = MIME_EXT.get((ctype or "").split(";")[0].strip().lower(), ext)
    known = ext in lib.IMG_EXT | CONVERT_EXT
    if not known and ext:
        raise ValueError("type", ext)
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}"
    raw = REFS / f"{base}{ext}"
    n = 1
    while raw.exists() or raw.with_suffix(".jpg").exists():  # two uploads in the same second
        n += 1
        raw = REFS / f"{base}-{n}{ext}"
    raw.write_bytes(data)
    raw.chmod(0o600)
    if not is_image(raw):
        raw.unlink(missing_ok=True)
        raise ValueError("broken", ext)
    if ext in CONVERT_EXT or not known:  # no extension: an image by its content is kept as JPEG
        dest = raw.with_name(raw.name.removesuffix(ext) + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92", str(raw), "--out", str(dest)],
                       capture_output=True)
        raw.unlink(missing_ok=True)
        if not dest.is_file():
            raise ValueError("broken", ext)
        dest.chmod(0o600)
        return dest
    return raw


def run_job(job):
    """Generate, then a face pass if listed, then the identity quality gate (score and retry)."""
    spec = job["spec"]
    with JOB_LOCK:
        job["status"] = "running"
        lib.unload_all(OLLAMA)  # model swap: the chat model leaves memory first
        batch = list(job["items"])
        generate(job, batch)
        face_pass(job, batch)
        refs = identity_refs(spec)
        if refs and not job.get("cancel"):
            q_min = float(env().get("QUALITY_MIN", "0.90"))
            retries = int(env().get("QUALITY_RETRIES", "2"))
            score_items(job, batch, refs)
            if not any(it.get("score") is not None for it in job["items"]):
                retries = 0  # no face found in the photo or results: nothing to hold to the standard
            for _ in range(retries):  # not good enough: more variations, keep them all
                best = max((it.get("score") or 0 for it in job["items"]), default=0)
                if best >= q_min or job.get("cancel"):
                    break
                top = max(it["seed"] for it in job["items"])
                batch = [{"seed": top + i + 1, "status": "queued", "seconds": 0, "retry": True}
                         for i in range(max(1, min(2, len(spec["seeds"]))))]
                job["items"].extend(batch)
                generate(job, batch)
                face_pass(job, batch)
                score_items(job, batch, refs)
            best = max((it.get("score") or 0 for it in job["items"]), default=0)
            job["quality"] = {"min": q_min, "best": round(best, 3), "passed": best >= q_min}
        for it in job["items"]:  # variations that never started
            if it["status"] == "queued" and job.get("cancel"):
                it["status"] = "stopped"
        job["status"], job["finished"] = ("stopped" if job.get("cancel") else "done"), time.time()
        store.save_job(job, {"chat": job["models"]["chat"], "gen": job["models"]["gen"],
                             "edit": job["models"]["edit_base"]})
        if job.get("tab_id"):
            tabs.add_iteration(job["tab_id"], spec, [it["file"] for it in job["items"] if it.get("file")])


def generate(job, items):
    """Make the given items. Edit loads its model once for all of them; gen keeps the image
    model loaded in Ollama between variations."""
    spec = job["spec"]
    calls = [items] if spec["mode"] == "edit" else [[it] for it in items]
    for group in calls:
        if job.get("cancel"):
            break
        cur = group[0]
        cur["status"], cur["started"] = "running", time.time()
        args = [str(ROOT / "bin" / "studio"), spec["mode"], spec["prompt"], *spec["refs"],
                "-W", str(spec["width"]), "-H", str(spec["height"]), "-s", str(spec["steps"]),
                "-S", ",".join(str(it["seed"]) for it in group), "-n", f"{job['id']}"]
        # Own process group, so Stop can end it together with the model runner it starts
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env={**os.environ, "STUDIO_OPEN": "0", **model_env(job["models"])},
                             start_new_session=True)
        job["proc"] = p
        tail = []
        for line in p.stdout:
            tail = (tail + [line.strip()])[-8:]
            m = re.match(r"studio: item (\d+) start", line)
            if m and int(m.group(1)) <= len(group):
                cur = group[int(m.group(1)) - 1]
                cur["status"], cur["started"] = "running", time.time()
            m = re.match(r"studio: saved (\S+\.png)", line)
            if m:
                cur["status"], cur["file"] = "done", Path(m.group(1)).name
                cur["seconds"] = round(time.time() - cur["started"])
            if re.match(r"studio: item \d+ failed", line):
                cur["status"] = "failed"
        p.wait()
        job.pop("proc", None)
        for it in group:
            if it["status"] in ("queued", "running"):
                if job.get("cancel"):
                    it["status"] = "stopped"
                else:
                    it["status"], it["error"] = "failed", "\n".join(tail)[-400:]


def identity_refs(spec):
    """Face photos to score against: the face pass photos, or the one photo of an edit in place."""
    if spec.get("face_pass"):
        return [f["ref"] for f in spec["face_pass"]]
    if spec["mode"] == "edit" and len(spec["refs"]) == 1:
        return list(spec["refs"])
    return []


def score_items(job, items, refs):
    """ArcFace similarity per finished image (bin/face_score.py, in its own process)."""
    files = [str(OUTPUTS / f) for it in items for f in (it.get("file"), it.get("first_pass")) if f]
    if not files:
        return
    for it in items:
        if it.get("file"):
            it["phase"] = "score"
    try:
        out = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-I", "-B", str(ROOT / "bin" / "face_score.py"),
                              ",".join(refs), *files], capture_output=True, text=True, timeout=300).stdout
        scores = json.loads(out.strip().splitlines()[-1])
    except Exception:
        scores = {}
    for it in items:
        if it.get("file"):
            it.pop("phase", None)
            got = (scores.get(it["file"]) or {}).get("score")
            first = (scores.get(it.get("first_pass") or "") or {}).get("score")
            if first is not None and (got is None or first > got):  # the face pass made it worse
                it["file"], it["first_pass"], got = it["first_pass"], it["file"], first
            if got is not None:
                it["score"] = got


def face_pass(job, items):
    """Second pass per finished image: [image, face photo] -> change only that face."""
    spec = job["spec"]
    if not spec.get("face_pass") or job.get("cancel"):
        return
    for it in items:
        if not it.get("file") or job.get("cancel"):
            continue
        cur = OUTPUTS / it["file"]
        it["status"], it["phase"], it["started"] = "running", "face", time.time()
        for k, f in enumerate(spec["face_pass"], 1):
            if job.get("cancel"):
                break
            args = [str(ROOT / "bin" / "studio"), "edit", lib.FACE_PASS.format(who=f["who"]), str(cur), f["ref"],
                    "-W", str(spec["width"]), "-H", str(spec["height"]), "-s", str(spec["steps"]),
                    "-S", str(it["seed"]), "-n", f"{job['id']}-face{k}"]
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 env={**os.environ, "STUDIO_OPEN": "0", **model_env(job["models"])},
                                 start_new_session=True)
            job["proc"] = p
            for line in p.stdout:
                m = re.match(r"studio: saved (\S+\.png)", line)
                if m:
                    cur = Path(m.group(1))
            p.wait()
            job.pop("proc", None)
        it["first_pass"], it["file"] = it["file"], cur.name  # keep the scene-only image too
        it["status"], it["seconds"] = "done", it.get("seconds", 0) + round(time.time() - it["started"])
        it.pop("phase", None)


def swap_to_chat(chat_model):
    """Before a text chat: other models (the image model) leave memory, unless a job runs."""
    if any(j["status"] in ("queued", "running") for j in JOBS.values()):
        return
    for m in lib.loaded_models(OLLAMA):
        if m["name"] != chat_model:
            try:
                lib.post(OLLAMA + "/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30).read()
            except Exception:
                pass


def stop_job(job):
    """Stop now: finished images stay, the rest is marked stopped."""
    job["cancel"] = True
    p = job.get("proc")
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    lib.unload_all(OLLAMA)  # also ends a text-to-image request inside Ollama
    if job["spec"]["mode"] == "gen":
        threading.Thread(target=free_stuck_image_runner, daemon=True).start()


def free_stuck_image_runner():
    """Ollama 0.32.5 can hang when a text-to-image request is cut while the model loads: every
    later request (chat, images, the memory list) waits forever. Ending the image runner, a child
    of this studio's own server, frees it. Nothing happens when the server still answers."""
    for _ in range(3):
        time.sleep(2)
        try:
            with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=4):
                return
        except Exception:
            pass
    try:
        pid = int((RUN / "server.pid").read_text().strip())
    except (OSError, ValueError):
        return
    subprocess.run(["pkill", "-P", str(pid), "-f", "imagegen-engine"], capture_output=True)


# How the director writes the prompt: off = the user's words, refine 1-3 = more and more creative
REFINE = {
    0: (0.3, "Prompt mode: NO REFINE. Use the user's own words as the prompt, translated to clear English. "
             "Add nothing of your own except the technical clauses the engine needs (the face-keeping "
             "clause for edits, size). Do not add style, light, mood or details the user did not ask for."),
    1: (0.4, "Prompt mode: REFINE 1 (stay close). Keep exactly what the user wrote. Only fix clarity and "
             "structure and add the minimum the image model needs to read it right."),
    2: (0.65, "Prompt mode: REFINE 2 (balanced). Keep the user's intent and add the sensible missing "
              "details: light, camera, framing, setting, texture, so the image comes out well."),
    3: (0.9, "Prompt mode: REFINE 3 (creative). Keep the core intent, then enhance freely: add elements, "
             "atmosphere, composition, details and instructions the user did not think of but that make "
             "the image clearly better. Be bold."),
}
# "Director review" button: comment on the results, do not start a new run by itself
REVIEW_NOTE = ("This turn is a review the user asked for. Look at the attached results and say in at most 4 "
               "short lines what works, what is wrong and what to change next. Do NOT write a FINAL block "
               "now: the user decides if and what to run next.")
REFINE_COMMON = ("You have no preconceptions about the subject or style: in any field, do what this "
                 "specific request needs at this level.")


def director_seeds(spec, tab_id, count):
    """Seeds for a director run. The director always writes [1], so the studio picks them:
    a changed prompt keeps the last seed (same composition, only the asked change), the same
    prompt again gets a new random seed (a real new variation). A seed the user named is kept."""
    n = count if 1 <= count <= 4 else (len(spec["seeds"]) or 1)
    last = None
    if tab_id:
        try:
            its = tabs.get(tab_id)["iterations"]
            last = its[-1] if its else None
        except ValueError:
            pass
    if spec["seeds"] and spec["seeds"][0] != 1:
        first = spec["seeds"][0]
    elif last and last.get("seeds") and last["prompt"] != spec["prompt"]:
        first = last["seeds"][0]
    else:
        first = secrets.randbelow(2 ** 31 - 8)
    return [first + i for i in range(n)]


def refine_setting(body):
    level = int(body.get("creativity", 2)) if body.get("refine") else 0
    temp, text = REFINE.get(level, REFINE[2])
    return temp, text + " " + REFINE_COMMON


def help_payload():
    """Docs and the repo layout for the Help screen."""
    docs = {}
    for name in ("README.md", "docs/models.md", "docs/face-best-practice.md", "prompts/interviewer.md"):
        f = ROOT / name
        if f.is_file():
            docs[name] = f.read_text()
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
    return {"root": str(ROOT), "docs": docs, "files": tracked,
            "local_only": ["models/", "outputs/", "refs/", "data/", "logs/", "run/", "vendor/", ".venv/"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "studio"

    def log_message(self, fmt, *args):  # quiet; errors still go to stderr
        pass

    # ---------- guards ----------
    def host_ok(self):
        # Blocks DNS-rebinding: only our exact loopback address is accepted
        return self.headers.get("Host") in (f"{HOST}:{PORT}", f"localhost:{PORT}")

    def authed(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "studio" in c and secrets.compare_digest(c["studio"].value, TOKEN)

    def api_ok(self):
        # Header token stops other local web pages from calling the API (CSRF)
        return self.authed() and secrets.compare_digest(self.headers.get("X-Studio-Token", ""), TOKEN)

    # ---------- replies ----------
    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # NDJSON stream: one JSON object per line, the end of the stream is the end of the reply
    def stream_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(obj):
            self.wfile.write((json.dumps(obj) + "\n").encode())
            self.wfile.flush()
        return emit

    def body_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    # ---------- GET ----------
    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "bad host", "text/plain")
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)

        if url.path == "/launch":  # from the fallback page: a top-level visit gets the cookie
            return self.send(303, "", "text/plain", {
                "Location": "/", "Set-Cookie": f"studio={TOKEN}; HttpOnly; SameSite=Lax; Path=/"})
        if url.path == "/ping":  # the fallback page checks if the portal is up
            return self.send(200, "ok", "text/plain", {"Access-Control-Allow-Origin": "null"})
        if url.path == "/" and "t" in q:  # first visit: trade the URL token for a cookie
            if not secrets.compare_digest(q["t"][0], TOKEN):
                return self.send(403, "bad token", "text/plain")
            return self.send(303, "", "text/plain", {
                "Location": "/", "Set-Cookie": f"studio={TOKEN}; HttpOnly; SameSite=Lax; Path=/"})
        if not self.authed():
            return self.send(403, "Open the portal with the link from img-on.", "text/plain")

        if url.path == "/":
            html = PAGE.read_text().replace("__TOKEN__", TOKEN)
            if local_file("portal.css"):
                html = html.replace("</head>", '<link rel="stylesheet" href="/local/portal.css">\n</head>', 1)
            if local_file("portal.js"):
                html = html.replace("<!-- local script -->", '<script src="/local/portal.js"></script>', 1)
            return self.send(200, html, "text/html; charset=utf-8",
                             {"Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; "
                              "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                              "connect-src 'self'; frame-ancestors 'none'"})
        if url.path == "/api/state":
            return self.send(200, state())
        if url.path == "/api/tabs":
            return self.send(200, tabs.listing())
        m = re.fullmatch(r"/api/tabs/([a-f0-9]+)", url.path)
        if m:
            try:
                return self.send(200, tabs.get(m.group(1)))
            except ValueError as e:
                return self.send(404, {"error": str(e)})
        if url.path == "/api/models":
            return self.send(200, mdl.overview())
        m = re.fullmatch(r"/api/task/([a-z0-9]+)", url.path)
        if m:
            t = mdl.TASKS.get(m.group(1))
            return self.send(200 if t else 404, t or {"error": "no such task"})
        if url.path == "/api/history":
            kind = q.get("kind", ["jobs"])[0]
            return self.send(200, store.list_history(kind, q.get("q", [""])[0]))
        m = re.fullmatch(r"/api/chat/([A-Za-z0-9-]+)", url.path)
        if m:
            c = store.get_chat(m.group(1))
            return self.send(200 if c else 404, c or {"error": "no such chat"})
        if url.path == "/api/library":
            def listing(folder, base):
                files = sorted((p for p in folder.iterdir() if p.suffix.lower() in lib.IMG_EXT),
                               key=lambda p: p.stat().st_mtime, reverse=True)
                return [{"name": p.name, "path": str(p), "url": f"/{base}/" + urllib.parse.quote(p.name),
                         "time": p.stat().st_mtime} for p in files[:200]]
            return self.send(200, {"refs": listing(REFS, "refs"), "outputs": listing(OUTPUTS, "outputs")})
        if url.path == "/api/examples":  # ?lang=he -> docs/examples.he.md, else English
            lang = q.get("lang", ["en"])[0]
            f = ROOT / "docs" / ("examples.he.md" if lang == "he" else "examples.md")
            return self.send(200, {"markdown": f.read_text() if f.is_file() else ""})
        m = re.fullmatch(r"/i18n/(en|he)\.json", url.path)
        if m:  # UI strings, one file per language (scripts/check-i18n.py keeps them in sync)
            f = ROOT / "portal" / "i18n" / f"{m.group(1)}.json"
            extra = local_file(f"i18n/{m.group(1)}.json")
            if not extra:
                return self.send(200, f.read_bytes(), "application/json")
            return self.send(200, {**json.loads(f.read_text()), **json.loads(extra.read_text())})
        m = re.fullmatch(r"/local/(portal\.js|portal\.css)", url.path)
        if m:  # optional local add-on files (fixed names only)
            f = local_file(m.group(1))
            if not f:
                return self.send(404, "not found", "text/plain")
            return self.send(200, f.read_bytes(), "text/javascript" if f.suffix == ".js" else "text/css")
        m = re.fullmatch(r"/docs/examples/([A-Za-z0-9._-]+\.(?:jpg|png))", url.path)
        if m:  # example images that ship with the repo (fictional people only)
            f = ROOT / "docs" / "examples" / m.group(1)
            if not f.is_file():
                return self.send(404, "not found", "text/plain")
            return self.send(200, f.read_bytes(), "image/jpeg" if f.suffix == ".jpg" else "image/png")
        if url.path == "/api/help":
            return self.send(200, help_payload())
        if url.path == "/api/outputs":
            files = sorted((p for p in OUTPUTS.glob("*.png")), key=lambda p: p.stat().st_mtime, reverse=True)
            return self.send(200, [{"file": p.name, "path": str(p)} for p in files[:60]])
        m = re.fullmatch(r"/api/job/([a-z0-9-]+)", url.path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:  # started before a portal restart: the page marks it lost (no console error)
                return self.send(200, {"id": m.group(1), "status": "lost", "unknown": True})
            now = time.time()
            for it in job["items"]:
                if it["status"] == "running":
                    it["seconds"] = round(now - it["started"])
            return self.send(200, {k: v for k, v in job.items() if k not in ("thread", "proc")})
        m = re.fullmatch(r"/(refs|outputs)/([^/]+)", url.path)
        if m:
            base = REFS if m.group(1) == "refs" else OUTPUTS
            f = (base / urllib.parse.unquote(m.group(2))).resolve()
            if f.parent != base.resolve() or not f.is_file():  # no path traversal
                return self.send(404, "not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if f.suffix.lower() == ".heic":  # browsers cannot show HEIC: serve a JPEG copy
                jpg = lib.TMP / f"view-{f.stem}.jpg"
                if not jpg.exists():
                    subprocess.run(["sips", "-s", "format", "jpeg", str(f), "--out", str(jpg)], capture_output=True)
                f, ctype = jpg, "image/jpeg"
            return self.send(200, f.read_bytes(), ctype)
        return self.send(404, "not found", "text/plain")

    # ---------- POST ----------
    def do_POST(self):
        if not (self.host_ok() and self.api_ok()):
            return self.send(403, {"error": "forbidden"})
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/upload":
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > MAX_UPLOAD:
                self.close_connection = True  # the body is not read
                return self.send(413, {"error": "file too large (max 40 MB)", "code": "big" if n > 0 else "empty"})
            data = self.rfile.read(n)
            try:
                dest = save_upload(data, urllib.parse.unquote(self.headers.get("X-File-Name", "image.jpg")),
                                   self.headers.get("Content-Type"))
            except ValueError as e:
                code, ext = e.args
                msg = f"not an image type: {ext}" if code == "type" else f"the file is not a readable image ({ext})"
                return self.send(415, {"error": msg, "code": code, "ext": ext})
            return self.send(200, {"name": dest.name, "path": str(dest)})

        if path == "/api/chat":
            return self.chat(self.body_json())
        if path == "/api/prompt-builder":
            return self.prompt_builder(self.body_json())

        if path == "/api/generate":
            try:
                body = self.body_json()
                allowed = (REFS.resolve(), OUTPUTS.resolve())  # refs only from the project
                body["refs"] = [r for r in body.get("refs", []) if Path(r).resolve().parent in allowed]
                spec = lib.fix_spec(body)
            except (ValueError, TypeError) as e:
                return self.send(400, {"error": str(e)})
            if any(j["status"] in ("queued", "running") for j in JOBS.values()):
                return self.send(409, {"error": "a generation is already running"})
            jid = time.strftime("%H%M%S") + "-" + secrets.token_hex(2)
            job = {"id": jid, "status": "queued", "spec": spec, "created": time.time(),
                   "chat_id": body.get("chat_id"), "tab_id": body.get("tab_id"), "models": pick_models(body),
                   "items": [{"seed": s, "status": "queued", "seconds": 0} for s in spec["seeds"]]}
            JOBS[jid] = job
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
            return self.send(200, {"id": jid, "spec": spec})

        m = re.fullmatch(r"/api/job/([a-z0-9-]+)/stop", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send(404, {"error": "no such job"})
            stop_job(job)
            return self.send(200, {"ok": True})
        if path == "/api/stop":  # stop whatever runs (a job of any tab)
            for job in JOBS.values():
                if job["status"] in ("queued", "running"):
                    stop_job(job)
            return self.send(200, {"ok": True})

        if path == "/api/tabs/new":
            return self.send(200, tabs.new())
        m = re.fullmatch(r"/api/tabs/([a-f0-9]+)/(save|done|close)", path)
        if m:
            body = self.body_json()
            try:
                if m.group(2) == "save":
                    tabs.save(m.group(1), body.get("state", {}), body.get("title"))
                    return self.send(200, {"ok": True})
                if m.group(2) == "done":
                    return self.send(200, tabs.done(m.group(1), body.get("title")))
                tabs.close(m.group(1))
                return self.send(200, {"ok": True})
            except ValueError as e:
                return self.send(404, {"error": str(e)})

        if path.startswith("/api/models/"):
            action = path.rsplit("/", 1)[1]
            body = self.body_json()
            refused = MODEL_GUARD and MODEL_GUARD(action)
            if refused:
                return self.send(403, {"error": refused})
            try:
                if action == "install":
                    return self.send(200, {"task": mdl.install(body["id"])})
                if action == "activate":
                    mdl.activate(body["id"])
                elif action == "remove":
                    mdl.remove(body["id"])
                elif action == "load":
                    if any(j["status"] == "running" for j in JOBS.values()):
                        return self.send(409, {"error": "a generation is running"})
                    mdl.load(body["id"])
                elif action == "unload":
                    mdl.unload(body.get("id"))
                else:
                    return self.send(404, {"error": "unknown action"})
            except (ValueError, KeyError) as e:
                return self.send(400, {"error": str(e)})
            except Exception as e:
                return self.send(500, {"error": f"{action} failed: {e}"})
            return self.send(200, mdl.overview())

        if path == "/api/history/delete":
            body = self.body_json()
            store.delete_history(body.get("kind", "jobs"), body["id"])
            return self.send(200, {"ok": True})
        if path == "/api/history/note":
            body = self.body_json()
            store.set_note(body["id"], body.get("note", ""))
            return self.send(200, {"ok": True})

        if path == "/api/off":
            self.send(200, {"ok": True, "restart": "img-on"})
            # Detached, so it survives this process being stopped by it
            if os.environ.get("STUDIO_OFF_CMD"):  # a launcher can set its own stop command
                subprocess.Popen([os.environ["STUDIO_OFF_CMD"], "off"], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen([str(ROOT / "bin" / "studio"), "off"], start_new_session=True,
                                 stdout=open(LOGS / "off.log", "a"), stderr=subprocess.STDOUT)
            return
        return self.send(404, {"error": "not found"})

    def chat(self, body):
        """The director: chats, and after the user approves it runs the image model itself,
        then looks at the results and says what to improve. Streams NDJSON events:
        {"t"} text, {"final"} approved spec, {"job"} started, {"progress"} items,
        {"results"} files, {"phase": "review"}, {"append"} messages to keep, {"done"}."""
        emit = self.stream_start()

        history = [dict(m) for m in body.get("messages", [])]
        tab_id, chat_id = body.get("tab_id"), body.get("chat_id")
        appended = []
        try:
            self.refine = refine_setting(body)
            self.review = bool(body.get("review"))
            self.models = pick_models(body)
            reply = self.director(history, tab_id, emit)
            appended.append({"role": "assistant", "content": reply})
            try:
                attached = list(dict.fromkeys(r for m in history if m.get("role") == "user" and not m.get("hidden")
                                              for r in m.get("refs", []) if Path(r).is_file()))
                final = lib.parse_final(reply, attached)
                if final:  # the user's image count in the composer wins
                    final["seeds"] = director_seeds(final, tab_id, int(body.get("count") or 0))
            except ValueError as e:
                final = None
                emit({"error": str(e)})
            if final:
                emit({"final": final})
            if final and body.get("auto_run", True):
                files, stopped = self.run_for_director(final, tab_id, chat_id, emit) or ([], False)
                if stopped:  # the user pressed Stop: keep what exists, no review, wait for the user
                    appended.append({"role": "user", "hidden": True, "refs": [str(OUTPUTS / f) for f in files],
                                     "content": f"[Stopped by the user] {len(files)} image(s) were finished before the stop: "
                                                f"{', '.join(files) or 'none'}. Wait for the user's next message."})
                elif files:  # no automatic review: the user asks for one with the "Director review" button
                    n = len(tabs.get(tab_id)["iterations"]) if tab_id else 1
                    appended.append({"role": "user", "hidden": True, "refs": [str(OUTPUTS / f) for f in files],
                                     "content": (f"[Iteration {n} result] The image model made {len(files)} image(s): "
                                                 f"{', '.join(files)} (attached). Prompt used: {final['prompt']}\n"
                                                 "Do not comment now. When the user talks about them, look at them.")})
            emit({"append": appended})
            emit({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            emit({"append": appended})
            emit({"done": True, "error": f"director failed: {e}"})
        if chat_id:
            full = history + appended
            store.save_chat(chat_id, env()["CHAT_MODEL"],
                            [{k: m.get(k) for k in ("role", "content", "refs", "hidden")} for m in full], None)

    def director(self, history, tab_id, emit):
        """One streamed reply of the chat model. Old images are dropped to keep it fast."""
        temp, refine_text = getattr(self, "refine", refine_setting({}))
        sys_msgs = [{"role": "system", "content": lib.SYSTEM_PROMPT_FILE.read_text()},
                    {"role": "system", "content": refine_text}]
        if getattr(self, "review", False):
            sys_msgs.append({"role": "system", "content": REVIEW_NOTE})
        ctx = tabs.context_for_chat(tab_id) if tab_id else ""
        if ctx:
            sys_msgs.append({"role": "system", "content": ctx})
        recent = history[-24:]
        with_images = {i for i, m in enumerate(recent) if m.get("refs")}
        keep_images = set(sorted(with_images)[-3:])  # the last 3 messages with images
        allowed = (REFS.resolve(), OUTPUTS.resolve())
        msgs = []
        for i, m in enumerate(recent):
            msg = {"role": m["role"], "content": m["content"]}
            refs = [r for r in m.get("refs", []) if Path(r).resolve().parent in allowed and Path(r).is_file()]
            if refs and i in keep_images:
                msg["images"] = [lib.to_llm_image(r) for r in refs]
            msgs.append(msg)
        req = {"model": getattr(self, "models", None) and self.models["chat"] or env()["CHAT_MODEL"],
               "messages": sys_msgs + msgs, "stream": True, "think": False,
               "keep_alive": env().get("STUDIO_KEEP_ALIVE", "10m"),
               "options": {"temperature": temp, "num_ctx": 24576, "num_predict": 600}}  # short replies = fast
        text = []
        with lib.post(OLLAMA + "/api/chat", req) as r:
            for line in r:
                part = json.loads(line)
                if part.get("error"):
                    raise RuntimeError(part["error"])
                piece = part.get("message", {}).get("content", "")
                if piece:
                    text.append(piece)
                    emit({"t": piece})
        return "".join(text)

    def prompt_builder(self, body):
        """Prompt builder tab: a text-only chat with the active chat model. Streams NDJSON:
        {"t"} text, {"think"} thinking (when the Thinking switch is on), {"done"}.
        mode "on": the role from prompts/prompt-builder.md. mode "off": a plain model, the
        messages are sent as they are, no system message and no options."""
        emit = self.stream_start()

        mode = "off" if body.get("mode") == "off" else "on"
        think = body.get("think") is not False  # the Thinking switch, on by default
        chat_id, model = body.get("chat_id"), pick_models(body)["chat"]  # the tab's choice if installed
        history = [{"role": m["role"], "content": str(m.get("content", ""))} for m in body.get("messages", [])
                   if m.get("role") in ("user", "assistant")]
        text = []
        try:
            swap_to_chat(model)
            req = {"model": model, "messages": history, "stream": True,
                   "keep_alive": env().get("STUDIO_KEEP_ALIVE", "10m")}
            if mode == "on":
                req["messages"] = [{"role": "system", "content": PROMPT_BUILDER_FILE.read_text()}] + history
                req["options"] = {"num_ctx": 24576}  # same context as the director: no model reload
            if mode == "on" or not think:
                req["think"] = think  # off mode with thinking on sends nothing: the model's own default
            with lib.post(OLLAMA + "/api/chat", req) as r:
                for line in r:
                    part = json.loads(line)
                    if part.get("error"):
                        raise RuntimeError(part["error"])
                    msg = part.get("message", {})
                    if msg.get("thinking"):
                        emit({"think": msg["thinking"]})
                    if msg.get("content"):
                        text.append(msg["content"])
                        emit({"t": msg["content"]})
            emit({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            pass  # Stop: closing the Ollama request ends the reply
        except Exception as e:
            try:
                emit({"done": True, "error": f"prompt builder failed: {e}"})
            except (BrokenPipeError, ConnectionResetError):
                pass
        if chat_id:
            full = history + ([{"role": "assistant", "content": "".join(text)}] if text else [])
            store.save_prompt_chat(chat_id, model, full, mode)

    def run_for_director(self, spec, tab_id, chat_id, emit):
        """Run the approved job now and stream its progress. Returns the new file names."""
        allowed = (REFS.resolve(), OUTPUTS.resolve())
        spec["refs"] = [r for r in spec["refs"] if Path(r).resolve().parent in allowed]
        if any(j["status"] in ("queued", "running") for j in JOBS.values()):
            emit({"error": "another generation is running - stop it or wait"})
            return [], False
        jid = time.strftime("%H%M%S") + "-" + secrets.token_hex(2)
        job = {"id": jid, "status": "queued", "spec": spec, "created": time.time(), "chat_id": chat_id,
               "tab_id": tab_id, "models": self.models, "items": [{"seed": sd, "status": "queued", "seconds": 0} for sd in spec["seeds"]]}
        JOBS[jid] = job
        emit({"job": {"id": jid, "spec": spec, "models": self.models}})
        t = threading.Thread(target=run_job, args=(job,), daemon=True)
        t.start()
        while t.is_alive():
            t.join(1)
            now = time.time()
            items = [{**it, "seconds": round(now - it["started"]) if it["status"] == "running" else it.get("seconds", 0)}
                     for it in job["items"]]
            try:
                emit({"progress": items})
            except (BrokenPipeError, ConnectionResetError):
                stop_job(job)  # the user interrupted: stop the images too
                raise
        files = [it["file"] for it in job["items"] if it.get("file")]
        emit({"results": files, "job_id": jid, "stopped": bool(job.get("cancel")), "quality": job.get("quality")})
        return files, bool(job.get("cancel"))

def main():
    load_local()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"portal on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
