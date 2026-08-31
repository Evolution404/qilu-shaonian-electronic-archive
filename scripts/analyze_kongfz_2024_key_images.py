#!/usr/bin/env python3
"""Resolve which Kongfz images belong to the 2024 Qilu Shaonian listing and extract issue/date metadata.

Uses the already-discovered image URLs. It correlates each basename to compact listing-HTML context,
then runs multi-pass OCR on full images and header/top crops. Image bytes are transient and never
committed; only derived metadata/excerpts are saved.
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
import urllib.request
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "repost_fullpage" / "kongfz_2024_bound_volume"
SRC = BASE / "images.csv"
OUT = BASE / "key_image_analysis.csv"
PAGE = "https://book.kongfz.com/837895/9278771011/"
UA = "Mozilla/5.0 (compatible; qilu-shaonian-archive/1.0; +https://github.com/Evolution404/qilu-shaonian-electronic-archive)"


def get(url: str, limit: int, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        return raw


def decode(raw: bytes):
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def compact_context(page: str, basename: str):
    positions = [m.start() for m in re.finditer(re.escape(basename), page, re.I)]
    out = []
    for p in positions[:4]:
        chunk = page[max(0, p-700): p+700]
        chunk = html.unescape(chunk).replace("\\/", "/")
        chunk = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", chunk)
        chunk = re.sub(r"(?s)<[^>]+>", " ", chunk)
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if chunk:
            out.append(chunk[:1200])
    return " || ".join(out)


def preprocess(im: Image.Image, crop: str):
    if crop == "header":
        im = im.crop((0, 0, im.width, max(1, int(im.height * 0.34))))
    elif crop == "tophalf":
        im = im.crop((0, 0, im.width, max(1, int(im.height * 0.55))))
    gray = ImageOps.grayscale(im)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)
    gray = gray.filter(ImageFilter.SHARPEN)
    if gray.width < 2200:
        scale = 2200 / gray.width
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
    return gray


def tesseract(im: Image.Image, psm: int):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        im.save(tmp.name, format="PNG")
        try:
            p = subprocess.run(
                ["tesseract", tmp.name, "stdout", "-l", "chi_sim+eng", "--psm", str(psm)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=35, check=False,
            )
            text = " ".join(p.stdout.split())
            return text[:7000], "" if p.returncode == 0 else p.stderr[-500:]
        except Exception as e:
            return "", f"{type(e).__name__}: {e}"[:500]


def derive(text: str):
    issue_hits = sorted(set(re.findall(r"第\s*(1[5-7]\d{2})\s*期", text)))
    range_hits = []
    for m in re.finditer(r"第?\s*(1[5-7]\d{2})\s*[—–-一至到]\s*(1[5-7]\d{2})\s*期", text):
        range_hits.append(f"{m.group(1)}-{m.group(2)}")
    dates = []
    for m in re.finditer(r"(2024)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
        dates.append(f"2024-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    page_hits = sorted(set(re.findall(r"第?\s*([一二三四五六七八A-D1-8])\s*版", text)))
    keywords = [k for k in ["齐鲁少年", "山东省少工委", "共青团山东省委", "山东青年报刊传媒中心", "2024年度合订本", "出版", "主管", "主办"] if k in text]
    return issue_hits, sorted(set(range_hits)), sorted(set(dates)), page_hits, keywords


def main():
    source = list(csv.DictReader(SRC.open(encoding="utf-8")))
    candidates = [r for r in source if r.get("source_url", "").endswith("_b.jpg") and r.get("width") == "1600" and r.get("height") == "2133"]
    page_text = ""
    page_error = ""
    try:
        page_text = decode(get(PAGE, 10 * 1024 * 1024))
    except Exception as e:
        page_error = f"{type(e).__name__}: {e}"

    rows = []
    for r in candidates:
        url = r["source_url"]
        basename = url.rsplit("/", 1)[-1]
        row = {
            "source_url": url, "sha256": r.get("sha256", ""), "html_occurrences": str(page_text.lower().count(basename.lower())) if page_text else "0",
            "html_context": compact_context(page_text, basename) if page_text else "", "issue_hits": "", "issue_range_hits": "",
            "date_hits": "", "page_hits": "", "identity_keywords": "", "header_ocr": "", "full_ocr_excerpt": "", "error": "",
        }
        try:
            raw = get(url, 20 * 1024 * 1024)
            if row["sha256"] and hashlib.sha256(raw).hexdigest() != row["sha256"]:
                row["error"] += "sha256_changed;"
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            texts = []
            for crop, psm in (("header", 6), ("header", 11), ("tophalf", 6), ("full", 3), ("full", 11)):
                text, err = tesseract(preprocess(im, crop), psm)
                if text:
                    texts.append(text)
                    if crop == "header":
                        row["header_ocr"] = (row["header_ocr"] + " || " + text)[:3500].strip(" |")
                if err:
                    row["error"] += err + ";"
            merged = " ".join(texts)
            issues, ranges, dates, pages, keys = derive(merged)
            row["issue_hits"] = "|".join(issues)
            row["issue_range_hits"] = "|".join(ranges)
            row["date_hits"] = "|".join(dates)
            row["page_hits"] = "|".join(pages)
            row["identity_keywords"] = "|".join(keys)
            row["full_ocr_excerpt"] = merged[:4500]
        except Exception as e:
            row["error"] += f"{type(e).__name__}: {e}"[:1000]
        rows.append(row)

    fields = ["source_url", "sha256", "html_occurrences", "html_context", "issue_hits", "issue_range_hits", "date_hits", "page_hits", "identity_keywords", "header_ocr", "full_ocr_excerpt", "error"]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    report = {
        "listing_url": PAGE,
        "page_fetch_error": page_error,
        "large_portrait_images_analyzed": len(rows),
        "images_with_html_listing_context": sum(int(r["html_occurrences"] or 0) > 0 for r in rows),
        "images_with_issue_number": sum(bool(r["issue_hits"]) for r in rows),
        "images_with_issue_range": sum(bool(r["issue_range_hits"]) for r in rows),
        "images_with_2024_date": sum(bool(r["date_hits"]) for r in rows),
        "images_with_identity_keywords": sum(bool(r["identity_keywords"]) for r in rows),
        "high_value_rows": [
            {k: r[k] for k in ("source_url", "html_occurrences", "issue_hits", "issue_range_hits", "date_hits", "page_hits", "identity_keywords", "header_ocr")}
            for r in rows if r["issue_hits"] or r["issue_range_hits"] or r["date_hits"] or "齐鲁少年" in r["identity_keywords"]
        ],
        "notes": [
            "Multiple OCR passes/crops are used because product photos include perspective and watermark noise.",
            "HTML occurrence/context helps distinguish the listing gallery from unrelated recommendation images.",
            "No image bytes are committed."
        ]
    }
    (BASE / "key_image_analysis_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
