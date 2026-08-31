#!/usr/bin/env python3
"""Enumerate independently archived qlsn.com media for the 2007-2010 era.

This v2 pass queries Wayback CDX directly instead of relying on the repository's earlier
seed inventory.  It therefore catches image/PDF captures whose parent HTML was never
saved. Newspaper bytes are transient; only URLs, capture metadata, hashes, geometry and
short OCR excerpts are committed.
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
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "independent_2007_2010_wayback_media"
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "qilu-shaonian-independent-media/2.0", "Accept": "image/*,application/pdf,*/*"})
CDX = "https://web.archive.org/cdx/search/cdx"
KEYS = ["齐鲁少年", "齐 鲁 少 年", "山东省少工委", "一版", "二版", "三版", "四版", "少先队", "本报记者"]
ISSUE_RE = re.compile(r"(?:第\s*)?(\d{3,4})\s*期")
IMG_EXT = re.compile(r"(?i)\.(?:jpe?g|png|gif|bmp|tiff?)(?:\?|$)")
PDF_EXT = re.compile(r"(?i)\.pdf(?:\?|$)")


def cdx_query(pattern: str, mime: str):
    params = [
        ("url", pattern),
        ("from", "2007"),
        ("to", "2011"),
        ("output", "json"),
        ("fl", "timestamp,original,statuscode,mimetype,digest"),
        ("filter", "statuscode:200"),
        ("filter", f"mimetype:{mime}"),
        ("collapse", "digest"),
    ]
    last = ""
    for n in range(5):
        try:
            r = S.get(CDX, params=params, timeout=(20, 90))
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"retryable status {r.status_code}")
            r.raise_for_status()
            data = r.json()
            if not data:
                return [], ""
            header = data[0]
            return [dict(zip(header, row)) for row in data[1:]], ""
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(min(20, 2 ** n))
    return [], last


def replay(row):
    return f"https://web.archive.org/web/{row['timestamp']}id_/{row['original']}"


def fetch(url: str):
    last = ""
    for n in range(4):
        try:
            r = S.get(url, timeout=(15, 60), allow_redirects=True)
            r.raise_for_status()
            return r.content, r.url, r.headers.get("Content-Type", ""), ""
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(1 + n * 2)
    return b"", url, "", last


def ocr(img: Image.Image) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im = img.convert("RGB")
        if max(im.size) > 3200:
            scale = 3200 / max(im.size)
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
        im.save(f.name)
        p = subprocess.run(
            ["tesseract", f.name, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=50,
        )
        return re.sub(r"\s+", " ", p.stdout).strip()


def main():
    query_rows = []
    query_errors = []
    patterns = ["www.qlsn.com/*", "qlsn.com/*"]
    mimes = ["image/jpeg", "image/png", "image/gif", "application/pdf"]
    for pattern in patterns:
        for mime in mimes:
            rows, err = cdx_query(pattern, mime)
            query_rows.extend(rows)
            if err:
                query_errors.append({"pattern": pattern, "mimetype": mime, "error": err})
            print(f"CDX {pattern} {mime}: {len(rows)} rows err={err[:80]}", flush=True)
            time.sleep(1.0)

    # Some old captures have generic/incorrect mimetype metadata. Add extension-based
    # inventory with one domain query if the typed queries produced little evidence.
    if len(query_rows) < 20:
        for pattern in patterns:
            params = [
                ("url", pattern), ("from", "2007"), ("to", "2011"), ("output", "json"),
                ("fl", "timestamp,original,statuscode,mimetype,digest"), ("filter", "statuscode:200"),
                ("collapse", "digest"),
            ]
            try:
                r = S.get(CDX, params=params, timeout=(20, 120)); r.raise_for_status(); data = r.json()
                if data:
                    header = data[0]
                    for raw in data[1:]:
                        row = dict(zip(header, raw)); u = row.get("original", "")
                        if IMG_EXT.search(u) or PDF_EXT.search(u):
                            query_rows.append(row)
            except Exception as e:
                query_errors.append({"pattern": pattern, "mimetype": "extension_fallback", "error": f"{type(e).__name__}: {e}"})

    uniq = {}
    for row in query_rows:
        u = row.get("original", "")
        host = (urlparse(u).hostname or "").lower()
        if host not in {"qlsn.com", "www.qlsn.com"}:
            continue
        key = row.get("digest") or f"{row.get('timestamp','')}|{u}"
        uniq[key] = row
    rows = sorted(uniq.values(), key=lambda r: (r.get("timestamp", ""), r.get("original", "")))
    # A pathological CDX response should not turn CI into an unbounded fetcher.
    rows = rows[:800]

    out = []
    for i, r in enumerate(rows, 1):
        rec = {
            "capture_timestamp": r.get("timestamp", ""),
            "original": r.get("original", ""),
            "archive_url": replay(r),
            "inventory_mimetype": r.get("mimetype", ""),
            "digest": r.get("digest", ""),
            "resolved_url": "", "content_type": "", "bytes": "", "sha256": "",
            "width": "", "height": "", "format": "", "aspect": "", "large": "no",
            "page_geometry": "no", "ocr_hits": "", "issue_numbers": "", "ocr_excerpt": "", "error": "",
        }
        body, resolved, ctype, err = fetch(rec["archive_url"])
        if err:
            rec["error"] = err
        else:
            rec["resolved_url"] = resolved
            rec["content_type"] = ctype
            rec["bytes"] = str(len(body))
            rec["sha256"] = hashlib.sha256(body).hexdigest()
            if body.startswith(b"%PDF"):
                rec["format"] = "PDF"
                rec["large"] = "yes"
                rec["page_geometry"] = "yes"
            else:
                try:
                    img = Image.open(io.BytesIO(body)); img.load()
                    rec["format"] = img.format or ""
                    rec["width"] = str(img.width); rec["height"] = str(img.height)
                    aspect = img.width / img.height if img.height else 0
                    rec["aspect"] = f"{aspect:.3f}"
                    large = img.width >= 700 and img.height >= 700 and len(body) >= 60_000
                    pagegeom = img.height >= 900 and 0.40 <= aspect <= 0.90
                    rec["large"] = "yes" if large else "no"
                    rec["page_geometry"] = "yes" if pagegeom else "no"
                    if large or pagegeom:
                        text = ocr(img)
                        rec["ocr_hits"] = "|".join(k for k in KEYS if k in text)
                        rec["issue_numbers"] = "|".join(sorted(set(ISSUE_RE.findall(text)), key=int))
                        rec["ocr_excerpt"] = text[:1600]
                except Exception as e:
                    rec["error"] = f"image_parse:{type(e).__name__}: {e}"
        out.append(rec)
        print(f"{i}/{len(rows)} {rec['original']} {rec['width']}x{rec['height']} fmt={rec['format']} hits={rec['ocr_hits']} issues={rec['issue_numbers']} err={rec['error'][:60]}", flush=True)

    fields = ["capture_timestamp", "original", "archive_url", "inventory_mimetype", "digest", "resolved_url", "content_type", "bytes", "sha256", "width", "height", "format", "aspect", "large", "page_geometry", "ocr_hits", "issue_numbers", "ocr_excerpt", "error"]
    with (OUT / "media.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
    with (OUT / "cdx_errors.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pattern", "mimetype", "error"]); w.writeheader(); w.writerows(query_errors)

    useful = [x for x in out if x["format"] == "PDF" or x["page_geometry"] == "yes" or x["ocr_hits"] or x["issue_numbers"]]
    report = {
        "cdx_queries": len(patterns) * len(mimes),
        "cdx_query_errors": len(query_errors),
        "cdx_media_rows_before_dedupe": len(query_rows),
        "media_selected_after_dedupe": len(rows),
        "responses_ok": sum(not x["error"] for x in out),
        "pdf_magic_rows": sum(x["format"] == "PDF" for x in out),
        "large_images": sum(x["large"] == "yes" for x in out),
        "page_geometry_images": sum(x["page_geometry"] == "yes" for x in out),
        "ocr_keyword_rows": sum(bool(x["ocr_hits"]) for x in out),
        "ocr_issue_number_rows": sum(bool(x["issue_numbers"]) for x in out),
        "candidate_rows": len(useful),
        "candidates": [{k: x[k] for k in ("capture_timestamp", "original", "archive_url", "bytes", "width", "height", "format", "sha256", "ocr_hits", "issue_numbers", "ocr_excerpt")} for x in useful[:100]],
        "notes": [
            "CDX is queried directly for qlsn.com/www.qlsn.com 2007-2011 image/PDF captures.",
            "Parent HTML is not required for discovery.",
            "Capture date is never treated as newspaper publication date by itself.",
            "Bytes are transient and are not committed.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
