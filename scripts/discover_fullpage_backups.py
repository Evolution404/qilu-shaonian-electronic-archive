#!/usr/bin/env python3
"""Discover full-page/issue backups for 《齐鲁少年》.

Goal: locate original-layout electronic newspaper material (PDFs, full-page JPEG/PNG,
digital-paper page assets), not ordinary article HTML.

The script queries:
- Internet Archive item metadata search
- Wayback CDX for image/PDF resources on known historical hosts
- Selected public evidence pages whose media may contain complete newspaper pages

Only metadata and technical fingerprints are committed. Third-party newspaper image/PDF
bytes are fetched transiently for dimensions/hashes and are never committed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fullpage_backup"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-fullpage-backup-discovery/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 25
WORKERS = 12
MAX_BYTES = 25 * 1024 * 1024

# Keep host/time interpretation conservative. qlsn.com is verified as 《齐鲁少年》 in
# 2004-2007, but later captures may belong to unrelated/repurposed sites.
WAYBACK_TARGETS = [
    ("dzwww", "www.dzwww.com/qilushaonian/*"),
    ("dzwww_ip", "202.102.188.131/qilushaonian/*"),
    ("qlsn_www", "www.qlsn.com/*"),
    ("qlsn_bare", "qlsn.com/*"),
    ("szb", "szb.cnssiot.cn/*"),
]

IA_QUERIES = [
    'title:("齐鲁少年")',
    'description:("齐鲁少年")',
    'subject:("齐鲁少年")',
    'title:("齐鲁少年报")',
    'description:("齐鲁少年报")',
    'identifier:(qlsn*)',
]

IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|tiff?|webp)(?:$|\?)", re.I)
PDF_EXT = re.compile(r"\.pdf(?:$|\?)", re.I)
PAGE_HINT = re.compile(
    r"(?:paper|newspaper|epaper|page|edition|banmian|ban|版|报|szb|newsimg|mobile20\d{6})",
    re.I,
)
DATE_HINT = re.compile(r"(?:20\d{2})[-_/]?(?:0[1-9]|1[0-2])[-_/]?(?:0[1-9]|[12]\d|3[01])")


def get(url: str, *, accept: str = "*/*", max_bytes: int | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read() if max_bytes is None else r.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(f"resource exceeds {max_bytes} bytes")
        return data, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def wayback_query(label: str, target: str):
    params = {
        "url": target,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "filter": "statuscode:200",
        "collapse": "digest",
    }
    endpoint = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
    try:
        raw, _, _ = get(endpoint, accept="application/json")
        payload = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        return [], {"source": "wayback", "target": target, "error": f"{type(exc).__name__}: {exc}"}
    if not payload:
        return [], None
    header = payload[0]
    rows = []
    for item in payload[1:]:
        d = dict(zip(header, item))
        original = d.get("original", "")
        mime = (d.get("mimetype") or "").lower()
        is_image = mime.startswith("image/") or bool(IMAGE_EXT.search(original))
        is_pdf = mime == "application/pdf" or bool(PDF_EXT.search(original))
        if not (is_image or is_pdf):
            continue
        ts = d.get("timestamp", "")
        archive_url = f"https://web.archive.org/web/{ts}id_/{original}" if ts else ""
        score = 0
        if is_pdf:
            score += 80
        if is_image:
            score += 20
        if PAGE_HINT.search(original):
            score += 35
        if DATE_HINT.search(original):
            score += 15
        host = (urllib.parse.urlparse(original).hostname or "").lower()
        # Old qlsn.com is known-good only through 2007; later resources remain candidates.
        historical_identity = ""
        if host in {"qlsn.com", "www.qlsn.com"}:
            try:
                capture_year = int(ts[:4])
            except Exception:
                capture_year = 9999
            historical_identity = "verified_old_site" if capture_year <= 2008 else "requires_context_verification"
        rows.append({
            "source": label,
            "timestamp": ts,
            "original": original,
            "archive_url": archive_url,
            "mimetype": mime,
            "digest": d.get("digest", ""),
            "reported_length": d.get("length", ""),
            "candidate_score": score,
            "historical_identity": historical_identity,
            "resource_type": "pdf" if is_pdf else "image",
        })
    return rows, None


def ia_search(query: str):
    params = [
        ("q", query),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "description"),
        ("fl[]", "date"),
        ("fl[]", "mediatype"),
        ("fl[]", "subject"),
        ("rows", "100"),
        ("page", "1"),
        ("output", "json"),
    ]
    endpoint = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(params)
    try:
        raw, _, _ = get(endpoint, accept="application/json")
        docs = json.loads(raw.decode("utf-8", "replace")).get("response", {}).get("docs", [])
    except Exception as exc:
        return [], {"source": "internet_archive", "target": query, "error": f"{type(exc).__name__}: {exc}"}
    out = []
    for d in docs:
        identifier = str(d.get("identifier", ""))
        out.append({
            "query": query,
            "identifier": identifier,
            "title": str(d.get("title", "")),
            "description": str(d.get("description", ""))[:1000],
            "date": str(d.get("date", "")),
            "mediatype": str(d.get("mediatype", "")),
            "subject": json.dumps(d.get("subject", ""), ensure_ascii=False),
            "item_url": f"https://archive.org/details/{identifier}" if identifier else "",
            "status": "candidate_requires_item_verification",
        })
    return out, None


def inspect_resource(row: dict):
    out = dict(row)
    out.update({
        "http_ok": "",
        "resolved_url": "",
        "content_type": "",
        "content_length": "",
        "sha256": "",
        "width": "",
        "height": "",
        "image_format": "",
        "portrait_ratio": "",
        "likely_fullpage": "",
        "fetch_error": "",
    })
    url = row.get("archive_url", "")
    if not url:
        return out
    try:
        raw, final_url, headers = get(url, max_bytes=MAX_BYTES)
        out["http_ok"] = "yes"
        out["resolved_url"] = final_url
        out["content_type"] = headers.get("content-type", "").split(";", 1)[0]
        out["content_length"] = str(len(raw))
        out["sha256"] = hashlib.sha256(raw).hexdigest()
        if row.get("resource_type") == "image":
            try:
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as im:
                    w, h = im.size
                    out["width"] = str(w)
                    out["height"] = str(h)
                    out["image_format"] = str(im.format or "")
                    ratio = h / w if w else 0
                    out["portrait_ratio"] = f"{ratio:.3f}"
                    # Newspaper full pages are typically large portrait images. This is only triage.
                    likely = h >= 1000 and ratio >= 1.15 and w >= 650
                    out["likely_fullpage"] = "yes" if likely else "no"
            except Exception as exc:
                out["fetch_error"] = f"image_parse:{type(exc).__name__}:{exc}"
        elif row.get("resource_type") == "pdf":
            out["likely_fullpage"] = "possible_pdf"
    except Exception as exc:
        out["fetch_error"] = f"{type(exc).__name__}: {exc}"
    return out


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    started = time.monotonic()
    errors = []
    wb_rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(wayback_query, label, target): (label, target) for label, target in WAYBACK_TARGETS}
        for fut in as_completed(futures):
            rows, err = fut.result()
            wb_rows.extend(rows)
            if err:
                errors.append(err)
            print(f"Wayback {futures[fut][0]}: {len(rows)} media/PDF candidates", flush=True)

    # De-duplicate by content digest when available, otherwise by original+timestamp.
    dedup = {}
    for r in wb_rows:
        key = (r.get("digest") or "", r.get("original") if not r.get("digest") else "", r.get("timestamp") if not r.get("digest") else "")
        dedup[key] = r
    wb_rows = sorted(dedup.values(), key=lambda r: (-int(r["candidate_score"]), r["original"]))

    inspected = []
    # Inspect the best 160 resources; metadata for all remains in wayback_media_candidates.csv.
    top = wb_rows[:160]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(inspect_resource, r): r for r in top}
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            inspected.append(row)
            if row.get("likely_fullpage") in {"yes", "possible_pdf"} or i % 25 == 0:
                print(f"inspect {i}/{len(top)} fullpage={row.get('likely_fullpage')} {row.get('original')}", flush=True)
    inspected.sort(key=lambda r: (r.get("likely_fullpage") != "yes", -int(r.get("candidate_score") or 0), r.get("original", "")))

    ia_rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(ia_search, q): q for q in IA_QUERIES}
        for fut in as_completed(futures):
            rows, err = fut.result()
            ia_rows.extend(rows)
            if err:
                errors.append(err)
            print(f"Internet Archive {futures[fut]}: {len(rows)} items", flush=True)
    ia_dedup = {}
    for r in ia_rows:
        ia_dedup[(r["identifier"], r["query"])] = r
    ia_rows = sorted(ia_dedup.values(), key=lambda r: (r["identifier"], r["query"]))

    wb_fields = ["source","timestamp","original","archive_url","mimetype","digest","reported_length","candidate_score","historical_identity","resource_type"]
    inspect_fields = wb_fields + ["http_ok","resolved_url","content_type","content_length","sha256","width","height","image_format","portrait_ratio","likely_fullpage","fetch_error"]
    ia_fields = ["query","identifier","title","description","date","mediatype","subject","item_url","status"]
    write_csv(OUT / "wayback_media_candidates.csv", wb_rows, wb_fields)
    write_csv(OUT / "inspected_media_candidates.csv", inspected, inspect_fields)
    write_csv(OUT / "internet_archive_items.csv", ia_rows, ia_fields)

    fullpages = [r for r in inspected if r.get("likely_fullpage") == "yes"]
    pdfs = [r for r in inspected if r.get("resource_type") == "pdf" and r.get("http_ok") == "yes"]
    report = {
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "wayback_media_candidates": len(wb_rows),
        "inspected_candidates": len(inspected),
        "large_portrait_candidates": len(fullpages),
        "reachable_pdf_candidates": len(pdfs),
        "internet_archive_item_hits": len(ia_rows),
        "large_portrait_examples": [
            {k: r.get(k, "") for k in ("original","archive_url","width","height","sha256","historical_identity")}
            for r in fullpages[:30]
        ],
        "errors": errors,
        "notes": [
            "Large portrait classification is triage only; visual identity/issue/page must be verified before promotion.",
            "qlsn.com resources after the verified historical period require contextual verification because the domain was later repurposed.",
            "Third-party image/PDF bytes are never committed; only URLs, dimensions, hashes and metadata are stored.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
