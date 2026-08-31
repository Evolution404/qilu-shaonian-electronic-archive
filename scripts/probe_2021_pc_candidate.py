#!/usr/bin/env python3
"""Verify the exact 2021-12-25 A1 PC-page image implied by 53BK MakePhoto naming.

The generic 53BK reference implementation proves that ProcessSavejpg emits:
  pc + yyyyMMdd + guid32 + .jpg
  mobile + yyyyMMdd + the same guid32 + .jpg
This probe applies only that deterministic transformation to the A1 mobile Pagepic already
observed in the archived qlsn root page. It does not enumerate speculative filename patterns.
No newspaper bytes are committed; only response metadata/hashes are retained.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "szb_2021_pc_probe"
OUT.mkdir(parents=True, exist_ok=True)

UA = "qilu-shaonian-2021-pc-probe/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 20
MAX_MEDIA = 40 * 1024 * 1024
WAYBACK_TS = "20220928010853"

MOBILE = "http://szb.cnssiot.cn/Img/2021/12/mobile202112245170816f8aaf438f9a1a9119831a2eab.jpg?editionid=326"
EXPECTED_MOBILE_BASENAME = "mobile202112245170816f8aaf438f9a1a9119831a2eab.jpg"
EXPECTED_PC_BASENAME = "pc202112245170816f8aaf438f9a1a9119831a2eab.jpg"


def derive_pc(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    basename = p.path.rsplit("/", 1)[-1]
    m = re.fullmatch(r"mobile(\d{8})([0-9a-fA-F]{32})\.jpg", basename)
    if not m:
        raise ValueError(f"unexpected mobile basename: {basename}")
    pc = f"pc{m.group(1)}{m.group(2)}.jpg"
    path = p.path.rsplit("/", 1)[0] + "/" + pc
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, ""))


def request(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(MAX_MEDIA + 1)
        if len(body) > MAX_MEDIA:
            raise ValueError("response too large")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}, getattr(r, "status", 200)


def wayback_exact(original: str):
    return f"https://web.archive.org/web/{WAYBACK_TS}id_/{original}"


def wayback_available(original: str):
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode(
        {"url": original, "timestamp": WAYBACK_TS[:8]}
    )
    raw, _, _, _ = request(api)
    data = json.loads(raw.decode("utf-8", "replace"))
    c = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not c.get("available") or not c.get("url"):
        return "", ""
    u = re.sub(r"/web/(\d+)/", r"/web/\1id_/", c["url"], count=1)
    return u, c.get("timestamp", "")


def is_jpeg(raw: bytes, ctype: str):
    return raw.startswith(b"\xff\xd8\xff") or ctype.lower().startswith("image/jpeg")


def probe(label: str, url: str):
    try:
        raw, final, headers, status = request(url)
        ctype = headers.get("content-type", "")
        media = is_jpeg(raw, ctype)
        return {
            "attempt": label,
            "url": url,
            "http_status": str(status),
            "resolved_url": final,
            "content_type": ctype,
            "bytes": str(len(raw)),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "is_jpeg": "yes" if media else "no",
            "error": "",
        }
    except Exception as e:
        return {
            "attempt": label,
            "url": url,
            "http_status": "",
            "resolved_url": "",
            "content_type": "",
            "bytes": "",
            "sha256": "",
            "is_jpeg": "no",
            "error": f"{type(e).__name__}: {e}"[:1500],
        }


def main():
    pc_with_query = derive_pc(MOBILE)
    p = urllib.parse.urlsplit(pc_with_query)
    pc_no_query = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    assert p.path.endswith(EXPECTED_PC_BASENAME)
    assert urllib.parse.urlsplit(MOBILE).path.endswith(EXPECTED_MOBILE_BASENAME)

    candidates = []
    for original, suffix in ((pc_with_query, "with_editionid"), (pc_no_query, "without_query")):
        candidates.append((f"wayback_exact_{suffix}", wayback_exact(original)))
        try:
            closest, ts = wayback_available(original)
        except Exception as e:
            closest, ts = "", ""
            candidates.append((f"wayback_available_error_{suffix}", f"ERROR:{type(e).__name__}:{e}"))
        if closest:
            candidates.append((f"wayback_closest_{suffix}_{ts}", closest))
        candidates.append((f"live_http_{suffix}", original))
        candidates.append((f"live_https_{suffix}", original.replace("http://", "https://", 1)))

    rows = []
    seen = set()
    for label, url in candidates:
        if url.startswith("ERROR:"):
            rows.append({
                "attempt": label, "url": "", "http_status": "", "resolved_url": "",
                "content_type": "", "bytes": "", "sha256": "", "is_jpeg": "no", "error": url,
            })
            continue
        if url in seen:
            continue
        seen.add(url)
        rows.append(probe(label, url))

    fields = ["attempt", "url", "http_status", "resolved_url", "content_type", "bytes", "sha256", "is_jpeg", "error"]
    with (OUT / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    verified = [r for r in rows if r["is_jpeg"] == "yes"]
    report = {
        "source_mobile_url": MOBILE,
        "derived_pc_url": pc_with_query,
        "derived_pc_basename": EXPECTED_PC_BASENAME,
        "formula": "pc{yyyyMMdd}{same_guid32}.jpg",
        "formula_evidence": "generic 53BK MakePhoto.ProcessSavejpg transient decompile; pc/mobile share the same date and GUID",
        "attempts": len(rows),
        "verified_jpeg_responses": len(verified),
        "verified": bool(verified),
        "verified_sha256": sorted({r["sha256"] for r in verified}),
        "notes": [
            "The filename transformation is deterministic CMS evidence; qlsn asset existence still requires a verified response.",
            "No PDF filename is inferred here because Pagepdf is a separate CMS field.",
            "No newspaper binary is committed.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
