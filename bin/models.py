"""Model catalog, install / remove / activate / load / unload for the portal."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request

import store
import studiolib as lib
from studiolib import ROOT

CATALOG = ROOT / "config" / "models.json"
HF_HOME = ROOT / "models" / "hf"
TASKS = {}  # task id -> {id, model, status, pct, log, started}
EDIT_RUNNER = str(ROOT / "bin" / "edit_batch.py")  # full path: never touch another copy's edit


def catalog():
    return json.loads(CATALOG.read_text())["models"]


def find(model_id):
    m = next((m for m in catalog() if m["id"] == model_id), None)
    if not m:
        raise ValueError(f"unknown model: {model_id}")
    return m


def ollama_url():
    env = lib.load_env()
    return f"http://{env['STUDIO_HOST']}:{env['STUDIO_PORT']}"


def ollama_tags():
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=5) as r:
            return {m["name"]: m for m in json.load(r).get("models", [])}
    except Exception:
        return {}


def hf_token():
    return (HF_HOME / "token").is_file()


def is_active(m, env):
    if m["role"] == "chat":
        return env.get("CHAT_MODEL") == m["tag"]
    if m["role"] == "gen":
        return env.get("STUDIO_MODEL") == m["tag"]
    return env.get("EDIT_BASE_MODEL") == m["base"] and env.get("EDIT_MODEL_PATH") == m["dir"]


def overview():
    env, tags = lib.load_env(), ollama_tags()
    loaded = {m["name"]: m for m in lib.loaded_models(ollama_url())}
    edit_running = bool(subprocess.run(["pgrep", "-f", EDIT_RUNNER], capture_output=True).stdout)
    items = []
    for m in catalog():
        if m["engine"] == "ollama":
            installed = m["tag"] in tags
            in_memory = m["tag"] in loaded
        else:
            installed = (ROOT / m["dir"] / "transformer").is_dir()
            in_memory = edit_running and is_active(m, env)
        task = next((t for t in TASKS.values() if t["model"] == m["id"] and t["status"] == "running"), None)
        items.append({**m, "installed": installed, "active": is_active(m, env), "in_memory": in_memory,
                      "task": task["id"] if task else None})
    usage = shutil.disk_usage(ROOT)
    return {"models": items, "hf_token": hf_token(), "disk_free_gb": round(usage.free / 1e9),
            "models_dir_gb": round(sum(f.stat().st_size for f in (ROOT / "models").rglob("*") if f.is_file()) / 1e9, 1),
            "tasks": list(TASKS.values())[-10:]}


def activate(model_id):
    m = find(model_id)
    if m["engine"] == "ollama" and m["tag"] not in ollama_tags():
        raise ValueError("install the model first")
    if m["engine"] != "ollama" and not (ROOT / m["dir"] / "transformer").is_dir():
        raise ValueError("install the model first")
    mem = str(m.get("mem_gb", 0))
    if m["role"] == "chat":
        store.set_env({"CHAT_MODEL": m["tag"], "CHAT_MEM_GB": mem})
    elif m["role"] == "gen":
        store.set_env({"STUDIO_MODEL": m["tag"], "GEN_MEM_GB": mem})
    else:
        store.set_env({"EDIT_BASE_MODEL": m["base"], "EDIT_MODEL_PATH": m["dir"], "EDIT_MEM_GB": mem})


def remove(model_id):
    m = find(model_id)
    if is_active(m, lib.load_env()):
        raise ValueError("this model is active - choose another one first")
    if m["engine"] == "ollama":
        req = urllib.request.Request(ollama_url() + "/api/delete", method="DELETE",
                                     data=json.dumps({"model": m["tag"]}).encode())
        urllib.request.urlopen(req, timeout=60).read()
    else:
        target = (ROOT / m["dir"]).resolve()
        if target.parent != (ROOT / "models").resolve():  # only folders directly in models/
            raise ValueError("refused: path outside models/")
        shutil.rmtree(target, ignore_errors=True)


def load(model_id):
    """Warm a model into memory (Ollama models only; the edit model loads per job)."""
    m = find(model_id)
    if m["engine"] != "ollama":
        raise ValueError("the edit model loads by itself for each job and leaves memory after it")
    lib.unload_all(ollama_url())
    env = lib.load_env()
    keep = env.get("STUDIO_KEEP_ALIVE", "10m") if m["role"] == "chat" else env.get("GEN_KEEP_ALIVE", "2m")
    body = {"model": m["tag"], "keep_alive": keep}
    if m["role"] == "chat":
        body["messages"] = []
        lib.post(ollama_url() + "/api/chat", body, timeout=600).read()
    else:
        lib.post(ollama_url() + "/api/generate", body, timeout=600).read()


def unload(model_id=None):
    if not model_id:
        lib.unload_all(ollama_url())
        subprocess.run(["pkill", "-f", EDIT_RUNNER])
        return
    m = find(model_id)
    if m["engine"] == "ollama":
        lib.post(ollama_url() + "/api/generate", {"model": m["tag"], "keep_alive": 0}, timeout=30).read()
    else:
        subprocess.run(["pkill", "-f", EDIT_RUNNER])


def install(model_id):
    m = find(model_id)
    if any(t["model"] == model_id and t["status"] == "running" for t in TASKS.values()):
        raise ValueError("already installing")
    if m.get("gated") and not hf_token():
        raise ValueError("this model needs a Hugging Face login first - see the steps on the Models page")
    tid = f"t{int(time.time())}"
    task = {"id": tid, "model": model_id, "name": m["name"], "status": "running", "pct": 0, "log": [],
            "started": time.time()}
    TASKS[tid] = task
    target = _install_ollama if m["engine"] == "ollama" else _install_mflux
    threading.Thread(target=target, args=(m, task), daemon=True).start()
    return tid


def _log(task, line):
    task["log"] = (task["log"] + [line.strip()])[-12:]


def _install_ollama(m, task):
    try:
        with lib.post(ollama_url() + "/api/pull", {"model": m["tag"], "stream": True}, timeout=24 * 3600) as r:
            for line in r:
                ev = json.loads(line)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                if ev.get("total"):
                    task["pct"] = round(100 * ev.get("completed", 0) / ev["total"])
                if ev.get("status") and (not task["log"] or task["log"][-1] != ev["status"]):
                    _log(task, ev["status"])
        task["status"], task["pct"] = "done", 100
    except Exception as e:
        task["status"] = "failed"
        _log(task, f"error: {e}")


def _install_mflux(m, task):
    """Download from Hugging Face into models/hf, save an 8-bit copy, delete the full-size download."""
    env = {**os.environ, "HF_HOME": str(HF_HOME), "HF_HUB_DISABLE_TELEMETRY": "1"}
    cmd = [str(ROOT / ".venv" / "bin" / "mflux-save"), "--model", m["base"], "--quantize", "8",
           "--path", str(ROOT / m["dir"])]
    want = m.get("download_gb", 20) * 1e9
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        done = threading.Event()

        def size_watch():  # progress from the size of the download folder
            while not done.wait(3):
                got = sum(f.stat().st_size for f in (HF_HOME / "hub").rglob("*") if f.is_file()) \
                    if (HF_HOME / "hub").exists() else 0
                task["pct"] = min(95, round(100 * got / want))
        threading.Thread(target=size_watch, daemon=True).start()
        for line in p.stdout:
            for part in re.split(r"[\r\n]", line):
                if part.strip():
                    _log(task, part[-160:])
        p.wait()
        done.set()
        if p.returncode != 0:
            raise RuntimeError("mflux-save failed (see log above)")
        for sub in ("hub", "xet"):  # keep models/hf/token
            shutil.rmtree(HF_HOME / sub, ignore_errors=True)
        task["status"], task["pct"] = "done", 100
    except Exception as e:
        task["status"] = "failed"
        _log(task, f"error: {e}")
