#!/usr/bin/env python3
"""Recover AJAX/data endpoint contracts from the deployed 2021 Qilu Shaonian digital-paper frontend.

This is deliberately deployment-first rather than generic-CMS guessing:
1. fetch the verified 2022-09-28 Wayback root snapshot of szb.cnssiot.cn;
2. recover every same-origin historical JS/CSS/HTML resource referenced by that snapshot;
3. extract concrete .aspx/.ashx/jquery/api endpoint strings plus nearby call context;
4. extract likely parameter names/values (edition ids, 2021-12-25, qi/date/id/page etc.);
5. probe conservative endpoint variants through Wayback and the live origin;
6. parse any response for Pagepic/Pagepdf/mobile/Img paths and edition ids.

Only URLs, endpoint contracts, response metadata and short contexts are committed. Newspaper
bytes are never committed by this discovery stage.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'szb_ajax_recovery'
OUT.mkdir(parents=True, exist_ok=True)
RESOURCES = OUT / 'resources.csv'
ENDPOINTS = OUT / 'endpoints.csv'
PROBES = OUT / 'probes.csv'
ASSETS = OUT / 'assets.csv'
REPORT = OUT / 'report.json'

ORIGIN = 'http://szb.cnssiot.cn/'
TS = '20220928010853'
SNAP = f'https://web.archive.org/web/{TS}id_/{ORIGIN}'
UA = 'qilu-shaonian-szb-ajax-recovery/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT = 35
MAX_TEXT = 8 * 1024 * 1024

EDITION_IDS = {'A1':'326','A2':'333','A3':'327','A4':'328','A5':'329','A6':'330','A7':'331','A8':'332'}
TARGET_DATE = '2021-12-25'

# Catch relative paths such as Jquery/Editionlist.aspx, /themes/.../Jquery/foo.aspx,
# jquery/GetEdition.ashx, and absolute same-origin handlers.
ENDPOINT_RE = re.compile(
    r'''(?ix)(?:https?://szb\.cnssiot\.cn/)?(?:[a-z0-9_./-]+/)?(?:jquery|content|api|ajax|handler|themes?/[a-z0-9_-]+/(?:jquery|content))?/?[a-z0-9_.-]+\.(?:aspx|ashx)(?:\?[^"'<>\s)]*)?'''
)
QUOTED_PATH_RE = re.compile(r'''(?is)["']([^"']+\.(?:aspx|ashx)(?:\?[^"']*)?)["']''')
ASSET_RE = re.compile(r'''(?i)(?:https?://szb\.cnssiot\.cn/)?/?Img/[^"'<>\s\\]+?\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s\\]*)?''')
PAGE_FIELD_RE = re.compile(r'''(?i)\b(Pagepic|Pagepdf|mobilepath|pcpath|editionid|edition_id|numberid|Qiid|qiid)\b''')
AJAX_RE = re.compile(r'''(?is)(?:\$\.ajax|\$\.getJSON|\$\.get|\$\.post|ajax)\s*\([^)]{0,1800}\)''')
URL_LITERAL_RE = re.compile(r'''(?is)\burl\s*:\s*["']([^"']+)["']''')
DATA_BLOCK_RE = re.compile(r'''(?is)\bdata\s*:\s*\{([^}]{0,1400})\}''')
KV_RE = re.compile(r'''(?is)["']?([a-zA-Z_][\w-]*)["']?\s*:\s*(?:["']([^"']*)["']|([^,}\n]+))''')
DATE_RE = re.compile(r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}')
EDITION_ROUTE_RE = re.compile(r'edition(\d+)_([A-Z]\d+)\.html', re.I)


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts=[]; self.styles=[]; self.links=[]
    def handle_starttag(self, tag, attrs):
        d={k.lower():(v or '') for k,v in attrs}; tag=tag.lower()
        if tag=='script' and d.get('src'): self.scripts.append(d['src'])
        if tag=='link' and d.get('href'): self.styles.append(d['href'])
        if tag=='a' and d.get('href'): self.links.append(d['href'])


def get(url, accept='*/*', limit=None, retries=3):
    last=None
    for a in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':accept})
            with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
                body=r.read((limit+1) if limit else -1)
                if limit and len(body)>limit: raise ValueError('response too large')
                return body,r.geturl(),{k.lower():v for k,v in r.headers.items()}
        except Exception as e:
            last=e; time.sleep(1+a*2)
    raise last


def decode(raw, headers=None):
    c=(headers or {}).get('content-type','')
    m=re.search(r'charset=([\w.-]+)',c,re.I)
    encs=([m.group(1)] if m else [])+['utf-8','gb18030']
    best=None
    for enc in encs:
        try:
            text=raw.decode(enc,'replace'); score=text.count('\ufffd')
            if best is None or score<best[0]: best=(score,text)
        except Exception: pass
    return best[1] if best else raw.decode('utf-8','replace')


def original_url(base, raw):
    v=html.unescape(raw.strip())
    if v.startswith('//'): v='http:'+v
    u=urllib.parse.urljoin(base,v)
    p=urllib.parse.urlsplit(u)
    if p.netloc=='web.archive.org':
        s=p.path+(('?'+p.query) if p.query else '')
        m=re.search(r'/web/\d+(?:[a-z_]+)?/(https?://.+)$',s,re.I)
        if m: return m.group(1)
    return u


def archive_url(original, ts=TS):
    return f'https://web.archive.org/web/{ts}id_/{original}'


def available(original):
    api='https://archive.org/wayback/available?'+urllib.parse.urlencode({'url':original,'timestamp':TS[:8]})
    try:
        raw,_,_=get(api,'application/json',2*1024*1024,2)
        d=json.loads(raw.decode('utf-8','replace')); c=(d.get('archived_snapshots') or {}).get('closest') or {}
        if c.get('available') and c.get('url'):
            u=re.sub(r'/web/(\d+)/',r'/web/\1id_/',c['url'],count=1)
            return u,c.get('timestamp','')
    except Exception: pass
    return '',''


def fetch_archived(original, accept='text/plain,text/javascript,text/css,text/html,*/*'):
    attempts=[archive_url(original)]
    close,ts=available(original)
    if close and close not in attempts: attempts.append(close)
    errors=[]
    for u in attempts:
        try:
            raw,final,h=get(u,accept,MAX_TEXT,2)
            return raw,final,h,'',ts
        except Exception as e: errors.append(f'{u}: {type(e).__name__}: {e}')
    return b'','',{},' | '.join(errors)[:1600],ts


def context(text,start,end,radius=650):
    return re.sub(r'\s+',' ',text[max(0,start-radius):min(len(text),end+radius)]).strip()[:1800]


def normalize_endpoint(source_original, raw):
    raw=html.unescape(raw).strip().strip('"\'')
    if not raw or raw.lower().startswith(('javascript:','#')): return ''
    # Remove simple string-concatenation tails that clearly aren't URL path text.
    raw=re.split(r'["\']\s*\+',raw,1)[0]
    u=urllib.parse.urljoin(source_original,raw)
    p=urllib.parse.urlsplit(u)
    if p.hostname and p.hostname.lower()!='szb.cnssiot.cn': return ''
    return urllib.parse.urlunsplit(('http', 'szb.cnssiot.cn', p.path, p.query, ''))


def extract_endpoints(text, source_original):
    rows=[]
    seen=set()
    for regex,kind in [(QUOTED_PATH_RE,'quoted_handler'),(ENDPOINT_RE,'handler_regex')]:
        for m in regex.finditer(text):
            raw=m.group(1) if regex is QUOTED_PATH_RE else m.group(0)
            ep=normalize_endpoint(source_original,raw)
            if not ep or not re.search(r'\.(?:aspx|ashx)(?:\?|$)',ep,re.I): continue
            key=(ep,kind)
            if key in seen: continue
            seen.add(key)
            rows.append({'endpoint_url':ep,'discovery':kind,'source_url':source_original,'context':context(text,m.start(),m.end()),'params':''})
    for a in AJAX_RE.finditer(text):
        block=a.group(0); um=URL_LITERAL_RE.search(block)
        if not um: continue
        ep=normalize_endpoint(source_original,um.group(1))
        if not ep: continue
        params=[]
        dm=DATA_BLOCK_RE.search(block)
        if dm:
            for km in KV_RE.finditer(dm.group(1)):
                val=(km.group(2) or km.group(3) or '').strip()
                params.append(f'{km.group(1)}={val[:100]}')
        rows.append({'endpoint_url':ep,'discovery':'ajax_call','source_url':source_original,'context':context(text,a.start(),a.end(),900),'params':'|'.join(params)})
    # Deduplicate keeping ajax context over regex context where possible.
    d={}
    for r in rows:
        k=(r['endpoint_url'],r['source_url'])
        old=d.get(k)
        if old is None or (old['discovery']!='ajax_call' and r['discovery']=='ajax_call'): d[k]=r
    return list(d.values())


def candidate_queries(endpoint_row):
    ep=endpoint_row['endpoint_url']; p=urllib.parse.urlsplit(ep)
    base=urllib.parse.urlunsplit(('http','szb.cnssiot.cn',p.path,'',''))
    candidates=[ep,base]
    ctx=(endpoint_row.get('context') or '')+' '+(endpoint_row.get('params') or '')
    names=set(re.findall(r'(?i)\b(qi|qiid|id|editionid|edition_id|pageid|numberid|date|day|time|d)\b',ctx))
    # Conservative known-value combinations: never brute force arbitrary ids.
    known=[]
    if re.search(r'(?i)edition|banmian|page',p.path+ctx):
        for page,eid in EDITION_IDS.items():
            for key in ('editionid','id'):
                known.append({key:eid})
    if 'date' in {x.lower() for x in names} or 'day' in {x.lower() for x in names} or DATE_RE.search(ctx):
        known.append({'date':TARGET_DATE}); known.append({'d':TARGET_DATE})
    # Common 53BK list contract variants constrained to the known publication date.
    if re.search(r'(?i)editionlist|edition|banmian|page',p.path):
        known += [
            {'date':TARGET_DATE}, {'day':TARGET_DATE}, {'d':TARGET_DATE},
            {'qi':TARGET_DATE}, {'time':TARGET_DATE},
        ]
    for q in known:
        query=urllib.parse.urlencode(q)
        candidates.append(base+'?'+query)
    return list(dict.fromkeys(candidates))[:30]


def probe(url):
    out={'probe_url':url,'source':'','status':'','resolved_url':'','content_type':'','bytes':'','sha256':'','signal_fields':'','asset_refs':'','excerpt':'','error':''}
    attempts=[('wayback',archive_url(url))]
    close,_=available(url)
    if close and close!=attempts[0][1]: attempts.append(('wayback_closest',close))
    attempts += [('live',url),('live_https',url.replace('http://','https://',1))]
    errors=[]
    for source,candidate in attempts:
        try:
            raw,final,h=get(candidate,'application/json,text/plain,text/html,*/*',MAX_TEXT,2)
            text=decode(raw,h)
            sig=sorted(set(m.group(1) for m in PAGE_FIELD_RE.finditer(text)))
            assets=sorted(set(original_url(ORIGIN,x) for x in ASSET_RE.findall(text)))
            # Keep useful non-404 server responses even if no field was found.
            ctype=h.get('content-type','')
            if sig or assets or ('json' in ctype.lower()) or text.lstrip().startswith(('{','[')):
                out.update({'source':source,'status':'recovered','resolved_url':final,'content_type':ctype,'bytes':str(len(raw)),'sha256':hashlib.sha256(raw).hexdigest(),'signal_fields':'|'.join(sig),'asset_refs':'|'.join(assets[:30]),'excerpt':re.sub(r'\s+',' ',text)[:1200]})
                return out
            errors.append(f'{candidate}: no page-data signal ({ctype}, {len(raw)} bytes)')
        except Exception as e: errors.append(f'{candidate}: {type(e).__name__}: {e}')
    out['status']='unverified'; out['error']=' | '.join(errors)[:3000]
    return out


def main():
    resource_rows=[]; endpoint_rows=[]; probe_rows=[]; asset_rows=[]; errors=[]
    raw,final,h=get(SNAP,'text/html,*/*',MAX_TEXT,3)
    root_text=decode(raw,h)
    parser=Parser(); parser.feed(root_text)
    # Root HTML itself is a resource and may contain inline AJAX.
    resource_rows.append({'kind':'root','original_url':ORIGIN,'archive_url':final,'status':'recovered','bytes':str(len(raw)),'content_type':h.get('content-type',''),'sha256':hashlib.sha256(raw).hexdigest(),'error':''})
    endpoint_rows.extend(extract_endpoints(root_text,ORIGIN))

    refs=[]
    for x in parser.scripts: refs.append(('script',original_url(ORIGIN,x)))
    for x in parser.styles: refs.append(('style',original_url(ORIGIN,x)))
    # Historical same-origin HTML links that look like handler/list data pages can reveal theme routes.
    for x in parser.links:
        u=original_url(ORIGIN,x)
        if re.search(r'\.(?:aspx|ashx)(?:\?|$)',u,re.I): refs.append(('linked_handler',u))
    refs=list(dict.fromkeys(refs))[:120]

    for kind,u in refs:
        p=urllib.parse.urlsplit(u)
        if p.hostname and p.hostname.lower()!='szb.cnssiot.cn': continue
        body,afinal,rh,err,_=fetch_archived(u)
        if body:
            text=decode(body,rh)
            resource_rows.append({'kind':kind,'original_url':u,'archive_url':afinal,'status':'recovered','bytes':str(len(body)),'content_type':rh.get('content-type',''),'sha256':hashlib.sha256(body).hexdigest(),'error':''})
            endpoint_rows.extend(extract_endpoints(text,u))
        else:
            resource_rows.append({'kind':kind,'original_url':u,'archive_url':'','status':'unrecovered','bytes':'','content_type':'','sha256':'','error':err})

    # Deduplicate endpoint rows globally, preferring rows with explicit ajax-call context.
    dedup={}
    for row in endpoint_rows:
        key=row['endpoint_url']
        old=dedup.get(key)
        if old is None or (old['discovery']!='ajax_call' and row['discovery']=='ajax_call'): dedup[key]=row
    endpoint_rows=sorted(dedup.values(),key=lambda r:r['endpoint_url'])

    seen_probe=set()
    for row in endpoint_rows:
        for candidate in candidate_queries(row):
            if candidate in seen_probe: continue
            seen_probe.add(candidate)
            pr=probe(candidate); pr.update({'endpoint_url':row['endpoint_url'],'discovery':row['discovery'],'source_url':row['source_url']})
            probe_rows.append(pr)
            for asset in (pr.get('asset_refs') or '').split('|'):
                if asset:
                    asset_rows.append({'asset_url':asset,'endpoint_url':row['endpoint_url'],'probe_url':candidate,'probe_source':pr.get('source',''),'status':'discovered'})
            if pr['status']=='recovered': print('DATA HIT',candidate,pr['signal_fields'],pr['asset_refs'][:200],flush=True)

    # Also capture any page assets directly embedded in recovered deployment resources.
    for text_source,text in [('root',root_text)]:
        for asset in sorted(set(original_url(ORIGIN,x) for x in ASSET_RE.findall(text))):
            asset_rows.append({'asset_url':asset,'endpoint_url':'','probe_url':'','probe_source':text_source,'status':'discovered'})

    fields=['kind','original_url','archive_url','status','bytes','content_type','sha256','error']
    with RESOURCES.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(resource_rows)
    fields=['endpoint_url','discovery','source_url','params','context']
    with ENDPOINTS.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(endpoint_rows)
    fields=['endpoint_url','discovery','source_url','probe_url','source','status','resolved_url','content_type','bytes','sha256','signal_fields','asset_refs','excerpt','error']
    with PROBES.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(probe_rows)
    fields=['asset_url','endpoint_url','probe_url','probe_source','status']
    uniq={r['asset_url']:r for r in asset_rows}
    with ASSETS.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(sorted(uniq.values(),key=lambda r:r['asset_url']))

    report={
        'snapshot':SNAP,
        'resources_discovered':len(refs)+1,
        'resources_recovered':sum(r['status']=='recovered' for r in resource_rows),
        'endpoints_discovered':len(endpoint_rows),
        'endpoint_probes':len(probe_rows),
        'data_responses_recovered':sum(r['status']=='recovered' for r in probe_rows),
        'responses_with_page_fields':sum(bool(r['signal_fields']) for r in probe_rows),
        'unique_asset_refs':len(uniq),
        'known_edition_ids':EDITION_IDS,
        'notes':[
            'Endpoints originate from the deployed historical frontend, not generic 53BK assumptions.',
            'Probe values are restricted to the verified publication date and verified edition ids; no broad id enumeration is performed.',
            'No newspaper/image bytes are committed.',
        ],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
