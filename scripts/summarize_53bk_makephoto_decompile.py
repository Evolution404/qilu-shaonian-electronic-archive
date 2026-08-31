#!/usr/bin/env python3
"""Reduce transient ILSpy output to non-source naming metadata.

The input decompiled C# is never committed. We retain only:
- relevant variable names and identifiers referenced by their assignments;
- relevant string literals (path fragments, extensions, date formats/prefixes);
- method/type names mentioning PDF/image conversion.
This is sufficient to reconstruct filename conventions without republishing source code.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cms_reference" / "makephoto_decompile_summary.json"

VARS = {
    "pcfilename",
    "mobilefilename",
    "pdfname",
    "pcdir",
    "mobiledir",
    "path",
    "path2",
    "pdf2jpg",
    "bigpageid",
    "pagenum",
    "isjpg",
}
INTEREST = re.compile(r"(?i)(img|pdf|jpg|jpeg|mobile|zoom|page|path|file|guid|date|time|convert|photo)")
STRING_RE = re.compile(r'@?"((?:[^"\\]|\\.)*)"')
ASSIGN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?);\s*$")
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
METHOD_RE = re.compile(r"\b(?:void|string|bool|int|object|[A-Za-z_][A-Za-z0-9_.<>]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def unescape(s: str):
    try:
        return bytes(s, "utf-8").decode("unicode_escape")
    except Exception:
        return s


def relevant_literal(s: str):
    low = s.lower()
    return (
        any(x in low for x in (".jpg", ".jpeg", ".pdf", "img/", "img\\", "mobile", "zoom", "page"))
        or bool(re.fullmatch(r"[yMdHhmsf_./\\-]{3,}", s))
        or ("/" in s or "\\" in s)
    )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: summarize_53bk_makephoto_decompile.py <decompiled-csharp>")
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")

    assignments = []
    literals = set()
    methods = set()
    type_names = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        for sm in STRING_RE.finditer(line):
            value = unescape(sm.group(1))
            if relevant_literal(value):
                literals.add(value)
        mm = METHOD_RE.search(line)
        if mm and INTEREST.search(mm.group(1)):
            methods.add(mm.group(1))
        if line.startswith(("public class ", "internal class ", "class ")):
            type_names.update(x for x in IDENT_RE.findall(line) if INTEREST.search(x))
        am = ASSIGN_RE.search(line)
        if not am:
            continue
        var, rhs = am.group(1), am.group(2)
        if var.lower() not in VARS and not INTEREST.search(var):
            continue
        rhs_literals = [unescape(x) for x in STRING_RE.findall(rhs)]
        identifiers = []
        seen = set()
        for ident in IDENT_RE.findall(STRING_RE.sub(" ", rhs)):
            if ident in {"string", "int", "bool", "true", "false", "null", "new"}:
                continue
            if ident not in seen:
                seen.add(ident)
                identifiers.append(ident)
        assignments.append(
            {
                "variable": var,
                "string_literals": rhs_literals,
                "identifiers": identifiers[:40],
            }
        )

    # Deduplicate assignment summaries without retaining source text.
    unique = []
    seen = set()
    for row in assignments:
        key = (row["variable"], tuple(row["string_literals"]), tuple(row["identifiers"]))
        if key not in seen:
            seen.add(key)
            unique.append(row)

    summary = {
        "type": "Mvcb2b.admin.jquery.MakePhoto",
        "relevant_assignments": unique,
        "relevant_string_literals": sorted(literals),
        "relevant_method_names": sorted(methods),
        "relevant_type_tokens": sorted(type_names),
        "notes": [
            "Derived metadata from transient ILSpy output; decompiled source is not committed.",
            "Generic 53BK reference implementation only; candidate qlsn paths still require independent verification.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
