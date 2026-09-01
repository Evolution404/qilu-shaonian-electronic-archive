#!/usr/bin/env python3
"""Build a page/title index for the verified 2012 winter combined issue from fee-table metadata."""
from __future__ import annotations
import csv, json, re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'/'fee_dictionary'/'table_rows.csv'
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'; OUT=OUTDIR/'page_title_index.csv'; REPORT=OUTDIR/'page_title_index_report.json'
SOURCE='https://blog.sina.com.cn/s/blog_4c4fc7d901012sbb.html'

def main():
    rows=list(csv.DictReader(SRC.open(encoding='utf-8')))
    started=False; page=''; out=[]
    for r in rows:
        cells=(r.get('cells') or '').split('|')
        cells += ['']*(5-len(cells))
        group,page_cell,author,school,title=[x.strip() for x in cells[:5]]
        if '寒合刊' in group or '寒假合刊' in group:
            started=True
        if not started:continue
        if page_cell:
            # Pages can be a single number or combined spread such as 28，29.
            page=re.sub(r'\s+','',page_cell).replace(',', '，')
        if not title or not author:continue
        out.append({'page':page,'author':author,'school_or_location':school,'title':title,'source_table_row':r.get('row',''),'source_url':SOURCE,'status':'metadata_verified'})
    fields=['page','author','school_or_location','title','source_table_row','source_url','status']
    with OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
    pages=sorted({r['page'] for r in out if r['page']},key=lambda x:int(re.match(r'\d+',x).group()) if re.match(r'\d+',x) else 999)
    by_page={p:sum(r['page']==p for r in out) for p in pages}
    report={'entries':len(out),'pages_with_fee_metadata':pages,'entries_by_page':by_page,'min_page':pages[0] if pages else '', 'max_page':pages[-1] if pages else '', 'combined_issue_identity':'2012-01-03 / 第1006-1014期 / 寒假合刊','notes':['Page numbers come from the second column of the editor-blog contributor-fee table after the 寒合刊 group marker.','Blank page cells inherit the preceding page value, matching the table layout.','This index proves content/page association, not availability of a page scan.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
