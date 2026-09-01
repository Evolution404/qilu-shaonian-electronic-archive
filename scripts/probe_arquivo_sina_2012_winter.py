#!/usr/bin/env python3
"""Probe Arquivo.pt for historical 2012 winter combined-issue Sina pages and media.

Arquivo.pt exposes a documented versionHistory API and full-text archive search. This script
uses both as an independent archive source after Wayback/Common Crawl index instability:
- query exact history for the five verified editor-blog URLs (HTTP and HTTPS);
- fetch archived original HTML when available and recover historical Sina image/media keys;
- full-text search the four Dec-31 post titles and the Jan-10 review title for mirrors/reposts;
- probe recovered live Sina CDN variants and record hashes/dimensions only.

No third-party newspaper/image bytes or archived HTML bodies are committed.
"""
from __future__ import annotations

import csv, hashlib, io, json, re, time, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'repost_fullpage' / 'sina_2012_winter_combined' / 'arquivo_pt'
OUT.mkdir(parents=True, exist_ok=True)
HISTORY_CSV = OUT / 'history.csv'
TEXT_CSV = OUT / 'text_hits.csv'
MEDIA_CSV = OUT / 'media.csv'
REPORT = OUT / 'report.json'
UA = 'qilu-shaonian-arquivo-pt/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT = 40
PLACEHOLDER = 'd2b5a30568572332968808f1fd3d0218cd8a8ca41889627168fc6d9ca487e766'
IDS = [
    'blog_4c4fc7d9010116nf',
    'blog_4c4fc7d9010116ni',
    'blog_4c4fc7d9010116nl',
    'blog_4c4fc7d9010116nn',
    'blog_4c4fc7d901011cp9',
]
TITLE = {
    IDS[0]: '2012年寒假合刊编辑部的故事之三',
    IDS[1]: '2012年寒假合刊编辑部的故事之四和六',
    IDS[2]: '2012年寒假合刊编辑部的故事之五',
    IDS[3]: '2012年寒假合刊编辑部的故事之七',
    IDS[4]: '今天我评报',
}
MEDIA_RE = re.compile(r'''(?i)(?:https?:)?//(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.(?:sinaimg\.cn|sina\.com\.cn)/[^"'<>\s\\]+''')
PATH_CLASS = re.compile(r'/(middle|bmiddle|large|orignal|thumbnail|mw\d+|orj\d+|square)/', re.I)
HOST_RE = re.compile(r'^(ss?|SS?)(\d+)\.sinaimg\.cn$', re.I)


