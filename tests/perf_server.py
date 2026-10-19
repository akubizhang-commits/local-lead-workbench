"""Helpers for LEAD_PERF measurements: a real uvicorn API on a local test port (P1, design §14)."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in {"MAPS_SCRAPER_BIN", "LEAD_TEST_FAULT", "LEAD_PERF"}}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def until(predicate, timeout, interval=0.1):
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


class Api:
    """uvicorn leadworkbench.api:create_app on 127.0.0.1:<port> with its own data directory."""

    def __init__(self, data_dir, port, log_path, **env):
        self.port = port
        self.log = open(log_path, "w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "leadworkbench.api:create_app", "--factory", "--host", "127.0.0.1",
             "--port", str(port), "--no-access-log"],
            cwd=ROOT, env=clean_env(LEAD_DATA_DIR=data_dir, **env), stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True)
        self.token = None
        ready = until(lambda: self.call("GET", "/api/session")[0] == 200, 60, 0.2)
        if not ready:
            self.stop()
            raise RuntimeError(f"API on {port} did not start; see {log_path}")
        self.token = self.call("GET", "/api/session")[2]["token"]

    def call(self, method, path, body=None, timeout=60):
        """(status, headers, parsed body or bytes, elapsed ms)."""
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if self.token:
            headers["X-Local-Token"] = self.token
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                         headers=headers)
        start = time.perf_counter()
        try:
            with OPENER.open(request, timeout=timeout) as response:
                raw = response.read()
                status, response_headers = response.status, response.headers
        except urllib.error.HTTPError as exc:
            raw, status, response_headers = exc.read(), exc.code, exc.headers
        elapsed = (time.perf_counter() - start) * 1000
        try:
            parsed = json.loads(raw) if raw and "json" in (response_headers.get("content-type") or "") else raw
        except ValueError:
            parsed = raw
        return status, response_headers, parsed, elapsed

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(15)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.process.wait()
        self.log.close()


def stats(values):
    """Nearest-rank p50 / p95 for 20 samples: the 10th and 19th smallest."""
    ordered = sorted(values)
    rank = lambda q: ordered[max(0, math.ceil(round(q * len(ordered), 6)) - 1)]
    return {"n": len(ordered), "p50_ms": round(rank(0.5), 1), "p95_ms": round(rank(0.95), 1),
            "max_ms": round(ordered[-1], 1)}


def out_dir():
    """LEAD_PERF_OUT may name a directory or a file; files go next to it."""
    value = os.environ.get("LEAD_PERF_OUT")
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else path.parent


def save(name, payload):
    folder = out_dir()
    if folder is not None:
        (folder / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def processes(pattern):
    out = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if pattern in line and "ps -A" not in line]
