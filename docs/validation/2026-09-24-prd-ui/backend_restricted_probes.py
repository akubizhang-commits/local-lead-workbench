"""Reproduce invalid-backup error handling without opening a production store."""
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile

HERE = Path(__file__).resolve().parent
runpy.run_path(str(HERE / "run_backend_offline.py"), run_name="offline_guards")

from fastapi.testclient import TestClient
from leadworkbench.api import create_app

results = []
with tempfile.TemporaryDirectory(prefix="prd-restore-probe-") as directory:
    app = create_app(directory, allowed_hosts={"testserver"})
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    store = app.state.store
    cid = store.manual_company("Fixture", "https://one.example")["id"]
    before = store.detail(cid)
    backups = Path(directory) / "backups"
    backups.mkdir()
    (backups / "corrupt.sqlite3").write_bytes(b"not a SQLite database")
    with sqlite3.connect(backups / "incomplete.sqlite3") as con:
        con.execute("PRAGMA user_version=2")
    for name in ("corrupt.sqlite3", "incomplete.sqlite3"):
        response = client.post("/api/backups/restore", json={"name": name})
        results.append({
            "case": name, "http_status": response.status_code, "body": response.text,
            "business_data_unchanged": store.detail(cid) == before,
        })
    client.close()

(HERE / "backend-restricted-probes.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results, indent=2))
