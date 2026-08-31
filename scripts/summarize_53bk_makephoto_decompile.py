#!/usr/bin/env python3
"""Reduce transient ILSpy output to non-source MakePhoto naming metadata.

The input decompiled C# is never committed. We retain only derived facts needed to recover
historical asset names: variable-dependency edges, string/numeric constants, selected method
calls, and naming-related method/type tokens. A transitive dependency walk starts from the
PC/mobile/PDF output variables so compiler-generated temporaries (text4/text6/etc.) are not
lost. No statement text or decompiled method body is persisted.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cms_reference" / "makephoto_decompile_summary.json"

SEEDS = {
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
    "ispdf",
    "ismobile",
}
INTEREST = re.compile(r"(?i)(img|pdf|jpg|jpeg|mobile|zoom|page|path|file|guid|date|time|convert|photo)")
STRING_RE = re.compile(r'@?"((?:[^"\\]|\\.)*)"')
# Handles declarations ("string text = ...;") and ordinary assignments while excluding ==, >=, etc.
ASSIGN_RE = re.compile(
    r"^(?:(?:var|string|int|long|bool|object|double|float|decimal|DateTime|[A-Za-z_][A-Za-z0-9_.<>\[\]?]*)\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?![=>])(.+?);\s*$"
)
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?(?![A-Za-z_])")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
METHOD_RE = re.compile(r"\b(?:void|string|bool|int|object|[A-Za-z_][A-Za-z0-9_.<>]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
KEYWORDS = {
    "string", "int", "long", "bool", "object", "double", "float", "decimal", "DateTime",
    "true", "false", "null", "new", "return", "if", "else", "for", "foreach", "while",
    "this", "base", "var", "void", "public", "private", "protected", "static", "class",
}


def unescape(s: str):
    # C# escape handling is only needed for path/literal metadata. Keep malformed sequences verbatim.
    try:
        return bytes(s, "utf-8").decode("unicode_escape")
    except Exception:
        return s


def relevant_literal(s: str):
    low = s.lower()
    return (
        any(x in low for x in (".jpg", ".jpeg", ".pdf", "img/", "img\\", "mobile", "zoom", "page"))
        or bool(re.fullmatch(r"[yMdHhmsf_./\\:-]{2,}", s))
        or ("/" in s or "\\" in s)
    )


def parse_assignment(line: str):
    am = ASSIGN_RE.match(line)
    if not am:
        return None
    var, rhs = am.group(1), am.group(2)
    string_literals = [unescape(x) for x in STRING_RE.findall(rhs)]
    stripped = STRING_RE.sub(" ", rhs)
    identifiers = []
    seen = set()
    for ident in IDENT_RE.findall(stripped):
        if ident in KEYWORDS or ident == var:
            continue
        if ident not in seen:
            seen.add(ident)
            identifiers.append(ident)
    calls = []
    call_seen = set()
    for call in CALL_RE.findall(stripped):
        if call not in call_seen:
            call_seen.add(call)
            calls.append(call)
    numbers = list(dict.fromkeys(NUMBER_RE.findall(stripped)))
    # Keep only the expression shape, not source text: whether it concatenates/uses ternary/indexing.
    operators = []
    for op, label in (("+", "concat_or_add"), ("?", "conditional"), ("[", "indexing")):
        if op in stripped:
            operators.append(label)
    return {
        "variable": var,
        "string_literals": string_literals,
        "numeric_literals": numbers[:20],
        "identifiers": identifiers[:80],
        "calls": calls[:40],
        "operators": operators,
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: summarize_53bk_makephoto_decompile.py <decompiled-csharp>")
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")

    all_assignments = []
    by_var = defaultdict(list)
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
        row = parse_assignment(line)
        if row:
            all_assignments.append(row)
            by_var[row["variable"]].append(row)

    # Walk backwards through local-variable dependencies. This captures decompiler temporaries
    # such as text4/text6 that feed pcdir/mobilefilename without storing the original expressions.
    selected_vars = set()
    queue = deque()
    for var in by_var:
        if var.lower() in SEEDS or INTEREST.search(var):
            selected_vars.add(var)
            queue.append(var)

    while queue:
        var = queue.popleft()
        for row in by_var.get(var, []):
            for dep in row["identifiers"]:
                if dep in by_var and dep not in selected_vars:
                    selected_vars.add(dep)
                    queue.append(dep)

    selected_rows = []
    for row in all_assignments:
        if row["variable"] in selected_vars:
            selected_rows.append(row)

    # De-duplicate structurally equivalent assignment summaries.
    unique = []
    seen = set()
    for row in selected_rows:
        key = (
            row["variable"],
            tuple(row["string_literals"]),
            tuple(row["numeric_literals"]),
            tuple(row["identifiers"]),
            tuple(row["calls"]),
            tuple(row["operators"]),
        )
        if key not in seen:
            seen.add(key)
            unique.append(row)

    dependency_edges = []
    edge_seen = set()
    for row in unique:
        for dep in row["identifiers"]:
            if dep in selected_vars:
                edge = (row["variable"], dep)
                if edge not in edge_seen:
                    edge_seen.add(edge)
                    dependency_edges.append({"from": row["variable"], "depends_on": dep})

    summary = {
        "type": "Mvcb2b.admin.jquery.MakePhoto",
        "seed_variables": sorted(SEEDS),
        "dependency_variables": sorted(selected_vars),
        "dependency_edges": dependency_edges,
        "transitive_assignments": unique,
        # Backwards-compatible alias used by earlier analysis.
        "relevant_assignments": [r for r in unique if r["variable"].lower() in SEEDS or INTEREST.search(r["variable"])],
        "relevant_string_literals": sorted(literals),
        "relevant_method_names": sorted(methods),
        "relevant_type_tokens": sorted(type_names),
        "notes": [
            "Derived metadata from transient ILSpy output; decompiled source and statement text are not committed.",
            "Transitive dependency summaries retain constants/identifiers/operators only, allowing filename reconstruction without source reproduction.",
            "Generic 53BK reference implementation only; candidate qlsn paths still require independent verification.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
