#!/usr/bin/env python3
"""Condense MakePhoto dependency metadata to the filename-building variables only."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "cms_reference" / "makephoto_decompile_summary.json"
OUT = ROOT / "data" / "cms_reference" / "makephoto_filename_facts.json"

KEEP = {
    "savedata", "dir", "text", "text2", "text3", "text4", "text5", "text6", "text7",
    "text8", "text9", "text10", "pcdir", "mobiledir", "pcfilename", "mobilefilename",
    "pdfname", "pdf2jpg", "bigpageid", "pagenum", "val", "val2", "i",
}


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    rows = []
    for row in data.get("transitive_assignments", []):
        if row.get("variable") not in KEEP:
            continue
        # Drop pure empty initializers; they do not help reconstruct a filename.
        if (
            row.get("string_literals") == [""]
            and not row.get("identifiers")
            and not row.get("calls")
            and not row.get("numeric_literals")
        ):
            continue
        rows.append(row)

    facts = {
        "type": data.get("type"),
        "filename_variable_assignments_in_source_order": rows,
        "high_value_literals": [
            x for x in data.get("relevant_string_literals", [])
            if x and (x in {"/", "-", ".jpg", ".pdf", "mobile", "yyyy-MM-dd"} or "img" in x.lower())
        ],
        "high_value_methods": [
            x for x in data.get("relevant_method_names", [])
            if x in {"Page_Load", "PdfChange", "PdftoJpg", "ProcessSavejpg"}
        ],
        "interpretation_guardrails": [
            "Rows preserve assignment order but omit original expressions.",
            "Repeated generic text/textN variables may belong to different methods/scopes; use neighboring dependencies and method semantics conservatively.",
            "This is generic 53BK reference behavior, not proof of a historical qlsn file.",
        ],
    }
    OUT.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(facts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
