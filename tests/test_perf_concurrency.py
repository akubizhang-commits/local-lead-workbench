"""P1 concurrency and export progress measurement (design §14.1, decision D8). LEAD_PERF=1 only.

20k companies / 100k contacts, a real uvicorn API on port 18820. p95 (nearest rank, 19th of 20)
of /api/settings, /api/overview and one company page: baseline, while a background XLSX export
runs, while the legacy unpaged GET /api/companies runs, and while the retention summary is
recomputed. Hard limit 200 ms, target 100 ms. Results: LEAD_PERF_OUT/p1-perf-concurrency.json.
"""
import json
import os
from pathlib import Path
import threading
import time
import uuid

import pytest

from perf_fixtures import build
from perf_server import Api, processes, save, stats, until

pytestmark = pytest.mark.skipif(os.environ.get("LEAD_PERF") != "1", reason="LEAD_PERF=1 only")
PORT = 18820
ENDPOINTS = {"settings": "/api/settings", "overview": "/api/overview",
             "company_page": "/api/company-page?page=1&page_size=50"}
SAMPLES, INTERVAL, ROUNDS = 20, 0.25, 3
HARD_MS, TARGET_MS = 200, 100
RESULTS = {}


def maps_dirs(root, count=200, days=40):
    stamp = time.time() - days * 86400
    for index in range(count):
        path = root / "runs" / f"perfrun{index % 20}" / f"item{index}" / f"maps-{uuid.uuid4().hex}"
        path.mkdir(parents=True)
        (path / "results.jsonl").write_text("{}\n" * 20)
        (path / "metadata.json").write_text(json.dumps({"finished_at": "2026-09-01T00:00:00+00:00"}))
        os.utime(path, (stamp, stamp))


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    root = tmp_path_factory.mktemp("perfdata")
    started = time.perf_counter()
    build(root, 20000, 5)._refresh_facets()
    maps_dirs(root)
    RESULTS["build_seconds"] = round(time.perf_counter() - started, 1)
    server = Api(root, PORT, root.parent / "perf-api.log")
    server.root = root
    try:
        yield server
    finally:
        server.stop()
        save("p1-perf-concurrency.json", RESULTS)
        assert not processes("leadworkbench.export_job")


