#!/usr/bin/env python3
"""OCR the already-inventoried 2026 Sohu repost images for newspaper-specific terms.

Image bytes are downloaded only to temporary files and deleted immediately. The repository keeps
only compact OCR excerpts and hashes already present in images.csv.
"""
from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "repost_fullpage" / "sohu_2026_zhenhua"
SRC = BASE / "images.csv"
OUT = BASE / "ocr.csv"
UA = "Mozilla/5.0 (compatible; qilu-shaonian-archive/1.0)"
KEYS = ["齐鲁少年", "振华", "头版", "版", "实验学校", "报"]


def download(url: str, path: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.sohu.com/"})
    with urllib.request.urlopen(req, timeout=20) as r, open(path, "wb") as f:
        data = r.read(15 * 1024 * 1024)
        f.write(data)


def ocr(path: str):
    try:
        p = subprocess.run(
            ["tesseract", path, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25, check=False,
        )
        return " ".join(p.stdout.split())[:3000], "" if p.returncode == 0 else p.stderr[-500:]
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"[:500]


def main():
    source = list(csv.DictReader(SRC.open(encoding="utf-8")))
    rows = []
    for i, r in enumerate(source, 1):
        text = ""; error = ""
        suffix = ".png" if r.get("image_format") == "PNG" else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            try:
                download(r["source_url"], tmp.name)
                text, error = ocr(tmp.name)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"[:500]
        hits = [k for k in KEYS if k in text]
        rows.append({
            "index": str(i), "source_url": r["source_url"], "sha256": r.get("sha256", ""),
            "width": r.get("width", ""), "height": r.get("height", ""),
            "keyword_hits": "|".join(hits), "ocr_excerpt": text[:1200], "error": error,
        })
    fields = ["index", "source_url", "sha256", "width", "height", "keyword_hits", "ocr_excerpt", "error"]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    report = {
        "images_ocr_attempted": len(rows),
        "images_with_qilu_shaonian": sum("齐鲁少年" in r["keyword_hits"] for r in rows),
        "images_with_zhenhua": sum("振华" in r["keyword_hits"] for r in rows),
        "images_with_frontpage": sum("头版" in r["keyword_hits"] for r in rows),
        "hit_rows": [
            {k: r[k] for k in ("index", "source_url", "width", "height", "keyword_hits", "ocr_excerpt")}
            for r in rows if r["keyword_hits"]
        ],
        "notes": ["OCR is triage evidence only; candidate images still require visual/content verification before promotion.", "Image bytes are not committed."],
    }
    (BASE / "ocr_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
