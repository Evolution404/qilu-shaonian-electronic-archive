#!/usr/bin/env python3
"""Exact-first Common Crawl/WARC recovery for historical szb.cnssiot.cn assets.

This recovers more than index metadata:
- query a small set of high-value exact URLs across crawls around the verified CMS period;
- retrieve matching WARC records using filename/offset/length;
- parse the captured HTTP response body and verify HTML/JPEG/PNG/PDF bytes by magic/hash;
- when an edition HTML page is recovered, extract its concrete Pagepic/Pagepdf/Img URLs and
  run a second exact Common Crawl pass for those media resources.

Only provenance, URLs, response metadata and hashes are committed. Third-party newspaper
bytes are intentionally not committed by this discovery/recovery stage.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "szb_commoncrawl"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-commoncrawl-warc/2.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 45
INDEX_WORKERS = 6
WARC_WORKERS = 6
MAX_WARC_RANGE = 50 * 1024 * 1024
MAX_BODY = 45 * 1024 * 1024
YEAR_RE = re.compile(r"CC-MAIN-(20\d{2})-(\d+)")

EDITIONS = {
    "A1": ("326", "http://szb.cnssiot.cn/content/2021-12/25/edition326_A1.html"),
    "A2": ("333", "http://szb.cnssiot.cn/content/2021-12/25/edition333_A2.html"),
    "A3": ("327", "http://szb.cnssiot.cn/content/2021-12/25/edition327_A3.html"),
    "A4": ("328", "http://szb.cnssiot.cn/content/2021-12/25/edition328_A4.html"),
    "A5": ("329", "http://szb.cnssiot.cn/content/2021-12/25/edition329_A5.html"),
    "A6": ("330", "http://szb.cnssiot.cn/content/2021-12/25/edition330_A6.html"),
    "A7": ("331", "http://szb.cnssiot.cn/content/2021-12/25/edition331_A7.html"),
    "A8": ("332", "http://szb.cnssiot.cn/content/2021-12/25/edition332_A8.html"),
}

A1_MEDIA = (
    "http://szb.cnssiot.cn/Img/2021/12/"
    "mobile202112245170816f8aaf438f9a1a9119831a2eab.jpg?editionid=326"
)

IMG_RE = re.compile(
    r'''(?i)(?:(?:https?:)?//szb\.cnssiot\.cn)?/?Img/[^"'<> \t\r\n]+?\.(?:jpe?g|png|pdf)(?:\?[^"'<> \t\r\n]*)?'''
)
PAGEPIC_RE = re.compile(r'''(?is)\bpagepic\s*=\s*["']([^"']+)["']''')
PAGEPDF_RE = re.compile(
    r'''(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(?:(?!</a>).){0,1400}?PDF原(?:版|面)(?:(?!</a>).){0,300}?</a>'''
)


def http_get(url: str, *, headers=None, max_bytes=None, retries=4) -> tuple[bytes, str, dict]:
    last = None
    hdr = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        hdr.update(headers)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                if max_bytes is None:
                    body = response.read()
                else:
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ValueError(f"response exceeds {max_bytes} bytes")
                return body, response.geturl(), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
        except Exception as exc:
            last = exc
        time.sleep(min(20, 2 * (attempt + 1)))
    raise last or RuntimeError("request failed")


def selected_indexes():
    raw, _, _ = http_get("https://index.commoncrawl.org/collinfo.json", max_bytes=4 * 1024 * 1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    selected = []
    for item in data:
        m = YEAR_RE.search(item.get("id", ""))
        if not m or not item.get("cdx-api"):
            continue
        year, week = int(m.group(1)), int(m.group(2))
        # Issue date is 2021-12-25; retain late-2021, all 2022, and early-2023 crawls.
        # A verified Wayback root snapshot still existed in Sep 2022, so 2022 is the key window.
        keep = (year == 2021 and week >= 43) or year == 2022 or (year == 2023 and week <= 14)
        if keep:
            selected.append(item)
    selected.sort(key=lambda x: x.get("id", ""))
    return selected


def target_rows():
    rows = [
        {"page": "", "edition_id": "", "provenance": "known_root", "url": "http://szb.cnssiot.cn/"},
    ]
    for page, (eid, url) in EDITIONS.items():
        rows.append({"page": page, "edition_id": eid, "provenance": "known_edition_route", "url": url})
    rows.extend(
        [
            {"page": "A1", "edition_id": "326", "provenance": "known_root_media_reference", "url": A1_MEDIA},
            {"page": "A1", "edition_id": "326", "provenance": "known_root_media_reference_no_query", "url": A1_MEDIA.split("?", 1)[0]},
        ]
    )
    p = urllib.parse.urlsplit(A1_MEDIA.split("?", 1)[0])
    name = Path(p.path).name
    stem = re.sub(r"^mobile", "", Path(name).stem, flags=re.I)
    directory = p.path.rsplit("/", 1)[0] + "/"
    for candidate_name in [f"{stem}.jpg", f"big{stem}.jpg", f"{stem}.pdf", f"mobile{stem}.pdf"]:
        u = urllib.parse.urlunsplit((p.scheme, p.netloc, directory + candidate_name, "", ""))
        if candidate_name.endswith(".jpg"):
            u += "?editionid=326"
        rows.append({"page": "A1", "edition_id": "326", "provenance": "inferred_from_real_mobile_hash", "url": u})
    return rows


def query_one(index: dict, target: dict):
    params = {
        "url": target["url"],
        "output": "json",
        "filter": "status:200",
        "matchType": "exact",
    }
    api = index["cdx-api"] + "?" + urllib.parse.urlencode(params)
    out = []
    try:
        raw, _, _ = http_get(api, headers={"Accept": "application/json,text/plain,*/*"}, max_bytes=8 * 1024 * 1024)
        for line in raw.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            out.append(
                {
                    "page": target.get("page", ""),
                    "edition_id": target.get("edition_id", ""),
                    "provenance": target.get("provenance", ""),
                    "query_url": target["url"],
                    "index": index["id"],
                    "timestamp": d.get("timestamp", ""),
                    "url": d.get("url", ""),
                    "status": d.get("status", ""),
                    "mime": d.get("mime", d.get("mime-detected", "")),
                    "digest": d.get("digest", ""),
                    "filename": d.get("filename", ""),
                    "offset": d.get("offset", ""),
                    "length": d.get("length", ""),
                }
            )
        return out, ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def query_targets(indexes: list[dict], targets: list[dict], stage: str):
    rows = []
    errors = []
    with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as pool:
        futures = {
            pool.submit(query_one, index, target): (index["id"], target)
            for index in indexes
            for target in targets
        }
        for n, fut in enumerate(as_completed(futures), 1):
            index_id, target = futures[fut]
            found, err = fut.result()
            rows.extend(found)
            if err:
                errors.append(
                    {
                        "stage": stage,
                        "index": index_id,
                        "query_url": target["url"],
                        "error": err,
                    }
                )
            if found:
                print(stage, "HIT", index_id, target["url"], "rows", len(found), flush=True)
            elif n % 50 == 0:
                print(stage, "progress", n, "/", len(futures), flush=True)
    uniq = {}
    for row in rows:
        key = (row["url"], row["timestamp"], row["digest"], row["filename"], row["offset"])
        old = uniq.get(key)
        if old is None or old["provenance"].startswith("inferred_"):
            uniq[key] = row
    return sorted(uniq.values(), key=lambda r: (r["timestamp"], r["url"])), errors


def dechunk(data: bytes):
    out = io.BytesIO()
    pos = 0
    try:
        while True:
            end = data.find(b"\r\n", pos)
            if end < 0:
                return data
            size_text = data[pos:end].split(b";", 1)[0].strip()
            size = int(size_text, 16)
            pos = end + 2
            if size == 0:
                return out.getvalue()
            out.write(data[pos : pos + size])
            pos += size + 2
    except Exception:
        return data


def split_headers(block: bytes):
    headers = {}
    first = ""
    lines = block.decode("latin1", "replace").split("\r\n")
    if lines:
        first = lines[0]
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return first, headers


def parse_warc_record(raw_gzip: bytes):
    try:
        record = gzip.decompress(raw_gzip)
    except Exception:
        with gzip.GzipFile(fileobj=io.BytesIO(raw_gzip)) as gz:
            record = gz.read(MAX_BODY + 4 * 1024 * 1024)
    warc_end = record.find(b"\r\n\r\n")
    if warc_end < 0:
        raise ValueError("missing WARC header terminator")
    warc_header = record[:warc_end]
    payload = record[warc_end + 4 :]
    _, warc_headers = split_headers(warc_header)
    http_end = payload.find(b"\r\n\r\n")
    if http_end < 0:
        return warc_headers, "", {}, payload
    status_line, http_headers = split_headers(payload[:http_end])
    body = payload[http_end + 4 :]
    if "chunked" in http_headers.get("transfer-encoding", "").lower():
        body = dechunk(body)
    encoding = http_headers.get("content-encoding", "").lower()
    try:
        if "gzip" in encoding:
            body = gzip.decompress(body)
        elif "deflate" in encoding:
            body = zlib.decompress(body)
    except Exception:
        pass
    if len(body) > MAX_BODY:
        raise ValueError(f"decoded body exceeds {MAX_BODY} bytes")
    return warc_headers, status_line, http_headers, body


def classify_body(body: bytes, http_headers: dict):
    ctype = http_headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if body.startswith(b"%PDF-"):
        return "pdf", ctype or "application/pdf"
    if body.startswith(b"\xff\xd8\xff"):
        return "jpeg", ctype or "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", ctype or "image/png"
    head = body[:4096].lstrip().lower()
    if "html" in ctype or head.startswith(b"<!doctype html") or b"<html" in head:
        return "html", ctype or "text/html"
    if ctype.startswith("image/"):
        return "image", ctype
    return "other", ctype


def fetch_warc(row: dict):
    out = dict(row)
    out.update(
        {
            "warc_fetch": "",
            "status_line": "",
            "content_type": "",
            "body_kind": "",
            "body_bytes": "",
            "sha256": "",
            "warc_target_uri": "",
            "error": "",
        }
    )
    try:
        offset = int(row["offset"])
        length = int(row["length"])
        if length <= 0 or length > MAX_WARC_RANGE:
            raise ValueError(f"invalid/oversize WARC range length={length}")
        url = "https://data.commoncrawl.org/" + row["filename"].lstrip("/")
        headers = {"Range": f"bytes={offset}-{offset + length - 1}", "Accept": "application/octet-stream"}
        raw, _, _ = http_get(url, headers=headers, max_bytes=length + 1024, retries=4)
        warc_headers, status_line, http_headers, body = parse_warc_record(raw)
        kind, ctype = classify_body(body, http_headers)
        out.update(
            {
                "warc_fetch": "verified",
                "status_line": status_line,
                "content_type": ctype,
                "body_kind": kind,
                "body_bytes": str(len(body)),
                "sha256": hashlib.sha256(body).hexdigest(),
                "warc_target_uri": warc_headers.get("warc-target-uri", ""),
                "error": "",
                "_body": body,
            }
        )
    except Exception as exc:
        out["warc_fetch"] = "failed"
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def recover_warc_rows(records: list[dict], stage: str):
    if not records:
        return []
    rows = []
    # One body per unique digest/range is enough for verification, but preserve the best page/provenance row.
    chosen = {}
    for r in records:
        key = (r.get("digest", ""), r.get("filename", ""), r.get("offset", ""), r.get("length", ""))
        if key not in chosen or chosen[key]["provenance"].startswith("inferred_"):
            chosen[key] = r
    with ThreadPoolExecutor(max_workers=WARC_WORKERS) as pool:
        futures = {pool.submit(fetch_warc, r): r for r in chosen.values()}
        for n, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            rows.append(row)
            print(
                stage,
                "WARC",
                row.get("warc_fetch"),
                row.get("body_kind"),
                row.get("page"),
                row.get("url"),
                flush=True,
            )
    return rows


def decode_text(body: bytes, content_type: str):
    m = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    encs = ([m.group(1)] if m else []) + ["utf-8", "gb18030"]
    best = None
    for enc in encs:
        try:
            text = body.decode(enc, "replace")
            score = text.count("\ufffd")
            if best is None or score < best[0]:
                best = (score, text)
        except Exception:
            pass
    return best[1] if best else body.decode("utf-8", "replace")


def normalize_media(base: str, raw: str, edition_id: str):
    v = raw.replace("&amp;", "&").strip()
    if v.startswith("//"):
        v = "http:" + v
    if v.lower().startswith("img/"):
        v = "/" + v
    u = urllib.parse.urljoin(base, v)
    if not u.startswith(("http://", "https://")):
        return ""
    if "/img/" in u.lower() and "editionid=" not in u.lower() and re.search(r"\.(?:jpe?g|png)$", u, re.I):
        u += ("&" if "?" in u else "?") + f"editionid={edition_id}"
    return u


def discover_from_html(recovered: list[dict]):
    targets = []
    for row in recovered:
        if row.get("warc_fetch") != "verified" or row.get("body_kind") != "html":
            continue
        page = row.get("page", "")
        eid = row.get("edition_id", "")
        body = row.get("_body", b"")
        text = decode_text(body, row.get("content_type", ""))
        base = row.get("url") or row.get("query_url") or "http://szb.cnssiot.cn/"
        found = []
        for raw in IMG_RE.findall(text):
            found.append((raw, "html_Img_reference"))
        for raw in PAGEPIC_RE.findall(text):
            found.append((raw, "html_Pagepic"))
        for raw in PAGEPDF_RE.findall(text):
            found.append((raw, "html_Pagepdf"))
        for raw, how in found:
            u = normalize_media(base, raw, eid)
            if not u or "szb.cnssiot.cn" not in urllib.parse.urlsplit(u).netloc.lower():
                continue
            if not ("/img/" in u.lower() or re.search(r"\.(?:pdf|jpe?g|png)(?:\?|$)", u, re.I)):
                continue
            targets.append({"page": page, "edition_id": eid, "provenance": how, "url": u})
    rank = {"html_Pagepdf": 3, "html_Pagepic": 2, "html_Img_reference": 1}
    uniq = {}
    for t in targets:
        key = (t["page"], t["edition_id"], t["url"])
        old = uniq.get(key)
        if old is None or rank[t["provenance"]] > rank[old["provenance"]]:
            uniq[key] = t
    return sorted(uniq.values(), key=lambda r: (r["page"], r["url"]))


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def serializable_recovery(rows: list[dict]):
    out = []
    for row in rows:
        r = dict(row)
        r.pop("_body", None)
        out.append(r)
    return out


def main():
    indexes = selected_indexes()
    initial_targets = target_rows()
    print("indexes", len(indexes), "initial targets", len(initial_targets), flush=True)

    initial_records, errors1 = query_targets(indexes, initial_targets, "initial")
    initial_recovered = recover_warc_rows(initial_records, "initial")

    discovered_targets = discover_from_html(initial_recovered)
    print("discovered exact media targets", len(discovered_targets), flush=True)
    second_records = []
    second_recovered = []
    errors2 = []
    if discovered_targets:
        second_records, errors2 = query_targets(indexes, discovered_targets, "discovered")
        second_recovered = recover_warc_rows(second_records, "discovered")

    all_records = initial_records + second_records
    all_recovered = initial_recovered + second_recovered

    record_fields = [
        "page",
        "edition_id",
        "provenance",
        "query_url",
        "index",
        "timestamp",
        "url",
        "status",
        "mime",
        "digest",
        "filename",
        "offset",
        "length",
    ]
    recovery_fields = record_fields + [
        "warc_fetch",
        "status_line",
        "content_type",
        "body_kind",
        "body_bytes",
        "sha256",
        "warc_target_uri",
        "error",
    ]
    target_fields = ["page", "edition_id", "provenance", "url"]
    error_fields = ["stage", "index", "query_url", "error"]

    write_csv(OUT / "initial_targets.csv", initial_targets, target_fields)
    write_csv(OUT / "index_records.csv", all_records, record_fields)
    write_csv(OUT / "warc_responses.csv", serializable_recovery(all_recovered), recovery_fields)
    write_csv(OUT / "discovered_assets.csv", discovered_targets, target_fields)
    write_csv(OUT / "errors.csv", errors1 + errors2, error_fields)

    verified = [r for r in all_recovered if r.get("warc_fetch") == "verified"]
    verified_media = [r for r in verified if r.get("body_kind") in {"pdf", "jpeg", "png", "image"}]
    verified_html = [r for r in verified if r.get("body_kind") == "html"]
    page_media = {}
    for r in verified_media:
        if r.get("page"):
            page_media.setdefault(r["page"], []).append(r)

    report = {
        "indexes_queried": len(indexes),
        "initial_exact_targets": len(initial_targets),
        "initial_index_records": len(initial_records),
        "verified_initial_warc_responses": sum(r.get("warc_fetch") == "verified" for r in initial_recovered),
        "verified_html_responses": len(verified_html),
        "discovered_media_targets": len(discovered_targets),
        "second_pass_index_records": len(second_records),
        "verified_media_responses": len(verified_media),
        "verified_pdf_responses": sum(r.get("body_kind") == "pdf" for r in verified_media),
        "verified_image_responses": sum(r.get("body_kind") in {"jpeg", "png", "image"} for r in verified_media),
        "pages_with_verified_media": sorted(page_media),
        "complete_a1_to_a8_media": all(page in page_media for page in EDITIONS),
        "query_errors": len(errors1) + len(errors2),
        "notes": [
            "Common Crawl capture timestamps are archival timestamps, not publication dates.",
            "A recovered Pagepdf can be PDF or a high-resolution JPG in 53BK deployments.",
            "Media bytes are verified transiently and hashed; this stage does not republish third-party newspaper binaries.",
            "inferred_from_real_mobile_hash targets remain hypotheses unless a WARC body verifies them.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
