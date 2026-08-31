#!/usr/bin/env python3
"""Promote verified/path-verified 2006-2009 legacy evidence into the canonical records.

This intentionally does not promote editor-blog/forum metadata as electronic pages.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RECORDS=ROOT/"data"/"electronic_records.csv"
COVERAGE=ROOT/"data"/"year_coverage.csv"
CONTEXT=ROOT/"data"/"legacy_2004_2010_home_issue_context"/"issue_links.csv"

RF=["publication_date","issue_number","page","record_type","title","original_url","archive_or_evidence_url","status","notes"]


def read_csv(path):
    with path.open(newline="",encoding="utf-8") as f:return list(csv.DictReader(f))

def write_csv(path,rows,fields):
    with path.open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def main():
    rows=read_csv(RECORDS)
    keys={(r["record_type"],r["original_url"]) for r in rows}
    verified_740={
        "publication_date":"","issue_number":"740","page":"","record_type":"html_article",
        "title":"科学让他们登上今天的舞台","original_url":"http://www.qlsn.com/news_view.asp?id=88",
        "archive_or_evidence_url":"https://web.archive.org/web/20070623224004id_/http://www.qlsn.com/news_view.asp?id=88",
        "status":"verified",
        "notes":"《齐鲁少年》旧官网原生HTML；页面网站日期2006-12-11；正文署名“本报记者 李珊 雨虹 第740期”；网站日期不推定为报纸出版日。",
    }
    if (verified_740["record_type"],verified_740["original_url"]) not in keys:
        rows.append(verified_740);keys.add((verified_740["record_type"],verified_740["original_url"]))

    wanted={"780","781","784","785","786","787","858","869","870","872","873","895","897","898","899","900"}
    for r in read_csv(CONTEXT):
        issue=r["issue_number"]
        if issue not in wanted:continue
        key=("html_article_path",r["child_url"])
        if key in keys:continue
        display=(r.get("nearby_dates") or "").split("|")[0]
        note=f"《齐鲁少年》旧官网首页Wayback快照直接列出此带期号子页路径；子页正文当前未恢复。"
        if display:note+=f" 首页条目附近显示日期{display}，仅作为网站显示/上传时间线索，不推定为报纸出版日。"
        rows.append({"publication_date":"","issue_number":issue,"page":"","record_type":"html_article_path","title":r["anchor_text"],"original_url":r["child_url"],"archive_or_evidence_url":r["seed_archive_url"],"status":"path_verified","notes":note})
        keys.add(key)

    # Keep canonical chronology without inventing dates: dated rows first, then numeric issue, then page/type/title.
    def sk(r):
        issue=int(r["issue_number"]) if (r.get("issue_number") or "").isdigit() else 999999
        return (r.get("publication_date") or "9999-99-99",issue,r.get("page") or "",r.get("record_type") or "",r.get("title") or "")
    rows.sort(key=sk)
    write_csv(RECORDS,rows,RF)

    cov=read_csv(COVERAGE);by={r["year"]:r for r in cov}
    by["2006"].update({"status":"found","electronic_evidence":"qlsn.com HTML","notes":"旧官网已恢复第740期原生HTML《科学让他们登上今天的舞台》；正文署名“本报记者 李珊 雨虹 第740期”，页面网站日期2006-12-11不作为报纸出版日。"})
    by["2007"].update({"status":"found","electronic_evidence":"qlsn.com HTML + official paths","notes":"第745期已恢复3篇原生HTML；官方首页快照另直接保存第780、781、784、785、786、787期的带期号文章路径及页面显示日期，子页正文大多未被Wayback保存。"})
    by["2008"].update({"status":"metadata_only","electronic_evidence":"award title anchors","notes":"中国少年儿童报刊工作者协会资料明确列出《齐鲁少年报》2008年三篇作品及作者：纪晶《尖子生落马三好评选——也谈德智体全面发展》《穿越罗布泊的女孩》、王育红《铁窗外的期盼——写给大墙内的妈妈》；尚未恢复原始电子页/扫描。"})
    by["2009"].update({"status":"path_coverage_proven","electronic_evidence":"qlsn.com official-home paths","notes":"2009官方首页Wayback快照直接保存第858、869、870、872、873、895、897、898、899、900期的带期号原始文章/公告路径；其中页面显示日期已单独记录，但不推定为报纸出版日；子页正文仍在专项恢复。"})
    by["2010"].update({"status":"metadata_only","electronic_evidence":"official editor blog + official forum","notes":"编辑部新浪博客已明确锚定941、950、951等期号；官方qlsn.com留言本还保留2010历史帖子，如“烦恼防火墙”第18波（928期）及“能不能邮寄一份944期的样报”。完整期号链正在从官方论坛快照提取。"})
    write_csv(COVERAGE,cov,["year","status","electronic_evidence","notes"])
    print(f"records={len(rows)}; promoted 740 and official 2007/2009 paths; coverage updated")
    return 0
if __name__=="__main__":raise SystemExit(main())
