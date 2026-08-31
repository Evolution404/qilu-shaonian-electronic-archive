#!/usr/bin/env python3
"""OCR large portrait backup candidates to identify actual 《齐鲁少年》 pages.

Requires tesseract with chi_sim language data. Candidate image bytes are transient and
never written to the repository; only short OCR excerpts and keyword hits are persisted.
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "fullpage_backup" / "inspected_media_candidates.csv"
OUT = ROOT / "data" / "fullpage_backup" / "ocr_candidates.csv"
UA = "qilu-shaonian-ocr-verifier/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 30
KEYWORDS = ["齐鲁少年", "齐鲁少年报", "第", "期", "版", "山东少先队", "山东省少工委", "少先队"]
ISSUE_RE = re.compile(r"第\s*([0-9０-９]{2,5})\s*期")
PAGE_RE = re.compile(r"(?:第\s*([0-9０-９A-DＡ-Ｄ]{1,3})\s*版|([A-DＡ-Ｄ][0-9０-９]?)\s*版)")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def normalize(text: str) -> str:
    text = text.replace("\x0c", " ")
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    with SOURCE.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    candidates = [r for r in rows if r.get("likely_fullpage") == "yes"]
    out_rows = []
    for row in candidates:
        result = {
            "original": row.get("original", ""),
            "archive_url": row.get("archive_url", ""),
            "width": row.get("width", ""),
            "height": row.get("height", ""),
            "sha256": row.get("sha256", ""),
            "historical_identity": row.get("historical_identity", ""),
            "ocr_keyword_hits": "",
            "issue_matches": "",
            "page_matches": "",
            "ocr_excerpt": "",
            "classification": "unverified",
            "ocr_error": "",
        }
        try:
            data = fetch(row["archive_url"])
            suffix = ".jpg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                path = tmp.name
            try:
                proc = subprocess.run(
                    ["tesseract", path, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                    check=False,
                )
                text = normalize(proc.stdout)
                hits = [kw for kw in KEYWORDS if kw in text]
                issues = ISSUE_RE.findall(text)
                pages = [a or b for a, b in PAGE_RE.findall(text)]
                result["ocr_keyword_hits"] = "|".join(hits)
                result["issue_matches"] = "|".join(dict.fromkeys(issues))
                result["page_matches"] = "|".join(dict.fromkeys(pages))
                result["ocr_excerpt"] = text[:1800]
                if "齐鲁少年" in text and (issues or "版" in text):
                    result["classification"] = "strong_newspaper_page_candidate"
                elif "齐鲁少年" in text:
                    result["classification"] = "qilu_shaonian_text_present"
                else:
                    result["classification"] = "no_qilu_shaonian_masthead_detected"
                if proc.returncode != 0:
                    result["ocr_error"] = proc.stderr[-800:]
            finally:
                os.unlink(path)
        except Exception as exc:
            result["ocr_error"] = f"{type(exc).__name__}: {exc}"
        out_rows.append(result)
        print(result["classification"], result["original"], result["ocr_keyword_hits"], flush=True)

    fields = ["original","archive_url","width","height","sha256","historical_identity","ocr_keyword_hits","issue_matches","page_matches","ocr_excerpt","classification","ocr_error"]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
