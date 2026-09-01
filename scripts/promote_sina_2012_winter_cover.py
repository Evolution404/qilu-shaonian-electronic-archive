#!/usr/bin/env python3
"""Promote the visually verified 2012 winter combined-issue cover into electronic_records.csv."""
from __future__ import annotations
import csv, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RECORDS=ROOT/'data'/'electronic_records.csv'
VERIFY=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'/'visual_verification.json'

def main():
    v=json.loads(VERIFY.read_text(encoding='utf-8'))
    with RECORDS.open(newline='',encoding='utf-8') as f:
        rows=list(csv.DictReader(f)); fields=list(rows[0].keys()) if rows else ['publication_date','issue_number','page','record_type','title','original_url','archive_or_evidence_url','status','notes']
    key=(v['publication_date'],v['issue_range'],'cover_scan')
    new={
        'publication_date':v['publication_date'],
        'issue_number':v['issue_range'],
        'page':'封面',
        'record_type':'cover_scan',
        'title':'2012年寒假合刊',
        'original_url':v['image_url'],
        'archive_or_evidence_url':v['source_post'],
        'status':'verified',
        'notes':f"编辑部新浪博客原图经视觉复核为《齐鲁少年》寒假合刊完整封面；封面明确写有‘第{v['issue_range']}期’、‘{v['visible_text_verified']['publication_date_line']}’、官网、QQ及‘{v['visible_text_verified']['sponsor']}’；JPEG {v['width']}×{v['height']}，{v['file_bytes']} bytes，SHA-256 {v['sha256']}。仅封面，不代表1006-1014合刊内页已经完整恢复。"
    }
    replaced=False
    for i,r in enumerate(rows):
        if r.get('publication_date')==key[0] and r.get('issue_number')==key[1] and r.get('record_type') in {'cover_scan','verified_cover_scan'}:
            rows[i]=new; replaced=True; break
    if not replaced: rows.append(new)
    with RECORDS.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    print('updated' if replaced else 'appended',new)
if __name__=='__main__':main()
