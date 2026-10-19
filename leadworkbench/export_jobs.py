"""Background export jobs: API-side manager (P1-2, design §8.2–8.6).

Each job runs in its own Python child process (``leadworkbench.export_job``), so
building a large XLSX never competes with the API for the GIL. Job state is a
file (``exports/jobs/<id>.json``), not a database row: restoring a backup must not
replace it, and progress writes must not contend for the SQLite write lock.
Only one job may be queued or running at a time (decision D4).
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from .errors import Conflict, DomainError, ExportBusy, NotFound

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_EXPORT_DAYS = 7
STALE_SECONDS = 30
CANCEL_TERM_SECONDS = 5
SHUTDOWN_WAIT_SECONDS = 5
LIVE = {"queued", "running"}
NAMES = {"entries": "联系入口", "companies": "企业表"}
ASCII_NAMES = {"entries": "contact-entries", "companies": "leads"}
INTERRUPTED_CHILD = "导出中断（导出进程意外退出），请重新导出"
INTERRUPTED_RESTART = "导出中断（本地服务已重启），请重新导出"
STOPPED = "本地服务已停止，导出已取消"


def now():
    return datetime.now(timezone.utc).isoformat()


def exports_dir(data_dir) -> Path:
    return Path(data_dir) / "exports"


def jobs_dir(data_dir) -> Path:
    return exports_dir(data_dir) / "jobs"


def ensure_dirs(data_dir):
    for folder in (exports_dir(data_dir), jobs_dir(data_dir)):
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)


def job_path(data_dir, job_id) -> Path:
    return jobs_dir(data_dir) / f"{job_id}.json"


def part_path(data_dir, job_id) -> Path:
    return exports_dir(data_dir) / f"{job_id}.part"


def cancel_path(data_dir, job_id) -> Path:
    return jobs_dir(data_dir) / f"{job_id}.cancel"


def file_path(data_dir, job) -> Path:
    return exports_dir(data_dir) / f"{job['id']}.{job['format']}"


def export_days(data_dir) -> int:
    """Retention for finished export files (decision D1: 7 days by default, set in retention.json)."""
    from .retention import export_days as configured
    return configured(data_dir)


def expires_at(data_dir, finished_at) -> str:
    return (datetime.fromisoformat(finished_at) + timedelta(days=export_days(data_dir))).isoformat()


def read_job(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(data_dir, job):
    """Atomic 0600 write: temporary file, then rename."""
    path = job_path(data_dir, job["id"])
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(job, handle, ensure_ascii=False)
    os.replace(temporary, path)


def _age(stamp) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def validate_id(job_id):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise NotFound("导出任务不存在")
    return job_id


class ExportJobs:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()  # guards _procs and "check -> create"
        self._procs: dict[str, subprocess.Popen] = {}

    # -- state ---------------------------------------------------------
    def _load_all(self):
        jobs = []
        folder = jobs_dir(self.data_dir)
        for path in folder.glob("*.json") if folder.is_dir() else ():
            try:
                job = read_job(path)
                if JOB_ID.fullmatch(str(job.get("id", ""))):
                    jobs.append(job)
            except (OSError, ValueError):
                log.warning("unreadable export job %s", path.name, extra={"event": "export_job_corrupt"})
        return sorted(jobs, key=lambda job: job.get("created_at") or "", reverse=True)

    def _load(self, job_id):
        try:
            return read_job(job_path(self.data_dir, job_id))
        except FileNotFoundError:
            raise NotFound("导出任务不存在") from None
        except (OSError, ValueError):
            raise NotFound("导出任务不存在") from None

    def is_live(self, job) -> bool:
        if job.get("status") not in LIVE:
            return False
        proc = self._procs.get(job["id"])
        if proc is not None:
            return proc.poll() is None
        return _age(job.get("updated_at")) < STALE_SECONDS

    def _finish(self, job, status, error=None):
        job.update(status=status, phase="done", finished_at=now(), updated_at=now(), error=error)
        write_job(self.data_dir, job)
        part_path(self.data_dir, job["id"]).unlink(missing_ok=True)
        cancel_path(self.data_dir, job["id"]).unlink(missing_ok=True)

    def reap(self):
        """Mark dead queued/running jobs as interrupted. Call with _lock held."""
        for job in self._load_all():
            if job.get("status") in LIVE and not self.is_live(job):
                # The child may have written its final state between the read
                # above and poll(); re-read before declaring it interrupted.
                try:
                    job = self._load(job["id"])
                except NotFound:
                    continue
                if job.get("status") not in LIVE:
                    continue
                message = INTERRUPTED_CHILD if job["id"] in self._procs else INTERRUPTED_RESTART
                log.warning("export job interrupted", extra={"event": "export_interrupted", "job_id": job["id"]})
                self._finish(job, "failed", {"code": "interrupted", "message": message})
        for job_id, proc in list(self._procs.items()):
            if proc.poll() is not None:
                del self._procs[job_id]

    # -- API -----------------------------------------------------------
    def list(self, limit=5):
        with self._lock:
            self.reap()
            return self._load_all()[:limit]

    def get(self, job_id):
        validate_id(job_id)
        with self._lock:
            self.reap()
            return self._load(job_id)

    def any_live(self) -> bool:
        with self._lock:
            self.reap()
            return any(self.is_live(job) for job in self._load_all())

    def create(self, spec, request_id=""):
        with self._lock:
            self.reap()
            live = [job for job in self._load_all() if self.is_live(job)]
            if live:
                raise ExportBusy("已有导出正在进行，请等待完成", job_id=live[0]["id"])
            ensure_dirs(self.data_dir)
            job_id = uuid.uuid4().hex
            stamp = now()
            name = NAMES[spec["layout"]]
            job = dict(id=job_id, status="queued", phase="queued", format=spec["format"], layout=spec["layout"],
                       filters=spec["filters"], label=f"{name} {spec['format'].upper()}", processed=0, total=None,
                       rows=0, file_name=f"{name}-{stamp[:10]}.{spec['format']}", size_bytes=None, pid=None,
                       created_at=stamp, started_at=None, updated_at=stamp, finished_at=None, expires_at=None,
                       error=None, request_id=request_id)
            write_job(self.data_dir, job)
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "leadworkbench.export_job", "--data-dir", str(self.data_dir),
                     "--job", job_id], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True)
            except OSError:
                self._finish(job, "failed", {"code": "internal", "message": "导出进程无法启动，请重试"})
                raise
            self._procs[job_id] = proc
            log.info("export job created", extra={"event": "export_created", "job_id": job_id})
            return {**job, "pid": proc.pid}

    def cancel(self, job_id):
        validate_id(job_id)
        with self._lock:
            self.reap()
            job = self._load(job_id)
            if not self.is_live(job):
                raise Conflict("导出已结束，无法取消")
            cancel_path(self.data_dir, job_id).touch(mode=0o600)
        timer = threading.Timer(CANCEL_TERM_SECONDS, self.term_if_alive, args=(job_id,))
        timer.daemon = True
        timer.start()
        return job

    def term_if_alive(self, job_id):
        with self._lock:
            proc = self._procs.get(job_id)
            if proc is not None and proc.poll() is None:
                proc.terminate()

    def file(self, job_id):
        validate_id(job_id)
        with self._lock:
            self.reap()
            try:
                job = read_job(job_path(self.data_dir, job_id))
            except (OSError, ValueError):
                job = None
        expired = DomainError("文件已按保留策略清理，请重新导出", code="export_expired", status=410)
        if job is None:
            raise expired
        if job.get("status") != "succeeded":
            raise Conflict("导出尚未完成")
        path = file_path(self.data_dir, job)
        if not path.is_file():
            raise expired
        return path, job

    def shutdown(self, timeout=SHUTDOWN_WAIT_SECONDS):
        """API stop: SIGTERM running children, wait up to 5 s, then mark them cancelled."""
        with self._lock:
            procs = {job_id: proc for job_id, proc in self._procs.items() if proc.poll() is None}
            for proc in procs.values():
                proc.terminate()
            deadline = time.monotonic() + timeout
            for proc in procs.values():
                try:
                    proc.wait(max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for job_id in procs:
                try:
                    job = self._load(job_id)
                except NotFound:
                    continue
                if job.get("status") in LIVE:
                    self._finish(job, "cancelled", {"code": "stopped", "message": STOPPED})
            self._procs.clear()


def content_disposition(job):
    from urllib.parse import quote
    fallback = f"{ASCII_NAMES.get(job.get('layout'), 'export')}.{job['format']}"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(job['file_name'])}"
