"""AWS trial migration: account and session tables (design §2.1).

Ledger rules apply (FREEZE RULE in migrations.py): the step uses only ``con``,
never BEGIN/COMMIT, and every statement is ``IF NOT EXISTS`` so it can run on any
database repeatedly (restore copies, offline restore).
"""
from __future__ import annotations

AUTH_DDL = (
    """CREATE TABLE IF NOT EXISTS auth_users(
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  username_key TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0,
  tenant_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  password_changed_at TEXT NOT NULL,
  last_login_at TEXT
)""",
    """CREATE TABLE IF NOT EXISTS auth_sessions(
  id_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  tenant_id TEXT
)""",
    "CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS auth_sessions_expires ON auth_sessions(expires_at)",
)


def _auth_ddl(store, con):
    """aws_20261021_auth. Does not use ``store``: callers may pass None (restore copies)."""
    for statement in AUTH_DDL:
        con.execute(statement)


MIGRATIONS = [("aws_20261021_auth", _auth_ddl)]
