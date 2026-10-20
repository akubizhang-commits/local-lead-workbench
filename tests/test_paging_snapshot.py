"""Deterministic probes for test_8_paged_lists_stay_consistent_during_writes (design §8, review R6).

Each probe commits a row from *another thread* at the exact point between a page's COUNT and its row
SELECT (or, for reviews, between the rows and the COUNT). If the page does not read in one snapshot,
`total` and `items` disagree. No timing assumptions: the insert is joined before the request continues.
"""
import contextlib
import threading
import uuid

from fastapi.testclient import TestClient

from leadworkbench.api import create_app


def _client(tmp_path):
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    return client


def _in_thread(fn, *args):
    errors = []
    def run():
        try:
            fn(*args)
        except BaseException as exc:  # surfaced below
            errors.append(exc)
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    assert not t.is_alive() and not errors, errors


def _companies_after_count(client, monkeypatch, store):
    original = store._company_total
    inserted = []
    def wrapped(con, query):
        total = original(con, query)
        if not inserted:
            inserted.append(1)
            _in_thread(store.manual_company, f"Inserted {uuid.uuid4().hex[:6]}", "https://inserted.example")
        return total
    monkeypatch.setattr(store, "_company_total", wrapped)
    results = []
    for path in ("/api/company-page?page=1&page_size=50", "/api/companies"):
        inserted.clear()
        body = client.get(path).json()
        results.append((path, len(body["items"]), body["total"]))
    return results


def test_company_page_total_and_items_share_a_snapshot(tmp_path, monkeypatch):
    with _client(tmp_path) as client:
        store = client.app.state.store
        for i in range(30):
            store.manual_company(f"Page {i}", f"https://p{i}.example")
        with store.connect() as con:  # main case: no exclusion rules, so nothing implicitly opens a transaction
            assert con.execute("SELECT count(*) FROM exclusion_rules").fetchone()[0] == 0
        for path, items, total in _companies_after_count(client, monkeypatch, store):
            assert items == min(50, total), (path, items, total)


def test_company_page_with_an_exclusion_rule_control(tmp_path, monkeypatch):
    # Control: a (non-matching) rule makes _materialize_exclusions write temp.excluded_ids, which implicitly
    # BEGINs before the COUNT, so this case reads one snapshot even without an explicit BEGIN.
    with _client(tmp_path) as client:
        store = client.app.state.store
        for i in range(30):
            store.manual_company(f"Page {i}", f"https://p{i}.example")
        assert client.post("/api/exclusion-rules", json={"scope": "global", "kind": "keyword", "value": "zzz-no-match"}).status_code in (200, 201)
        for path, items, total in _companies_after_count(client, monkeypatch, store):
            assert items == min(50, total), (path, items, total)


def test_review_page_total_and_items_share_a_snapshot(tmp_path, monkeypatch):
    with _client(tmp_path) as client:
        store = client.app.state.store
        company = store.manual_company("Review Co", "https://review.example")
        def add_review(n):
            with store.connect() as con:
                store._review(con, company["id"], "field_update", "notes", "", f"note {n}", "https://review.example/a", "e")
        add_review(0)
        original = store._decorate_reviews
        done = []
        def wrapped(con, reviews):
            original(con, reviews)
            if not done:
                done.append(1)
                _in_thread(add_review, 1)
        monkeypatch.setattr(store, "_decorate_reviews", wrapped)
        body = client.get("/api/reviews?page=1&page_size=20").json()
        assert len(body["items"]) == min(20, body["total"]), body


class _Proxy:
    def __init__(self, con, after_count):
        self._con, self._after_count = con, after_count
    def execute(self, sql, *args):
        cursor = self._con.execute(sql, *args)
        if sql.lstrip().startswith("SELECT count(*)") and "work_items" in sql:
            cursor = _Materialized(cursor.fetchall())
            self._after_count()
        return cursor
    def __getattr__(self, name):
        return getattr(self._con, name)


class _Materialized:
    def __init__(self, rows):
        self._rows = rows
    def fetchone(self):
        return self._rows[0] if self._rows else None
    def fetchall(self):
        return self._rows
    def __iter__(self):
        return iter(self._rows)


def test_refresh_activity_total_and_items_share_a_snapshot(tmp_path, monkeypatch):
    with _client(tmp_path) as client:
        store = client.app.state.store
        company = client.post("/api/companies", json={"name": "Refresh Co", "website": "https://refresh.example"}).json()
        run = client.post(f"/api/companies/{company['id']}/refresh").json()
        other = store.manual_company("Other Co", "https://other.example")
        original = store.connect
        def insert_item():
            with original() as con:
                con.execute("INSERT INTO work_items(id,run_id,stage,target,state,result) VALUES(?,?,'refresh',?,'pending',NULL)",
                            (uuid.uuid4().hex, run["id"], other["id"]))
        fired = []
        def after_count():
            if not fired:
                fired.append(1)
                _in_thread(insert_item)
        @contextlib.contextmanager
        def connect():
            with original() as con:
                yield _Proxy(con, after_count)
        monkeypatch.setattr(store, "connect", connect)
        body = client.get("/api/refresh-activity?page=1").json()
        assert fired
        assert len(body["items"]) == min(50, body["total"]), body
