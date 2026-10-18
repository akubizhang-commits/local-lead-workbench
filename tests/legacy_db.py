"""Load committed legacy (9423c53, user_version=2) SQL dumps into a data directory."""
from contextlib import closing
from pathlib import Path
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
    return path


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
