"""P1-9: every INSERT names its columns (design §3.2, A-15)."""
from pathlib import Path
import re

IMPLICIT = re.compile(r"INSERT(\s+OR\s+\w+)?\s+INTO\s+[\w.]+\s+(VALUES|SELECT)", re.IGNORECASE)
PACKAGE = Path(__file__).resolve().parent.parent / "leadworkbench"


def test_no_insert_without_column_list():
    hits = []
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in IMPLICIT.finditer(text):
            hits.append(f"{path.relative_to(PACKAGE)}:{text.count(chr(10), 0, match.start()) + 1}")
    assert hits == []
