#!/usr/bin/env python3
"""Materialize one binary from the public 53BK reference package for transient CI analysis.

Usage: materialize_53bk_binary.py <zip-entry-suffix> <output-path>
The downloaded package and binary are never added to the repository.
"""
from __future__ import annotations

import io
import sys
import urllib.parse
import urllib.request
import zipfile

PAGE = "https://www.onlinedown.net/soft/117759.htm"
UA = "Mozilla/5.0 qilu-shaonian-53bk-transient-binary/1.0"


def get(url: str, limit=100 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl()


def package():
    raw, final = get(PAGE, 8 * 1024 * 1024)
    text = raw.decode("utf-8", "replace").replace("&amp;", "&")
    import re

    href = re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')
    candidates = []
    for h in href.findall(text):
        u = urllib.parse.urljoin(final, h)
        if "117759" in u and ("download" in u.lower() or "iopdfbhjl" in u.lower()):
            candidates.append(u)
    candidates += [
        "https://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website",
        "http://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website",
    ]
    for u in dict.fromkeys(candidates):
        try:
            body, _ = get(u)
            if body[:2] == b"PK":
                return body
        except Exception:
            pass
    raise RuntimeError("reference package unavailable")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: materialize_53bk_binary.py <entry-suffix> <output-path>")
    suffix = sys.argv[1].replace("\\", "/").lower()
    output = sys.argv[2]
    raw = package()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        matches = [n for n in zf.namelist() if n.replace("\\", "/").lower().endswith(suffix)]
        if len(matches) != 1:
            raise RuntimeError(f"expected one match for {suffix}, got {matches}")
        data = zf.read(matches[0])
    with open(output, "wb") as f:
        f.write(data)
    print(f"materialized {matches[0]} -> {output} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
