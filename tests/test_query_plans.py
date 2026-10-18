"""Hot SQL statements use indexes after the P0 migrations (design §2.1)."""
import pytest

from leadworkbench.db import Store

QUERIES = [
    ("SELECT * FROM contacts WHERE company_id=?", ("x",)),
    ("""SELECT * FROM contacts WHERE company_id=? AND lower(trim(email))=lower(trim(?))
        AND review_status NOT IN ('invalid','superseded')""", ("x", "a@b.example")),
    ("SELECT payload FROM contact_versions WHERE contact_id=? ORDER BY observed_at", ("x",)),
    ("SELECT * FROM reviews WHERE status='pending' ORDER BY created_at DESC", ()),
    ("SELECT 1 FROM reviews WHERE kind='contact' AND new_value=? AND status='pending'", ("x",)),
    ("""SELECT id FROM reviews WHERE company_id=? AND kind='field_update' AND field=? AND status='pending'""",
     ("x", "phone")),
    ("SELECT * FROM locations WHERE company_id=?", ("x",)),
    ("SELECT * FROM changes WHERE company_id=? ORDER BY created_at DESC", ("x",)),
    ("""SELECT value FROM observations WHERE company_id=? AND field='operating_status'
        ORDER BY observed_at DESC""", ("x",)),
    ("SELECT id FROM companies ORDER BY created_at DESC,id LIMIT 50", ()),
]


@pytest.mark.parametrize("sql,params", QUERIES)
def test_hot_queries_do_not_scan(tmp_path, sql, params):
    store = Store(tmp_path)
    with store.connect() as con:
        plan = [row[3] for row in con.execute("EXPLAIN QUERY PLAN " + sql, params)]
    # An ordered index walk (SCAN … USING INDEX, stopping at LIMIT) is fine; a table scan is not.
    assert plan and not any(step.startswith("SCAN") and "USING" not in step for step in plan), plan
