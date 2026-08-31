#!/usr/bin/env python3
"""Recover original-resolution images referenced by the historical 《齐鲁少年》 Sina blog.

Legacy Sina blog HTML frequently embeds URLs such as ``s13.sinaimg.cn/middle/<key>``.
The old CDN used sibling paths including ``large`` and the historically misspelled
``orignal`` for higher-resolution objects. This probe derives only those sibling paths
from media URLs already observed in the verified editor blog, fetches them transiently,
records dimensions/hash, and OCRs plausible document/page images.

No image bytes are committed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "repost_fullpage" / "media_candidates.csv"
OUT = ROOT / "data" / "repost_fullpage" / "sina_original_variants.csv"
UA = "Mozilla/5.0 qilu-shaonian-sina-original-probe/1.0"
TIMEOUT = 20
MAX_BYTES = 24 * 1024 * 1024
WORKERS = 12
KEYWORDS = ["齐鲁少年", "齐鲁少年报", "第", "期", "版", "编辑部"]
ISSUE_RE = re.compile(r"第\s*([0-9０-９]{2,5})\s*期")
PAGE_RE = re.compile(r"(?:第\s*([A-DＡ-Ｄ0-9０-９]{1,3})\s*版|([A-DＡ-Ｄ][0-9０-９]?)\s*版)")


def variants(url: str):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if "sinaimg.cn" not in host:
        return []
    m = re.search(r"/(middle|bmiddle|thumbnail|mw\d+|orj\d+|square)/", p.path, re.I)
    if not m:
        return []
    out = []
    for name in ("large", "orignal"):
        path = p.path[: m.start()] + f"/{name}/" + p.path[m.end() :]
        out.append((name, urllib.parse.urlunsplit((p.scheme or "http", p.netloc, path, p.query, p.fragment))))
    return out


def fetch(url: str, referer: str = ""):
    headers = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("resource too large")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def inspect(item):
    base_row, variant_name, url = item
    out = {
        "source_post": base_row.get("post_url", ""),
        "post_date": base_row.get("post_date", ""),
        "post_title": base_row.get("post_title", ""),
        "issue_hints": base_row.get("issue_hints", ""),
        "page_text_hint": base_row.get("page_text_hint", ""),
        "source_media_url": base_row.get("media_url", ""),
        "variant": variant_name,
        "candidate_url": url,
        "resolved_url": "",
        "http_status": "",
        "content_type": "",
        "bytes": "",
        "sha256": "",
        "width": "",
        "height": "",
        "image_format": "",
        "is_placeholder": "",
        "ocr_keywords": "",
        "ocr_issue_matches": "",
        "ocr_page_matches": "",
        "ocr_excerpt": "",
        "classification": "unverified",
        "error": "",
    }
    tmp = None
    try:
        body, final, headers = fetch(url, base_row.get("post_url", ""))
        ctype = headers.get("content-type", "").split(";", 1)[0].lower()
        out.update(
            {
                "resolved_url": final,
                "http_status": "200",
                "content_type": ctype,
                "bytes": str(len(body)),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        with Image.open(io.BytesIO(body)) as im:
            w, h = im.size
            fmt = im.format or ""
            out.update({"width": str(w), "height": str(h), "image_format": fmt})
        # Sina's current missing-image fallback observed in the archive scan is 360x360 GIF.
        placeholder = (w == 360 and h == 360 and fmt.upper() == "GIF") or "default_s_bmiddle" in final
        out["is_placeholder"] = "yes" if placeholder else "no"
        if placeholder:
            out["classification"] = "placeholder"
            return out

        # OCR plausible document/page images. Keep threshold deliberately lower than the
        # first-pass page-scan heuristic because old blog originals can be modest resolution.
        if w >= 380 and h >= 480:
            suffix = ".png" if fmt.upper() == "PNG" else ".jpg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(body)
                tmp = f.name
            p = subprocess.run(
                ["tesseract", tmp, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=90,
            )
            text = re.sub(r"\s+", " ", p.stdout.replace("\x0c", " ")).strip()
            hits = [k for k in KEYWORDS if k in text]
            issues = list(dict.fromkeys(ISSUE_RE.findall(text)))
            pages = list(dict.fromkeys((a or b) for a, b in PAGE_RE.findall(text)))
            out.update(
                {
                    "ocr_keywords": "|".join(hits),
                    "ocr_issue_matches": "|".join(issues),
                    "ocr_page_matches": "|".join(pages),
                    "ocr_excerpt": text[:2200],
                }
            )
            if "齐鲁少年" in text and (issues or pages):
                out["classification"] = "strong_newspaper_page_candidate"
            elif "齐鲁少年" in text:
                out["classification"] = "qilu_shaonian_present"
            elif hits:
                out["classification"] = "weak_document_candidate"
            else:
                out["classification"] = "no_qilu_shaonian_detected"
        else:
            out["classification"] = "reachable_small_image"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return out


def main():
    if not SRC.exists():
        raise SystemExit(f"missing {SRC}")
    with SRC.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # One source association per unique original media URL; prefer posts with issue/page hints.
    source_by_url = {}
    for row in rows:
        if row.get("source") != "sina_editor_blog":
            continue
        u = row.get("media_url", "")
        vs = variants(u)
        if not vs:
            continue
        score = (2 if row.get("issue_hints") else 0) + (2 if row.get("page_text_hint") else 0)
        old = source_by_url.get(u)
        if old is None or score > old[0]:
            source_by_url[u] = (score, row, vs)

    jobs = []
    for _, row, vs in source_by_url.values():
        for variant_name, url in vs:
            jobs.append((row, variant_name, url))

    print(f"source Sina CDN keys={len(source_by_url)} variants={len(jobs)}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(inspect, item) for item in jobs]
        for n, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            if r["classification"] in {"strong_newspaper_page_candidate", "qilu_shaonian_present"}:
                print("HIT", r["classification"], r["post_title"], r["candidate_url"], flush=True)
            elif n % 25 == 0:
                print("progress", n, "/", len(futures), flush=True)

    results.sort(
        key=lambda r: (
            r["classification"] not in {"strong_newspaper_page_candidate", "qilu_shaonian_present"},
            r["is_placeholder"] == "yes",
            r["post_date"],
            r["source_post"],
            r["variant"],
        )
    )
    fields = [
        "source_post", "post_date", "post_title", "issue_hints", "page_text_hint",
        "source_media_url", "variant", "candidate_url", "resolved_url", "http_status",
        "content_type", "bytes", "sha256", "width", "height", "image_format",
        "is_placeholder", "ocr_keywords", "ocr_issue_matches", "ocr_page_matches",
        "ocr_excerpt", "classification", "error",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    summary = {
        "source_cdn_keys": len(source_by_url),
        "variants_probed": len(results),
        "reachable_non_placeholder": sum(r["http_status"] == "200" and r["is_placeholder"] == "no" for r in results),
        "strong_newspaper_page_candidates": sum(r["classification"] == "strong_newspaper_page_candidate" for r in results),
        "qilu_shaonian_present": sum(r["classification"] == "qilu_shaonian_present" for r in results),
    }
    print(summary, flush=True)


if __name__ == "__main__":
    main()
