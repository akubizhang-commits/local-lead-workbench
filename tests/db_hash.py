"""Content hash of a SQLite file that ignores sqlite_* tables (design §2.4).

`iterdump()` includes sqlite_stat1/stat4 rows, which change whenever
`PRAGMA optimize` re-analyses (and only on builds with STAT4). This hash covers
user tables only, row by row in primary-key or rowid order.
"""
import hashlib
import json
import sqlite3


def _order(con, table):
    keys = [row[1] for row in sorted(
        (r for r in con.execute(f'PRAGMA table_info("{table}")') if r[5]), key=lambda r: r[5])]
    without_rowid = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                (table,)).fetchone()[0] or ""
    if keys:
        return ",".join(f'"{k}"' for k in keys)
    return "rowid" if "WITHOUT ROWID" not in without_rowid.upper() else "1"


def content_hash(path, tables=None):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if tables is not None:
            names = [n for n in names if n in set(tables)]
        digest = hashlib.sha256()
        for name in names:
            digest.update(json.dumps(["table", name]).encode())
            for row in con.execute(f'SELECT * FROM "{name}" ORDER BY {_order(con, name)}'):
                digest.update(json.dumps(list(row), default=repr).encode())
        return digest.hexdigest()
    finally:
        con.close()
