"""P1-5: real scripts/start.sh with a temporary data directory (design §4.4–4.5)."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parent.parent
PORT = 18823
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

pytestmark = pytest.mark.skipif(not (ROOT / "frontend" / "dist" / "index.html").is_file(),
                                reason="frontend not built")


def request(path, token=None):
    headers = {"X-Local-Token": token} if token else {}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=headers)
    with OPENER.open(req, timeout=3) as response:
        return response.status, response.headers, json.loads(response.read() or b"null")


def until(predicate, timeout, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(interval)
    return None


def worker_pids(directory):
    out = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [int(line.split()[0]) for line in out.splitlines()
            if "leadworkbench.worker" in line and str(directory) in line]


def port_closed():
    try:
        request("/api/session")
    except (urllib.error.URLError, ConnectionError, OSError):
        return True
    return False


class Script:
    def __init__(self, directory, tmp_path, **env):
        self.log = tmp_path / "start.log"
        environment = {k: v for k, v in os.environ.items() if k not in {"MAPS_SCRAPER_BIN", "LEAD_TEST_FAULT"}}
        environment.update(LEAD_DATA_DIR=str(directory), LEAD_PORT=str(PORT), **env)
        self.handle = self.log.open("w")
        self.process = subprocess.Popen(["bash", str(ROOT / "scripts" / "start.sh")], cwd=ROOT, env=environment,
                                        stdout=self.handle, stderr=subprocess.STDOUT, start_new_session=True)

    def output(self):
        return self.log.read_text(encoding="utf-8", errors="replace")

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.handle.close()


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "data"
    path.mkdir()
    yield path
    path.chmod(stat.S_IRWXU)
    for pid in worker_pids(path):
        os.kill(pid, signal.SIGKILL)


def token():
    return request("/api/session")[2]["token"]


def test_a_normal_start_then_sigint_stops_both(directory, tmp_path):
    script = Script(directory, tmp_path)
    try:
        assert until(lambda: request("/api/overview", token())[2]["worker"]["alive"], 25), script.output()
        assert worker_pids(directory)
        script.process.send_signal(signal.SIGINT)
        script.process.wait(30)
        assert until(lambda: not worker_pids(directory) and port_closed(), 10)
    finally:
        script.stop()


def test_b_slow_migration_keeps_waiting(directory, tmp_path):
    script = Script(directory, tmp_path, LEAD_TEST_FAULT="migration_slow")
    try:
        assert until(lambda: "正在初始化数据库" in script.output(), 19), script.output()
        assert script.process.poll() is None
        assert until(lambda: request("/api/session")[0] == 200, 15), script.output()
    finally:
        script.stop()


def test_c_storage_unavailable_starts_api_without_worker(directory, tmp_path):
    script = Script(directory, tmp_path, LEAD_TEST_FAULT="storage_unavailable")
    try:
        assert until(lambda: "受限模式" in script.output(), 20), script.output()
        status, headers, _ = request("/api/session")
        assert status == 200 and headers["X-Lead-Unavailable"] == "storage_unavailable"
        time.sleep(1)
        assert not worker_pids(directory)
        assert script.process.poll() is None
    finally:
        script.stop()


def test_d_readonly_directory_exits_1(directory, tmp_path):
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    script = Script(directory, tmp_path)
    try:
        assert script.process.wait(15) == 1
        assert "数据目录不可写" in script.output()
        assert not worker_pids(directory) and port_closed()
    finally:
        script.stop()


def test_e_worker_killed_is_restarted_and_stopped(directory, tmp_path):
    script = Script(directory, tmp_path)
    try:
        first = until(lambda: worker_pids(directory), 20)
        assert first, script.output()
        assert until(lambda: request("/api/overview", token())[2]["worker"]["alive"], 15)
        os.kill(first[0], signal.SIGKILL)
        assert until(lambda: "第 1 次自动重启" in script.output(), 5), script.output()
        second = until(lambda: [p for p in worker_pids(directory) if p != first[0]], 12)
        assert second, script.output()
        script.process.send_signal(signal.SIGINT)
        script.process.wait(30)
        assert until(lambda: not worker_pids(directory), 10)
    finally:
        script.stop()


def post(path, token, body):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Local-Token": token, "Content-Type": "application/json"})
    with OPENER.open(req, timeout=5) as response:
        return json.loads(response.read())


def export_children():
    out = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if "leadworkbench.export_job" in line]


@pytest.mark.parametrize("how", ["script", "group"])
def test_a_stop_during_export_leaves_no_export_child(directory, tmp_path, how):
    from perf_fixtures import build
    build(directory, companies=2000, contacts_per_company=2, markets=1)
    script = Script(directory, tmp_path, LEAD_TEST_FAULT="export_slow")
    try:
        assert until(lambda: request("/api/session")[0] == 200, 25), script.output()
        key = token()
        job = post("/api/export-jobs", key, {"format": "xlsx", "layout": "entries"})
        assert until(lambda: request(f"/api/export-jobs/{job['id']}", key)[2]["status"] == "running", 15)
        if how == "script":
            script.process.send_signal(signal.SIGINT)
        else:  # Ctrl+C in a terminal: the whole process group gets SIGINT
            os.killpg(script.process.pid, signal.SIGINT)
        script.process.wait(30)
        assert until(lambda: not export_children(), 10), export_children()
        state = json.loads((directory / "exports" / "jobs" / f"{job['id']}.json").read_text())
        assert state["status"] == "cancelled"
        assert not (directory / "exports" / f"{job['id']}.part").exists()
    finally:
        script.stop()