def sample_all(server, window=None):
    """Three threads, one per endpoint, 20 requests each 250 ms apart. window() -> still inside?"""
    found = {name: [] for name in ENDPOINTS}
    def worker(name, path):
        for _ in range(SAMPLES):
            status, _, _, elapsed = server.call("GET", path)
            assert status == 200, (name, status)
            if window is None or window():
                found[name].append(elapsed)
            time.sleep(INTERVAL)
    threads = [threading.Thread(target=worker, args=item) for item in ENDPOINTS.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return found


def summarize(found):
    return {name: stats(values[:SAMPLES]) for name, values in found.items() if values}


def verdict(scenario):
    worst = max(item["p95_ms"] for item in scenario.values())
    return {"worst_p95_ms": worst, "hard_200ms": worst < HARD_MS, "target_100ms": worst < TARGET_MS}


def test_1_baseline(api):
    api.call("GET", "/api/overview")  # warm caches
    RESULTS["baseline"] = summarize(sample_all(api))
    RESULTS["baseline_verdict"] = verdict(RESULTS["baseline"])
    assert RESULTS["baseline_verdict"]["hard_200ms"], RESULTS["baseline"]


def test_2_during_xlsx_export(api):
    collected = {name: [] for name in ENDPOINTS}
    exports, intervals = [], []
    for round_number in range(ROUNDS):
        status, _, job, _ = api.call("POST", "/api/export-jobs", {"format": "xlsx", "layout": "entries"})
        assert status == 202, job
        changes, done = [], threading.Event()
        def poll(job_id=job["id"]):
            last = None
            while not done.is_set():
                current = api.call("GET", f"/api/export-jobs/{job_id}")[2]
                key = (current["phase"], current.get("processed"), current.get("rows"))
                if key != last:
                    changes.append((time.monotonic(), key))
                    last = key
                if current["status"] not in ("queued", "running"):
                    exports.append(current)
                    return
                time.sleep(0.1)
        poller = threading.Thread(target=poll)
        poller.start()
        assert until(lambda: changes and changes[-1][1][0] != "queued", 30)
        found = sample_all(api, window=lambda: poller.is_alive())
        for name, values in found.items():
            collected[name] += values
        poller.join(600)
        done.set()
        stamps = [t for t, _ in changes]
        intervals += [b - a for a, b in zip(stamps, stamps[1:])]
        if all(len(values) >= SAMPLES for values in collected.values()):
            break
    RESULTS["during_export"] = summarize(collected)
    RESULTS["during_export_rounds"] = round_number + 1
    RESULTS["during_export_verdict"] = verdict(RESULTS["during_export"])
    last = exports[-1]
    assert last["status"] == "succeeded", last
    started = time.mktime(time.strptime(last["created_at"][:19], "%Y-%m-%dT%H:%M:%S"))
    finished = time.mktime(time.strptime(last["finished_at"][:19], "%Y-%m-%dT%H:%M:%S"))
    intervals.sort()
    RESULTS["export"] = {"total_seconds": finished - started, "rows": last.get("rows"),
                         "size_bytes": last.get("size_bytes"),
                         "progress_interval_median_s": round(intervals[len(intervals) // 2], 2) if intervals else None,
                         "progress_interval_max_s": round(intervals[-1], 2) if intervals else None,
                         "progress_updates": len(intervals) + 1}
    RESULTS["export"]["median_le_1s"] = bool(intervals) and intervals[len(intervals) // 2] <= 1
    RESULTS["export"]["max_le_5s"] = bool(intervals) and intervals[-1] <= 5
    assert all(len(values) >= SAMPLES for values in collected.values()), {k: len(v) for k, v in collected.items()}
    assert RESULTS["during_export_verdict"]["hard_200ms"], RESULTS["during_export"]


def test_3_during_legacy_unpaged_companies(api):
    done = threading.Event()
    lists = []
    def big():
        while not done.is_set():
            status, headers, body, elapsed = api.call("GET", "/api/companies", timeout=120)
            assert status == 200
            lists.append({"ms": round(elapsed), "total": headers.get("X-Total-Count")})
    thread = threading.Thread(target=big)
    thread.start()
    time.sleep(0.3)
    try:
        found = sample_all(api, window=lambda: thread.is_alive())
    finally:
        done.set()
        thread.join(180)
    RESULTS["during_unpaged_companies"] = summarize(found)
    RESULTS["unpaged_companies_calls"] = lists
    RESULTS["during_unpaged_companies_verdict"] = verdict(RESULTS["during_unpaged_companies"])
    assert RESULTS["during_unpaged_companies_verdict"]["hard_200ms"], RESULTS["during_unpaged_companies"]


def test_4_during_retention_recompute(api):
    found = {name: [] for name in ENDPOINTS}
    recomputes = []
    def invalidate():
        while not stop.is_set():
            started = time.perf_counter()
            api.call("GET", "/api/retention")  # invalidates the summary; the next overview recomputes it
            recomputes.append(round((time.perf_counter() - started) * 1000))
            time.sleep(0.5)
    stop = threading.Event()
    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        found = sample_all(api)
    finally:
        stop.set()
        thread.join(60)
    RESULTS["during_retention_recompute"] = summarize(found)
    RESULTS["retention_get_ms"] = recomputes
    RESULTS["during_retention_recompute_verdict"] = verdict(RESULTS["during_retention_recompute"])
    overview = api.call("GET", "/api/overview")[2]["retention"]
    RESULTS["retention_summary"] = overview
    assert RESULTS["during_retention_recompute_verdict"]["hard_200ms"], RESULTS["during_retention_recompute"]


# ---- reliability (design §14.2; simulated scraper only, never Google) ----------------------------

import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile

from perf_server import ROOT, clean_env

RELIABILITY_PORT = 18821
SHOT_PORT, OFFLINE_PORT = 18822, 18824
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def worker_errors(directory):
    path = Path(directory) / "logs" / "worker.log"
    lines = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [line for line in lines if line.get("level") in ("ERROR", "CRITICAL")]


def overview_alive(api):
    status, _, body, _ = api.call("GET", "/api/overview")
    return status == 200 and body["worker"]["alive"]


def test_5_fresh_directory_group_a_api_and_worker_together(tmp_path):
    """Ten fresh directories; API and worker start at the same moment (the original race)."""
    runs = []
    for index in range(10):
        directory = Path(tempfile.mkdtemp(prefix="p1-fresh-a-"))
        log = (tmp_path / f"a{index}-worker.out").open("w")
        worker = subprocess.Popen([sys.executable, "-m", "leadworkbench.worker", "--data-dir", str(directory)],
                                  cwd=ROOT, env=clean_env(LEAD_DATA_DIR=directory), stdout=log,
                                  stderr=subprocess.STDOUT)
        api = None
        try:
            api = Api(directory, RELIABILITY_PORT, tmp_path / f"a{index}-api.log")
            alive = bool(until(lambda: overview_alive(api), 15, 0.5))
            time.sleep(1)
            runs.append({"run": index, "worker_alive": alive, "worker_running": worker.poll() is None,
                         "worker_log_errors": len(worker_errors(directory))})
        finally:
            if api is not None:
                api.stop()
            worker.send_signal(signal.SIGTERM)
            worker.wait(15)
            log.close()
            shutil.rmtree(directory, ignore_errors=True)
    RESULTS["fresh_group_a"] = runs
    assert all(r["worker_alive"] and r["worker_running"] and r["worker_log_errors"] == 0 for r in runs), runs


def start_script(directory, log_path, port=RELIABILITY_PORT, **env):
    handle = open(log_path, "w")
    process = subprocess.Popen(["bash", str(ROOT / "scripts" / "start.sh")], cwd=ROOT,
                               env=clean_env(LEAD_DATA_DIR=directory, LEAD_PORT=port, **env),
                               stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    return process, handle


def stop_script(process, handle):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    handle.close()


def test_6_fresh_directory_group_b_start_script(tmp_path):
    runs = []
    for index in range(10):
        directory = Path(tempfile.mkdtemp(prefix="p1-fresh-b-"))
        process, handle = start_script(directory, tmp_path / f"b{index}.log")
        try:
            probe = type("Probe", (), {})()
            probe.port, probe.token = RELIABILITY_PORT, None
            probe.call = lambda *a, **k: Api.call(probe, *a, **k)
            token = until(lambda: probe.call("GET", "/api/session")[2]["token"], 30, 0.25)
            probe.token = token
            alive = bool(until(lambda: overview_alive(probe), 20, 0.5))
            time.sleep(1)
            runs.append({"run": index, "worker_alive": alive, "script_running": process.poll() is None,
                         "worker_log_errors": len(worker_errors(directory))})
        finally:
            stop_script(process, handle)
            shutil.rmtree(directory, ignore_errors=True)
    RESULTS["fresh_group_b"] = runs
    RESULTS["fresh_group_b_leftovers"] = processes("p1-fresh-b-")
    assert RESULTS["fresh_group_b_leftovers"] == []
    assert all(r["worker_alive"] and r["script_running"] and r["worker_log_errors"] == 0 for r in runs), runs


def error_record(response):
    status, headers, body, _ = response
    return {"status": status, "code": body.get("code") if isinstance(body, dict) else None,
            "detail": body.get("detail") if isinstance(body, dict) else None,
            "request_id": body.get("request_id") if isinstance(body, dict) else None,
            "x_request_id": headers.get("X-Request-ID"), "x_lead_unavailable": headers.get("X-Lead-Unavailable")}


def make_readonly(directory):
    for root, dirs, files in os.walk(directory):
        for name in files:
            os.chmod(os.path.join(root, name), 0o400)
        os.chmod(root, 0o500)


def make_writable(directory):
    for root, dirs, files in os.walk(directory, topdown=True):
        os.chmod(root, 0o700)
        for name in files:
            os.chmod(os.path.join(root, name), 0o600)


def test_7_readonly_directory(tmp_path):
    result = {}
    # (a0) through start.sh: refused before anything starts.
    a0 = tmp_path / "ro-a0"
    a0.mkdir()
    (a0 / "logs").mkdir()
    make_readonly(a0)
    process, handle = start_script(a0, tmp_path / "a0.log")
    process.wait(30)
    stop_script(process, handle)
    result["a0_start_script"] = {"exit_code": process.returncode,
                                 "output": (tmp_path / "a0.log").read_text().strip().splitlines()[-1:]}
    make_writable(a0)
    # (a) uvicorn on an initialised directory that is read-only before start: degraded mode.
    a = tmp_path / "ro-a"
    Api(a, RELIABILITY_PORT, tmp_path / "a-init.log").stop()
    make_readonly(a)
    api = Api(a, RELIABILITY_PORT, tmp_path / "a.log")
    try:
        result["a_session"] = error_record(api.call("GET", "/api/session"))
        result["a_settings"] = error_record(api.call("GET", "/api/settings"))
    finally:
        api.stop()
        make_writable(a)
    # (b) read-only after start: writes fail with a Chinese message and a request id.
    b = tmp_path / "ro-b"
    api = Api(b, RELIABILITY_PORT, tmp_path / "b.log")
    try:
        assert api.call("POST", "/api/companies", {"name": "Before", "website": "https://before.example"})[0] in (200, 201)
        make_readonly(b)
        result["b_write"] = error_record(api.call("POST", "/api/companies",
                                                  {"name": "After", "website": "https://after.example"}))
        result["b_read"] = error_record(api.call("GET", "/api/settings"))
    finally:
        make_writable(b)
        api.stop()
    RESULTS["readonly"] = result
    assert result["a0_start_script"]["exit_code"] == 1
    assert result["a_session"]["status"] == 200 and result["a_session"]["x_lead_unavailable"] == "readonly_storage"
    assert result["a_settings"]["status"] == 503 and result["a_settings"]["code"] == "readonly_storage"
    assert result["b_write"]["status"] == 503 and result["b_write"]["code"] == "readonly_storage"
    for key in ("a_settings", "b_write"):
        assert result[key]["request_id"] and result[key]["request_id"] == result[key]["x_request_id"]


def test_8_disk_full_small_volume(tmp_path):
    image, mount = tmp_path / "full.dmg", tmp_path / "mnt"
    mount.mkdir()
    subprocess.run(["hdiutil", "create", "-size", "3m", "-fs", "HFS+", "-volname", "leadfull", str(image)],
                   check=True, capture_output=True)
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)],
                   check=True, capture_output=True)
    result = {"attempts": 0}
    api = None
    try:
        directory = mount / "data"
        api = Api(directory, RELIABILITY_PORT, tmp_path / "full-api.log")
        note = "x" * 3000
        for attempt in range(5000):
            response = api.call("POST", "/api/companies",
                                {"name": f"Full {attempt} {note[:150]}", "website": f"https://f{attempt}.example/{note}"})
            result["attempts"] = attempt + 1
            if response[0] >= 500:
                result["error"] = error_record(response)
                break
        log = directory / "logs" / "api.log"
        codes = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        storage = [line for line in codes if line.get("event") == "storage_error"]
        result["sqlite_codes"] = sorted({line.get("sqlite_code") for line in storage if line.get("sqlite_code")})
        result["errnos"] = sorted({line.get("errno") for line in storage if line.get("errno")})
    finally:
        if api is not None:
            api.stop()
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        image.unlink(missing_ok=True)
    RESULTS["disk_full"] = result
    assert result["error"]["status"] == 507 and result["error"]["code"] == "disk_full", result
    assert result["error"]["request_id"]


def snapshot_files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() or p.is_dir())


def dump_hash(path):
    import hashlib
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        return hashlib.sha256("\n".join(con.iterdump()).encode()).hexdigest()


def test_9_retention_first_confirmation_on_the_synthetic_library(api):
    root = api.root
    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    for index in range(12):
        path = backups / f"auto-pre-restore-20260901T0000{index:02d}-{index:08x}.sqlite3"
        path.write_bytes(b"b")
        os.utime(path, (time.time() - (20 - index) * 86400,) * 2)
    for index in range(3):
        (backups / f"backup-20260901T00000{index}-manual{index}.sqlite3").write_bytes(b"m")
    view = api.call("GET", "/api/retention")[2]
    before, before_hash = snapshot_files(root), dump_hash(root / "workbench.sqlite3")
    listed = {item["path"] for item in view["preview"]["items"]}
    status, _, confirmed, _ = api.call("POST", "/api/retention/confirm", {"plan_id": view["preview"]["plan_id"]})
    after = snapshot_files(root)
    removed = set(before) - set(after)
    removed_roots = {p for p in removed if p in listed}
    RESULTS["retention_e2e"] = {"listed": len(listed), "deleted": confirmed.get("deleted"),
                                "state": confirmed.get("state"), "removed_paths_total": len(removed),
                                "manual_backups_kept": sum(p.startswith("backups/backup-") for p in after),
                                "db_iterdump_unchanged": dump_hash(root / "workbench.sqlite3") == before_hash}
    assert status == 200 and confirmed["deleted"] == view["preview"]["total_files"] == 202
    assert removed_roots == listed
    assert all(any(p == item or p.startswith(item + "/") for item in listed) or p.startswith("runs/perfrun")
               for p in removed)
    assert RESULTS["retention_e2e"]["manual_backups_kept"] == 3 and RESULTS["retention_e2e"]["db_iterdump_unchanged"]


def screenshot(url, target, budget=6000):
    profile = tempfile.mkdtemp(prefix="p1-chrome-")
    try:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        "--no-proxy-server", f"--user-data-dir={profile}", "--window-size=1280,860",
                        f"--virtual-time-budget={budget}", f"--screenshot={target}", url],
                       check=True, capture_output=True, timeout=60)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


@pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome not installed")
def test_10_error_state_screenshots(tmp_path):
    folder = Path(os.environ.get("LEAD_PERF_OUT") or tmp_path)
    folder = folder if folder.is_dir() else folder.parent
    shots = {}
    api = Api(tmp_path / "shots", SHOT_PORT, tmp_path / "shots.log")
    try:
        screenshot(f"http://127.0.0.1:{SHOT_PORT}/?view=detail&company=missing-company", folder / "p1-error-not-found.png")
        shots["not_found"] = "p1-error-not-found.png"
    finally:
        api.stop()
    api = Api(tmp_path / "shots-internal", SHOT_PORT, tmp_path / "shots-internal.log", LEAD_TEST_FAULT="internal")
    try:
        screenshot(f"http://127.0.0.1:{SHOT_PORT}/?view=settings", folder / "p1-error-internal.png")
        shots["internal"] = "p1-error-internal.png"
    finally:
        api.stop()
    # Offline: the page loads from a static server whose /api/* drops the connection.
    server = subprocess.Popen([sys.executable, "-c", OFFLINE_SERVER, str(ROOT / "frontend" / "dist"), str(OFFLINE_PORT)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until(lambda: subprocess.run(["curl", "-s", "-o", "/dev/null", f"http://127.0.0.1:{OFFLINE_PORT}/"]).returncode == 0, 10)
        screenshot(f"http://127.0.0.1:{OFFLINE_PORT}/?view=companies", folder / "p1-error-offline.png")
        shots["offline"] = "p1-error-offline.png"
    finally:
        server.terminate()
        server.wait(10)
    RESULTS["screenshots"] = shots
    assert all((folder / name).stat().st_size > 10_000 for name in shots.values())


OFFLINE_SERVER = r"""
import http.server, socketserver, sys, functools
root, port = sys.argv[1], int(sys.argv[2])
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/'):
            self.close_connection = True
            self.connection.close()
            return
        if '?' in self.path or self.path == '/':
            self.path = '/index.html'
        return super().do_GET()
    def log_message(self, *args):
        pass
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(('127.0.0.1', port), functools.partial(Handler, directory=root)) as httpd:
    httpd.serve_forever()
"""
