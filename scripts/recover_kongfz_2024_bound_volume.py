#!/usr/bin/env python3
"""Recover image metadata from the public Kongfz listing 齐鲁少年/2024年合订本.

The listing exposes three 'view original' product images. This script extracts candidate product
image URLs, transiently inspects dimensions and OCR text, and commits metadata only. It does not
store or redistribute the product images.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "repost_fullpage" / "kongfz_2024_bound_volume"
OUT.mkdir(parents=True, exist_ok=True)
PAGE = "https://book.kongfz.com/837895/9278771011/"
UA = "Mozilla/5.0 (compatible; qilu-shaonian-archive/1.0; +https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
MAX_HTML = 10 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024


def get(url: str, limit: int, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        return raw, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def decode(raw: bytes):
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def extract_urls(text: str):
    text = html.unescape(text).replace("\\/", "/")
    urls = set()
    # Absolute image URLs in src/data-src/JSON.
    for u in re.findall(r'https?://[^"\'<>\s]+', text, flags=re.I):
        u = u.rstrip("\\,);}")
        if re.search(r"(?i)\.(?:jpe?g|png|webp)(?:\?|$)", u):
            urls.add(u)
    # Protocol-relative CDN URLs.
    for u in re.findall(r'//[^"\'<>\s]+', text):
        u = u.rstrip("\\,);}")
        if re.search(r"(?i)\.(?:jpe?g|png|webp)(?:\?|$)", u):
            urls.add("https:" + u)
    # Filter obvious UI/static assets; retain large/product/photo domains and product-id context.
    selected = []
    for u in urls:
        low = u.lower()
        if any(x in low for x in ("logo", "icon", "sprite", "avatar", "qrcode", "loading", "default")):
            continue
        if "kongfz" in low or "kf" in urllib.parse.urlsplit(u).netloc.lower():
            selected.append(u)
    return sorted(set(selected))


def ocr_bytes(raw: bytes, suffix: str):
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(raw); tmp.flush()
            p = subprocess.run(
                ["tesseract", tmp.name, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25, check=False,
            )
            return " ".join(p.stdout.split())[:1800], "" if p.returncode == 0 else p.stderr[-500:]
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"[:500]


def inspect(url: str):
    row = {"source_url": url, "resolved_url": "", "content_type": "", "bytes": "", "sha256": "", "width": "", "height": "", "image_format": "", "ocr_hits": "", "ocr_excerpt": "", "error": ""}
    try:
        raw, final, headers = get(url, MAX_IMAGE)
        row.update({"resolved_url": final, "content_type": headers.get("content-type", ""), "bytes": str(len(raw)), "sha256": hashlib.sha256(raw).hexdigest()})
        try:
            im = Image.open(io.BytesIO(raw)); row["width"] = str(im.width); row["height"] = str(im.height); row["image_format"] = im.format or ""
        except Exception:
            pass
        if row["width"] and row["height"] and int(row["width"]) >= 700 and int(row["height"]) >= 700:
            suffix = ".png" if row["image_format"] == "PNG" else ".jpg"
            text, err = ocr_bytes(raw, suffix)
            row["ocr_excerpt"] = text
            row["ocr_hits"] = "|".join(k for k in ["齐鲁少年", "2024", "期", "版", "年", "月", "日"] if k in text)
            if err:
                row["error"] = err
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"[:1000]
    return row


def main():
    page_error = ""; page_title_found = False; urls = []; rows = []
    try:
        raw, _, _ = get(PAGE, MAX_HTML)
        text = decode(raw)
        page_title_found = "齐鲁少年/2024年合订本" in text
        urls = extract_urls(text)
        for u in urls:
            rows.append(inspect(u))
    except Exception as e:
        page_error = f"{type(e).__name__}: {e}"

    fields = ["source_url", "resolved_url", "content_type", "bytes", "sha256", "width", "height", "image_format", "ocr_hits", "ocr_excerpt", "error"]
    with (OUT / "images.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    likely = [r for r in rows if "齐鲁少年" in r["ocr_hits"] or (r["width"] and r["height"] and int(r["width"]) >= 1000 and int(r["height"]) >= 1000)]
    report = {
        "listing_url": PAGE,
        "page_title_found": page_title_found,
        "page_error": page_error,
        "candidate_image_urls": len(urls),
        "reachable_images": sum(bool(r["sha256"]) for r in rows),
        "images_with_qilu_shaonian_ocr": sum("齐鲁少年" in r["ocr_hits"] for r in rows),
        "likely_product_images": likely,
        "notes": [
            "Kongfz search cache independently identifies this listing as 齐鲁少年/2024年合订本 with three original-image entries.",
            "OCR is triage only; readable issue/date evidence must be independently verified before promotion.",
            "No product image bytes are committed."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