def get(url, headers=None, limit=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            h = {'User-Agent': UA, 'Accept': '*/*'}
            h.update(headers or {})
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                b = r.read((limit + 1) if limit else -1)
                if limit and len(b) > limit:
                    raise ValueError('response too large')
                return b, r.geturl(), {k.lower(): v for k, v in r.headers.items()}
        except Exception as exc:
            last = exc
            time.sleep(1 + attempt * 2)
    raise last


def decode_json(url):
    b, _, _ = get(url, headers={'Accept': 'application/json,*/*;q=0.5'}, limit=12 * 1024 * 1024)
    return json.loads(b.decode('utf-8', 'replace'))


def history_queries():
    rows = []
    errors = []
    for pid in IDS:
        for scheme in ('http', 'https'):
            original = f'{scheme}://blog.sina.com.cn/s/{pid}.html'
            params = {
                'versionHistory': original,
                'from': '20111201000000',
                'to': '20131231235959',
                'offset': '0',
                'maxItems': '50',
            }
            api = 'https://arquivo.pt/textsearch?' + urllib.parse.urlencode(params)
            try:
                data = decode_json(api)
                items = data.get('response_items', []) or []
                for item in items:
                    rows.append({
                        'post_id': pid,
                        'post_title': TITLE[pid],
                        'query_original': original,
                        'tstamp': str(item.get('tstamp', '')),
                        'original_url': item.get('originalURL', ''),
                        'status': str(item.get('statusCode', item.get('status', ''))),
                        'mime': item.get('mimeType', ''),
                        'content_length': str(item.get('contentLength', '')),
                        'digest': item.get('digest', ''),
                        'link_to_archive': item.get('linkToArchive', ''),
                        'link_to_noframe': item.get('linkToNoFrame', ''),
                        'link_to_original_file': item.get('linkToOriginalFile', ''),
                        'collection': item.get('collection', ''),
                    })
                print('HISTORY', pid, scheme, len(items), flush=True)
            except Exception as exc:
                errors.append({'stage': 'history', 'post_id': pid, 'url': original, 'error': f'{type(exc).__name__}: {exc}'})
            time.sleep(0.6)
    uniq = {}
    for row in rows:
        uniq[(row['post_id'], row['tstamp'], row['original_url'], row['digest'])] = row
    return list(uniq.values()), errors


def text_queries():
    rows = []
    errors = []
    for pid in IDS:
        query = f'"{TITLE[pid]}"'
        params = {'q': query, 'from': '2011', 'to': '2014', 'offset': '0', 'maxItems': '50'}
        api = 'https://arquivo.pt/textsearch?' + urllib.parse.urlencode(params)
        try:
            data = decode_json(api)
            items = data.get('response_items', []) or []
            for item in items:
                rows.append({
                    'query_post_id': pid,
                    'query_title': TITLE[pid],
                    'tstamp': str(item.get('tstamp', '')),
                    'title': item.get('title', ''),
                    'original_url': item.get('originalURL', ''),
                    'status': str(item.get('statusCode', item.get('status', ''))),
                    'mime': item.get('mimeType', ''),
                    'digest': item.get('digest', ''),
                    'link_to_archive': item.get('linkToArchive', ''),
                    'link_to_original_file': item.get('linkToOriginalFile', ''),
                    'collection': item.get('collection', ''),
                })
            print('TEXT', pid, len(items), flush=True)
        except Exception as exc:
            errors.append({'stage': 'textsearch', 'post_id': pid, 'query': query, 'error': f'{type(exc).__name__}: {exc}'})
        time.sleep(0.6)
    uniq = {}
    for row in rows:
        uniq[(row['query_post_id'], row['tstamp'], row['original_url'], row['digest'])] = row
    return list(uniq.values()), errors


def extract_media(text):
    text = text.replace('\\/', '/').replace('\\u0026', '&')
    return sorted(set(x.strip('"\'()[]{};,') for x in MEDIA_RE.findall(text)))


def variants(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    m = PATH_CLASS.search(p.path)
    if 'sinaimg.cn' not in host or not m:
        return [('as_found', url)]
    hosts = [p.netloc]
    hm = HOST_RE.match(host)
    if hm:
        n = hm.group(2)
        hosts.append(('s' if host.startswith('ss') else 'ss') + n + '.sinaimg.cn')
    out = []
    for netloc in dict.fromkeys(hosts):
        for scheme in ('https', 'http'):
            for cls in dict.fromkeys([m.group(1).lower(), 'middle', 'bmiddle', 'large', 'orignal']):
                path = p.path[:m.start()] + f'/{cls}/' + p.path[m.end():]
                out.append((f'{netloc}:{cls}:{scheme}', urllib.parse.urlunsplit((scheme, netloc, path, p.query, p.fragment))))
    return list(dict.fromkeys(out))


def inspect(source, variant_name, url):
    row = {**source, 'variant': variant_name, 'candidate_url': url, 'resolved_url': '', 'http_status': '', 'bytes': '', 'sha256': '', 'width': '', 'height': '', 'image_format': '', 'is_placeholder': '', 'likely_document': '', 'error': ''}
    try:
        b, final, _ = get(url, headers={'Referer': source['source_post_url'], 'Accept': 'image/*,*/*;q=0.6'}, limit=25 * 1024 * 1024, retries=2)
        with Image.open(io.BytesIO(b)) as im:
            w, h = im.size
            fmt = im.format or ''
        sha = hashlib.sha256(b).hexdigest()
        placeholder = sha == PLACEHOLDER or 'default_s_' in final or (w == 360 and h == 360 and fmt.upper() == 'GIF')
        ratio = h / w if w else 0
        row.update({'resolved_url': final, 'http_status': '200', 'bytes': str(len(b)), 'sha256': sha, 'width': str(w), 'height': str(h), 'image_format': fmt, 'is_placeholder': 'yes' if placeholder else 'no', 'likely_document': 'yes' if (not placeholder and w >= 500 and h >= 650 and ratio >= 1.12) else 'no'})
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'[:800]
    return row


def recover_history_media(history_rows):
    sources = []
    errors = []
    for row in history_rows:
        if row['status'] not in ('', '200') or ('html' not in row['mime'].lower() and row['mime']):
            continue
        replay = row['link_to_original_file'] or row['link_to_noframe'] or row['link_to_archive']
        if not replay:
            continue
        try:
            b, _, _ = get(replay, headers={'Accept': 'text/html,*/*;q=0.8'}, limit=8 * 1024 * 1024, retries=2)
            text = b.decode('utf-8', 'replace')
            refs = extract_media(text)
            for media_url in refs:
                sources.append({'post_id': row['post_id'], 'post_title': row['post_title'], 'archive_tstamp': row['tstamp'], 'archive_page_url': replay, 'source_post_url': row['original_url'] or row['query_original'], 'source_media_url': media_url})
            print('ARCHIVED HTML', row['post_id'], row['tstamp'], 'media', len(refs), flush=True)
        except Exception as exc:
            errors.append({'stage': 'archive_html', 'post_id': row['post_id'], 'tstamp': row['tstamp'], 'url': replay, 'error': f'{type(exc).__name__}: {exc}'})
    uniq = {}
    for source in sources:
        uniq[(source['post_id'], source['source_media_url'])] = source
    inspected = []
    for source in uniq.values():
        for variant_name, candidate in variants(source['source_media_url']):
            inspected.append(inspect(source, variant_name, candidate))
    return list(uniq.values()), inspected, errors


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def main():
    history, errors1 = history_queries()
    text_hits, errors2 = text_queries()
    sources, media, errors3 = recover_history_media(history)
    write_csv(HISTORY_CSV, history, ['post_id', 'post_title', 'query_original', 'tstamp', 'original_url', 'status', 'mime', 'content_length', 'digest', 'link_to_archive', 'link_to_noframe', 'link_to_original_file', 'collection'])
    write_csv(TEXT_CSV, text_hits, ['query_post_id', 'query_title', 'tstamp', 'title', 'original_url', 'status', 'mime', 'digest', 'link_to_archive', 'link_to_original_file', 'collection'])
    write_csv(MEDIA_CSV, media, ['post_id', 'post_title', 'archive_tstamp', 'archive_page_url', 'source_post_url', 'source_media_url', 'variant', 'candidate_url', 'resolved_url', 'http_status', 'bytes', 'sha256', 'width', 'height', 'image_format', 'is_placeholder', 'likely_document', 'error'])
    dec = set(IDS[:4])
    report = {
        'history_records': len(history),
        'history_posts_with_records': len({r['post_id'] for r in history}),
        'text_search_records': len(text_hits),
        'historical_media_urls': len(sources),
        'dec31_historical_media_urls': len({r['source_media_url'] for r in sources if r['post_id'] in dec}),
        'reachable_non_placeholder': sum(r['http_status'] == '200' and r['is_placeholder'] == 'no' for r in media),
        'dec31_likely_document': sum(r['post_id'] in dec and r['likely_document'] == 'yes' for r in media),
        'errors': (errors1 + errors2 + errors3)[:60],
        'notes': [
            'Arquivo.pt is an independent web-archive source; results still require content verification.',
            'No third-party media or archived HTML bodies are committed.',
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
