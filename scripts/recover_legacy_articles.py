#!/usr/bin/env python3
"""Recover issue-linked 《齐鲁少年》 HTML articles from the verified legacy qlsn.com site.

The script starts from archived official-site pages, extracts original content links,
queries Wayback CDX for snapshots, fetches the archived HTML, and parses only metadata
needed for archival indexing (title, site date, author, source, issue number). It does not
copy full article bodies into the repository.
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
UA = "qilu-shaonian-electronic-archive/legacy-recovery-1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 15
WORKERS = 10

SEEDS = [
    ("2007_home", "https://web.archive.org/web/20070623224142id_/http://www.qlsn.com/index.asp", "http://www.qlsn.com/index.asp"),
    ("2004_home", "https://web.archive.org/web/20040716162949id_/http://www.qlsn.com/", "http://www.qlsn.com/"),
]
CONTENT_ROUTE = re.compile(r"(?:article_view\.asp|news_view\.asp|pic_view\.asp|announce_view\.asp)\?[^#]+", re.I)
ARTICLE_ROUTE = re.compile(r"article_view\.asp\?", re.I)
ISSUE_RE = re.compile(r"(?:来源[：:]?\s*)?齐鲁少年\s*[（(]\s*(\d{2,5})\s*期\s*[）)]")
DATE_RE = re.compile(r"【?日期】?\s*[：:]?\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)")
AUTHOR_RE = re.compile(r"【?作者】?\s*[：:]?\s*([^【\n]{1,80})")
SOURCE_RE = re.compile(r"【?来源】?\s*[：:]?\s*([^【\n]{1,120})")


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
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def decode(raw: bytes) -> tuple[str, str]:
    candidates = []
    for enc in ("utf-8", "gb18030", "big5"):
        text = raw.decode(enc, errors="replace")
        candidates.append((text.count("\ufffd"), enc, text))
    _, enc, text = min(candidates, key=lambda x: x[0])
    return text, enc


def canonical_original(base_original: str, href: str) -> str:
    href = html.unescape(href.strip())
    # Wayback id_ pages generally retain relative originals; strip accidental Wayback wrappers if present.
    m = re.search(r"/web/\d+(?:id_)?/(https?://.+)$", href)
    if m:
        href = m.group(1)
    return urllib.parse.urljoin(base_original, href)


def extract_seed_links(seed_id: str, archive_url: str, original_url: str) -> list[dict]:
    raw = request(archive_url)
    text, enc = decode(raw)
    p = LinkTextParser(); p.feed(text)
    out = []
    seen = set()
    for link in p.links:
        original = canonical_original(original_url, link["href"])
        parsed = urllib.parse.urlparse(original)
        if (parsed.hostname or "").lower() not in {"qlsn.com", "www.qlsn.com"}:
            continue
        route = parsed.path.rsplit("/", 1)[-1] + ("?" + parsed.query if parsed.query else "")
        if not CONTENT_ROUTE.search(route):
            continue
        key = original.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "seed_id": seed_id,
            "seed_archive_url": archive_url,
            "seed_encoding": enc,
            "anchor_text": link["text"],
            "original_url": original,
            "route_type": "article" if ARTICLE_ROUTE.search(route) else route.split("?", 1)[0].replace("_view.asp", ""),
        })
    return out


def cdx_snapshot(original: str) -> tuple[str, str, str]:
    params = {
        "url": original,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "filter": "statuscode:200",
        "filter": "mimetype:text/html",
        "collapse": "digest",
        "limit": "5",
        "from": "2000",
        "to": "2010",
    }
    # urlencoding a dict cannot express duplicate filter keys; mime filter is optional, so use one status filter only.
    params.pop("filter", None)
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
    r = html_rows[0]
    ts = r.get("timestamp", "")
    archived = f"https://web.archive.org/web/{ts}id_/{r.get('original') or original}" if ts else ""
    return ts, archived, r.get("digest", "")


def parse_article(original: str, archive_url: str) -> dict:
    base = {
        "original_url": original, "archive_url": archive_url, "title": "", "site_date": "",
        "author": "", "source_text": "", "issue_number": "", "encoding": "",
        "identity_verified": "", "metadata_excerpt": "", "fetch_error": "",
    }
    try:
        raw = request(archive_url)
        text, enc = decode(raw)
        p = LinkTextParser(); p.feed(text)
        visible = re.sub(r"\s+", " ", html.unescape(" ".join(p.visible))).strip()
        title = " ".join(p.title).strip()
        # Remove generic site prefix where possible; retain page title if article title cannot be isolated.
        article_title = ""
        m_browser = re.search(r"浏览文章\s+(.+?)\s+【?日期】?", visible)
        if m_browser:
            article_title = m_browser.group(1).strip()
        date_m = DATE_RE.search(visible)
        author_m = AUTHOR_RE.search(visible)
        source_m = SOURCE_RE.search(visible)
        issue_m = ISSUE_RE.search(visible)
        identity = "齐鲁少年" in title or "齐鲁少年报简介" in visible or "版权所有: 齐鲁少年" in visible or "版权所有：齐鲁少年" in visible
        base.update({
            "title": article_title or title,
            "site_date": date_m.group(1) if date_m else "",
            "author": author_m.group(1).strip() if author_m else "",
            "source_text": source_m.group(1).strip() if source_m else "",
            "issue_number": issue_m.group(1) if issue_m else "",
            "encoding": enc,
            "identity_verified": "yes" if identity else "no",
            "metadata_excerpt": visible[:800],
        })
    except Exception as exc:
        base["fetch_error"] = f"{type(exc).__name__}: {exc}"
    return base


def recover_one(link: dict) -> dict:
    row = dict(link)
    try:
        ts, archive_url, digest = cdx_snapshot(link["original_url"])
        row.update({"snapshot_timestamp": ts, "archive_url": archive_url, "digest": digest})
        if archive_url and link["route_type"] == "article":
            row.update(parse_article(link["original_url"], archive_url))
        else:
            row.update({"title": "", "site_date": "", "author": "", "source_text": "", "issue_number": "", "encoding": "", "identity_verified": "", "metadata_excerpt": "", "fetch_error": ""})
    except Exception as exc:
        row.update({"snapshot_timestamp": "", "archive_url": "", "digest": "", "title": "", "site_date": "", "author": "", "source_text": "", "issue_number": "", "encoding": "", "identity_verified": "", "metadata_excerpt": "", "fetch_error": f"{type(exc).__name__}: {exc}"})
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "seed_id", "seed_archive_url", "seed_encoding", "anchor_text", "route_type", "original_url",
        "snapshot_timestamp", "archive_url", "digest", "title", "site_date", "author", "source_text",
        "issue_number", "encoding", "identity_verified", "metadata_excerpt", "fetch_error",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def main() -> int:
    started = time.monotonic()
    links = []
    errors = []
    for seed_id, archive_url, original in SEEDS:
        try:
            found = extract_seed_links(seed_id, archive_url, original)
            links.extend(found)
            print(f"seed {seed_id}: {len(found)} content links", flush=True)
        except Exception as exc:
            errors.append({"seed_id": seed_id, "error": f"{type(exc).__name__}: {exc}"})
    dedup = {x["original_url"].lower(): x for x in links}
    links = list(dedup.values())

    recovered = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(recover_one, link): link for link in links}
        for future in as_completed(futures):
            row = future.result(); recovered.append(row)
            if row.get("issue_number") or row.get("fetch_error"):
                print(f"{row['original_url']} issue={row.get('issue_number')} title={row.get('title','')[:60]!r} err={row.get('fetch_error')}", flush=True)
    recovered.sort(key=lambda r: (r.get("route_type", ""), r.get("issue_number", ""), r.get("original_url", "")))
    write_csv(OUT / "legacy_content_links.csv", recovered)

    issue_articles = [r for r in recovered if r.get("route_type") == "article" and r.get("issue_number") and r.get("identity_verified") == "yes"]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "seed_count": len(SEEDS), "unique_content_links": len(links), "recovered_rows": len(recovered),
        "verified_issue_linked_articles": len(issue_articles),
        "issues": sorted({r["issue_number"] for r in issue_articles}, key=int),
        "verified_articles": [
            {"issue": r["issue_number"], "title": r["title"], "site_date": r["site_date"], "original_url": r["original_url"], "archive_url": r["archive_url"]}
            for r in issue_articles
        ],
        "seed_errors": errors,
        "notes": [
            "Site dates are not automatically treated as newspaper publication dates.",
            "Only pages explicitly tied to a 《齐鲁少年》 issue are candidates for promotion to electronic_records.csv.",
            "Full article bodies are not persisted; only metadata excerpts are stored for verification.",
        ],
    }
    (OUT / "legacy_recovery_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
