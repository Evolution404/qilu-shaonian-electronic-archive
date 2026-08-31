#!/usr/bin/env python3
"""Inspect archived qlsn.com media referenced by 2004-2010 legacy pages.

Downloads are transient. Only URL, dimensions, hashes and short OCR excerpts are committed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "legacy_2004_2010_inventory" / "media_refs.csv"
OUT = ROOT / "data" / "legacy_2004_2010_media_analysis"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/legacy-media-1.0"

KEYWORDS = ["齐鲁少年", "第", "期", "版", "山东省少工委", "少先队", "记者", "报"]


def fetch(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,application/pdf,*/*"})
    with urllib.request.urlopen(req, timeout=18) as r:
        data = r.read(12 * 1024 * 1024)
        return data, r.geturl(), r.headers.get_content_type()


def ocr_image(img: Image.Image) -> str:
    # OCR only likely document/large editorial images; no bytes persist after process exit.
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im = img.convert("RGB")
        # Cap very large images for predictable CI cost while preserving text readability.
        if max(im.size) > 2600:
            scale = 2600 / max(im.size)
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
        im.save(f.name)
        proc = subprocess.run(
            ["tesseract", f.name, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=35,
        )
        return re.sub(r"\s+", " ", proc.stdout).strip()


def main() -> int:
    rows = list(csv.DictReader(SRC.open(newline="", encoding="utf-8")))
    selected = []
    seen = set()
    for row in rows:
        url = row.get("archived_media_url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        selected.append(row)

    out_rows = []
    for i, row in enumerate(selected, 1):
        rec = {
            "parent_original": row.get("parent_original", ""),
            "parent_archive_url": row.get("parent_archive_url", ""),
            "parent_content_year": row.get("parent_content_year", ""),
            "parent_issue_numbers": row.get("parent_issue_numbers", ""),
            "media_original": row.get("media_original", ""),
            "archived_media_url": row.get("archived_media_url", ""),
            "resolved_url": "", "content_type": "", "bytes": "", "sha256": "",
            "width": "", "height": "", "format": "", "aspect": "",
            "large_image": "no", "document_geometry": "no", "ocr_hits": "", "ocr_excerpt": "", "error": "",
        }
        try:
            data, resolved, ctype = fetch(rec["archived_media_url"])
            rec["resolved_url"] = resolved; rec["content_type"] = ctype
            rec["bytes"] = str(len(data)); rec["sha256"] = hashlib.sha256(data).hexdigest()
            if data.startswith(b"%PDF"):
                rec["format"] = "PDF"
            else:
                img = Image.open(io.BytesIO(data)); img.load()
                rec["width"] = str(img.width); rec["height"] = str(img.height); rec["format"] = img.format or ""
                aspect = img.width / img.height if img.height else 0
                rec["aspect"] = f"{aspect:.3f}"
                large = img.width >= 500 and img.height >= 500 and len(data) >= 35_000
                doc_geom = img.height >= 700 and 0.45 <= aspect <= 0.85
                rec["large_image"] = "yes" if large else "no"
                rec["document_geometry"] = "yes" if doc_geom else "no"
                if large or doc_geom:
                    text = ocr_image(img)
                    hits = [k for k in KEYWORDS if k in text]
                    rec["ocr_hits"] = "|".join(hits)
                    rec["ocr_excerpt"] = text[:1000]
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        out_rows.append(rec)
        print(f"{i}/{len(selected)} {rec['media_original']} {rec['width']}x{rec['height']} hits={rec['ocr_hits']} err={rec['error'][:60]}", flush=True)

    fields = ["parent_original","parent_archive_url","parent_content_year","parent_issue_numbers","media_original","archived_media_url","resolved_url","content_type","bytes","sha256","width","height","format","aspect","large_image","document_geometry","ocr_hits","ocr_excerpt","error"]
    with (OUT / "media.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out_rows)

    useful = [r for r in out_rows if r["large_image"] == "yes" or r["document_geometry"] == "yes" or r["ocr_hits"]]
    report = {
        "archived_media_urls_selected": len(selected),
        "responses_ok": sum(not r["error"] for r in out_rows),
        "image_rows": sum(bool(r["width"]) for r in out_rows),
        "large_images": sum(r["large_image"] == "yes" for r in out_rows),
        "document_geometry_images": sum(r["document_geometry"] == "yes" for r in out_rows),
        "ocr_keyword_rows": sum(bool(r["ocr_hits"]) for r in out_rows),
        "candidate_rows": len(useful),
        "candidate_media": [{k: r[k] for k in ("parent_content_year","parent_issue_numbers","media_original","archived_media_url","width","height","sha256","ocr_hits","ocr_excerpt")} for r in useful[:40]],
        "notes": ["Media bytes are downloaded transiently and are not committed.", "OCR and geometry are triage evidence only; newspaper identity/issue must be verified before promotion."],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
