"""Repair the old broadcast-blocked queue; default is a rollback-only preview.

Run from the project root:
  PYTHONPATH=. .venv/bin/python scripts/repair_maps_queue.py [--apply]
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from leadworkbench.db import Store
from leadworkbench.worker import worker_lock
from leadworkbench.maps_migration import repair_legacy_maps


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default='data')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    directory=Path(args.data_dir).resolve()
    with worker_lock(directory,'worker-single.lock'), worker_lock(directory):
        store=Store(directory)
        with store.connect() as con:
            if args.apply:
                backup=directory/f"before-maps-queue-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
                with sqlite3.connect(backup) as dest:
                    con.backup(dest)
                backup.chmod(0o600)
                print('Backup:',backup)
            con.execute('BEGIN IMMEDIATE')
            summary=repair_legacy_maps(con,directory)
            if args.apply:
                con.commit()
            else:
                con.rollback()
            print(json.dumps(summary,ensure_ascii=False,indent=2))
        if args.apply:
            for run_id in summary['runs']:
                store.progress(run_id)


if __name__=='__main__':
    main()
