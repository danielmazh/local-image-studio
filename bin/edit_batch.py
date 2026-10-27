"""Edit with reference photos: load the FLUX.2 edit model ONCE, make every variation, exit.

Called by `bin/studio edit` (not by hand). Prints one line per image for the portal:
  studio: item <n> start
  studio: saved <path> (<seconds>s)
The process ends after the last image, so the model leaves memory at once.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--model-path", required=True)
ap.add_argument("--base-model", required=True)
ap.add_argument("--prompt", required=True)
ap.add_argument("--refs", nargs="+", required=True)
ap.add_argument("--seeds", required=True, help="comma list, e.g. 11,12,13")
ap.add_argument("--steps", type=int, default=4)
ap.add_argument("--width", type=int)
ap.add_argument("--height", type=int)
ap.add_argument("--out-prefix", required=True, help="path prefix; -v<n>.png is added")
ap.add_argument("--lora", nargs=2, action="append", metavar=("PATH", "SCALE"), default=[])
a = ap.parse_args()

# HEIC and other types the model cannot open: a JPEG copy in a temp folder (deleted at exit)
_tmp = tempfile.TemporaryDirectory(prefix="studio-edit-")
for i, ref in enumerate(a.refs):
    if Path(ref).suffix.lower() in {".heic", ".heif", ".tif", ".tiff", ".gif", ".bmp", ".avif"}:
        jpg = Path(_tmp.name) / f"ref{i + 1}.jpg"
        subprocess.run(["sips", "-s", "format", "jpeg", ref, "--out", str(jpg)], capture_output=True)
        if jpg.is_file():
            a.refs[i] = str(jpg)

os.environ.setdefault("HF_HUB_OFFLINE", "1")
from mflux.models.common.config import ModelConfig  # noqa: E402
from mflux.models.flux2.variants import Flux2KleinEdit  # noqa: E402

t_load = time.time()
model = Flux2KleinEdit(
    model_path=a.model_path,
    model_config=ModelConfig.from_name(a.base_model),
    lora_paths=[p for p, _ in a.lora] or None,
    lora_scales=[float(s) for _, s in a.lora] or None,
)
print(f"studio: model loaded ({time.time() - t_load:.0f}s)", flush=True)

size = {}
if a.width and a.height:
    size = {"width": a.width, "height": a.height}
for n, seed in enumerate((int(s) for s in a.seeds.split(",")), start=1):
    print(f"studio: item {n} start", flush=True)
    t0 = time.time()
    try:
        image = model.generate_image(seed=seed, prompt=a.prompt, image_paths=a.refs,
                                     num_inference_steps=a.steps, **size)
        out = Path(f"{a.out_prefix}-v{n}.png")
        image.save(str(out))
        out.chmod(0o600)
        print(f"studio: saved {out} ({time.time() - t0:.0f}s)", flush=True)
    except Exception as e:  # keep going with the next seed
        print(f"studio: item {n} failed: {e}", file=sys.stderr, flush=True)
        print(f"studio: item {n} failed", flush=True)
