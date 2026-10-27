#!/usr/bin/env python3
"""Drift guard for the portal languages (stdlib only). Exit 1 with a clear message on any problem.

Checks:
- portal/i18n/en.json and he.json have exactly the same keys, and no empty values
- every key used in portal/index.html exists, and every key in the JSON is used
  (uses: data-i18n="k", data-i18n-title/-placeholder/-aria="k", t("k"), tn("k") = k.one + k.other,
  .dataset.i18n... = "k")
- no Hebrew letters in portal/index.html (Hebrew lives only in he.json)
- no en dash or em dash in the JSON files, index.html, launcher.html or the examples docs
- every entry in config/models.json that has use_he also has use_en
- docs/examples.md and docs/examples.he.md have the same number of "## " sections,
  ```try blocks and images
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "portal" / "i18n"
PAGE = ROOT / "portal" / "index.html"
HEBREW = re.compile(r"[֐-׿]")
DASHES = re.compile("[\u2013\u2014]")  # en dash, em dash
errors = []


def load(name):
    p = I18N / name
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{p.relative_to(ROOT)}: cannot read JSON ({e})")
        return {}
    if not isinstance(d, dict) or not all(isinstance(v, str) for v in d.values()):
        errors.append(f"{p.relative_to(ROOT)}: must be a flat object of strings")
        return {}
    return d


def short(keys, n=20):
    keys = sorted(keys)
    return ", ".join(keys[:n]) + (f" (+{len(keys) - n} more)" if len(keys) > n else "")


en, he = load("en.json"), load("he.json")
if set(en) != set(he):
    if set(en) - set(he):
        errors.append(f"keys in en.json but not in he.json: {short(set(en) - set(he))}")
    if set(he) - set(en):
        errors.append(f"keys in he.json but not in en.json: {short(set(he) - set(en))}")
for lang, d in (("en", en), ("he", he)):
    empty = [k for k, v in d.items() if not v.strip()]
    if empty:
        errors.append(f"empty values in {lang}.json: {short(empty)}")

html = PAGE.read_text(encoding="utf-8")
used = set(re.findall(r'data-i18n(?:-title|-placeholder|-aria)?="([^"]+)"', html))
used |= set(re.findall(r'\.dataset\.i18n\w*\s*=\s*["\']([^"\']+)["\']', html))
used |= set(re.findall(r'(?<![\w.$])t\(\s*["\']([^"\']+)["\']', html))
for k in re.findall(r'(?<![\w.$])tn\(\s*["\']([^"\']+)["\']', html):
    used |= {k + ".one", k + ".other"}
keys = set(en) | set(he)
if used - keys:
    errors.append(f"keys used in index.html but missing in the JSON: {short(used - keys)}")
if keys - used:
    errors.append(f"keys in the JSON but not used in index.html: {short(keys - used)}")

bad = [n for n, line in enumerate(html.splitlines(), 1) if HEBREW.search(line)]
if bad:
    errors.append(f"Hebrew letters in portal/index.html (move them to he.json), lines: {bad[:20]}")

for p in [I18N / "en.json", I18N / "he.json", PAGE, ROOT / "portal" / "launcher.html",
          ROOT / "docs" / "examples.md", ROOT / "docs" / "examples.he.md"]:
    if p.is_file():
        lines = [n for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if DASHES.search(line)]
        if lines:
            errors.append(f"en dash or em dash in {p.relative_to(ROOT)} (use a hyphen), lines: {lines[:20]}")

catalog = ROOT / "config" / "models.json"


def walk(node, path):
    if isinstance(node, dict):
        if "use_he" in node and not str(node.get("use_en", "")).strip():
            errors.append(f"config/models.json: {node.get('id', path)} has use_he but no use_en")
        for k, v in node.items():
            walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]")


try:
    walk(json.loads(catalog.read_text(encoding="utf-8")), "models.json")
except (OSError, ValueError) as e:
    errors.append(f"config/models.json: cannot read JSON ({e})")


def shape(p):
    s = p.read_text(encoding="utf-8")
    return {"'## ' sections": len(re.findall(r"^## ", s, re.M)),
            "```try blocks": len(re.findall(r"^```try\s*$", s, re.M)),
            "images": len(re.findall(r"!\[[^\]]*\]\([^)]+\)", s))}


ex_en, ex_he = ROOT / "docs" / "examples.md", ROOT / "docs" / "examples.he.md"
if not (ex_en.is_file() and ex_he.is_file()):
    errors.append("docs/examples.md and docs/examples.he.md must both exist")
else:
    a, b = shape(ex_en), shape(ex_he)
    for k in a:
        if a[k] != b[k]:
            errors.append(f"examples docs differ in {k}: examples.md has {a[k]}, examples.he.md has {b[k]}")

if errors:
    print("check-i18n: FAILED")
    for e in errors:
        print(" - " + e)
    sys.exit(1)
print(f"check-i18n: OK ({len(en)} keys in each language, {len(used)} used in index.html)")
