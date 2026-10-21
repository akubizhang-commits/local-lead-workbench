"""AWS trial D1: serve.py holds {data}/api.lock (design §1.3)."""
import subprocess
import sys

import pytest

from leadworkbench import serve

HOLDER = """
import fcntl, sys, time
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(30)
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("LEAD_MODE", "LEAD_AUTH", "LEAD_BIND", "LEAD_ALLOWED_HOSTS", "LEAD_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LEAD_DATA_DIR", str(tmp_path))
    calls = []
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(k))
    return calls


def test_exits_2_when_another_process_holds_the_lock(env, tmp_path, capsys):
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path / "api.lock")],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(SystemExit) as info:
            serve.main()
        assert info.value.code == 2
        assert serve.API_LOCK_BUSY in capsys.readouterr().err
        assert env == []
    finally:
        holder.kill()
        holder.wait()


def test_runs_uvicorn_with_the_lock(env, tmp_path):
    serve.main()
    assert env and env[0]["factory"] is True and env[0]["server_header"] is False
    assert env[0]["access_log"] is False and env[0]["proxy_headers"] is False


def test_config_error_exits_2(env, monkeypatch, capsys):
    monkeypatch.setenv("LEAD_BIND", "0.0.0.0")
    with pytest.raises(SystemExit) as info:
        serve.main()
    assert info.value.code == 2
    assert "未开启登录时只能监听本机地址" in capsys.readouterr().err
    assert env == []


def test_lock_released_after_exit(env, tmp_path):
    serve.main()
    handle = serve.hold_api_lock(tmp_path)
    assert handle is not None
    handle.close()
