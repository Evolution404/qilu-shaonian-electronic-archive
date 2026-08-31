#!/usr/bin/env python3
"""Build a normalized 2004-2010 Qilu Shaonian issue-evidence index.

Sources are repository evidence already recovered from official qlsn.com pages/home snapshots
and the official-editor Sina blog. The index distinguishes verified content, official-path
anchors, and editor metadata. It does not infer publication dates from weekly cadence.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "legacy_2004_2010_issue_index.csv"
REPORT = ROOT / "data" / "legacy_2004_2010_issue_index_report.json"

FULL_ISSUES = ROOT / "data" / "legacy_2004_2010_inventory" / "issue_hits.csv"
TIMELINE = ROOT / "data" / "legacy_timeline_2004_2010" / "legacy_content_links.csv"
SINA = ROOT / "data" / "repost_fullpage" / "sina_posts.csv"

ISSUE_RE = re.compile(r"(?<!\d)(\d{3,4})\s*期")
YEAR_RE = re.compile(r"^(200[4-9]|2010)-")


def add(rows: list[dict], seen: set[tuple], **r) -> None:
    key = (r.get("issue_number",""), r.get("evidence_level",""), r.get("source_url",""), r.get("title",""))
    if not r.get("issue_number") or key in seen:
        return
    seen.add(key); rows.append(r)


def main() -> int:
    rows=[]; seen=set()

    # 1) Parsed official content pages: strongest evidence where the issue appears in body/source.
    if FULL_ISSUES.exists():
        with FULL_ISSUES.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                issue=r.get("issue_number","")
                year=r.get("content_year","")
                if year and not (2004 <= int(year) <= 2010):
                    continue
                conf=r.get("confidence","")
                level="verified_content" if conf in {"source_qilu","qilu_context"} else "official_content_issue_marker"
                add(rows,seen,
                    issue_number=issue, evidence_level=level, evidence_source="qlsn_official_content",
                    evidence_date=r.get("site_date","") or r.get("content_year",""), title=r.get("title",""),
                    source_url=r.get("original","") , archive_url=r.get("archive_url",""),
                    context=r.get("context","")[:500], notes=f"parser_confidence={conf}")

    # 2) Official homepage anchors with explicit issue number, even when child page bytes are gone.
    if TIMELINE.exists():
        with TIMELINE.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                text=" ".join([r.get("anchor_text", ""), r.get("title", "")])
                issues=sorted(set(ISSUE_RE.findall(text)), key=int)
                if not issues:
                    continue
                seed=r.get("seed_id","")
                # Restrict to relevant historical seeds; 2011 seed is only allowed if the linked text itself carries an issue.
                if not any(x in seed for x in ("2007","2009","2011_apr_home_for_2010")):
                    continue
                for issue in issues:
                    add(rows,seen,
                        issue_number=issue, evidence_level="official_path_verified", evidence_source="qlsn_official_home_anchor",
                        evidence_date="", title=r.get("anchor_text","") or r.get("title",""),
                        source_url=r.get("original_url","") , archive_url=r.get("seed_archive_url",""),
                        context=f"official-home anchor: {r.get('anchor_text','')}",
                        notes=f"seed={seed}; child_snapshot_source={r.get('snapshot_source','') or 'unrecovered'}")

    # 3) Official-editor Sina posts during 2009-2010 with explicit issue numbers in title/body hints.
    if SINA.exists():
        with SINA.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                date=r.get("post_date","")
                if not YEAR_RE.match(date):
                    continue
                text=" ".join([r.get("post_title",""),r.get("issue_hints",""),r.get("page_text_hint","")])
                issues=set(ISSUE_RE.findall(text))
                for token in re.split(r"[|,;/\s]+", r.get("issue_hints", "")):
                    if token.isdigit() and 3 <= len(token) <= 4:
                        issues.add(token)
                for issue in sorted(issues,key=int):
                    add(rows,seen,
                        issue_number=issue, evidence_level="editor_metadata", evidence_source="official_editor_sina",
                        evidence_date=date, title=r.get("post_title",""), source_url=r.get("post_url",""),
                        archive_url="", context=(r.get("page_text_hint","") or "")[:500],
                        notes="Issue number appears in official-editor post title/body hint; page scan not implied.")

    # Collapse report by issue and strongest level.
    rank={"verified_content":3,"official_content_issue_marker":3,"official_path_verified":2,"editor_metadata":1}
    rows.sort(key=lambda r:(int(r["issue_number"]),-rank.get(r["evidence_level"],0),r["evidence_date"],r["source_url"]))
    fields=["issue_number","evidence_level","evidence_source","evidence_date","title","source_url","archive_url","context","notes"]
    with OUT.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

    issues=sorted({r["issue_number"] for r in rows},key=int)
    strongest={}
    for issue in issues:
        rr=[r for r in rows if r["issue_number"]==issue]
        strongest[issue]=max(rr,key=lambda x:rank.get(x["evidence_level"],0))["evidence_level"]
    report={
        "evidence_rows":len(rows),
        "distinct_issues":len(issues),
        "issue_numbers":issues,
        "strongest_level_by_issue":strongest,
        "levels":{k:sum(r["evidence_level"]==k for r in rows) for k in rank},
        "notes":[
            "official_path_verified means an archived official qlsn.com homepage exposed an issue-numbered child link; the child body may be missing.",
            "editor_metadata means the official-editor Sina blog names the issue; it is not itself a newspaper page.",
            "No publication date is inferred merely from weekly cadence or snapshot date.",
        ],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
