"""Durable same-host access policy shared across companies and market runs."""
import json
import math
import time
from urllib.parse import urlsplit

DDL = """
CREATE TABLE IF NOT EXISTS website_host_gates(
 host TEXT PRIMARY KEY, gate TEXT NOT NULL DEFAULT '{}',
 last_request REAL NOT NULL DEFAULT 0, min_interval REAL NOT NULL DEFAULT 2);
"""


def host_key(url):
    try:
        return (urlsplit(url).hostname or '').lower().removeprefix('www.')
    except ValueError:
        return ''


def migrate(con):
    """Only bootstrap still-active legacy gates when the shared table is new."""
    owns_transaction = not con.in_transaction
    con.execute("BEGIN IMMEDIATE" if owns_transaction else "SAVEPOINT website_policy_bootstrap")
    try:
        _migrate_locked(con)
    except BaseException:
        if owns_transaction:
            con.rollback()
        else:
            con.execute("ROLLBACK TO website_policy_bootstrap")
            con.execute("RELEASE website_policy_bootstrap")
        raise
    else:
        con.execute("COMMIT" if owns_transaction else "RELEASE website_policy_bootstrap")


def _migrate_locked(con):
    existed = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='website_host_gates'").fetchone()
    con.execute(DDL)
    if existed:
        return
    current = time.time()
    for row in con.execute("""SELECT w.result FROM work_items w JOIN runs r ON r.id=w.run_id
        WHERE w.stage='refresh' AND w.state IN ('pending','running','blocked','failed','partial')
        AND r.status!='cancelled'""").fetchall():
        try:
            checkpoint = json.loads(row[0] or '{}').get('checkpoint') or {}
            host = host_key(checkpoint.get('start', ''))
            gate = (checkpoint.get('host_gates') or {}).get(host)
            if not host or not isinstance(gate, dict):
                continue
            deadline = float(gate.get('not_before', 0))
            if not math.isfinite(deadline) or not (gate.get('manual_required') or deadline > current):
                continue
            existing = con.execute("SELECT gate FROM website_host_gates WHERE host=?", (host,)).fetchone()
            old = json.loads(existing[0]) if existing else {}
            value = {**gate, 'host': host, 'not_before': max(deadline, old.get('not_before', 0)),
                     'manual_required': bool(gate.get('manual_required') or old.get('manual_required'))}
            if old.get('manual_required'):
                value['reason'] = old.get('reason', value.get('reason', ''))
            con.execute("""INSERT INTO website_host_gates(host,gate) VALUES(?,?)
                ON CONFLICT(host) DO UPDATE SET gate=excluded.gate""", (host, json.dumps(value)))
        except (ValueError, TypeError, AttributeError):
            # A malformed historical payload must not prevent opening a DB.
            continue


class WebsiteAccessPolicy:
    """Short DB transactions only; pacing sleeps belong to the source adapter."""
    def __init__(self, store):
        self.store = store

    def gates(self):
        with self.store.connect() as con:
            return {row['host']: json.loads(row['gate']) for row in
                    con.execute("SELECT host,gate FROM website_host_gates WHERE gate!='{}'")}

    def timing(self, origin):
        with self.store.connect() as con:
            row = con.execute("SELECT last_request,min_interval FROM website_host_gates WHERE host=?",
                              (host_key(origin),)).fetchone()
        return (row[0], row[1]) if row else (0, 2)

    def record_request(self, origin, at, delay):
        host = host_key(origin)
        if not host:
            return
        with self.store.connect() as con:
            con.execute("""INSERT INTO website_host_gates(host,last_request,min_interval) VALUES(?,?,?)
                ON CONFLICT(host) DO UPDATE SET last_request=excluded.last_request,
                min_interval=max(website_host_gates.min_interval,excluded.min_interval)""",
                        (host, at, max(2, delay)))

    def observe(self, gate):
        """Commit a restriction before later extraction/ingestion can fail."""
        host = gate.get('host')
        if not host:
            return
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT gate FROM website_host_gates WHERE host=?", (host,)).fetchone()
            old = json.loads(row[0]) if row else {}
            # An observed manual restriction is cleared only by explicit retry.
            if old.get('manual_required'):
                return
            value = {**gate, 'not_before': max(old.get('not_before', 0), gate.get('not_before', 0))}
            con.execute("""INSERT INTO website_host_gates(host,gate) VALUES(?,?)
                ON CONFLICT(host) DO UPDATE SET gate=excluded.gate""", (host, json.dumps(value)))


def resume(con, checkpoint):
    """An explicit retry may release manual gates, never a live Retry-After."""
    gates = checkpoint.get('host_gates', {})
    for host in gates:
        row = con.execute("SELECT gate FROM website_host_gates WHERE host=?", (host,)).fetchone()
        gate = json.loads(row[0]) if row else gates[host]
        if gate.get('not_before', 0) > time.time():
            remaining = max(1, int((gate['not_before'] - time.time() + 59) // 60))
            raise ValueError(f"该官网仍在冷却，约 {remaining} 分钟后可继续")
    for host in gates:
        con.execute("UPDATE website_host_gates SET gate='{}' WHERE host=?", (host,))
    checkpoint['host_gates'] = {}
