"""Shared helpers for the terminal chat (chat.py) and the web portal (portal.py)."""
import base64
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Temp files (photo copies for the chat model). A launcher can move them with STUDIO_TMP.
TMP = Path(os.environ.get("STUDIO_TMP") or ROOT / "run")
SYSTEM_PROMPT_FILE = ROOT / "prompts" / "interviewer.md"
IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
FINAL_RE = re.compile(r"<<<FINAL>>>\s*(\{.*?\})\s*<<<END>>>", re.S)
IDENTITY = ("Keep the person's face exactly as in the reference image: same face shape, eyes, "
            "eyebrows, nose, lips, beard, skin tone, skin texture, ears, hairline and hair.")


def load_env():
    """config/studio.env as a dict (simple KEY="value" lines)."""
    env = {}
    for line in (ROOT / "config" / "studio.env").read_text().splitlines():
        m = re.match(r'\s*([A-Z_]+)="?([^"]*)"?\s*$', line)
        if m:
            env[m.group(1)] = m.group(2)
    # A launcher can run a second server and portal on other ports
    for key in ("STUDIO_PORT", "PORTAL_PORT"):
        if os.environ.get(key + "_OVERRIDE"):
            env[key] = os.environ[key + "_OVERRIDE"]
    return env


def post(url, body, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def loaded_models(url):
    try:
        with urllib.request.urlopen(url + "/api/ps", timeout=5) as r:
            return json.load(r).get("models", [])
    except Exception:
        return []


def unload_all(url):
    """Free memory before the image model runs."""
    for m in loaded_models(url):
        try:
            post(url + "/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30).read()
        except Exception:
            pass


def to_llm_image(path):
    """Small JPEG copy for the chat model to look at (saves memory and time)."""
    path = Path(path)
    out = TMP / f"llm-{abs(hash(str(path)))}.jpg"
    if not out.exists():
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "1024", str(path), "--out", str(out)],
                       check=True, capture_output=True)
    return base64.b64encode(out.read_bytes()).decode()


def parse_final(text, fallback_refs=None):
    """The approved spec from a model reply, or None. Raises ValueError if it is broken."""
    m = FINAL_RE.search(text)
    if not m:
        return None
    try:
        spec = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"final block is not valid JSON: {e}")
    # The model sometimes mistypes a photo path: use the photos attached in the chat instead
    if fallback_refs and not [r for r in spec.get("refs", []) if Path(str(r)).is_file()]:
        if spec.get("mode") == "edit" or spec.get("refs"):
            spec["refs"], spec["mode"] = list(fallback_refs), "edit"
    return fix_spec(spec)


def fix_spec(spec):
    """Guard rails the small model sometimes misses."""
    spec.setdefault("prompt", "")
    spec["refs"] = [str(r) for r in spec.get("refs", []) if Path(str(r)).is_file()]
    if spec.get("mode") not in ("gen", "edit"):
        spec["mode"] = "edit" if spec["refs"] else "gen"
    if spec["mode"] == "edit" and not spec["refs"]:
        raise ValueError("edit needs at least one existing reference image")
    w, h = int(spec.get("width", 1024)), int(spec.get("height", 1024))
    scale = min(1.0, 1664 / max(w, h))  # tested sweet spot: faster, no quality loss
    for k, v in (("width", w * scale), ("height", h * scale)):  # multiples of 16
        spec[k] = max(256, int(round(v / 16) * 16))
    spec["steps"] = int(spec.get("steps", 9 if spec["mode"] == "gen" else 4))
    spec["seeds"] = [int(x) for x in spec.get("seeds", [1])][:4] or [1]
    if spec["mode"] == "edit" and "exactly" not in spec["prompt"]:
        spec["prompt"] = spec["prompt"].rstrip() + " " + IDENTITY
    # Face pass: after a new scene, redo only each listed face from its own photo (max 3 people)
    faces = []
    for f in spec.get("face_pass") or []:
        if isinstance(f, dict) and Path(str(f.get("ref", ""))).is_file():
            faces.append({"ref": str(f["ref"]), "who": str(f.get("who") or "the person")[:80]})
    spec["face_pass"] = faces[:3]
    return spec


FACE_PASS = ("Image 1 is the photo to keep. Image 2 is the identity reference. Keep image 1 exactly: the "
             "scene, every other person, body pose, head angle and size, clothing, hands, light, shadows, "
             "colors and framing. Change only the face and hair of {who} so it becomes exactly the person "
             "from image 2: same face shape, eyes, eyebrows, nose, lips, beard, skin tone, skin texture, "
             "ears, hairline and hair. Match the light, shadows and color of image 1. "
             "Real photo, natural skin texture with pores, no retouching, no smoothing.")
