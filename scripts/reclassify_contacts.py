#!/usr/bin/env python3
"""Preview/contact-quality migration. --apply makes a SQLite snapshot first."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from leadworkbench.contact_quality import invalid_contact_reason


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    path=args.data_dir.resolve()/'workbench.sqlite3'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(r) for r in con.execute("SELECT * FROM contacts WHERE review_status NOT IN ('reject','invalid','superseded','accept')")]
        reasons=Counter(reason for row in rows if (reason:=invalid_contact_reason(row)))
        result={'candidates':sum(reasons.values()),'reasons':dict(reasons),'applied':False}
        if args.apply:
            backup=path.parent/'backups'/('contact-quality-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
            backup.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(backup) as dest:
                con.backup(dest)
            backup.chmod(0o600)
            # Store startup has other existing reconciliation hooks; snapshot
            # deliberately precedes constructing it.
            from leadworkbench.db import Store
            result.update(Store(path.parent).reclassify_contacts())
            result.update(applied=True,backup=str(backup))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
