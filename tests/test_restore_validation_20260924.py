import sqlite3
import pytest
from fastapi.testclient import TestClient
from leadworkbench.api import create_app


@pytest.mark.parametrize('kind', ['corrupt', 'incomplete'])
def test_invalid_backup_rejected_without_mutating_data(tmp_path, kind):
    app = create_app(tmp_path, allowed_hosts={'testserver'})
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers['X-Local-Token'] = client.get('/api/session').json()['token']
        store = app.state.store
        cid = store.manual_company('Fixture', 'https://one.example')['id']
        before = store.detail(cid)
        folder = tmp_path / 'backups'
        folder.mkdir()
        path = folder / f'{kind}.sqlite3'
        if kind == 'corrupt':
            path.write_bytes(b'not a SQLite database')
        else:
            with sqlite3.connect(path) as con:
                con.execute('PRAGMA user_version=2')
        response = client.post('/api/backups/restore', json={'name': path.name})
        assert response.status_code == 400
        assert '备份损坏或版本不兼容' in response.text
        assert store.detail(cid) == before
        assert list(folder.iterdir()) == [path]
