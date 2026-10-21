"""AWS trial D5: real boto3 against the local S3 stand-in (design §5.4). Never contacts AWS."""
import hashlib

import pytest

boto3 = pytest.importorskip("boto3")

from leadworkbench import daily_backup as daily  # noqa: E402
from leadworkbench.backup_upload import S3Uploader, from_config  # noqa: E402
from leadworkbench.config import load  # noqa: E402
from leadworkbench.db import Store  # noqa: E402
import s3_stub  # noqa: E402

PORT = 18854
ENDPOINT = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def stub(tmp_path, monkeypatch):
    # Stand-in credentials and isolation from ~/.aws and the instance metadata service.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "stub")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "stub")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION", "AWS_REGION", "AWS_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
    objects = tmp_path / "objects"
    objects.mkdir()
    server = s3_stub.start(PORT, objects, deny={"denied-bucket"})
    try:
        yield objects
    finally:
        server.shutdown()
        server.server_close()


def config(bucket):
    return load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test", "LEAD_BACKUP_S3_BUCKET": bucket,
                 "LEAD_BACKUP_S3_REGION": "ap-northeast-1", "LEAD_BACKUP_S3_ENDPOINT": ENDPOINT})


def test_daily_backup_reaches_the_stub_byte_for_byte(tmp_path, stub):
    data = tmp_path / "data"
    store = Store(data)
    store.manual_company("Stub Upload Co", "https://stub.example")
    cfg = config("lw-backups")
    local = daily.run_local(store, cfg)
    state = daily.upload_pending(data, cfg, from_config(cfg))
    assert state["status"] == "ok", state
    name = local["last_daily_name"]
    assert state["key"].startswith("leadworkbench/") and state["key"].endswith(f"/{name}")
    uploaded = stub / "lw-backups" / state["key"]
    assert hashlib.sha256(uploaded.read_bytes()).digest() == hashlib.sha256(
        (data / "backups" / name).read_bytes()).digest()
    assert "AES256" in (uploaded.parent / f"{uploaded.name}.headers").read_text()
    assert daily.upload_due(state, daily.read_state(data, daily.STATE), daily.utcnow()) is False


def test_access_denied_is_recorded(tmp_path, stub):
    data = tmp_path / "data"
    store = Store(data)
    cfg = config("denied-bucket")
    daily.run_local(store, cfg)
    state = daily.upload_pending(data, cfg, from_config(cfg))
    assert state["status"] == "failed" and state["error_code"] == "AccessDenied" and state["attempts"] == 1
    assert "denied-bucket" not in str(state)


def test_uploader_key_layout():
    uploader = S3Uploader("b", "p/", "us-east-1")
    from datetime import datetime, timezone
    assert uploader.key("x.sqlite3", datetime(2026, 1, 5, tzinfo=timezone.utc)) == "p/2026/01/x.sqlite3"
