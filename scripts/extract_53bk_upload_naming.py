#!/usr/bin/env python3
"""Extract 53BK backend upload/processing naming rules for Pagepic/Pagepdf recovery.

The public reference package is downloaded transiently. We commit only short source
contexts around filename construction and Pagepic/Pagepdf assignment, never the package.
The output is generic CMS evidence and must not be treated as proof that a historical
szb.cnssiot.cn asset exists until that URL is independently verified.
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
UA = "Mozilla/5.0 qilu-shaonian-53bk-upload-naming/1.0"
HREF = re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')
TARGET = re.compile(
    r"(?i)(Editionadd|Editionedit|Pdfadd|Pdfedit|PdfProcess|Upqipdf|PdfText|pubmobile|pageswf|upload|image|edition).*\.(?:aspx|ascx|cs|js)$"
)
TOKENS = re.compile(
    r"(?i)(Pagepic|Pagepdf|mobile|SaveAs|Guid|NewGuid|Path\.GetFileName|GetExtension|FileName|upload|Img[/\\]|\.pdf|\.jpg|PdfProcess|MakeThumbnail|thumbnail|resize|convert)"
)
ASSIGN = re.compile(r"(?i)(Pagepic|Pagepdf)\s*=")


def get(url: str, limit: int = 90 * 1024 * 1024):
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


def contexts(text: str, entry: str):
    rows = []
    seen = set()
    # Wider windows than the generic extractor because naming logic often spans several statements.
    for match in TOKENS.finditer(text):
        start = max(0, match.start() - 900)
        end = min(len(text), match.end() + 1700)
        ctx = re.sub(r"\s+", " ", text[start:end]).strip()
        if not ctx or ctx in seen:
            continue
        seen.add(ctx)
        score = 0
        low = ctx.lower()
        for needle, weight in (
            ("pagepic", 5),
            ("pagepdf", 5),
            ("mobile", 5),
            ("saveas", 3),
            ("newguid", 3),
            ("filename", 2),
            ("img/", 2),
            (".pdf", 2),
            (".jpg", 2),
        ):
            if needle in low:
                score += weight
        rows.append(
            {
                "entry": entry,
                "token": match.group(0),
                "score": score,
                "context": ctx[:3200],
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["entry"], r["token"].lower()))
    return rows[:80]


def main():
    raw, reference_url = package()
    all_rows = []
    assignments = []
    names = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            norm = name.replace("\\", "/")
            base = norm.rsplit("/", 1)[-1]
            if not TARGET.search(base):
                continue
            try:
                body = zf.read(name)
            except Exception:
                continue
            if len(body) > 4_000_000:
                continue
            text = decode(body)
            if not TOKENS.search(text):
                continue
            names.append(name)
            rows = contexts(text, name)
            all_rows.extend(rows)
            for r in rows:
                if ASSIGN.search(r["context"]) or "mobile" in r["context"].lower():
                    assignments.append(r)

    def dedupe(rows):
        out = []
        seen = set()
        for r in rows:
            key = (r["entry"], r["context"])
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    all_rows = dedupe(all_rows)
    assignments = dedupe(assignments)
    fields = ["entry", "token", "score", "context"]
    with (OUT / "upload_naming_contexts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)
    with (OUT / "upload_naming_assignments.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(assignments)

    report = {
        "reference_url": reference_url,
        "matched_files": len(set(names)),
        "contexts": len(all_rows),
        "assignment_or_mobile_contexts": len(assignments),
        "top_entries": sorted(set(names))[:200],
        "notes": [
            "Generic 53BK reference implementation only; not proof of a specific qlsn asset.",
            "The highest-value evidence is an explicit Pagepic/Pagepdf assignment near filename construction.",
            "Any inferred historical URL still requires an actual media response and hash before promotion.",
        ],
    }
    (OUT / "upload_naming_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
