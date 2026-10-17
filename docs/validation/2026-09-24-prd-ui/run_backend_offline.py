"""Run backend tests with temporary stores and test-owned loopback sockets only.

Run from the repository root:
  .venv/bin/python docs/validation/2026-09-24-prd-ui/run_backend_offline.py [pytest args]
"""
import os
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
TEMP = Path(tempfile.gettempdir()).resolve()
bound_ports = set()
original_bind = socket.socket.bind
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def guarded_bind(sock, address):
    result = original_bind(sock, address)
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        bound_ports.add(sock.getsockname()[1])
    return result


def check_destination(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        if address[0] not in {"127.0.0.1", "::1", "localhost"} or address[1] not in bound_ports:
            raise RuntimeError(f"Offline acceptance guard blocked network destination: {address!r}")


def guarded_connect(sock, address):
    check_destination(sock, address)
    return original_connect(sock, address)


def guarded_connect_ex(sock, address):
    check_destination(sock, address)
    return original_connect_ex(sock, address)


socket.socket.bind = guarded_bind
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex

from leadworkbench.db import Store
original_init = Store.__init__


def guarded_store(self, directory):
    resolved = Path(directory).expanduser().resolve()
    if not resolved.is_relative_to(TEMP):
        raise RuntimeError(f"Offline acceptance guard blocked non-temporary Store: {resolved}")
    return original_init(self, directory)


Store.__init__ = guarded_store


def audit(event, args):
    if event == "sqlite3.connect":
        database = os.fspath(args[0])
        if database == ":memory:":
            return
        path = database.removeprefix("file:").split("?", 1)[0]
        if not Path(path).resolve().is_relative_to(TEMP):
            raise RuntimeError("Offline acceptance guard blocked non-temporary SQLite connection")
    elif event == "subprocess.Popen":
        executable = Path(os.fsdecode(args[0])).resolve()
        # Existing Maps tests create their fake executable in tmp_path.
        # Python children are fixture-only process-cleanup tests.
        if not executable.is_relative_to(TEMP) and executable != Path(sys.executable).resolve():
            raise RuntimeError(f"Offline acceptance guard blocked external executable: {executable}")


sys.addaudithook(audit)

if __name__ == "__main__":
    import pytest
    print("OFFLINE GUARDS: temporary Store/SQLite only; test-bound loopback sockets only; no real browser/Maps binary.")
    raise SystemExit(pytest.main(sys.argv[1:] or ["tests", "-q"]))
