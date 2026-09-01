#!/usr/bin/env python3
"""Low-load Common Crawl/WARC recovery for 2012 winter combined-issue Sina posts.

The first exact-query pass issued 30 concurrent historical-index requests and all were lost to
503/timeouts. Common Crawl asks clients not to overload the CDXJ service and exposes a test
index server as an alternate endpoint. This pass therefore:
- makes one prefix query per historical collection instead of ten exact queries;
- runs serially with backoff;
- falls back from index.commoncrawl.org to test-index.commoncrawl.org;
- filters the returned prefix rows locally to the five verified editor-blog posts;
- range-fetches WARC records and recovers historical Sina media keys when present.

Only provenance, URLs, hashes and dimensions are committed. No newspaper/image/WARC bytes are
committed by this discovery stage.
"""
from __future__ import annotations

import csv, gzip, hashlib, io, json, re, time, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / 'data' / 'repost_fullpage' / 'sina_2012_winter_combined' / 'commoncrawl'
OUTDIR.mkdir(parents=True, exist_ok=True)
INDEX_ROWS = OUTDIR / 'index_rows.csv'
WARC_ROWS = OUTDIR / 'warc_rows.csv'
MEDIA = OUTDIR / 'media.csv'
REPORT = OUTDIR / 'report.json'
UA = 'qilu-shaonian-sina-winter-commoncrawl/2.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT = 50
MAX_RANGE = 20 * 1024 * 1024
MAX_BODY = 15 * 1024 * 1024
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
TARGET_SET = set(IDS)
PREFIX = 'blog.sina.com.cn/s/blog_4c4fc7d901011'
MEDIA_RE = re.compile(r'''(?i)(?:https?:)?//(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.(?:sinaimg\.cn|sina\.com\.cn)/[^"'<>\s\\]+''')
PATH_CLASS = re.compile(r'/(middle|bmiddle|large|orignal|thumbnail|mw\d+|orj\d+|square)/', re.I)
HOST_RE = re.compile(r'^(ss?|SS?)(\d+)\.sinaimg\.cn$', re.I)
POST_ID_RE = re.compile(r'(blog_4c4fc7d9[0-9a-z]+)\.html', re.I)


def get(url, headers=None, limit=None, retries=4):
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
            time.sleep(min(12, 2 + attempt * 3))
    raise last


def indexes():
    b, _, _ = get('https://index.commoncrawl.org/collinfo.json', limit=5 * 1024 * 1024)
    items = json.loads(b.decode('utf-8', 'replace'))
    out = []
    for item in items:
        m = re.search(r'CC-MAIN-(20\d{2})', item.get('id', ''))
        if m and 2011 <= int(m.group(1)) <= 2013:
            out.append({'id': item['id']})
    out.sort(key=lambda x: x['id'])
    return out


def query_index(index):
    params = {
        'url': PREFIX,
        'output': 'json',
        'filter': 'status:200',
        'matchType': 'prefix',
    }
    query = urllib.parse.urlencode(params)
    hosts = ['index.commoncrawl.org', 'test-index.commoncrawl.org']
    errors = []
    for host in hosts:
        url = f'https://{host}/{index["id"]}-index?{query}'
        try:
            b, _, _ = get(url, headers={'Accept': 'application/json,text/plain,*/*'}, limit=12 * 1024 * 1024, retries=3)
            rows = []
            for line in b.decode('utf-8', 'replace').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                original = d.get('url', '')
                m = POST_ID_RE.search(original)
                if not m:
                    continue
                pid = m.group(1).lower()
                if pid not in TARGET_SET:
                    continue
                rows.append({
                    'post_id': pid,
                    'post_title': TITLE[pid],
                    'query_url': url,
                    'index_host': host,
                    'index': index['id'],
                    'timestamp': d.get('timestamp', ''),
                    'url': original,
                    'status': d.get('status', ''),
                    'mime': d.get('mime', d.get('mime-detected', '')),
                    'digest': d.get('digest', ''),
                    'filename': d.get('filename', ''),
                    'offset': d.get('offset', ''),
                    'length': d.get('length', ''),
                })
            return rows, errors, host
        except Exception as exc:
            errors.append({'index': index['id'], 'host': host, 'url': url, 'error': f'{type(exc).__name__}: {exc}'})
    return [], errors, ''


def parse_headers(block):
    lines = block.decode('latin1', 'replace').split('\r\n')
    headers = {}
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            headers[k.strip().lower()] = v.strip()
    return (lines[0] if lines else ''), headers


def warc_body(row):
    off = int(row['offset'])
    ln = int(row['length'])
    if ln <= 0 or ln > MAX_RANGE:
        raise ValueError(f'invalid WARC range {ln}')
    url = 'https://data.commoncrawl.org/' + row['filename']
    b, _, _ = get(url, headers={'Range': f'bytes={off}-{off + ln - 1}'}, limit=MAX_RANGE, retries=3)
    try:
        raw = gzip.decompress(b)
    except Exception:
        raw = gzip.GzipFile(fileobj=io.BytesIO(b)).read(MAX_BODY + 3 * 1024 * 1024)
    p = raw.find(b'\r\n\r\n')
    payload = raw[p + 4:] if p >= 0 else raw
    q = payload.find(b'\r\n\r\n')
    if q >= 0:
        status, headers = parse_headers(payload[:q])
        body = payload[q + 4:]
    else:
        status, headers, body = '', {}, payload
    if headers.get('content-encoding', '').lower() == 'gzip':
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    return status, headers, body[:MAX_BODY]


