#!/usr/bin/env python3
"""Targeted OCR/content verification for recovered historical 《齐鲁少年》 Sina media.

This pass deliberately avoids OCRing the whole recovered corpus.  It selects issue 941
and high-value document-like images from review/layout/combined-issue posts, deduplicates
by SHA-256, fetches each already-verified media URL with its source blog as Referer,
resizes large originals before OCR, and classifies the result as full-page candidate,
newspaper fragment, dense printed material, or non-newspaper image.

Image bytes are transient and are never committed.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "repost_fullpage" / "sina_inline_media.csv"
OUT = ROOT / "data" / "repost_fullpage" / "sina_targeted_ocr.csv"
REPORT = ROOT / "data" / "repost_fullpage" / "sina_targeted_ocr_report.json"
UA = "Mozilla/5.0 qilu-shaonian-targeted-sina-ocr/1.0"
MAX_BYTES = 25 * 1024 * 1024
MAX_DIM = 1800
MAX_CANDIDATES = 24

SEMANTIC = re.compile(r"今天我评报|评报|看图说事|合刊|版面|报纸|队报", re.I)
ISSUE_RE = re.compile(r"第\s*([0-9０-９]{2,5})\s*期")
PAGE_RE = re.compile(r"(?:第\s*([A-DＡ-Ｄ0-9０-９]{1,3})\s*版|\b([A-DＡ-Ｄ][0-9０-９]?)\b)")
KEYWORDS = ["齐鲁少年报", "齐鲁少年", "941", "头版", "一版", "二版", "三版", "四版", "第", "期", "版"]


def nint(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def fetch(url: str, referer: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "image/*,*/*;q=0.7",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("image too large")
    return body


def prepare_image(body: bytes) -> tuple[str, int, int]:
    with Image.open(io.BytesIO(body)) as im:
        im = im.convert("L")
        im = ImageOps.autocontrast(im)
        w, h = im.size
        scale = min(1.0, MAX_DIM / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))))
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        im.save(tmp.name, "PNG", optimize=True)
        return tmp.name, w, h


def run_ocr(path: str) -> tuple[str, str]:
    try:
        p = subprocess.run(
            ["tesseract", path, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=40,
            check=False,
        )
        text = re.sub(r"\s+", " ", p.stdout.replace("\x0c", " ")).strip()
        err = "" if p.returncode == 0 else p.stderr[-700:]
        return text, err
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def geometry_class(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    ratio = h / w
    if ratio >= 2.3 or (w < 500 and ratio >= 1.9):
        return "vertical_strip_fragment"
    if 1.18 <= ratio <= 1.85 and w >= 500:
        return "portrait_page_or_photo"
    if ratio < 1.05:
        return "landscape_photo_or_layout"
    return "other_portrait"


def select_rows(rows):
    # Prefer one row per recovered file hash; score issue 941 highest, then semantic
    # document-like portraits, then other issue-hinted document-like images.
    by_sha = {}
    for r in rows:
        if r.get("http_status") != "200" or r.get("is_placeholder") != "no" or not r.get("sha256"):
            continue
        issue = r.get("issue_hints", "")
        title = r.get("post_title", "")
        is_941 = "941" in re.split(r"[^0-9]+", issue)
        semantic = bool(SEMANTIC.search(title))
        doc = r.get("likely_document") == "yes"
        if not (is_941 or (semantic and doc) or (issue and doc)):
            continue
        w, h = nint(r.get("width")), nint(r.get("height"))
        area = w * h
        score = (100 if is_941 else 0) + (25 if semantic else 0) + (20 if doc else 0) + min(area // 100000, 25)
        old = by_sha.get(r["sha256"])
        if old is None or (score, area) > (old[0], old[1]):
            by_sha[r["sha256"]] = (score, area, r)
    picked = [x[2] for x in sorted(by_sha.values(), key=lambda x: (x[0], x[1]), reverse=True)]
    return picked[:MAX_CANDIDATES]


def classify(text: str, row, geom: str, w: int, h: int) -> tuple[str, list[str], list[str], list[str]]:
    hits = [k for k in KEYWORDS if k in text]
    issues = list(dict.fromkeys(ISSUE_RE.findall(text)))
    pages = list(dict.fromkeys((a or b) for a, b in PAGE_RE.findall(text)))
    title_issue_941 = "941" in re.split(r"[^0-9]+", row.get("issue_hints", ""))
    has_name = "齐鲁少年" in text
    has_issue = bool(issues) or "941" in text
    has_page = bool(pages) or any(x in text for x in ("头版", "一版", "二版", "三版", "四版"))
    chars = len(re.sub(r"\s+", "", text))

    if has_name and (has_issue or has_page) and chars >= 180 and geom != "vertical_strip_fragment":
        cls = "strong_fullpage_candidate"
    elif (has_name or (title_issue_941 and (has_issue or chars >= 80))) and geom == "vertical_strip_fragment":
        cls = "newspaper_fragment_candidate"
    elif has_name and chars >= 80:
        cls = "newspaper_content_candidate"
    elif chars >= 220 and geom in {"portrait_page_or_photo", "other_portrait"}:
        cls = "dense_print_candidate"
    else:
        cls = "non_newspaper_or_low_text"
    return cls, hits, issues, pages


def main():
    with SRC.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    selected = select_rows(rows)
    print(f"selected unique target images={len(selected)}", flush=True)

    out = []
    for i, r in enumerate(selected, 1):
        w, h = nint(r.get("width")), nint(r.get("height"))
        geom = geometry_class(w, h)
        result = {
            "rank": str(i),
            "post_url": r.get("post_url", ""),
            "post_date": r.get("post_date", ""),
            "post_title": r.get("post_title", ""),
            "issue_hints": r.get("issue_hints", ""),
            "media_url": r.get("media_url", ""),
            "sha256": r.get("sha256", ""),
            "width": str(w),
            "height": str(h),
            "geometry_class": geom,
            "text_chars": "0",
            "keyword_hits": "",
            "issue_matches": "",
            "page_matches": "",
            "ocr_excerpt": "",
            "classification": "ocr_error",
            "error": "",
        }
        tmp = None
        try:
            body = fetch(r["media_url"], r["post_url"])
            tmp, _, _ = prepare_image(body)
            text, err = run_ocr(tmp)
            cls, hits, issues, pages = classify(text, r, geom, w, h)
            result.update(
                {
                    "text_chars": str(len(re.sub(r"\s+", "", text))),
                    "keyword_hits": "|".join(hits),
                    "issue_matches": "|".join(issues),
                    "page_matches": "|".join(pages),
                    "ocr_excerpt": text[:2600],
                    "classification": cls,
                    "error": err,
                }
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"[:800]
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        out.append(result)
        print(i, result["classification"], geom, result["post_title"], result["keyword_hits"], flush=True)

    fields = [
        "rank", "post_url", "post_date", "post_title", "issue_hints", "media_url", "sha256",
        "width", "height", "geometry_class", "text_chars", "keyword_hits", "issue_matches",
        "page_matches", "ocr_excerpt", "classification", "error",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        wri = csv.DictWriter(f, fieldnames=fields)
        wri.writeheader(); wri.writerows(out)

    report = {
        "source_rows": len(rows),
        "selected_unique_images": len(selected),
        "issue_941_selected": sum("941" in re.split(r"[^0-9]+", r.get("issue_hints", "")) for r in selected),
        "strong_fullpage_candidates": sum(r["classification"] == "strong_fullpage_candidate" for r in out),
        "newspaper_fragment_candidates": sum(r["classification"] == "newspaper_fragment_candidate" for r in out),
        "newspaper_content_candidates": sum(r["classification"] == "newspaper_content_candidate" for r in out),
        "dense_print_candidates": sum(r["classification"] == "dense_print_candidate" for r in out),
        "ocr_errors": sum(r["classification"] == "ocr_error" for r in out),
        "candidate_rows": [
            {k: r[k] for k in ("rank", "post_date", "post_title", "issue_hints", "media_url", "sha256", "width", "height", "geometry_class", "text_chars", "keyword_hits", "issue_matches", "page_matches", "classification", "ocr_excerpt")}
            for r in out if r["classification"] in {"strong_fullpage_candidate", "newspaper_fragment_candidate", "newspaper_content_candidate", "dense_print_candidate"}
        ],
        "notes": [
            "OCR is triage evidence; full-page promotion requires newspaper identity plus layout/page evidence.",
            "Very narrow issue-941 images are classified as fragments even if newspaper text is detected.",
            "Image bytes are downloaded only transiently and are not committed."
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
