#!/usr/bin/env python3
"""Extract a compact, high-signal path/API summary from the public 53BK v6.3 package.

The package is downloaded transiently; no third-party source/archive bytes are committed.
Only filenames and short string/path excerpts relevant to recovering historical e-paper
PDF/image assets are written to data/cms_reference/focused_*.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cms_reference"
OUT.mkdir(parents=True, exist_ok=True)
DOWNLOAD_PAGE = "https://www.onlinedown.net/soft/117759.htm"
FALLBACKS = [
    "https://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website",
    "http://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website",
    "https://www2.futureware.at/~philippe/SiliconMirror/softdown/soft/201306/53BK_v6.3_jb51.rar",
]
UA = "Mozilla/5.0 qilu-shaonian-53bk-focused/1.1"

EXACT = re.compile(r"(?i)(pubmobile|slidedata|calendarstart|calendarshow|pagelist|dayslist|readtitle|edition|pdf|/img/|\\img\\|mobile)")
TEXT_EXT = re.compile(r"(?i)\.(?:js|cs|aspx|ascx|config|xml|txt|sql)$")
QUOTED = re.compile(r'''["']([^"'\r\n]{1,260})["']''')
PATH_SIGNAL = re.compile(r"(?i)(?:pagelist|dayslist|readtitle|edition|\.pdf(?:$|[?&#])|(?:^|[/\\])img[/\\]|mobile|slidedata|calendarstart|calendarshow)")
HREF = re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')
ABS = re.compile(r'''(?i)https?://[^"'<>\s]+''')


def http_get(url: str, limit: int = 80 * 1024 * 1024) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": DOWNLOAD_PAGE})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError("download exceeds limit")
        return data, r.geturl()


def decode(raw: bytes) -> str:
    best = None
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(enc, "replace")
            q = text.count("\ufffd")
            if best is None or q < best[0]:
                best = (q, text)
        except Exception:
            pass
    return best[1] if best else raw.decode("latin1", "replace")


def fetch() -> tuple[bytes, str]:
    candidates: list[str] = []
    try:
        page_raw, final_page = http_get(DOWNLOAD_PAGE, 8 * 1024 * 1024)
        page = decode(page_raw).replace("&amp;", "&")
        for value in HREF.findall(page):
            u = urllib.parse.urljoin(final_page, value)
            if "117759" in u and ("download" in u.lower() or "iopdfbhjl" in u.lower()):
                candidates.append(u)
        for value in ABS.findall(page):
            if "117759" in value and ("download" in value.lower() or "iopdfbhjl" in value.lower()):
                candidates.append(value)
    except Exception:
        pass
    candidates.extend(FALLBACKS)

    errors = []
    for url in dict.fromkeys(candidates):
        try:
            raw, resolved = http_get(url)
            if raw[:2] == b"PK":
                return raw, resolved
            errors.append(f"{url}: not ZIP ({len(raw)} bytes)")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    raise RuntimeError("no ZIP package found; " + " | ".join(errors[-10:]))


def short_context(text: str, m: re.Match[str]) -> str:
    a = max(0, m.start() - 140)
    b = min(len(text), m.end() + 260)
    return re.sub(r"\s+", " ", text[a:b]).strip()[:500]


def weight(s: str) -> int:
    low = s.lower()
    score = 0
    for token, pts in (("pagelist",100),("dayslist",100),("readtitle",90),("pubmobile",80),("slidedata",80),("pdf",70),("edition",60),("img",40),("mobile",30)):
        if token in low:
            score += pts
    return score


def main() -> int:
    raw, reference_url = fetch()
    entry_rows = []
    literal_rows = []
    contexts = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        for name in names:
            if EXACT.search(name):
                entry_rows.append({"entry": name, "kind": "filename_match"})

        for name in names:
            if not TEXT_EXT.search(name):
                continue
            try:
                data = z.read(name)
            except Exception:
                continue
            if len(data) > 3_000_000:
                continue
            text = decode(data)
            if not EXACT.search(text):
                continue

            seen_ctx = set()
            for m in EXACT.finditer(text):
                c = short_context(text, m)
                if c and c not in seen_ctx:
                    seen_ctx.add(c)
                    contexts.append({"entry": name, "token": m.group(0), "context": c})
                if len(seen_ctx) >= 12:
                    break

            seen_lit = set()
            for q in QUOTED.finditer(text):
                value = q.group(1).strip()
                if not value or not PATH_SIGNAL.search(value) or value in seen_lit:
                    continue
                seen_lit.add(value)
                literal_rows.append({"entry": name, "literal": value[:260]})

    entry_rows.sort(key=lambda r: (-weight(r["entry"]), r["entry"]))
    literal_rows.sort(key=lambda r: (-weight(r["literal"]), r["entry"], r["literal"]))
    contexts.sort(key=lambda r: (-weight(r["token"] + " " + r["context"]), r["entry"]))

    with (OUT / "focused_entries.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry", "kind"]); w.writeheader(); w.writerows(entry_rows)
    with (OUT / "focused_literals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry", "literal"]); w.writeheader(); w.writerows(literal_rows)
    with (OUT / "focused_contexts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry", "token", "context"]); w.writeheader(); w.writerows(contexts[:600])

    ext_counts = Counter()
    top_dirs = Counter()
    for row in entry_rows:
        n = row["entry"].replace("\\", "/")
        ext_counts[Path(n).suffix.lower() or "<none>"] += 1
        parts = [p for p in n.split("/") if p]
        if len(parts) >= 2:
            top_dirs["/".join(parts[:2])] += 1

    report = {
        "reference_url": reference_url,
        "package_entries": len(names),
        "focused_filename_matches": len(entry_rows),
        "focused_quoted_literals": len(literal_rows),
        "focused_contexts": len(contexts),
        "top_filename_matches": [r["entry"] for r in entry_rows[:80]],
        "top_literals": literal_rows[:120],
        "matching_extension_counts": dict(ext_counts.most_common()),
        "matching_top_directories": dict(top_dirs.most_common(40)),
        "notes": [
            "Reference package bytes/source are not committed.",
            "Quoted literals and short contexts are generic CMS evidence only; they do not prove a particular path existed on szb.cnssiot.cn.",
        ],
    }
    (OUT / "focused_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("reference_url","package_entries","focused_filename_matches","focused_quoted_literals","focused_contexts")}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
