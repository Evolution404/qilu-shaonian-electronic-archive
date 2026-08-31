#!/usr/bin/env python3
"""Extract the exact 53BK PDF->PC/mobile image generation and upload-control naming logic.

This is a narrow follow-up to the generic CMS inspection. It downloads the public reference
package transiently and inspects MakePhoto/Upmappic/Upzoompic/Uponenopostpic-related files.
Only short source contexts and filenames are committed; no package/source archive is retained.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cms_reference"
OUT.mkdir(parents=True, exist_ok=True)
PAGE = "https://www.onlinedown.net/soft/117759.htm"
UA = "Mozilla/5.0 qilu-shaonian-53bk-makephoto/1.0"
HREF = re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')
NAME_NEEDLES = (
    "makephoto",
    "upmappic",
    "upzoompic",
    "uponenopostpic",
    "upqipdf",
    "pdfprocess",
)
TOKENS = re.compile(
    r"(?i)(pcpath|mobilepath|Pagepic|Pagepdf|mobile|SaveAs|Guid|NewGuid|FileName|GetExtension|Img[/\\]|\.pdf|\.jpg|\.jpeg|MakePhoto|Resize|Thumbnail|Bitmap|Image\.FromFile|Magick|Ghostscript|pdfto|convert)"
)


def get(url: str, limit=90 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl()


def decode(raw: bytes):
    best = None
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(enc, "replace")
            score = text.count("\ufffd")
            if best is None or score < best[0]:
                best = (score, text)
        except Exception:
            pass
    return best[1] if best else raw.decode("latin1", "replace")


def package():
    raw, final = get(PAGE, 8 * 1024 * 1024)
    text = decode(raw).replace("&amp;", "&")
    candidates = []
    for href in HREF.findall(text):
        u = urllib.parse.urljoin(final, href)
        if "117759" in u and ("download" in u.lower() or "iopdfbhjl" in u.lower()):
            candidates.append(u)
    candidates += [
        "https://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website",
        "http://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website",
    ]
    for u in dict.fromkeys(candidates):
        try:
            body, resolved = get(u)
            if body[:2] == b"PK":
                return body, resolved
        except Exception:
            continue
    raise RuntimeError("reference package unavailable")


def score_context(ctx: str):
    low = ctx.lower()
    score = 0
    for needle, weight in (
        ("pcpath", 8),
        ("mobilepath", 8),
        ("pagepic", 6),
        ("pagepdf", 6),
        ("saveas", 4),
        ("newguid", 4),
        ("filename", 3),
        ("img/", 3),
        (".pdf", 2),
        (".jpg", 2),
    ):
        if needle in low:
            score += weight
    return score


def main():
    raw, reference_url = package()
    matched_names = []
    rows = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        for name in names:
            norm = name.replace("\\", "/")
            low_name = norm.lower()
            if not any(n in low_name for n in NAME_NEEDLES):
                continue
            matched_names.append(name)
            try:
                body = zf.read(name)
            except Exception:
                continue
            if len(body) > 5_000_000:
                continue
            text = decode(body)
            if not TOKENS.search(text):
                rows.append({"entry": name, "token": "filename_only", "score": 0, "context": ""})
                continue
            seen = set()
            for m in TOKENS.finditer(text):
                start = max(0, m.start() - 1800)
                end = min(len(text), m.end() + 3200)
                ctx = re.sub(r"\s+", " ", text[start:end]).strip()
                if not ctx or ctx in seen:
                    continue
                seen.add(ctx)
                rows.append(
                    {
                        "entry": name,
                        "token": m.group(0),
                        "score": score_context(ctx),
                        "context": ctx[:6500],
                    }
                )
    rows.sort(key=lambda r: (-int(r["score"]), r["entry"], r["token"].lower()))
    fields = ["entry", "token", "score", "context"]
    with (OUT / "makephoto_logic.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows[:500])

    report = {
        "reference_url": reference_url,
        "matched_file_count": len(set(matched_names)),
        "matched_files": sorted(set(matched_names)),
        "context_count": len(rows),
        "top_score": max((int(r["score"]) for r in rows), default=0),
        "notes": [
            "Generic 53BK implementation evidence only.",
            "Use this output to derive candidate filename mappings; verify every qlsn URL independently.",
        ],
    }
    (OUT / "makephoto_logic_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
