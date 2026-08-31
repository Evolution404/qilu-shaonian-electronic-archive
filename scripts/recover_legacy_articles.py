#!/usr/bin/env python3
"""Recover issue-linked 《齐鲁少年》 HTML articles from the verified legacy qlsn.com site.

The script starts from archived official-site pages, extracts original content links,
resolves snapshots from the repository's existing Wayback inventory first, and only then
falls back to CDX. It stores metadata needed for archival indexing; full article bodies
are not persisted.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "legacy_recovery"
OUT.mkdir(parents=True, exist_ok=True)
INVENTORY = ROOT / "data" / "archive_crawl" / "wayback_urls.csv"
UA = "qilu-shaonian-electronic-archive/legacy-recovery-1.1 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 12
WORKERS = 10
RETRIES = 2
SNAPSHOTS: dict[str, list[dict]] = {}

SEEDS = [
    ("2007_home", "https://web.archive.org/web/20070623224142id_/http://www.qlsn.com/index.asp", "http://www.qlsn.com/index.asp"),
    ("2004_home", "https://web.archive.org/web/20040716162949id_/http://www.qlsn.com/", "http://www.qlsn.com/"),
]
CONTENT_ROUTE = re.compile(r"(?:article_view\.asp|news_view\.asp|pic_view\.asp|announce_view\.asp)\?[^#]+", re.I)
ARTICLE_ROUTE = re.compile(r"article_view\.asp\?", re.I)
ISSUE_RE = re.compile(r"齐鲁少年\s*[（(]\s*(\d{2,5})\s*期\s*[）)]")
DATE_RE = re.compile(r"【?日期】?\s*[：:]?\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)")
AUTHOR_RE = re.compile(r"【?作者】?\s*[：:]?\s*([^【]{1,80})")


class LinkTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.in_title = False
        self.title: list[str] = []
        self.skip = 0
        self.visible: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and d.get("href"):
            self.current_href = d["href"]
            self.current_text = []
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self.current_href is not None:
            self.links.append({"href": self.current_href, "text": " ".join(self.current_text).strip()})
            self.current_href = None
            self.current_text = []
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        t = " ".join(data.split())
        if not t:
            return
        if self.current_href is not None:
            self.current_text.append(t)
        if self.in_title:
            self.title.append(t)
        if not self.skip:
            self.visible.append(t)


def request(url: str, accept: str = "text/html,*/*") -> bytes:
    last_exc = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception as exc:
            last_exc = exc
            if attempt < RETRIES:
                time.sleep(0.6 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def decode(raw: bytes) -> tuple[str, str]:
    candidates = []
    for enc in ("utf-8", "gb18030", "big5"):
        text = raw.decode(enc, errors="replace")
        candidates.append((text.count("\ufffd"), enc, text))
    _, enc, text = min(candidates, key=lambda x: x[0])
    return text, enc


def canonical_key(url: str) -> str:
    p = urllib.parse.urlsplit(html.unescape(url.strip()))
    hostname = (p.hostname or "").lower()
    scheme = (p.scheme or "http").lower()
    netloc = hostname
    path = p.path or "/"
    # Query order is retained because old ASP routes use simple id parameters.
    return urllib.parse.urlunsplit((scheme, netloc, path, p.query, "")).lower()


def load_inventory() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with INVENTORY.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            original = row.get("original", "")
            if not original:
                continue
            out.setdefault(canonical_key(original), []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r.get("timestamp", ""))
    return out


def canonical_original(base_original: str, href: str) -> str:
    href = html.unescape(href.strip())
    m = re.search(r"/web/\d+(?:id_)?/(https?://.+)$", href)
    if m:
        href = m.group(1)
    return urllib.parse.urljoin(base_original, href)


def extract_seed_links(seed_id: str, archive_url: str, original_url: str) -> list[dict]:
    raw = request(archive_url)
    text, enc = decode(raw)
    p = LinkTextParser(); p.feed(text)
    out = []; seen = set()
    for link in p.links:
        original = canonical_original(original_url, link["href"])
        parsed = urllib.parse.urlparse(original)
        if (parsed.hostname or "").lower() not in {"qlsn.com", "www.qlsn.com"}:
            continue
        route = parsed.path.rsplit("/", 1)[-1] + ("?" + parsed.query if parsed.query else "")
        if not CONTENT_ROUTE.search(route):
            continue
        key = canonical_key(original)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "seed_id": seed_id, "seed_archive_url": archive_url, "seed_encoding": enc,
            "anchor_text": link["text"], "original_url": original,
            "route_type": "article" if ARTICLE_ROUTE.search(route) else route.split("?", 1)[0].replace("_view.asp", ""),
        })
    return out


def inventory_snapshot(original: str) -> tuple[str, str, str]:
    rows = SNAPSHOTS.get(canonical_key(original), [])
    if not rows:
        return "", "", ""
    # Prefer earliest successful capture because it is closest to original publication era.
    r = rows[0]
    ts = r.get("timestamp", "")
    archived = r.get("archive_url", "")
    if not archived and ts:
        archived = f"https://web.archive.org/web/{ts}id_/{r.get('original') or original}"
    return ts, archived, r.get("digest", "")


def cdx_snapshot(original: str) -> tuple[str, str, str]:
    query = urllib.parse.urlencode({
        "url": original, "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest",
        "filter": "statuscode:200", "collapse": "digest", "limit": "5", "from": "2000", "to": "2010",
    })
    endpoint = "https://web.archive.org/cdx/search/cdx?" + query
    payload = json.loads(request(endpoint, "application/json,text/plain,*/*").decode("utf-8", "replace"))
    if len(payload) < 2:
        return "", "", ""
    header = payload[0]
    rows = [dict(zip(header, x)) for x in payload[1:]]
    html_rows = [r for r in rows if "html" in (r.get("mimetype") or "").lower()] or rows
    r = html_rows[0]; ts = r.get("timestamp", "")
    archived = f"https://web.archive.org/web/{ts}id_/{r.get('original') or original}" if ts else ""
    return ts, archived, r.get("digest", "")


def resolve_snapshot(original: str) -> tuple[str, str, str, str]:
    ts, archived, digest = inventory_snapshot(original)
    if archived:
        return ts, archived, digest, "local_inventory"
    ts, archived, digest = cdx_snapshot(original)
    return ts, archived, digest, "cdx_fallback" if archived else "not_found"


def parse_article(original: str, archive_url: str, anchor_text: str) -> dict:
    base = {"original_url": original, "archive_url": archive_url, "title": anchor_text, "site_date": "", "author": "", "source_text": "", "issue_number": "", "encoding": "", "identity_verified": "", "metadata_excerpt": "", "fetch_error": ""}
    try:
        raw = request(archive_url); text, enc = decode(raw)
        p = LinkTextParser(); p.feed(text)
        visible = re.sub(r"\s+", " ", html.unescape(" ".join(p.visible))).strip()
        browser_title = " ".join(p.title).strip()
        issue_m = ISSUE_RE.search(visible)
        date_m = DATE_RE.search(visible)
        author_m = AUTHOR_RE.search(visible)
        identity = "齐鲁少年" in browser_title or "齐鲁少年报简介" in visible or "版权所有: 齐鲁少年" in visible or "版权所有：齐鲁少年" in visible
        if not anchor_text:
            m = re.search(r"浏览文章\s+(.+?)\s+【?日期】?", visible)
            if m:
                anchor_text = m.group(1).strip()
        issue = issue_m.group(1) if issue_m else ""
        base.update({
            "title": anchor_text or browser_title,
            "site_date": date_m.group(1) if date_m else "",
            "author": author_m.group(1).strip()[:80] if author_m else "",
            "source_text": f"齐鲁少年（{issue}期）" if issue else "",
            "issue_number": issue, "encoding": enc,
            "identity_verified": "yes" if identity else "no", "metadata_excerpt": visible[:800],
        })
    except Exception as exc:
        base["fetch_error"] = f"{type(exc).__name__}: {exc}"
    return base


def recover_one(link: dict) -> dict:
    row = dict(link)
    try:
        ts, archive_url, digest, source = resolve_snapshot(link["original_url"])
        row.update({"snapshot_timestamp": ts, "archive_url": archive_url, "digest": digest, "snapshot_source": source})
        if archive_url and link["route_type"] == "article":
            row.update(parse_article(link["original_url"], archive_url, link.get("anchor_text", "")))
        else:
            row.update({"title": link.get("anchor_text", ""), "site_date": "", "author": "", "source_text": "", "issue_number": "", "encoding": "", "identity_verified": "", "metadata_excerpt": "", "fetch_error": ""})
    except Exception as exc:
        row.update({"snapshot_timestamp": "", "archive_url": "", "digest": "", "snapshot_source": "error", "title": link.get("anchor_text", ""), "site_date": "", "author": "", "source_text": "", "issue_number": "", "encoding": "", "identity_verified": "", "metadata_excerpt": "", "fetch_error": f"{type(exc).__name__}: {exc}"})
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = ["seed_id", "seed_archive_url", "seed_encoding", "anchor_text", "route_type", "original_url", "snapshot_timestamp", "archive_url", "digest", "snapshot_source", "title", "site_date", "author", "source_text", "issue_number", "encoding", "identity_verified", "metadata_excerpt", "fetch_error"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def main() -> int:
    global SNAPSHOTS
    started = time.monotonic(); SNAPSHOTS = load_inventory()
    links = []; errors = []
    for seed_id, archive_url, original in SEEDS:
        try:
            found = extract_seed_links(seed_id, archive_url, original); links.extend(found)
            print(f"seed {seed_id}: {len(found)} content links", flush=True)
        except Exception as exc:
            errors.append({"seed_id": seed_id, "error": f"{type(exc).__name__}: {exc}"})
    dedup = {canonical_key(x["original_url"]): x for x in links}; links = list(dedup.values())
    recovered = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(recover_one, link): link for link in links}
        for future in as_completed(futures):
            row = future.result(); recovered.append(row)
            if row.get("issue_number") or row.get("fetch_error"):
                print(f"{row['original_url']} issue={row.get('issue_number')} title={row.get('title','')[:60]!r} src={row.get('snapshot_source')} err={row.get('fetch_error')}", flush=True)
    recovered.sort(key=lambda r: (r.get("route_type", ""), int(r.get("issue_number") or 999999), r.get("original_url", "")))
    write_csv(OUT / "legacy_content_links.csv", recovered)
    issue_articles = [r for r in recovered if r.get("route_type") == "article" and r.get("issue_number") and r.get("identity_verified") == "yes"]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "elapsed_seconds": round(time.monotonic() - started, 2),
        "recovery_version": "1.1", "seed_count": len(SEEDS), "inventory_keys": len(SNAPSHOTS),
        "unique_content_links": len(links), "recovered_rows": len(recovered), "verified_issue_linked_articles": len(issue_articles),
        "issues": sorted({r["issue_number"] for r in issue_articles}, key=int),
        "verified_articles": [{"issue": r["issue_number"], "title": r["title"], "site_date": r["site_date"], "original_url": r["original_url"], "archive_url": r["archive_url"], "snapshot_source": r["snapshot_source"]} for r in issue_articles],
        "seed_errors": errors,
        "notes": ["Local Wayback inventory is preferred over live CDX queries to avoid rate limits/timeouts.", "Site dates are not automatically treated as newspaper publication dates.", "Only pages explicitly tied to a 《齐鲁少年》 issue are candidates for promotion to electronic_records.csv.", "Full article bodies are not persisted; only metadata excerpts are stored for verification."],
    }
    (OUT / "legacy_recovery_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
