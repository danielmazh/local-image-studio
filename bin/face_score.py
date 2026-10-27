"""Face identity score (ArcFace cosine similarity, 0-1) of result images against face photos.

usage: face_score.py ref1.jpg[,ref2.jpg] img1.png [img2.png ...]
Prints one JSON line: {"img1.png": {"score": 0.91, "per_ref": [0.91, 0.88]}, ...}
score = the weakest person: for each reference, the best matching face in the image; then the minimum.
Two real photos of the same person score about 0.9. Below about 0.3 it is another person.
The model (insightface buffalo_l) downloads once into models/insightface.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis

ROOT = Path(__file__).resolve().parent.parent
app = FaceAnalysis(name="buffalo_l", root=str(ROOT / "models" / "insightface"),
                   providers=["CPUExecutionProvider"], allowed_modules=["detection", "recognition"])
app.prepare(ctx_id=-1, det_size=(640, 640))


def faces(path):
    img = cv2.imread(str(path))
    if img is None and Path(path).is_file():  # HEIC and other types OpenCV cannot read: a JPEG copy
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d) / "face.jpg"
            subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(jpg)], capture_output=True)
            img = cv2.imread(str(jpg))
    if img is None:
        return []
    pad = img.shape[0] // 3  # close-ups fill the frame; a border helps the detector
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(127, 127, 127))
    found = sorted(app.get(img), key=lambda f: -(f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return [f.normed_embedding for f in found]  # largest face first


def main():
    refs = []
    for r in sys.argv[1].split(","):
        f = faces(r)
        refs.append(f[0] if f else None)  # the photo's main (largest) face
    out = {}
    for p in sys.argv[2:]:
        if not Path(p).is_file():
            out[os.path.basename(p)] = {"score": None, "per_ref": [], "error": "file not found"}
            continue
        found = faces(p)
        per = []
        for ref in refs:
            per.append(round(max((float(np.dot(ref, e)) for e in found), default=0.0), 3) if ref is not None else None)
        valid = [x for x in per if x is not None]
        out[os.path.basename(p)] = {"score": min(valid) if valid else None, "per_ref": per}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
