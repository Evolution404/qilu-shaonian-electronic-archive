#!/usr/bin/env python3
"""Recover original-layout page assets from the historical szb.cnssiot.cn CMS.

The recovery has two layers:
1. inspect the verified Wayback root snapshot and its historical scripts;
2. fetch every known 2021-12-25 edition page (A1-A8) from Wayback and extract the
   concrete image/PDF URLs that the CMS rendered for that edition.

Only URLs, response metadata and hashes are committed. Newspaper bytes are not committed
by this script; verified public assets can be promoted separately after provenance review.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "szb_recovery"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-szb-recovery/1.1 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 30
MAX_ASSET_BYTES = 40 * 1024 * 1024
TS = "20220928010853"
ORIGIN = "http://szb.cnssiot.cn/"
SNAP = f"https://web.archive.org/web/{TS}id_/{ORIGIN}"

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

IMG_RE = re.compile(
    r"(?:https?://szb\.cnssiot\.cn/)?(?:Img|img)/[^\"'<>\s]+?\.(?:jpe?g|png|pdf)(?:\?[^\"'<>\s]*)?",
    re.I,
)
EDITION_RE = re.compile(r"content/(20\d{2}-\d{2}/\d{2})/edition(\d+)_([A-Z]\d+)\.html", re.I)
ARTICLE_RE = re.compile(r"content/(20\d{2}-\d{2}/\d{2})/(\d+)\.html", re.I)
ENDPOINT_RE = re.compile(r"[\"']((?:/api/|/jquery/)[^\"']+)[\"']", re.I)
MOBILE_RE = re.compile(r"mobile20\d{10,}[^\"'<>\s]*\.(?:jpe?g|png)", re.I)
MEDIA_ATTR_RE = re.compile(
    r'''(?is)(?:href|src)\s*=\s*["']([^"']+\.(?:pdf|jpe?g|png)(?:\?[^"']*)?)["']'''
)
PAGEPIC_JS_RE = re.compile(r'''(?is)\bpagepic\s*=\s*["']([^"']+)["']''')
PDF_TEXT_RE = re.compile(r"PDF原(?:版|面)|pdficon|pdf\.gif", re.I)
PDF_HREF_RE = re.compile(r'''(?is)<a[^>]+href=["']([^"']+)["'][^>]*>[^<]*(?:<[^>]+>[^<]*){0,3}PDF原(?:版|面)''')


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "script" and d.get("src"):
            self.scripts.append(d["src"])
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        if tag == "img" and d.get("src"):
            self.images.append(d["src"])


def get(url, accept="*/*", max_bytes=None):
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(r, timeout=TIMEOUT) as x:
        if max_bytes is None:
            body = x.read()
        else:
            body = x.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError(f"response larger than {max_bytes} bytes")
        return body, x.geturl(), {k.lower(): v for k, v in x.headers.items()}


def decode(raw, h=None):
    c = (h or {}).get("content-type", "")
    m = re.search(r"charset=([\w.-]+)", c, re.I)
    encs = ([m.group(1)] if m else []) + ["utf-8", "gb18030"]
    best = None
    for e in encs:
        try:
            t = raw.decode(e, "replace")
            q = t.count("\ufffd")
            if best is None or q < best[0]:
                best = (q, t)
        except Exception:
            pass
    return best[1] if best else raw.decode("utf-8", "replace")


def archive_for_original(url, timestamp=TS):
    if url.startswith("//"):
        url = "http:" + url
    if url.startswith("/"):
        url = urllib.parse.urljoin(ORIGIN, url)
    return f"https://web.archive.org/web/{timestamp}id_/{url}"


def normalize_origin(base, raw):
    v = html.unescape(raw.strip())
    if v.startswith("//"):
        v = "http:" + v
    u = urllib.parse.urljoin(base, v)
    p = urllib.parse.urlsplit(u)
    if p.netloc == "web.archive.org":
        m = re.search(r"/web/\d+(?:id_)?/(https?://.+)$", p.path + (("?" + p.query) if p.query else ""))
        if m:
            return m.group(1)
    return u


def extract(text, source):
    rows = []
    for m in IMG_RE.findall(text):
        rows.append(("image", m, source))
    for date, eid, page in EDITION_RE.findall(text):
        rows.append(
            (
                "edition_route",
                f"http://szb.cnssiot.cn/content/{date}/edition{eid}_{page}.html",
                source,
            )
        )
    for date, aid in ARTICLE_RE.findall(text):
        rows.append(("article_route", f"http://szb.cnssiot.cn/content/{date}/{aid}.html", source))
    for ep in ENDPOINT_RE.findall(text):
        rows.append(("endpoint", urllib.parse.urljoin(ORIGIN, ep), source))
    for m in MOBILE_RE.findall(text):
        rows.append(("mobile_asset_fragment", m, source))
    return rows


def cdx(target):
    params = {
        "url": target,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "collapse": "urlkey",
    }
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
    try:
        raw, _, _ = get(url, "application/json")
        data = json.loads(raw.decode("utf-8", "replace"))
        if not data:
            return []
        hdr = data[0]
        return [dict(zip(hdr, x)) for x in data[1:]]
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}", "original": target}]


def wayback_available(url, timestamp=TS):
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode(
        {"url": url, "timestamp": timestamp[:8]}
    )
    try:
        raw, _, _ = get(api, "application/json")
        data = json.loads(raw.decode("utf-8", "replace"))
        c = (data.get("archived_snapshots") or {}).get("closest") or {}
        if c.get("available") and c.get("url"):
            snap_url = c["url"]
            snap_url = re.sub(r"/web/(\d+)/", r"/web/\1id_/", snap_url, count=1)
            return snap_url, c.get("timestamp", "")
    except Exception:
        pass
    return "", ""


def fetch_archived_page(original):
    attempts = [archive_for_original(original)]
    closest, _ = wayback_available(original)
    if closest and closest not in attempts:
        attempts.append(closest)
    errors = []
    for u in attempts:
        try:
            raw, final, h = get(u, "text/html,*/*", 8 * 1024 * 1024)
            text = decode(raw, h)
            if "<html" in text.lower() or "<!doctype" in text.lower():
                return text, final, h, ""
            errors.append(f"{u}: non-html response {h.get('content-type','')}")
        except Exception as e:
            errors.append(f"{u}: {type(e).__name__}: {e}")
    return "", "", {}, " | ".join(errors)


def media_urls_from_edition(text, page_url, edition_id):
    found = []

    def add(url, discovery):
        if not url:
            return
        u = normalize_origin(page_url, url)
        if not u.startswith(("http://", "https://")):
            return
        low = u.lower()
        # Avoid probing theme logos/icons. Historical newspaper assets live under
        # the CMS Img tree, while Pagepdf can also be a direct PDF elsewhere.
        if not ("/img/" in low or re.search(r"\.pdf(?:\?|$)", low)):
            return
        found.append((u, discovery))

    parser = P()
    try:
        parser.feed(text)
    except Exception:
        pass

    for u in parser.images:
        add(u, "edition_html_img")
    for u in parser.links:
        if re.search(r"\.(?:pdf|jpe?g|png)(?:\?|$)", u, re.I):
            add(u, "edition_html_link")

    for u in MEDIA_ATTR_RE.findall(text):
        add(u, "edition_html_attr")
    for u in IMG_RE.findall(text):
        add(u, "edition_html_regex")
    for u in PAGEPIC_JS_RE.findall(text):
        add(u, "edition_pagepic_js")
    # Pagepdf may itself be a high-resolution JPG rather than a .pdf. Capture
    # the href specifically associated with the UI label "PDF原版/原面".
    for u in PDF_HREF_RE.findall(text):
        absu = normalize_origin(page_url, u)
        if absu.startswith(("http://", "https://")):
            found.append((absu, "edition_pagepdf_anchor"))

    normalized = []
    seen = set()
    for u, discovery in found:
        if "/img/" in u.lower() and "editionid=" not in u.lower() and u.lower().endswith(
            (".jpg", ".jpeg", ".png")
        ):
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}editionid={edition_id}"
        k = (u, discovery)
        if k not in seen:
            seen.add(k)
            normalized.append(k)
    return normalized


def sibling_candidates(url, edition_id):
    """Generate conservative candidates only from a real mobile Pagepic filename."""
    p = urllib.parse.urlsplit(url)
    name = Path(p.path).name
    if not name.lower().startswith("mobile") or not re.search(r"\.(?:jpe?g|png)$", name, re.I):
        return []
    stem = re.sub(r"^mobile", "", Path(name).stem, flags=re.I)
    directory = p.path.rsplit("/", 1)[0] + "/"
    names = [
        f"{stem}.jpg",
        f"big{stem}.jpg",
        f"{stem}.pdf",
        f"mobile{stem}.pdf",
    ]
    out = []
    for n in names:
        path = directory + n
        candidate = urllib.parse.urlunsplit((p.scheme or "http", p.netloc or "szb.cnssiot.cn", path, "", ""))
        if n.lower().endswith(".jpg"):
            candidate += f"?editionid={edition_id}"
        out.append(candidate)
    return out


def probe_asset(url):
    out = {
        "status": "",
        "resolved_url": "",
        "content_type": "",
        "length": "",
        "sha256": "",
        "archive_timestamp": "",
        "error": "",
    }
    errors = []

    def try_one(candidate, closest_ts=""):
        try:
            raw, final, h = get(candidate, "*/*", MAX_ASSET_BYTES)
            ctype = h.get("content-type", "").lower()
            magic_pdf = raw.startswith(b"%PDF-")
            magic_img = raw[:3] == b"\xff\xd8\xff" or raw.startswith(b"\x89PNG\r\n\x1a\n")
            if not (magic_pdf or magic_img or ctype.startswith("image/") or "pdf" in ctype):
                errors.append(f"{candidate}: non-media {ctype} ({len(raw)} bytes)")
                return False
            ts_match = re.search(r"/web/(\d+)", final)
            out.update(
                {
                    "status": "verified",
                    "resolved_url": final,
                    "content_type": ctype,
                    "length": str(len(raw)),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "archive_timestamp": ts_match.group(1) if ts_match else (closest_ts if "web.archive.org" in final else ""),
                    "error": "",
                }
            )
            return True
        except Exception as e:
            errors.append(f"{candidate}: {type(e).__name__}: {e}")
            return False

    # Historical evidence first: the exact root-snapshot timestamp is both faster
    # and stronger provenance than a live-domain probe.
    exact = archive_for_original(url)
    if try_one(exact):
        return out

    closest, closest_ts = wayback_available(url)
    if closest and closest != exact and try_one(closest, closest_ts):
        return out

    # Live origin is a last fallback because the historical host is often offline.
    live = [url]
    if url.startswith("http://"):
        live.append(url.replace("http://", "https://", 1))
    for candidate in dict.fromkeys(live):
        if try_one(candidate):
            return out

    out["status"] = "unverified"
    out["error"] = " | ".join(errors)[:4000]
    return out


def main():
    discovered = []
    resource_results = []
    probe_results = []
    edition_page_results = []
    errors = []

    try:
        raw, final, h = get(SNAP, "text/html,*/*")
        text = decode(raw, h)
        parser = P()
        parser.feed(text)
        discovered += extract(text, "root_snapshot")
        for u in parser.images:
            discovered.append(("html_img", normalize_origin(ORIGIN, u), "root_snapshot"))
        for u in parser.links:
            absu = normalize_origin(ORIGIN, u)
            if "szb.cnssiot.cn" in absu:
                discovered.append(("html_link", absu, "root_snapshot"))
        scripts = []
        for src in parser.scripts:
            original = normalize_origin(ORIGIN, src)
            scripts.append(original)
            discovered.append(("script", original, "root_snapshot"))

        def script_one(original):
            try:
                r, _, hh = get(archive_for_original(original))
                t = decode(r, hh)
                return original, extract(t, original), t[:20000], ""
            except Exception as e:
                return original, [], "", f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=10) as pool:
            fs = [pool.submit(script_one, s) for s in scripts]
            for fut in as_completed(fs):
                original, rows, snippet, err = fut.result()
                discovered += rows
                resource_results.append(
                    {
                        "resource": original,
                        "archive_url": archive_for_original(original),
                        "kind": "script",
                        "extract_count": len(rows),
                        "text_snippet": snippet[:3000],
                        "error": err,
                    }
                )
    except Exception as e:
        errors.append({"stage": "root_snapshot", "error": f"{type(e).__name__}: {e}"})

    edition_candidates = []

    def edition_one(page, eid, original):
        text, final, h, err = fetch_archived_page(original)
        row = {
            "page": page,
            "edition_id": eid,
            "original_url": original,
            "archive_url": final,
            "content_type": h.get("content-type", "") if h else "",
            "html_length": str(len(text.encode("utf-8", "replace"))) if text else "0",
            "media_count": "0",
            "mentions_pdf_ui": "yes" if text and PDF_TEXT_RE.search(text) else "no",
            "error": err,
        }
        found = media_urls_from_edition(text, original, eid) if text else []
        row["media_count"] = str(len(found))
        return row, found

    with ThreadPoolExecutor(max_workers=8) as pool:
        fs = {
            pool.submit(edition_one, page, eid, url): (page, eid, url)
            for page, (eid, url) in EDITIONS.items()
        }
        for fut in as_completed(fs):
            row, found = fut.result()
            edition_page_results.append(row)
            page, eid, _ = fs[fut]
            for u, discovery in found:
                edition_candidates.append(
                    {
                        "page": page,
                        "edition_id": eid,
                        "source_page": row["archive_url"] or row["original_url"],
                        "discovery": discovery,
                        "candidate_url": u,
                    }
                )

    for kind, val, src in discovered:
        if kind not in {"image", "html_img"}:
            continue
        if "/img/" not in val.lower():
            continue
        m = re.search(r"[?&]editionid=(\d+)", val, re.I)
        eid = m.group(1) if m else ""
        page = next((p for p, (i, _) in EDITIONS.items() if i == eid), "")
        edition_candidates.append(
            {
                "page": page,
                "edition_id": eid,
                "source_page": src,
                "discovery": "verified_root_media_reference",
                "candidate_url": val,
            }
        )
        if eid:
            for sib in sibling_candidates(val, eid):
                edition_candidates.append(
                    {
                        "page": page,
                        "edition_id": eid,
                        "source_page": val,
                        "discovery": "inferred_sibling_from_mobile_pagepic",
                        "candidate_url": sib,
                    }
                )

    unique_candidates = {}
    for r in edition_candidates:
        key = (r["page"], r["edition_id"], r["candidate_url"])
        old = unique_candidates.get(key)
        if old is None or old["discovery"].startswith("inferred_"):
            unique_candidates[key] = r
    edition_candidates = list(unique_candidates.values())

    def asset_one(r):
        out = dict(r)
        out.update(probe_asset(r["candidate_url"]))
        return out

    edition_asset_results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        fs = [pool.submit(asset_one, r) for r in edition_candidates]
        for fut in as_completed(fs):
            edition_asset_results.append(fut.result())

    targets = [
        "szb.cnssiot.cn/Img/*",
        "szb.cnssiot.cn/img/*",
        "szb.cnssiot.cn/*.pdf",
        "szb.cnssiot.cn/Img/*.pdf",
        "szb.cnssiot.cn/content/*",
        "szb.cnssiot.cn/api/*",
        "szb.cnssiot.cn/jquery/*",
        "szb.cnssiot.cn/js/*",
        "szb.cnssiot.cn/css/*",
    ]
    cdx_rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        fs = {pool.submit(cdx, t): t for t in targets}
        for fut in as_completed(fs):
            for r in fut.result():
                r["query_target"] = fs[fut]
                cdx_rows.append(r)

    known = [
        "http://szb.cnssiot.cn/api/Pagelist/48",
        "http://szb.cnssiot.cn/jquery/dayslist",
        "http://szb.cnssiot.cn/jquery/readtitle",
    ] + [u for _, u in EDITIONS.values()]
    for kind, val, _ in discovered:
        if kind in {"image", "html_img", "edition_route", "endpoint"}:
            if val.startswith("/"):
                val = urllib.parse.urljoin(ORIGIN, val)
            if val.startswith("http"):
                known.append(val)
    known = list(dict.fromkeys(known))[:240]

    def probe(url):
        out = {
            "url": url,
            "ok": "",
            "resolved_url": "",
            "content_type": "",
            "length": "",
            "error": "",
        }
        candidates = [url]
        if url.startswith("http://"):
            candidates.append(url.replace("http://", "https://", 1))
        for candidate in dict.fromkeys(candidates):
            try:
                r, f, h = get(candidate, "*/*", 8 * 1024 * 1024)
                out.update(
                    {
                        "ok": "yes",
                        "resolved_url": f,
                        "content_type": h.get("content-type", ""),
                        "length": str(len(r)),
                    }
                )
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
        return out

    with ThreadPoolExecutor(max_workers=16) as pool:
        fs = [pool.submit(probe, u) for u in known]
        for fut in as_completed(fs):
            probe_results.append(fut.result())

    drows = []
    seen = set()
    for kind, val, src in discovered:
        if kind == "image" and not val.startswith("http"):
            val = urllib.parse.urljoin(ORIGIN, val)
        key = (kind, val, src)
        if key in seen:
            continue
        seen.add(key)
        drows.append({"kind": kind, "value": val, "source": src})
    drows.sort(key=lambda r: (r["kind"], r["value"]))
    cdx_rows.sort(
        key=lambda r: (r.get("query_target", ""), r.get("original", ""), r.get("timestamp", ""))
    )
    probe_results.sort(key=lambda r: (r["ok"] != "yes", r["url"]))
    edition_page_results.sort(key=lambda r: r["page"])
    edition_asset_results.sort(
        key=lambda r: (
            r["page"],
            r["status"] != "verified",
            r["discovery"].startswith("inferred_"),
            r["candidate_url"],
        )
    )

    with (OUT / "discovered_strings.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "value", "source"])
        w.writeheader()
        w.writerows(drows)

    cfields = [
        "query_target",
        "timestamp",
        "original",
        "statuscode",
        "mimetype",
        "digest",
        "length",
        "error",
    ]
    with (OUT / "cdx_rows.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cfields, extrasaction="ignore")
        w.writeheader()
        w.writerows(cdx_rows)

    with (OUT / "resource_extracts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "resource",
                "archive_url",
                "kind",
                "extract_count",
                "text_snippet",
                "error",
            ],
        )
        w.writeheader()
        w.writerows(resource_results)

    with (OUT / "live_probes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["url", "ok", "resolved_url", "content_type", "length", "error"],
        )
        w.writeheader()
        w.writerows(probe_results)

    with (OUT / "edition_pages.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "page",
                "edition_id",
                "original_url",
                "archive_url",
                "content_type",
                "html_length",
                "media_count",
                "mentions_pdf_ui",
                "error",
            ],
        )
        w.writeheader()
        w.writerows(edition_page_results)

    with (OUT / "edition_assets.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "page",
                "edition_id",
                "source_page",
                "discovery",
                "candidate_url",
                "status",
                "resolved_url",
                "content_type",
                "length",
                "sha256",
                "archive_timestamp",
                "error",
            ],
        )
        w.writeheader()
        w.writerows(edition_asset_results)

    report = {
        "discovered_strings": len(drows),
        "cdx_rows": len(cdx_rows),
        "scripts_inspected": len(resource_results),
        "live_probes": len(probe_results),
        "live_hits": sum(1 for r in probe_results if r["ok"] == "yes"),
        "edition_pages": len(edition_page_results),
        "edition_pages_recovered": sum(1 for r in edition_page_results if r["archive_url"]),
        "edition_asset_candidates": len(edition_asset_results),
        "verified_edition_assets": sum(1 for r in edition_asset_results if r["status"] == "verified"),
        "verified_non_inferred_assets": sum(
            1
            for r in edition_asset_results
            if r["status"] == "verified" and not r["discovery"].startswith("inferred_")
        ),
        "cdx_image_rows": sum(
            1 for r in cdx_rows if str(r.get("mimetype", "")).startswith("image/")
        ),
        "cdx_pdf_rows": sum(
            1
            for r in cdx_rows
            if "pdf" in str(r.get("mimetype", "")).lower()
            or ".pdf" in str(r.get("original", "")).lower()
        ),
        "errors": errors,
        "notes": [
            "Archive timestamps are not publication dates.",
            "53BK Pagepdf can be JPG or PDF; only a verified response is promoted.",
            "inferred_sibling_from_mobile_pagepic rows are hypotheses until verified.",
            "Newspaper bytes are intentionally not committed by this discovery script.",
        ],
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
