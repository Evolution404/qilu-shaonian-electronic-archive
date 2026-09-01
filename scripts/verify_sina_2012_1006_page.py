#!/usr/bin/env python3
"""Deep verification of the 2012-01-03 《齐鲁少年》 page candidate recovered from Sina.

The source post is the editor blog entry “今天我评报” (2012-01-10).  A recovered
1944x2592 JPEG already OCRs the official qlsn.com site, official QQ 857087447,
publisher/sponsor text and the publication date 2012-01-03.  This verifier performs
multi-region/multi-PSM OCR without storing the image bytes and evaluates a composite
identity score rather than requiring OCR to recognize the stylized masthead.
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

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "repost_fullpage" / "sina_inline_media.csv"
OUTDIR = ROOT / "data" / "repost_fullpage" / "sina_2012_1006"
OUTDIR.mkdir(parents=True, exist_ok=True)
OUT = OUTDIR / "ocr_regions.csv"
REPORT = OUTDIR / "report.json"
TARGET_SHA = "92592bb66b826a7d65856b6d713334d7f648a43aa3d4e9e27cbdf7c2dc32631f"
UA = "Mozilla/5.0 qilu-shaonian-2012-1006-verifier/1.0"
MAX_BYTES = 20 * 1024 * 1024

ISSUE_RE = re.compile(r"(?:第\s*)?([0-9０-９]{3,5})\s*期")
DATE_RE = re.compile(r"2012\s*年\s*1\s*月\s*3\s*日")
PAGE_RE = re.compile(r"(?:第\s*)?([1-8A-DＡ-Ｄ])\s*版|\b(A[1-8])\b", re.I)


def find_target():
    with SRC.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    matches = [r for r in rows if r.get("sha256") == TARGET_SHA and r.get("http_status") == "200"]
    if not matches:
        raise SystemExit("target SHA not found")
    # Prefer HTTPS original/large variant.
    matches.sort(key=lambda r: (
        not r.get("media_url", "").startswith("https://"),
        "orignal" not in r.get("media_url", "") and "large" not in r.get("media_url", ""),
    ))
    return matches[0]


def fetch(row):
    req = urllib.request.Request(row["media_url"], headers={
        "User-Agent": UA,
        "Accept": "image/*,*/*;q=0.7",
        "Referer": row["post_url"],
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("target too large")
    return body


def prep(im: Image.Image, scale=1.0, threshold=False):
    x = im.convert("L")
    x = ImageOps.autocontrast(x)
    x = ImageEnhance.Contrast(x).enhance(1.35)
    x = x.filter(ImageFilter.SHARPEN)
    if scale != 1.0:
        x = x.resize((max(1, round(x.width * scale)), max(1, round(x.height * scale))))
    if threshold:
        x = x.point(lambda p: 255 if p > 178 else 0)
    return x


def ocr(im: Image.Image, psm: int):
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        im.save(path, "PNG", optimize=True)
        p = subprocess.run(
            ["tesseract", path, "stdout", "-l", "chi_sim+eng", "--psm", str(psm)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45, check=False,
        )
        text = re.sub(r"\s+", " ", p.stdout.replace("\x0c", " ")).strip()
        return text, "" if p.returncode == 0 else p.stderr[-500:]
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def evidence(text: str):
    normalized = text.replace(" ", "").lower()
    hits = []
    if "齐鲁少年" in text: hits.append("masthead")
    if "qlsn.com" in normalized or "www.qlsn" in normalized or "qisn.com" in normalized: hits.append("official_site")
    if "857087447" in normalized: hits.append("official_qq")
    if "少工委" in text or "少工" in text: hits.append("sponsor")
    if DATE_RE.search(text.replace(" ", "")): hits.append("publication_date")
    if "1006" in normalized: hits.append("issue_1006")
    if "山东" in text: hits.append("shandong")
    issues = list(dict.fromkeys(ISSUE_RE.findall(text)))
    pages = []
    for m in PAGE_RE.findall(text):
        a, b = m
        pages.append(a or b)
    return hits, issues, list(dict.fromkeys(pages))


def main():
    row = find_target()
    body = fetch(row)
    with Image.open(io.BytesIO(body)) as src:
        src = src.convert("RGB")
        W, H = src.size
        crops = {
            "full": (0, 0, W, H),
            "top_18": (0, 0, W, round(H * 0.18)),
            "top_32": (0, 0, W, round(H * 0.32)),
            "top_left": (0, 0, round(W * 0.58), round(H * 0.35)),
            "top_right": (round(W * 0.42), 0, W, round(H * 0.35)),
            "middle": (0, round(H * 0.25), W, round(H * 0.78)),
            "bottom_25": (0, round(H * 0.75), W, H),
            "bottom_left": (0, round(H * 0.72), round(W * 0.55), H),
            "bottom_right": (round(W * 0.45), round(H * 0.72), W, H),
        }
        rows = []
        combined = []
        for name, box in crops.items():
            crop = src.crop(box)
            # Full page: test layout-aware psm 3 and sparse psm 11. Header/corners: psm 6/11.
            psms = (3, 11) if name == "full" else (6, 11)
            for threshold in (False, True):
                scale = min(1.5, 2200 / max(crop.width, 1)) if name != "full" else min(1.0, 1800 / max(crop.width, crop.height))
                prepared = prep(crop, scale=scale, threshold=threshold)
                for psm in psms:
                    text, err = ocr(prepared, psm)
                    hits, issues, pages = evidence(text)
                    combined.append(text)
                    rows.append({
                        "region": name,
                        "psm": str(psm),
                        "threshold": "yes" if threshold else "no",
                        "crop_width": str(crop.width),
                        "crop_height": str(crop.height),
                        "text_chars": str(len(re.sub(r"\s+", "", text))),
                        "evidence_hits": "|".join(hits),
                        "issue_matches": "|".join(issues),
                        "page_matches": "|".join(pages),
                        "ocr_excerpt": text[:2500],
                        "error": err,
                    })
                    if hits or issues or pages:
                        print(name, psm, threshold, hits, issues, pages, text[:300], flush=True)

    merged = " ".join(combined)
    merged_hits, merged_issues, merged_pages = evidence(merged)
    # Composite identity: any four independent historical identifiers is enough to
    # identify the newspaper even when stylized masthead OCR fails. Full-page status
    # additionally requires broad OCR coverage across top/middle/bottom regions.
    region_chars = {}
    for r in rows:
        region_chars[r["region"]] = max(region_chars.get(r["region"], 0), int(r["text_chars"]))
    vertical_coverage = sum(region_chars.get(k, 0) >= 45 for k in ("top_32", "middle", "bottom_25"))
    identity_core = {x for x in merged_hits if x in {"masthead", "official_site", "official_qq", "sponsor", "publication_date", "issue_1006", "shandong"}}
    identity_score = len(identity_core)
    issue_1006 = "issue_1006" in identity_core or "1006" in merged.replace(" ", "")
    date_ok = "publication_date" in identity_core
    identity_verified = identity_score >= 4 and date_ok and issue_1006
    full_page_layout_candidate = identity_verified and vertical_coverage >= 2 and W/H >= 0.68 and W/H <= 0.82

    fields = ["region", "psm", "threshold", "crop_width", "crop_height", "text_chars", "evidence_hits", "issue_matches", "page_matches", "ocr_excerpt", "error"]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    report = {
        "source_post": row["post_url"],
        "source_post_date": row.get("post_date", ""),
        "source_post_title": row.get("post_title", ""),
        "media_url": row["media_url"],
        "sha256": row["sha256"],
        "width": W,
        "height": H,
        "aspect_ratio": round(H / W, 4),
        "merged_evidence_hits": sorted(identity_core),
        "merged_issue_matches": merged_issues,
        "merged_page_matches": merged_pages,
        "identity_score": identity_score,
        "publication_date_2012_01_03": date_ok,
        "issue_1006_detected": issue_1006,
        "vertical_text_coverage_zones": vertical_coverage,
        "identity_verified": identity_verified,
        "full_page_layout_candidate": full_page_layout_candidate,
        "inferred_issue_sequence_note": "Issue 1000 is independently anchored on 2011-11-22 in the editor blog; weekly cadence places 2012-01-03 at issue 1006. OCR must independently detect 1006 before identity_verified becomes true.",
        "classification": (
            "verified_issue_1006_fullpage_candidate" if full_page_layout_candidate
            else "verified_issue_1006_page_image" if identity_verified
            else "strong_newspaper_page_candidate" if identity_score >= 3
            else "unverified"
        ),
        "notes": [
            "No image bytes are committed.",
            "Composite newspaper identity uses independent masthead/site/QQ/sponsor/date/issue/location evidence; it does not rely solely on stylized masthead OCR.",
            "A complete issue PDF is not created unless all pages of the issue are recovered and verified."
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
