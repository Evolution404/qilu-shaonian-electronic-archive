#!/usr/bin/env python3
"""List filename relationships inside the public 53BK reference package's paperweb/Img tree.

No image/PDF bytes are extracted or committed. The goal is only to verify generic naming
relationships such as `mobile<stem>.jpg` vs `<stem>.jpg` and same-stem PDF assets.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import PurePosixPath, Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cms_reference"
OUT.mkdir(parents=True, exist_ok=True)
PAGE = "https://www.onlinedown.net/soft/117759.htm"
UA = "Mozilla/5.0 qilu-shaonian-53bk-img-pairs/1.0"
HREF = re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')


def get(url: str, limit=100 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl()


def package():
    raw, final = get(PAGE, 8 * 1024 * 1024)
    text = raw.decode("utf-8", "replace").replace("&amp;", "&")
    candidates = []
    for h in HREF.findall(text):
        u = urllib.parse.urljoin(final, h)
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
            pass
    raise RuntimeError("reference package unavailable")


def main():
    raw, reference_url = package()
    files = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/")
            low = norm.lower()
            if "/paperweb/img/" not in "/" + low or norm.endswith("/"):
                continue
            p = PurePosixPath(norm)
            files.append(
                {
                    "entry": norm,
                    "directory": str(p.parent),
                    "name": p.name,
                    "suffix": p.suffix.lower(),
                    "size": info.file_size,
                }
            )

    by_dir_name = {(f["directory"].lower(), f["name"].lower()): f for f in files}
    rows = []
    mobile_count = 0
    paired_pc = 0
    same_stem_pdf = 0
    for f in files:
        name = f["name"]
        if not name.lower().startswith("mobile") or f["suffix"] not in {".jpg", ".jpeg", ".png"}:
            continue
        mobile_count += 1
        base_name = name[len("mobile"):]
        key = (f["directory"].lower(), base_name.lower())
        pc = by_dir_name.get(key)
        stem = PurePosixPath(base_name).stem
        pdf = by_dir_name.get((f["directory"].lower(), (stem + ".pdf").lower()))
        if pc:
            paired_pc += 1
        if pdf:
            same_stem_pdf += 1
        rows.append(
            {
                "directory": f["directory"],
                "mobile_name": name,
                "mobile_size": f["size"],
                "pc_name_without_mobile": pc["name"] if pc else "",
                "pc_size": pc["size"] if pc else "",
                "same_stem_pdf": pdf["name"] if pdf else "",
                "pdf_size": pdf["size"] if pdf else "",
                "mobile_stem": PurePosixPath(name).stem,
                "base_stem": stem,
            }
        )

    rows.sort(key=lambda r: (r["directory"], r["mobile_name"]))
    fields = [
        "directory", "mobile_name", "mobile_size", "pc_name_without_mobile", "pc_size",
        "same_stem_pdf", "pdf_size", "mobile_stem", "base_stem",
    ]
    with (OUT / "img_filename_pairs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    report = {
        "reference_url": reference_url,
        "img_tree_files": len(files),
        "mobile_image_names": mobile_count,
        "mobile_with_exact_pc_pair": paired_pc,
        "mobile_with_same_stem_pdf": same_stem_pdf,
        "all_mobile_have_exact_pc_pair": bool(mobile_count) and paired_pc == mobile_count,
        "notes": [
            "This proves only generic reference-package naming relationships, not historical qlsn asset existence.",
            "No file bytes were extracted; ZIP entry names and sizes only are recorded.",
        ],
    }
    (OUT / "img_filename_pairs_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
