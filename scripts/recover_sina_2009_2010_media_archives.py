#!/usr/bin/env python3
"""Recover historical media bytes for 2009-2010 official-editor Sina posts.

Current sinaimg URLs frequently redirect to a generic placeholder. This pass uses the media
keys already proven to have appeared in editor-post HTML and asks Wayback for the closest
historical capture near the post date. Image bytes are transient; only metadata/hash/OCR is
committed.
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
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "repost_fullpage" / "sina_inline_media.csv"
OUT = ROOT / "data" / "legacy_2009_2010_sina_media"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/sina-2009-2010-archive-1.0"
TIMEOUT = 16

FOCUS = re.compile(r"合刊|编辑部.*故事|白小葱|老编说事|评报|看图|版面|报纸|\b(?:9\d\d)期\b|第\s*9\d\d\s*期", re.I)
PATH_CLASS = re.compile(r"/(middle|bmiddle|thumbnail|mw\d+|orj\d+|large|orignal)/", re.I)
PLACEHOLDER_SHA = "d2b5a30568572332968808f1fd3d0218cd8a8ca41889627168fc6d9ca487e766"


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def get_bytes(url: str, limit=20 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError("too large")
        return data, r.geturl(), r.headers.get_content_type()


def derive(url: str) -> list[str]:
    url = html.unescape(url.strip()).replace("\\/", "/")
    p = urllib.parse.urlsplit(url)
    if not p.hostname or "sinaimg.cn" not in p.hostname.lower():
        return []
    out = []
    m = PATH_CLASS.search(p.path)
    classes = ["middle", "bmiddle", "large", "orignal"] if m else [""]
    for scheme in ("http", "https"):
        for cls in classes:
            path = p.path
            if m and cls:
                path = p.path[:m.start()] + f"/{cls}/" + p.path[m.end():]
            u = urllib.parse.urlunsplit((scheme, p.netloc, path, p.query, ""))
            if u not in out:
                out.append(u)
    return out


def available(url: str, date: str) -> tuple[str, str, str]:
    qs = urllib.parse.urlencode({"url": url, "timestamp": date.replace("-", "") + "120000"})
    endpoint = "https://archive.org/wayback/available?" + qs
    data = get_json(endpoint)
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if closest.get("available") and closest.get("url"):
        return closest.get("timestamp", ""), closest["url"], ""
    return "", "", ""


def raw_archive_url(snapshot_url: str) -> str:
    # Force raw archived response when possible.
    return re.sub(r"/web/(\d+)(?:[a-z_]+)?/", r"/web/\1id_/", snapshot_url, count=1)


def ocr(img: Image.Image) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im = img.convert("RGB")
        if max(im.size) > 2800:
            s = 2800 / max(im.size)
            im = im.resize((max(1, int(im.width*s)), max(1, int(im.height*s))))
        im.save(f.name)
        p = subprocess.run(["tesseract", f.name, "stdout", "-l", "chi_sim+eng", "--psm", "6"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=40)
        return re.sub(r"\s+", " ", p.stdout).strip()


def main() -> int:
    rows = list(csv.DictReader(SRC.open(newline="", encoding="utf-8")))
    selected = []
    for r in rows:
        date = r.get("post_date", "")
        if not (date.startswith("2009-") or date.startswith("2010-")):
            continue
        semantic = " ".join([r.get("post_title", ""), r.get("issue_hints", ""), r.get("page_text_hint", "")])
        if not (r.get("issue_hints") or FOCUS.search(semantic)):
            continue
        selected.append(r)

    # One post/media-key association, preserving strongest issue hint.
    grouped: dict[tuple[str,str], dict] = {}
    for r in selected:
        base = r.get("source_media_url") or r.get("media_url") or ""
        key = (r.get("post_url", ""), base)
        old = grouped.get(key)
        score = 2 if r.get("issue_hints") else 0
        if old is None or score > (2 if old.get("issue_hints") else 0):
            grouped[key] = r

    attempts = []
    verified = []
    seen_query = set()
    for idx, r in enumerate(grouped.values(), 1):
        base = r.get("source_media_url") or r.get("media_url") or ""
        for candidate in derive(base):
            query_key = (candidate, r.get("post_date", ""))
            if query_key in seen_query:
                continue
            seen_query.add(query_key)
            a = {
                "post_url": r.get("post_url", ""), "post_date": r.get("post_date", ""),
                "post_title": r.get("post_title", ""), "issue_hints": r.get("issue_hints", ""),
                "media_candidate": candidate, "snapshot_timestamp": "", "snapshot_url": "",
                "resolved_archive_url": "", "content_type": "", "bytes": "", "sha256": "",
                "width": "", "height": "", "format": "", "placeholder": "", "ocr_hits": "", "ocr_excerpt": "", "error": "",
            }
            try:
                ts, snap, _ = available(candidate, r.get("post_date", ""))
                a["snapshot_timestamp"] = ts; a["snapshot_url"] = snap
                if snap:
                    raw_url = raw_archive_url(snap)
                    data, resolved, ctype = get_bytes(raw_url)
                    sha = hashlib.sha256(data).hexdigest()
                    a.update({"resolved_archive_url": resolved, "content_type": ctype, "bytes": str(len(data)), "sha256": sha, "placeholder": "yes" if sha == PLACEHOLDER_SHA else "no"})
                    try:
                        img = Image.open(io.BytesIO(data)); img.load()
                        a["width"] = str(img.width); a["height"] = str(img.height); a["format"] = img.format or ""
                        if sha != PLACEHOLDER_SHA and img.width >= 500 and img.height >= 500:
                            text = ocr(img)
                            hits = [k for k in ("齐鲁少年","第","期","版","少先队","合刊","记者","山东") if k in text]
                            a["ocr_hits"] = "|".join(hits); a["ocr_excerpt"] = text[:1000]
                    except Exception:
                        pass
                    if sha != PLACEHOLDER_SHA and (a["width"] or ctype.startswith("image/")):
                        verified.append(a.copy())
            except Exception as exc:
                a["error"] = f"{type(exc).__name__}: {exc}"
            attempts.append(a)
        if idx % 10 == 0:
            print(f"posts {idx}/{len(grouped)} attempts={len(attempts)} verified={len(verified)}", flush=True)

    fields = ["post_url","post_date","post_title","issue_hints","media_candidate","snapshot_timestamp","snapshot_url","resolved_archive_url","content_type","bytes","sha256","width","height","format","placeholder","ocr_hits","ocr_excerpt","error"]
    with (OUT / "attempts.csv").open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(attempts)
    with (OUT / "verified_media.csv").open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(verified)

    report = {
        "source_rows_2009_2010_focus": len(selected),
        "distinct_post_media_keys": len(grouped),
        "wayback_candidate_attempts": len(attempts),
        "attempts_with_snapshot": sum(bool(a["snapshot_url"]) for a in attempts),
        "verified_nonplaceholder_images": len(verified),
        "verified_issue_hints": sorted({a["issue_hints"] for a in verified if a["issue_hints"]}),
        "verified_media": [{k:a[k] for k in ("post_date","post_title","issue_hints","media_candidate","snapshot_timestamp","resolved_archive_url","bytes","sha256","width","height","ocr_hits","ocr_excerpt")} for a in verified[:30]],
        "notes": ["Only media keys already embedded in official-editor Sina post HTML are queried.", "Archived image bytes are transient and are not committed.", "A Wayback snapshot is not promoted as a newspaper page until image content/issue identity is verified."],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
