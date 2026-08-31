#!/usr/bin/env python3
"""Mine every archived qlsn.com HTML record relevant to 2004-2010.

Unlike the seed-based legacy recovery, this pass starts from the repository's complete
Wayback inventory and parses every distinct successful qlsn.com HTML capture. It records
only compact metadata/excerpts and media references; archived article/image bodies are
not committed.
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
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "archive_crawl" / "wayback_urls.csv"
OUT = ROOT / "data" / "legacy_2004_2010_inventory"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/legacy-2004-2010-1.0"
TIMEOUT = 14
WORKERS = 8

TARGET_START = 2004
TARGET_END = 2010
# Later captures can preserve pages published during the target interval.
CAPTURE_END = 2013

ROUTE_RE = re.compile(r"(?i)(article_view\.asp|news_view\.asp|announce_view\.asp|pic_view\.asp|index\.asp|/page/|/news/|/article/)")
DATE_RE = re.compile(r"(?:(?:【?日期】?\s*[：:]?\s*)|(?:日期[:：]?\s*))((?:200[4-9]|2010)[-/.年]\d{1,2}[-/.月]\d{1,2}日?)")
SOURCE_ISSUE_RE = re.compile(r"(?i)(?:来源\s*[：:]?\s*)?齐鲁少年(?:报)?\s*[（(]\s*(\d{2,5})\s*期\s*[）)]")
QILU_ISSUE_RE = re.compile(r"《?齐鲁少年(?:报)?》?[^\n。；;]{0,40}?(?:总?第\s*)?(\d{2,5})\s*期")
GENERIC_ISSUE_RE = re.compile(r"(?:总?第\s*)(\d{3,5})\s*期")
ABS_MEDIA_RE = re.compile(r"(?i)\.(?:jpe?g|png|gif|bmp|pdf)(?:$|[?#])")


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.media: list[tuple[str, str]] = []
        self.visible: list[str] = []
        self.title: list[str] = []
        self._href: str | None = None
        self._anchor: list[str] = []
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and d.get("href"):
            self._href = d["href"]
            self._anchor = []
        if tag in {"img", "embed", "source"}:
            for key in ("src", "data-src", "file"):
                if d.get(key):
                    self.media.append((tag, d[key]))
        if tag == "object" and d.get("data"):
            self.media.append((tag, d["data"]))
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None
            self._anchor = []
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        t = " ".join(data.split())
        if not t:
            return
        if self._href is not None:
            self._anchor.append(t)
        if self._in_title:
            self.title.append(t)
        if not self._skip:
            self.visible.append(t)


def decode(raw: bytes) -> tuple[str, str]:
    choices = []
    for enc in ("utf-8", "gb18030", "big5"):
        text = raw.decode(enc, "replace")
        choices.append((text.count("\ufffd"), enc, text))
    _, enc, text = min(choices, key=lambda x: x[0])
    return text, enc


def fetch(url: str) -> bytes:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read(5 * 1024 * 1024)
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    raise last  # type: ignore[misc]


def canon(url: str) -> str:
    p = urllib.parse.urlsplit(html.unescape(url.strip()))
    host = (p.hostname or "").lower()
    scheme = (p.scheme or "http").lower()
    return urllib.parse.urlunsplit((scheme, host, p.path or "/", p.query, "")).lower()


def unwayback(base_original: str, raw_url: str) -> str:
    value = html.unescape(raw_url.strip())
    m = re.search(r"/web/\d+(?:[a-z_]+)?/(https?://.+)$", value, re.I)
    if m:
        value = m.group(1)
    return urllib.parse.urljoin(base_original, value)


def load_inventory() -> tuple[list[dict], dict[str, list[dict]]]:
    html_rows: list[dict] = []
    by_key: dict[str, list[dict]] = defaultdict(list)
    with INVENTORY.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            original = row.get("original", "")
            if not original:
                continue
            p = urllib.parse.urlsplit(original)
            host = (p.hostname or "").lower()
            if host not in {"qlsn.com", "www.qlsn.com"}:
                continue
            by_key[canon(original)].append(row)
            ts = row.get("timestamp", "")
            year = int(ts[:4]) if len(ts) >= 4 and ts[:4].isdigit() else 0
            if not (TARGET_START <= year <= CAPTURE_END):
                continue
            if row.get("statuscode") != "200" or "html" not in (row.get("mimetype") or "").lower():
                continue
            if not ROUTE_RE.search(p.path + ("?" + p.query if p.query else "")):
                # Retain roots too; old layouts can expose article links from plain /.
                if p.path not in {"", "/"}:
                    continue
            html_rows.append(row)
    for rows in by_key.values():
        rows.sort(key=lambda r: r.get("timestamp", ""))
    # One representative per (original,digest); a changed digest is a distinct historical page state.
    dedup = {}
    for r in html_rows:
        k = (canon(r.get("original", "")), r.get("digest", "") or r.get("timestamp", ""))
        dedup.setdefault(k, r)
    return list(dedup.values()), by_key


def classify_year(visible: str, site_date: str) -> str:
    if site_date:
        m = re.match(r"(20\d{2})", site_date)
        if m:
            return m.group(1)
    years = [int(y) for y in re.findall(r"(?<!\d)(200[4-9]|2010)(?!\d)", visible)]
    if years:
        return str(Counter(years).most_common(1)[0][0])
    return ""


def issue_candidates(visible: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for m in SOURCE_ISSUE_RE.finditer(visible):
        found.setdefault(m.group(1), "source_qilu")
    for m in QILU_ISSUE_RE.finditer(visible):
        found.setdefault(m.group(1), "qilu_context")
    # Generic issue text is lower confidence and only retained on pages whose title/body clearly identify the newspaper.
    if "齐鲁少年" in visible:
        for m in GENERIC_ISSUE_RE.finditer(visible):
            found.setdefault(m.group(1), "generic_on_qilu_page")
    return sorted(found.items(), key=lambda x: int(x[0]))


def media_archive(by_key: dict[str, list[dict]], url: str) -> tuple[str, str]:
    rows = by_key.get(canon(url), [])
    image_rows = [r for r in rows if r.get("statuscode") == "200" and ("image" in (r.get("mimetype") or "").lower() or "pdf" in (r.get("mimetype") or "").lower())]
    if not image_rows:
        return "", ""
    r = image_rows[0]
    return r.get("archive_url", ""), r.get("digest", "")


def parse_one(row: dict, by_key: dict[str, list[dict]]) -> tuple[dict, list[dict], list[dict]]:
    original = row.get("original", "")
    archive_url = row.get("archive_url", "") or f"https://web.archive.org/web/{row.get('timestamp','')}id_/{original}"
    base = {
        "timestamp": row.get("timestamp", ""), "original": original, "archive_url": archive_url,
        "digest": row.get("digest", ""), "title": "", "site_date": "", "content_year": "",
        "issue_numbers": "", "issue_confidence": "", "encoding": "", "link_count": "0",
        "media_count": "0", "excerpt": "", "error": "",
    }
    issues: list[dict] = []
    media_rows: list[dict] = []
    try:
        raw = fetch(archive_url)
        text, enc = decode(raw)
        p = Parser(); p.feed(text)
        visible = re.sub(r"\s+", " ", html.unescape(" ".join(p.visible))).strip()
        title = " ".join(p.title).strip()
        dm = DATE_RE.search(visible)
        site_date = dm.group(1) if dm else ""
        candidates = issue_candidates(visible)
        content_year = classify_year(visible, site_date)
        base.update({
            "title": title, "site_date": site_date, "content_year": content_year,
            "issue_numbers": "|".join(x[0] for x in candidates),
            "issue_confidence": "|".join(x[1] for x in candidates),
            "encoding": enc, "link_count": str(len(p.links)), "media_count": str(len(p.media)),
            "excerpt": visible[:700],
        })
        for issue, confidence in candidates:
            pos = visible.find(issue)
            around = visible[max(0, pos - 130):pos + 220] if pos >= 0 else visible[:350]
            issues.append({
                "issue_number": issue, "confidence": confidence, "content_year": content_year,
                "site_date": site_date, "title": title, "original": original,
                "archive_url": archive_url, "context": around,
            })
        seen_media = set()
        candidates_media = list(p.media)
        for href, anchor in p.links:
            if ABS_MEDIA_RE.search(urllib.parse.urlsplit(href).path):
                candidates_media.append(("a", href))
        for kind, src in candidates_media:
            resolved = unwayback(original, src)
            hp = urllib.parse.urlsplit(resolved)
            if (hp.hostname or "").lower() not in {"qlsn.com", "www.qlsn.com"}:
                continue
            if not ABS_MEDIA_RE.search(hp.path):
                continue
            key = canon(resolved)
            if key in seen_media:
                continue
            seen_media.add(key)
            saved_url, saved_digest = media_archive(by_key, resolved)
            media_rows.append({
                "parent_original": original, "parent_archive_url": archive_url,
                "parent_content_year": content_year, "parent_issue_numbers": base["issue_numbers"],
                "kind": kind, "media_original": resolved, "archived_media_url": saved_url,
                "archived_media_digest": saved_digest,
            })
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
    return base, issues, media_rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def main() -> int:
    started = time.monotonic()
    html_rows, by_key = load_inventory()
    pages: list[dict] = []
    issues: list[dict] = []
    media: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(parse_one, row, by_key) for row in html_rows]
        for i, future in enumerate(as_completed(futures), 1):
            page, hits, refs = future.result()
            pages.append(page); issues.extend(hits); media.extend(refs)
            if hits:
                print(f"issue hit {page['original']} -> {page['issue_numbers']} {page['site_date']}", flush=True)
            if i % 20 == 0:
                print(f"parsed {i}/{len(futures)}", flush=True)
    pages.sort(key=lambda r: (r.get("content_year", "9999"), r.get("original", ""), r.get("timestamp", "")))
    issues.sort(key=lambda r: (int(r.get("issue_number") or 999999), r.get("original", "")))
    media.sort(key=lambda r: (r.get("parent_content_year", ""), r.get("media_original", "")))

    write_csv(OUT / "pages.csv", pages, ["timestamp","original","archive_url","digest","title","site_date","content_year","issue_numbers","issue_confidence","encoding","link_count","media_count","excerpt","error"])
    write_csv(OUT / "issue_hits.csv", issues, ["issue_number","confidence","content_year","site_date","title","original","archive_url","context"])
    write_csv(OUT / "media_refs.csv", media, ["parent_original","parent_archive_url","parent_content_year","parent_issue_numbers","kind","media_original","archived_media_url","archived_media_digest"])

    target_pages = [p for p in pages if p.get("content_year") and TARGET_START <= int(p["content_year"]) <= TARGET_END]
    high_issue = [r for r in issues if r["confidence"] in {"source_qilu", "qilu_context"}]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "inventory_html_states_selected": len(html_rows),
        "pages_parsed": len(pages),
        "pages_with_errors": sum(bool(p.get("error")) for p in pages),
        "pages_classified_2004_2010": len(target_pages),
        "issue_hit_rows": len(issues),
        "high_confidence_issue_hit_rows": len(high_issue),
        "distinct_high_confidence_issues": sorted({r["issue_number"] for r in high_issue}, key=int),
        "media_refs": len(media),
        "media_refs_with_archived_binary": sum(bool(r.get("archived_media_url")) for r in media),
        "pages_by_content_year": dict(sorted(Counter(p["content_year"] for p in target_pages).items())),
        "notes": [
            "This is a full-inventory pass, not a seed-link crawl.",
            "Capture year and publication/content year are kept separate.",
            "Generic 第N期 matches are lower confidence; promotion requires explicit 齐鲁少年 context/source evidence.",
            "Archived article/image bodies are not committed; only metadata and short verification excerpts are stored.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
