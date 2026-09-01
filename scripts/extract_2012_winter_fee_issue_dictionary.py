#!/usr/bin/env python3
"""Extract structured issue/title hints from the editor-blog 1002-1014 contributor-fee post.

This is a metadata lead generator, not a text mirror. It fetches the verified editor blog post
《齐鲁少年》1002-1014期外稿费（2011年12月至2012年2月）, isolates the article body,
and records short issue/page contexts plus compact HTML-table rows. Full post prose is not
committed.
"""
from __future__ import annotations

import csv, json, re, urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'/'fee_dictionary'; OUTDIR.mkdir(parents=True,exist_ok=True)
OUT=OUTDIR/'hints.csv'; TABLE_OUT=OUTDIR/'table_rows.csv'; REPORT=OUTDIR/'report.json'
URL='https://blog.sina.com.cn/s/blog_4c4fc7d901012sbb.html'
UA='Mozilla/5.0 qilu-shaonian-winter-fee-dictionary/2.0'
ISSUE_RE=re.compile(r'(?<!\d)(100[2-9]|101[0-4])(?!\d)')
PAGE_RE=re.compile(r'(?:第\s*)?([1-8一二三四五六七八A-DＡ-Ｄ])\s*[版页]?')
MONEY_RE=re.compile(r'\d+(?:\.\d+)?\s*元')


def fetch():
    req=urllib.request.Request(URL,headers={'User-Agent':UA,'Accept':'text/html,*/*;q=0.7'})
    with urllib.request.urlopen(req,timeout=30) as r:b=r.read(7*1024*1024);ct=r.headers.get('content-type','')
    best=None
    for enc in ['utf-8','gb18030']:
        t=b.decode(enc,'replace'); score=t.count('\ufffd')
        if best is None or score<best[0]:best=(score,t)
    return best[1],len(b),ct

def article_node(text):
    soup=BeautifulSoup(text,'html.parser')
    node=(soup.select_one('#sina_keyword_ad_area2') or soup.select_one('.articalContent') or soup.select_one('.article-content') or soup.select_one('article'))
    if not node:
        divs=soup.find_all('div'); node=max(divs,key=lambda x:len(x.get_text(' ',strip=True)),default=soup)
    for x in node.find_all(['script','style','noscript']):x.decompose()
    return node

def clean(s):return re.sub(r'\s+',' ',s).strip()

def compact_table_rows(node):
    out=[]
    for ti,table in enumerate(node.find_all('table'),1):
        active={}  # col -> [remaining_rows, text], for rowspan inheritance
        for ri,tr in enumerate(table.find_all('tr'),1):
            explicit=[]
            cells=tr.find_all(['th','td'],recursive=False)
            if not cells:
                cells=tr.find_all(['th','td'])
            col=0; rowvals={}
            def fill_active():
                nonlocal col
                while col in active:
                    rowvals[col]=active[col][1]
                    col+=1
            fill_active()
            for cell in cells:
                fill_active()
                text=clean(cell.get_text(' ',strip=True))[:180]
                try:colspan=max(1,int(cell.get('colspan',1)))
                except:colspan=1
                try:rowspan=max(1,int(cell.get('rowspan',1)))
                except:rowspan=1
                explicit.append(text)
                for j in range(colspan):
                    rowvals[col+j]=text
                    if rowspan>1:active[col+j]=[rowspan,text]
                col+=colspan
            # Apply remaining inherited cells after explicit cells only if needed.
            fill_active()
            if not rowvals:continue
            maxcol=max(rowvals)
            vals=[rowvals.get(i,'') for i in range(maxcol+1)]
            joined=' ｜ '.join(v for v in vals if v)[:520]
            issues=ISSUE_RE.findall(joined)
            # Avoid treating arbitrary single digits as page unless the table/header context says 版面.
            pages=[]
            if '版面' in joined or any('版' in v or '页' in v for v in vals):pages=PAGE_RE.findall(joined)
            money=MONEY_RE.findall(joined)
            out.append({'table':str(ti),'row':str(ri),'cells':'|'.join(vals[:10]),'explicit_cells':'|'.join(explicit[:10]),'issue_hints':'|'.join(dict.fromkeys(issues)),'page_hints':'|'.join(dict.fromkeys(pages)),'money_hints':'|'.join(money),'compact_row':joined})
            # decrement rowspan counts after completing this physical row
            expired=[]
            for c,(remaining,text) in active.items():
                remaining-=1
                if remaining<=1:expired.append(c)
                else:active[c][0]=remaining
            for c in expired:active.pop(c,None)
    return out

def main():
    html,bytes_,ctype=fetch(); node=article_node(html); text=node.get_text('\n',strip=True)
    rawlines=[clean(x) for x in text.splitlines() if clean(x)]
    rows=[]; seen=set()
    for i,line in enumerate(rawlines):
        issues=ISSUE_RE.findall(line); pages=[]; money=MONEY_RE.findall(line)
        if '版' in line or '页' in line:pages=PAGE_RE.findall(line)
        if not (issues or pages or money or '稿费' in line or '期' in line):continue
        window=' ｜ '.join(rawlines[max(0,i-1):min(len(rawlines),i+2)])[:420]
        key=(tuple(issues),tuple(pages),window)
        if key in seen:continue
        seen.add(key)
        rows.append({'line_no':str(i+1),'issue_hints':'|'.join(dict.fromkeys(issues)),'page_hints':'|'.join(dict.fromkeys(pages)),'money_hints':'|'.join(money),'context':window})
    fields=['line_no','issue_hints','page_hints','money_hints','context']
    with OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

    table_rows=compact_table_rows(node)
    tf=['table','row','cells','explicit_cells','issue_hints','page_hints','money_hints','compact_row']
    with TABLE_OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=tf);w.writeheader();w.writerows(table_rows)

    issue_counts={str(n):0 for n in range(1002,1015)}
    for r in table_rows:
        for x in r['issue_hints'].split('|'):
            if x:issue_counts[x]=issue_counts.get(x,0)+1
    report={'source_url':URL,'source_bytes':bytes_,'content_type':ctype,'body_chars':len(text),'body_lines':len(rawlines),'structured_hint_rows':len(rows),'html_tables':len(node.find_all('table')),'table_rows':len(table_rows),'table_issue_counts':issue_counts,'table_page_hint_rows':sum(bool(r['page_hints']) for r in table_rows),'table_money_hint_rows':sum(bool(r['money_hints']) for r in table_rows),'notes':['Only compact table metadata/short contexts are stored; the full blog post is not mirrored.','Table rows preserve HTML column relationships and rowspan-derived values where possible.','Issue/title/author clues are for reverse-search and require independent page-image verification.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    for r in table_rows[:60]:print(r,flush=True)
if __name__=='__main__':main()
