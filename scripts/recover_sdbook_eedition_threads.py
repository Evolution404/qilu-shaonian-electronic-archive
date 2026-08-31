#!/usr/bin/env python3
"""Recover official qlsn.com forum threads about electronic editions/sample papers/downloads.

This pass is deliberately narrow and conservative.  Historical list pages are scanned
for access-related topics.  A thread counts as recovered only when an exact Wayback
snapshot contains qlsn/forum body text; Internet Archive fallback/landing pages are
explicitly rejected.
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sdbook_eedition_threads"
OUT.mkdir(parents=True, exist_ok=True)
KNOWN = ROOT / "data" / "legacy_2009_2010_sdbook" / "pages.csv"

S = requests.Session()
S.headers.update({"User-Agent": "qilu-shaonian-eedition-thread-recovery/2.0"})
KEY_RE = re.compile(r"电子版|电子报|样报|邮寄|PDF|pdf|下载|合刊|报纸|订阅|扫描")
FILE_RE = re.compile(r"(?i)\.(?:pdf|zip|rar|7z|jpe?g|png|gif|docx?)(?:\?|$)")
TOPIC_RE = re.compile(r"(?:\?/|index\.asp\?/)(\d+)-1-0-(\d+)-1\.html", re.I)
IA_MARKERS = (
    "Wayback Machine Keep the news",
    "The Wayback Machine requires your browser",
    "Search the history of more than",
    "Internet Archive logo",
)
LISTS = [
    ("201105_root", "https://web.archive.org/web/20110529092430id_/http://www.qlsn.com/sdbook/index.asp?"),
    ("201109_root", "https://web.archive.org/web/20110918052805id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&action=list"),
    ("201109_p1", "https://web.archive.org/web/20110918104834id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=1&action="),
    ("201109_p2", "https://web.archive.org/web/20110918112837id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=2&action="),
    ("201109_p3", "https://web.archive.org/web/20110918115201id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=3&action="),
    ("201109_p4", "https://web.archive.org/web/20110918084350id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=4&action="),
    ("201109_p5", "https://web.archive.org/web/20110918121616id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=5&action="),
]


def fetch(url: str, timeout: int = 50):
    last = ""
    for n in range(3):
        try:
            r = S.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content, r.url, ""
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(1 + n * 2)
    return b"", url, last


def decode(b: bytes) -> str:
    for enc in ("gb18030", "utf-8", "big5"):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("latin1", "replace")


def original_from_replay(url: str) -> str:
    m = re.search(r"/web/\d+(?:id_|if_)?/(https?://.*)$", url)
    return m.group(1) if m else url


def topic_id(url: str) -> str:
    m = TOPIC_RE.search(url)
    return m.group(1) if m else ""


def normalize_topic(url: str) -> str:
    url = original_from_replay(url)
    # urljoin against index.asp? can produce index.asp?/NN-...; the archived
    # canonical topic route is /sdbook/?/NN-1-0-X-1.html.
    url = re.sub(r"/sdbook/index\.asp\?/", "/sdbook/?/", url, flags=re.I)
    return url


def id_replay(url: str) -> str:
    if "web.archive.org/web/" not in url:
        return url
    if re.search(r"/web/\d+id_/", url):
        return url
    return re.sub(r"/web/(\d+)(?:if_)?/", r"/web/\1id_/", url)


def known_snapshots():
    out: dict[str, list[str]] = {}
    if not KNOWN.exists():
        return out
    with KNOWN.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            original = row.get("original", "")
            tid = topic_id(original)
            archive_url = row.get("archive_url", "")
            if tid and archive_url and not row.get("error"):
                out.setdefault(tid, []).append(id_replay(archive_url))
    return out


def availability(original: str, timestamp: str):
    try:
        r = S.get(
            "https://archive.org/wayback/available",
            params={"url": original, "timestamp": timestamp},
            timeout=30,
        )
        r.raise_for_status()
        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        if snap.get("available") and str(snap.get("status")) == "200" and snap.get("url"):
            return id_replay(snap["url"]), ""
        return "", "no_available_snapshot"
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def valid_thread_body(plain: str, expected_title: str) -> bool:
    if not plain:
        return False
    if any(marker in plain for marker in IA_MARKERS):
        return False
    compact = re.sub(r"\s+", "", plain)
    title_compact = re.sub(r"\s+", "", expected_title)
    if title_compact and title_compact in compact:
        return True
    return "齐鲁少年" in compact and any(k in compact for k in ("样报", "电子版", "邮寄", "回复", "管理员", "版主", "发表于"))


def candidate_snapshots(original: str, known: dict[str, list[str]]):
    tid = topic_id(original)
    urls = list(known.get(tid, []))
    variants = [original]
    if "www.qlsn.com" in original:
        variants.append(original.replace("www.qlsn.com", "qlsn.com"))
    for ts in ("20110918", "20110529", "20101231", "20110115"):
        for variant in variants:
            u, _ = availability(variant, ts)
            if u:
                urls.append(u)
    return list(dict.fromkeys(urls))


def main():
    known = known_snapshots()
    list_rows = []
    threads: dict[str, dict[str, str]] = {}

    for label, u in LISTS:
        body, resolved, err = fetch(u)
        matches = 0
        if body:
            soup = BeautifulSoup(decode(body), "html.parser")
            base = original_from_replay(resolved)
            for a in soup.find_all("a", href=True):
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                if not title or not KEY_RE.search(title):
                    continue
                href = normalize_topic(urljoin(base, a["href"].strip()))
                if topic_id(href):
                    matches += 1
                    # Deduplicate by topic id because the same topic can appear on multiple list pages.
                    tid = topic_id(href)
                    threads.setdefault(tid, {"url": href, "title": title, "discovered_from": label})
        list_rows.append({
            "label": label,
            "source_url": u,
            "resolved_url": resolved,
            "bytes": len(body),
            "matching_anchors": matches,
            "error": err,
        })

    thread_rows = []
    links = []
    for tid, meta in sorted(threads.items(), key=lambda kv: int(kv[0])):
        original = meta["url"]
        body = b""
        resolved = ""
        err = ""
        plain = ""
        valid = False
        attempted = []
        for replay in candidate_snapshots(original, known):
            attempted.append(replay)
            b, r, e = fetch(replay)
            if not b:
                err = e
                continue
            soup = BeautifulSoup(decode(b), "html.parser")
            p = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            if valid_thread_body(p, meta["title"]):
                body, resolved, plain, valid, err = b, r, p, True, ""
                break
            err = "snapshot_returned_non_thread_or_wayback_landing_page"

        external = []
        files = []
        reply_markers = 0
        if valid:
            soup = BeautifulSoup(decode(body), "html.parser")
            reply_markers = len(re.findall(r"回复|楼主|版主|管理员|妮子|海霞|苍天一笑|发表于", plain))
            base = original_from_replay(resolved)
            for a in soup.find_all("a", href=True):
                full = urljoin(base, a["href"].strip())
                txt = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                if FILE_RE.search(full):
                    files.append(full)
                    links.append({"thread_url": original, "anchor_text": txt, "url": full, "kind": "file"})
                host = (urlparse(full).hostname or "").lower()
                if host and "qlsn.com" not in host and "web.archive.org" not in host and "archive.org" not in host:
                    external.append(full)
                    links.append({"thread_url": original, "anchor_text": txt, "url": full, "kind": "external"})

        thread_rows.append({
            "topic_id": tid,
            "thread_url": original,
            "discovered_title": meta["title"],
            "discovered_from": meta["discovered_from"],
            "resolved_archive_url": resolved,
            "recovered": "yes" if valid else "no",
            "bytes": len(body),
            "reply_markers": reply_markers,
            "external_links": "|".join(dict.fromkeys(external)),
            "file_refs": "|".join(dict.fromkeys(files)),
            "attempted_snapshots": "|".join(attempted),
            "excerpt": plain[:5000] if valid else "",
            "error": err,
        })

    def write(name, rows, fields):
        with (OUT / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    write("list_pages.csv", list_rows, ["label", "source_url", "resolved_url", "bytes", "matching_anchors", "error"])
    write(
        "threads.csv",
        thread_rows,
        ["topic_id", "thread_url", "discovered_title", "discovered_from", "resolved_archive_url", "recovered", "bytes", "reply_markers", "external_links", "file_refs", "attempted_snapshots", "excerpt", "error"],
    )
    write("links.csv", links, ["thread_url", "anchor_text", "url", "kind"])

    report = {
        "list_pages": len(LISTS),
        "list_pages_recovered": sum(bool(r["bytes"]) for r in list_rows),
        "matching_thread_ids": len(threads),
        "threads_with_valid_archived_body": sum(r["recovered"] == "yes" for r in thread_rows),
        "threads_rejected_as_non_body": sum(r["recovered"] != "yes" for r in thread_rows),
        "threads_with_file_refs": sum(bool(r["file_refs"]) for r in thread_rows),
        "threads_with_external_links": sum(bool(r["external_links"]) for r in thread_rows),
        "titles": [r["discovered_title"] for r in thread_rows],
        "notes": [
            "Internet Archive fallback/landing HTML is explicitly rejected and never counted as a recovered forum thread.",
            "Known exact sdbook captures are tried before Wayback availability lookup.",
            "A question about an electronic edition is evidence only after its actual historical body/replies are recovered.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
