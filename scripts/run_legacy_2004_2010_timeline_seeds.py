#!/usr/bin/env python3
"""Run legacy article recovery from official-home snapshots spanning 2007-2010."""
from pathlib import Path
import recover_legacy_articles as base

ROOT = Path(__file__).resolve().parents[1]
base.OUT = ROOT / "data" / "legacy_timeline_2004_2010"
base.OUT.mkdir(parents=True, exist_ok=True)
base.SEEDS = [
    ("2004_home", "https://web.archive.org/web/20040716162949id_/http://www.qlsn.com/", "http://www.qlsn.com/"),
    ("2007_jun_home", "https://web.archive.org/web/20070623224142id_/http://www.qlsn.com/index.asp", "http://www.qlsn.com/index.asp"),
    ("2007_nov_home", "https://web.archive.org/web/20071103154018id_/http://www.qlsn.com:80/", "http://www.qlsn.com/"),
    ("2009_aug_home", "https://web.archive.org/web/20090830081307id_/http://www.qlsn.com:80/", "http://www.qlsn.com/"),
    ("2009_dec_home", "https://web.archive.org/web/20091225202932id_/http://www.qlsn.com:80/", "http://www.qlsn.com/"),
    # A 2011 capture can preserve links to content created during late 2010.
    ("2011_apr_home_for_2010_residue", "https://web.archive.org/web/20110426183705id_/http://www.qlsn.com/", "http://www.qlsn.com/"),
]

if __name__ == "__main__":
    raise SystemExit(base.main())
