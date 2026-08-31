#!/usr/bin/env python3
"""Extract naming-related strings/symbols from the compiled 53BK .NET assemblies/PDBs.

The public reference package ships ASPX shells whose code-behind is compiled. This script
scans only the package's ``paperweb/bin`` binaries transiently and commits a compact list of
printable strings containing high-value tokens such as MakePhoto, pcpath/mobilepath,
Pagepic/Pagepdf, Img paths, GUID/file-name construction, and PDF/JPG conversion hints.

No binary from the reference package is committed.
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
UA = "Mozilla/5.0 qilu-shaonian-53bk-binary-strings/1.0"
HREF = re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')
ASCII = re.compile(rb"[\x20-\x7e]{4,}")
UTF16 = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
NEEDLES = (
    "makephoto",
    "pdfprocess",
    "pcpath",
    "mobilepath",
    "pagepic",
    "pagepdf",
    "upmappic",
    "upzoompic",
    "uponenopostpic",
    "upqipdf",
    "newguid",
    "guid",
    "filename",
    "saveas",
    "img/",
    "img\\",
    ".pdf",
    ".jpg",
    ".jpeg",
    "ghostscript",
    "pdfto",
    "convert",
    "thumbnail",
    "resize",
)


def get(url: str, limit=100 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl()


def decode_text(raw: bytes):
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
    text = decode_text(raw).replace("&amp;", "&")
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


def extract_strings(data: bytes):
    items = []
    for m in ASCII.finditer(data):
        try:
            s = m.group(0).decode("ascii")
        except Exception:
            continue
        items.append((m.start(), "ascii", s))
    for m in UTF16.finditer(data):
        try:
            s = m.group(0).decode("utf-16le")
        except Exception:
            continue
        items.append((m.start(), "utf16le", s))
    items.sort(key=lambda x: x[0])
    return items


def score(s: str):
    low = s.lower()
    n = 0
    for needle, weight in (
        ("makephoto", 10),
        ("pcpath", 10),
        ("mobilepath", 10),
        ("pagepic", 8),
        ("pagepdf", 8),
        ("newguid", 6),
        ("saveas", 5),
        ("filename", 4),
        ("img/", 4),
        ("img\\", 4),
        (".pdf", 3),
        (".jpg", 3),
        ("ghostscript", 5),
        ("convert", 3),
        ("mobile", 2),
    ):
        if needle in low:
            n += weight
    return n


def main():
    raw, reference_url = package()
    rows = []
    binaries = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            norm = name.replace("\\", "/")
            low = norm.lower()
            if "/bin/" not in low or not low.endswith((".dll", ".pdb")):
                continue
            try:
                data = zf.read(name)
            except Exception:
                continue
            binaries.append({"entry": name, "bytes": len(data)})
            strings = extract_strings(data)
            for idx, (offset, encoding, s) in enumerate(strings):
                low_s = s.lower()
                if not any(n in low_s for n in NEEDLES):
                    continue
                # Neighboring printable strings often represent adjacent IL literals/symbols.
                before = " | ".join(x[2] for x in strings[max(0, idx - 4) : idx])
                after = " | ".join(x[2] for x in strings[idx + 1 : idx + 5])
                rows.append(
                    {
                        "entry": name,
                        "offset": offset,
                        "encoding": encoding,
                        "score": score(s),
                        "string": s[:1800],
                        "neighbors_before": before[-2600:],
                        "neighbors_after": after[:2600],
                    }
                )
    # Deduplicate repeated compiler metadata but preserve assembly distinctions.
    dedup = {}
    for r in rows:
        key = (r["entry"], r["string"], r["neighbors_before"], r["neighbors_after"])
        old = dedup.get(key)
        if old is None or int(r["score"]) > int(old["score"]):
            dedup[key] = r
    rows = list(dedup.values())
    rows.sort(key=lambda r: (-int(r["score"]), r["entry"], int(r["offset"])))

    fields = [
        "entry",
        "offset",
        "encoding",
        "score",
        "string",
        "neighbors_before",
        "neighbors_after",
    ]
    with (OUT / "binary_naming_strings.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows[:1200])

    report = {
        "reference_url": reference_url,
        "binary_count": len(binaries),
        "binaries": sorted(binaries, key=lambda x: x["entry"]),
        "matched_string_contexts": len(rows),
        "top_score": max((int(r["score"]) for r in rows), default=0),
        "top_entries": sorted({r["entry"] for r in rows[:150]}),
        "notes": [
            "Strings/symbols come from generic 53BK reference binaries, not the historical qlsn server.",
            "Any recovered naming template must still be independently validated against a real archived/live media response.",
        ],
    }
    (OUT / "binary_naming_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
