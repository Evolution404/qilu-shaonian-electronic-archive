#!/usr/bin/env python3
"""Inspect the archived qlsn.com '快乐下载' section for historical attachment handlers.

The old site often exposed downloads through handler links whose URL had no file
extension. This pass records those anchors/onclick targets, replays them at exact-era
Wayback timestamps, follows redirects/HTML redirect hints, and identifies payloads by
magic bytes. Newspaper files are never inferred solely from a download handler.
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
OUT = ROOT / 'data' / 'legacy_downloads'
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({'User-Agent': 'qilu-shaonian-archive-download-inspector/2.0'})
SEEDS = [
    ('list', 'https://web.archive.org/web/20070623224444id_/http://www.qlsn.com/downloads_list.asp'),
    ('list50', 'https://web.archive.org/web/20070625133455id_/http://www.qlsn.com/downloads_list.asp?show=50'),
    ('known125', 'https://web.archive.org/web/20070623224226id_/http://www.qlsn.com/downloads_view.asp?id=125'),
]
VIEW_RE = re.compile(r'downloads_view\.asp\?id=(\d+)', re.I)
FILE_RE = re.compile(r'(?i)\.(?:pdf|zip|rar|7z|docx?|xlsx?|pptx?|jpe?g|png|gif|txt)(?:\?|$)')
HANDLER_RE = re.compile(r'(?i)(?:download|down|file|soft|attachment|upload|href|url)')
URL_IN_JS_RE = re.compile(r"(?i)(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)|(?:open|location\.replace)\s*\(\s*['\"]([^'\"]+)")
META_REFRESH_RE = re.compile(r'(?i)url\s*=\s*([^;\s]+)')
IA_MARKERS = ('Wayback Machine Keep the news', 'Search the history of more than', 'Internet Archive logo')


def fetch(url, timeout=35):
    err = ''
    for n in range(3):
        try:
            r = S.get(url, timeout=(15, timeout), allow_redirects=True)
            r.raise_for_status()
            return r.content, r.url, r.headers.get('Content-Type', ''), ''
        except Exception as e:
            err = f'{type(e).__name__}: {e}'
            time.sleep(1 + n * 2)
    return b'', url, '', err


def decode(b):
    for enc in ('gb18030', 'utf-8', 'big5'):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode('latin1', 'replace')


def original_from_wayback(url):
    m = re.search(r'/web/\d+(?:id_|if_)?/(https?://.*)$', url)
    return m.group(1) if m else url


def archive_for(original, ts='20070625133455'):
    return f'https://web.archive.org/web/{ts}id_/{original}'


def payload_kind(body, content_type='', url=''):
    if not body:
        return ''
    if body.startswith(b'%PDF'):
        return 'pdf'
    if body.startswith(b'PK\x03\x04'):
        return 'zip'
    if body.startswith(b'Rar!'):
        return 'rar'
    if body.startswith(b'7z\xbc\xaf\x27\x1c'):
        return '7z'
    if body.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    low = (content_type + ' ' + url).lower()
    if 'application/pdf' in low:
        return 'pdf_declared'
    if 'text/html' in low or body[:200].lower().find(b'<html') >= 0:
        return 'html'
    return 'other'


def is_wayback_landing(body):
    if not body:
        return False
    text = decode(body[:200000])
    return any(x in text for x in IA_MARKERS)


def extract_redirect_hints(body, resolved):
    if not body:
        return []
    text = decode(body)
    soup = BeautifulSoup(text, 'html.parser')
    base = original_from_wayback(resolved)
    out = []
    for meta in soup.find_all('meta'):
        if str(meta.get('http-equiv', '')).lower() == 'refresh':
            m = META_REFRESH_RE.search(str(meta.get('content', '')))
            if m:
                out.append(urljoin(base, m.group(1).strip("'\"")))
    for script in soup.find_all('script'):
        src = script.get_text(' ', strip=True)
        for m in URL_IN_JS_RE.finditer(src):
            target = next((g for g in m.groups() if g), '')
            if target:
                out.append(urljoin(base, target))
    for tag in soup.find_all(['iframe', 'frame'], src=True):
        out.append(urljoin(base, tag['src'].strip()))
    return list(dict.fromkeys(out))


def collect_links(soup, base, label, did=''):
    direct_files = []
    handlers = []
    view_urls = []
    if not soup:
        return direct_files, handlers, view_urls
    for a in soup.find_all('a'):
        href = (a.get('href') or '').strip()
        onclick = (a.get('onclick') or '').strip()
        text = re.sub(r'\s+', ' ', a.get_text(' ', strip=True))
        full = urljoin(base, href) if href and not href.lower().startswith(('javascript:', '#')) else ''
        if full:
            m = VIEW_RE.search(full)
            if m:
                view_urls.append((m.group(1), full))
            if FILE_RE.search(full):
                direct_files.append((label, did, text, full, 'direct_extension'))
        # Capture non-extension download handlers and JS-bound destinations.
        handlerish = bool(re.search(r'下载|download|down', text, re.I)) or bool(full and HANDLER_RE.search(full)) or bool(onclick and HANDLER_RE.search(onclick))
        if handlerish:
            if full and not VIEW_RE.search(full):
                handlers.append((label, did, text, full, onclick, 'anchor'))
            for m in URL_IN_JS_RE.finditer(onclick):
                target = next((g for g in m.groups() if g), '')
                if target:
                    handlers.append((label, did, text, urljoin(base, target), onclick, 'onclick'))
    return direct_files, handlers, view_urls


def replay_target(original, timestamps):
    attempts = []
    for ts in timestamps:
        u = archive_for(original, ts)
        body, resolved, ctype, err = fetch(u, timeout=28)
        attempts.append(f'{ts}:{err or "ok"}')
        if not body or is_wayback_landing(body):
            continue
        return body, resolved, ctype, '', '|'.join(attempts)
    return b'', '', '', 'no_valid_archived_payload', '|'.join(attempts)


def main():
    page_rows = []
    direct_candidates = []
    handler_candidates = []
    view_urls = {}

    for label, url in SEEDS:
        body, resolved, ctype, err = fetch(url)
        text = decode(body) if body else ''
        soup = BeautifulSoup(text, 'html.parser') if text else None
        plain = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)) if soup else ''
        base = original_from_wayback(resolved)
        direct, handlers, views = collect_links(soup, base, label)
        direct_candidates.extend(direct)
        handler_candidates.extend(handlers)
        for did, full in views:
            view_urls[did] = full
        page_rows.append({
            'kind': label, 'source_url': url, 'resolved_url': resolved,
            'title': soup.title.get_text(' ', strip=True) if soup and soup.title else '',
            'bytes': len(body), 'link_count': len(soup.find_all('a')) if soup else 0,
            'handler_links': len(handlers), 'excerpt': plain[:1600], 'error': err,
        })

    view_urls.setdefault('125', 'http://www.qlsn.com/downloads_view.asp?id=125')
    view_rows = []
    for did, original in sorted(view_urls.items(), key=lambda x: int(x[0])):
        original = original_from_wayback(original)
        body, resolved, ctype, err, attempts = replay_target(original, ('20070625133455', '20070623224226'))
        text = decode(body) if body else ''
        soup = BeautifulSoup(text, 'html.parser') if text else None
        plain = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)) if soup else ''
        base = original_from_wayback(resolved) if resolved else original
        direct, handlers, _ = collect_links(soup, base, 'view', did)
        direct_candidates.extend(direct)
        handler_candidates.extend(handlers)
        view_rows.append({
            'download_id': did, 'original_url': original, 'resolved_archive_url': resolved,
            'title': soup.title.get_text(' ', strip=True) if soup and soup.title else '',
            'bytes': len(body), 'direct_file_refs': '|'.join(dict.fromkeys(x[3] for x in direct)),
            'handler_refs': '|'.join(dict.fromkeys(x[3] for x in handlers)),
            'excerpt': plain[:1800], 'attempts': attempts, 'error': err,
        })

    # Deduplicate all direct and handler targets, then resolve them by historical replay.
    target_map = {}
    for parent, did, text, original, source_kind in direct_candidates:
        target_map.setdefault(original, {'parent': parent, 'download_id': did, 'anchor_text': text, 'source_kind': source_kind, 'onclick': ''})
    for parent, did, text, original, onclick, source_kind in handler_candidates:
        if not original:
            continue
        target_map.setdefault(original, {'parent': parent, 'download_id': did, 'anchor_text': text, 'source_kind': source_kind, 'onclick': onclick})

    target_rows = []
    secondary_targets = []
    for original, meta in target_map.items():
        body, resolved, ctype, err, attempts = replay_target(original, ('20070623224226', '20070625133455', '20071225', '20091225', '20110918'))
        kind = payload_kind(body, ctype, resolved or original)
        hints = extract_redirect_hints(body, resolved) if kind == 'html' else []
        for h in hints:
            secondary_targets.append((original, h))
        target_rows.append({
            **meta, 'original_url': original, 'archive_recovered': 'yes' if body else 'no',
            'resolved_archive_url': resolved, 'content_type': ctype, 'bytes': len(body),
            'payload_kind': kind, 'magic': body[:16].hex() if body else '',
            'redirect_hints': '|'.join(hints), 'attempts': attempts, 'error': err,
        })

    secondary_rows = []
    seen_secondary = set()
    for parent_handler, original in secondary_targets:
        if original in seen_secondary:
            continue
        seen_secondary.add(original)
        body, resolved, ctype, err, attempts = replay_target(original, ('20070623224226', '20070625133455', '20071225', '20091225', '20110918'))
        secondary_rows.append({
            'parent_handler': parent_handler, 'original_url': original,
            'archive_recovered': 'yes' if body else 'no', 'resolved_archive_url': resolved,
            'content_type': ctype, 'bytes': len(body), 'payload_kind': payload_kind(body, ctype, resolved or original),
            'magic': body[:16].hex() if body else '', 'attempts': attempts, 'error': err,
        })

    def write(name, rows, fields):
        with (OUT / name).open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)

    write('pages.csv', page_rows, ['kind','source_url','resolved_url','title','bytes','link_count','handler_links','excerpt','error'])
    write('download_views.csv', view_rows, ['download_id','original_url','resolved_archive_url','title','bytes','direct_file_refs','handler_refs','excerpt','attempts','error'])
    write('handler_targets.csv', target_rows, ['parent','download_id','anchor_text','source_kind','onclick','original_url','archive_recovered','resolved_archive_url','content_type','bytes','payload_kind','magic','redirect_hints','attempts','error'])
    write('secondary_targets.csv', secondary_rows, ['parent_handler','original_url','archive_recovered','resolved_archive_url','content_type','bytes','payload_kind','magic','attempts','error'])

    all_payloads = target_rows + secondary_rows
    report = {
        'seed_pages': len(SEEDS),
        'download_view_ids': len(view_urls),
        'download_views_recovered': sum(bool(r['bytes']) and not r['error'] for r in view_rows),
        'direct_file_candidates': len({x[3] for x in direct_candidates}),
        'handler_candidates': len({x[3] for x in handler_candidates if x[3]}),
        'handler_targets_recovered': sum(r['archive_recovered'] == 'yes' for r in target_rows),
        'secondary_redirect_targets': len(secondary_rows),
        'binary_payloads_recovered': sum(r.get('payload_kind') in {'pdf','zip','rar','7z','jpeg','png'} for r in all_payloads),
        'pdf_payloads_recovered': sum(r.get('payload_kind') == 'pdf' for r in all_payloads),
        'notes': [
            "The 2007 '快乐下载' section primarily lists software/games; this v2 pass checks non-extension download handlers before closing that route.",
            'Internet Archive fallback landing pages are rejected as recovered payloads.',
            'No payload is promoted as a newspaper issue without independent title/issue/page evidence.'
        ]
    }
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
