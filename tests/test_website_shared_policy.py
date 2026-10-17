import pytest

from leadworkbench.db import Store
from leadworkbench.website_policy import WebsiteAccessPolicy, resume


def test_gate_and_spacing_are_shared_across_companies_and_restart(tmp_path):
    store = Store(tmp_path)
    policy = WebsiteAccessPolicy(store)
    policy.record_request('https://www.brand.example/a', 1000, 8)
    policy.observe(dict(host='brand.example', not_before=2000, manual_required=False, reason='HTTP 429'))
    reopened = WebsiteAccessPolicy(Store(tmp_path))
    assert reopened.timing('http://brand.example/b') == (1000, 8)
    assert reopened.gates()['brand.example']['not_before'] == 2000
    assert reopened.timing('https://other.example') == (0, 2)


def test_manual_retry_cannot_bypass_shared_cooldown(tmp_path, monkeypatch):
    import leadworkbench.website_policy as policy_module
    monkeypatch.setattr(policy_module.time, 'time', lambda: 1000)
    store = Store(tmp_path)
    gate = dict(host='brand.example', not_before=2000, manual_required=False, reason='HTTP 429')
    policy = WebsiteAccessPolicy(store)
    policy.observe(gate)
    checkpoint = {'host_gates': {'brand.example': gate}}
    with store.connect() as con, pytest.raises(ValueError, match='冷却'):
        resume(con, checkpoint)
    assert policy.gates()['brand.example'] == gate
    monkeypatch.setattr(policy_module.time, 'time', lambda: 2001)
    with store.connect() as con:
        resume(con, checkpoint)
    assert policy.gates() == {}
    assert checkpoint['host_gates'] == {}


def test_ordinary_restriction_cannot_clear_manual_gate(tmp_path):
    store = Store(tmp_path)
    policy = WebsiteAccessPolicy(store)
    gate = dict(host='brand.example', not_before=0, manual_required=True, reason='HTTP 403')
    policy.observe(gate)
    policy.observe(dict(host='brand.example', not_before=2000, manual_required=False, reason='HTTP 429'))
    assert policy.gates()['brand.example'] == gate


@pytest.mark.asyncio
async def test_worker_reuses_host_gate_for_another_company_without_network(tmp_path, monkeypatch):
    from leadworkbench.sources import website
    from leadworkbench.work_item_executor import execute_run_item
    from test_website import Session, Response
    clock = [1000.0]

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(website.time, 'time', lambda: clock[0])
    monkeypatch.setattr(website.asyncio, 'sleep', sleep)
    session = Session({'https://brand.example/robots.txt': Response(status=404),
                       'https://brand.example/': Response(status=403)})
    monkeypatch.setattr(website.aiohttp, 'ClientSession', lambda **kwargs: session)
    store = Store(tmp_path)
    first = store.manual_company('First branch', 'https://brand.example')
    second = store.manual_company('Second branch', 'https://brand.example')
    for company in [first, second]:
        run = store.refresh(company['id'])
        await execute_run_item(store, run['id'], enrich=website.enrich_website)
        assert store.run(run['id'])['status'] == 'blocked'
    assert session.requested == ['https://brand.example/robots.txt', 'https://brand.example/']
    assert WebsiteAccessPolicy(store).gates()['brand.example']['manual_required']


def test_upgrade_preserves_unresolved_legacy_host_gate_without_resurrecting_it(tmp_path):
    import json
    store = Store(tmp_path)
    company = store.manual_company('Branch', 'https://brand.example')
    run = store.refresh(company['id'])
    gate = dict(host='brand.example', not_before=0, manual_required=True, reason='HTTP 403')
    checkpoint = dict(version=1, start='https://brand.example/', host_gates={'brand.example': gate})
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='blocked',result=? WHERE run_id=?",
                    (json.dumps({'checkpoint': checkpoint}), run['id']))
        con.execute("DROP TABLE website_host_gates")
    upgraded = Store(tmp_path)
    assert WebsiteAccessPolicy(upgraded).gates()['brand.example'] == gate
    with upgraded.connect() as con:
        resume(con, checkpoint)
    assert WebsiteAccessPolicy(Store(tmp_path)).gates() == {}


def test_failed_gate_bootstrap_rolls_back_table_and_can_retry():
    import json
    import sqlite3
    from leadworkbench.website_policy import migrate
    con = sqlite3.connect(':memory:')
    con.executescript("""CREATE TABLE runs(id TEXT,status TEXT);
        CREATE TABLE work_items(run_id TEXT,stage TEXT,state TEXT,result TEXT);
        INSERT INTO runs VALUES('r','partial');""")
    gate = dict(host='brand.example', not_before=0, manual_required=True, reason='HTTP 403')
    con.execute("INSERT INTO work_items VALUES('r','refresh','blocked',?)",
                (json.dumps({'checkpoint': {'start': 'https://brand.example/',
                                          'host_gates': {'brand.example': gate}}}),))
    con.commit()

    class InterruptedConnection:
        def __getattr__(self, name):
            return getattr(con, name)

        def execute(self, sql, *args):
            if sql.lstrip().startswith('INSERT INTO website_host_gates'):
                raise sqlite3.OperationalError('simulated interrupted migration')
            return con.execute(sql, *args)

    with pytest.raises(sqlite3.OperationalError, match='interrupted'):
        migrate(InterruptedConnection())
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='website_host_gates'").fetchone() is None
    migrate(con)
    assert json.loads(con.execute('SELECT gate FROM website_host_gates').fetchone()[0]) == gate
