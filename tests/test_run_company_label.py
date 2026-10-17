from leadworkbench.db import Store


def test_existing_refresh_runs_include_company_identity(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company('Ambiance Apparel', 'https://example.org')
    run = store.refresh(company['id'])
    for result in (store.run(run['id']), next(r for r in store.runs() if r['id'] == run['id'])):
        assert result['company_id'] == company['id']
        assert result['company_name'] == 'Ambiance Apparel'
    # Historical runs resolve the current name without requiring another crawl.
    with store.connect() as con:
        con.execute('UPDATE companies SET name=? WHERE id=?', ('Updated company', company['id']))
    assert store.run(run['id'])['company_name'] == 'Updated company'
