#!/usr/bin/env python3
"""Run the repost scanner with the corrected Sina legacy blog-id grammar.

Sina article ids are base36-like/alphanumeric (for example ``...010mhj``), not hex-only.
Keeping the compatibility shim separate lets old scan data remain reproducible while the
crawler follows real previous/next links that contain letters outside a-f.
"""
from __future__ import annotations

import re

import discover_repost_fullpages as scanner

scanner.BLOG_RE = re.compile(
    r"(?:https?://blog\.sina\.com\.cn/s/)?(blog_4c4fc7d9[0-9a-z]+\.html)",
    re.I,
)

if __name__ == "__main__":
    raise SystemExit(scanner.main())
