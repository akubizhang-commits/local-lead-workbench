"""Load committed legacy (9423c53, user_version=2) SQL dumps into a data directory."""
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY_COMMIT = "9423c53"
REPO = Path(__file__).resolve().parent.parent


def load_legacy(directory, sql="legacy_v2_9423c53.sql"):
    """iterdump omits user_version, so set it explicitly after executescript."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "workbench.sqlite3"
    dump = (FIXTURES / sql).read_text(encoding="utf-8")
    with closing(sqlite3.connect(path)) as con:
        con.executescript(dump)
        con.execute("PRAGMA user_version=2")
        con.commit()
        # Databases created by 9423c53 are persistently in WAL mode.
        con.execute("PRAGMA journal_mode=WAL")
    return path


UPGRADE_FIXTURES = ("v1", "v2_early", "v2_9423c53")
_BACKUP_NOTE = re.compile(r"（修复前备份 [^）]+）")


def load_upgrade_fixture(directory, name):
    """Load one of the three pre-upgrade fixtures used by the upgrade snapshot (P1-9)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if name == "v2_9423c53":
        path = load_legacy(directory)
        extra = (FIXTURES / "legacy_v2_9423c53_extra.sql").read_text(encoding="utf-8")
    else:
        path = directory / "workbench.sqlite3"
        extra = (FIXTURES / f"legacy_{name}.sql").read_text(encoding="utf-8")
    with closing(sqlite3.connect(path)) as con:
        con.executescript(extra)
        con.commit()
        con.execute("PRAGMA journal_mode=WAL")
    return path


def dump_database(path):
    """Raw upgrade result: object names (no autoindexes), ledger ids, rows per table."""
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        names = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex_%'"))
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        ledger = sorted(r[0] for r in con.execute("SELECT id FROM schema_migrations")) if "schema_migrations" in tables else []
        rows = {}
        for table in tables:
            if table == "schema_migrations":
                continue
            values = [dict(r) for r in con.execute(f'SELECT * FROM "{table}"')]
            for row in values:
                for key, value in row.items():
                    if isinstance(value, str) and table == "changes":
                        row[key] = _BACKUP_NOTE.sub("（修复前备份 <name>）", value)
            rows[table] = values
    return dict(sqlite_master_names=names, ledger_ids=ledger, tables=rows)


def volatile_columns(first, second):
    """Columns whose value multiset differs between two independent upgrades."""
    result = {}
    for table, rows in first["tables"].items():
        other = second["tables"].get(table, [])
        columns = sorted({key for row in rows + other for key in row})
        changed = [c for c in columns if sorted(map(repr, (r.get(c) for r in rows)))
                   != sorted(map(repr, (r.get(c) for r in other)))]
        if changed:
            result[table] = changed
    return result


def normalize(dump, volatile):
    """Replace volatile columns first, then sort rows by all columns (design §3.2)."""
    tables = {}
    for table, rows in dump["tables"].items():
        cols = set(volatile.get(table, ()))
        out = []
        for row in rows:
            row = {k: ("<volatile>" if k in cols else v) for k, v in sorted(row.items())}
            out.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
        tables[table] = [json.loads(text) for text in sorted(out)]
    return dict(sqlite_master_names=dump["sqlite_master_names"], ledger_ids=dump["ledger_ids"], tables=tables)


def legacy_code(target):
    """Extract leadworkbench/ at LEGACY_COMMIT into target; None when git is unavailable."""
    git = shutil.which("git")
    if not git:
        return None
    target = Path(target)
    archive = target / "code.tar"
    try:
        with open(archive, "wb") as handle:
            subprocess.run([git, "-C", str(REPO), "archive", LEGACY_COMMIT, "leadworkbench"],
                           stdout=handle, check=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return None
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    archive.unlink()
    return target


def legacy_code_tmp():
    folder = Path(tempfile.mkdtemp(prefix="legacy-code-"))
    return legacy_code(folder)