def extract(text):
    text = text.replace('\\/', '/').replace('\\u0026', '&')
    return sorted(set(x.strip('"\'()[]{};,') for x in MEDIA_RE.findall(text)))


def variants(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    m = PATH_CLASS.search(p.path)
    if 'sinaimg.cn' not in host or not m:
        return [('as_found', url)]
    tail = p.path[m.end():]
    if len(tail) < 8:
        return []
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


def inspect(src, vname, url):
    row = {
        **src,
        'variant': vname,
        'candidate_url': url,
        'resolved_url': '',
        'http_status': '',
        'bytes': '',
        'sha256': '',
        'width': '',
        'height': '',
        'image_format': '',
        'is_placeholder': '',
        'likely_document': '',
        'error': '',
    }
    try:
        b, final, _ = get(url, headers={'Referer': src['post_url'], 'Accept': 'image/*,*/*;q=0.6'}, limit=25 * 1024 * 1024, retries=2)
        with Image.open(io.BytesIO(b)) as im:
            w, h = im.size
            fmt = im.format or ''
        sha = hashlib.sha256(b).hexdigest()
        placeholder = sha == PLACEHOLDER or 'default_s_' in final or (w == 360 and h == 360 and fmt.upper() == 'GIF')
        ratio = h / w if w else 0
        row.update({
            'resolved_url': final,
            'http_status': '200',
            'bytes': str(len(b)),
            'sha256': sha,
            'width': str(w),
            'height': str(h),
            'image_format': fmt,
            'is_placeholder': 'yes' if placeholder else 'no',
            'likely_document': 'yes' if (not placeholder and w >= 500 and h >= 650 and ratio >= 1.12) else 'no',
        })
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'[:800]
    return row


def main():
    idx = indexes()
    print('indexes', [x['id'] for x in idx], flush=True)
    rows = []
    errors = []
    hosts_used = {}
    for item in idx:
        rr, ee, host = query_index(item)
        rows.extend(rr)
        errors.extend(ee)
        hosts_used[item['id']] = host
        print('INDEX', item['id'], 'host', host or 'none', 'target rows', len(rr), 'errors', len(ee), flush=True)
        time.sleep(2)

    uniq = {}
    for row in rows:
        uniq[(row['filename'], row['offset'], row['digest'])] = row
    rows = list(uniq.values())

    warc_rows = []
    sources = []
    for row in rows:
        out = {**row, 'warc_recovered': 'no', 'body_bytes': '', 'media_refs': '0', 'error': ''}
        try:
            _, _, body = warc_body(row)
            text = body.decode('utf-8', 'replace') if body else ''
            media = extract(text)
            out.update({'warc_recovered': 'yes', 'body_bytes': str(len(body)), 'media_refs': str(len(media))})
            for media_url in media:
                sources.append({
                    'post_id': row['post_id'],
                    'post_title': row['post_title'],
                    'post_url': row['url'],
                    'index': row['index'],
                    'timestamp': row['timestamp'],
                    'source_media_url': media_url,
                })
        except Exception as exc:
            out['error'] = f'{type(exc).__name__}: {exc}'[:900]
        warc_rows.append(out)

    srcuniq = {}
    for source in sources:
        srcuniq[(source['post_id'], source['source_media_url'])] = source
    media_rows = []
    for source in srcuniq.values():
        for variant_name, candidate in variants(source['source_media_url']):
            media_rows.append(inspect(source, variant_name, candidate))

    idxfields = ['post_id', 'post_title', 'query_url', 'index_host', 'index', 'timestamp', 'url', 'status', 'mime', 'digest', 'filename', 'offset', 'length']
    with INDEX_ROWS.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=idxfields)
        writer.writeheader()
        writer.writerows(rows)
    wfields = idxfields + ['warc_recovered', 'body_bytes', 'media_refs', 'error']
    with WARC_ROWS.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=wfields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(warc_rows)
    mfields = ['post_id', 'post_title', 'post_url', 'index', 'timestamp', 'source_media_url', 'variant', 'candidate_url', 'resolved_url', 'http_status', 'bytes', 'sha256', 'width', 'height', 'image_format', 'is_placeholder', 'likely_document', 'error']
    with MEDIA.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=mfields)
        writer.writeheader()
        writer.writerows(media_rows)

    dec = set(IDS[:4])
    report = {
        'strategy': 'serial_prefix_with_test_index_fallback',
        'prefix': PREFIX,
        'indexes_selected': len(idx),
        'index_queries_primary_expected': len(idx),
        'index_rows': len(rows),
        'index_errors': len(errors),
        'index_hosts_used': hosts_used,
        'warc_records_recovered': sum(r['warc_recovered'] == 'yes' for r in warc_rows),
        'historical_media_urls': len(srcuniq),
        'dec31_historical_media_urls': len({r['source_media_url'] for r in sources if r['post_id'] in dec}),
        'reachable_non_placeholder': sum(r['http_status'] == '200' and r['is_placeholder'] == 'no' for r in media_rows),
        'dec31_likely_document': sum(r['post_id'] in dec and r['likely_document'] == 'yes' for r in media_rows),
        'errors': errors[:50],
        'notes': [
            'A zero result is considered meaningful only for an index query that completed successfully; service errors remain service errors.',
            'No newspaper/image bytes or WARC bodies are committed.',
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
