"""Export job child process: ``python -m leadworkbench.export_job --data-dir D --job ID``.

Started by the API (export_jobs.ExportJobs). Filters come from the job JSON, never
the command line (visible in ps). SIGINT is ignored: start.sh runs the API in the
background, so stopping is the API lifespan's SIGTERM, handled as "cancelled".
"""
from __future__ import annotations
import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time

from . import export_jobs as jobs

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 2.0
PROGRESS_SECONDS = 0.5
FAILURE_MESSAGES = {
    "disk_full": "磁盘空间不足，导出未完成。请清理磁盘后重新导出。",
    "readonly_storage": "数据目录不可写，导出未完成。",
}


class JobState:
    """The job JSON, shared by the main thread, the heartbeat thread and the SIGTERM handler."""

    def __init__(self, data_dir, job_id):
        self.data_dir = Path(data_dir)
        self.lock = threading.RLock()
        self.job = jobs.read_job(jobs.job_path(data_dir, job_id))
        self.part = jobs.part_path(data_dir, job_id)
        self.cancel_file = jobs.cancel_path(data_dir, job_id)
        self._written_at = 0.0
        self._phase = None

    def update(self, **values):
        with self.lock:
            self.job.update(values, updated_at=jobs.now())
            jobs.write_job(self.data_dir, self.job)
            self._written_at = time.monotonic()

    def heartbeat(self):
        while True:
            time.sleep(HEARTBEAT_SECONDS)
            with self.lock:
                if self.job.get("status") not in jobs.LIVE:
                    return
                self.update()

    def progress(self, phase, **values):
        with self.lock:
            changed = phase != self._phase
            self._phase = phase
            self.job.update(phase=phase, **values)
            if changed or time.monotonic() - self._written_at >= PROGRESS_SECONDS:
                self.update()

    def cancelled(self) -> bool:
        return self.cancel_file.exists()

    def finish(self, status, error=None, **values):
        with self.lock:
            self.part.unlink(missing_ok=True)
            self.cancel_file.unlink(missing_ok=True)
            self.update(status=status, phase="done", finished_at=jobs.now(), error=error, **values)

    def terminate(self, *_):
        # API stop (SIGTERM): no half-written file, job marked cancelled.
        with self.lock:
            if self.job.get("status") in jobs.LIVE:
                # SIGTERM after a user cancel (the API's 5 s fallback) is a plain cancel.
                stopped = None if self.cancelled() else {"code": "stopped", "message": jobs.STOPPED}
                self.finish("cancelled", stopped)
            os._exit(0)


def _test_faults(state):
    """LEAD_TEST_FAULT hooks for the export tests (process boundary: no monkeypatching)."""
    from .errors import test_fault as fault
    progress, should_stop = state.progress, state.cancelled
    if fault("export_slow") or fault("export_ignore_cancel"):
        def progress(phase, _inner=progress, **values):
            time.sleep(0.2)
            _inner(phase, **values)
    if fault("export_ignore_cancel"):
        def should_stop():
            return False
    if fault("export_disk_full"):
        def progress(phase, _inner=progress, **values):
            if phase == "writing":
                import errno
                raise OSError(errno.ENOSPC, "No space left on device")
            _inner(phase, **values)
    if fault("export_save_slow"):
        from openpyxl import Workbook
        original = Workbook.save
        def save(self, filename):
            time.sleep(5)
            return original(self, filename)
        Workbook.save = save
    return progress, should_stop


def run(data_dir, job_id) -> int:
    from .db import Store
    from .errors import classify_storage_error
    from .export import ExportCancelled, export_to_file
    state = JobState(data_dir, job_id)
    progress, should_stop = _test_faults(state)
    signal.signal(signal.SIGTERM, state.terminate)
    threading.Thread(target=state.heartbeat, daemon=True, name="export-heartbeat").start()
    job = state.job
    try:
        store = Store(data_dir, migrate=False)
        state.update(status="running", phase="reading", started_at=jobs.now(), pid=os.getpid())
        fd = os.open(state.part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        result = export_to_file(store, state.part, job["format"], job["layout"], job.get("filters") or {},
                                progress=progress, should_stop=should_stop)
        if should_stop():  # cancelled during the final, uninterruptible save
            raise ExportCancelled()
        final = jobs.file_path(data_dir, job)
        os.replace(state.part, final)
        final.chmod(0o600)
        finished = jobs.now()
        with state.lock:
            state.cancel_file.unlink(missing_ok=True)
            state.update(status="succeeded", phase="done", finished_at=finished, size_bytes=final.stat().st_size,
                         expires_at=jobs.expires_at(data_dir, finished), processed=result["total"],
                         total=result["total"], rows=result["rows"])
        log.info("export job finished", extra={"event": "export_finished", "job_id": job_id})
        return 0
    except ExportCancelled:
        state.finish("cancelled")
        return 0
    except Exception as exc:
        classified = classify_storage_error(exc, data_dir)
        code = classified.code if classified is not None and classified.code in FAILURE_MESSAGES else "internal"
        message = FAILURE_MESSAGES.get(code, f"导出失败，请重试。如果反复出现，请导出诊断包（作业编号 {job_id}）。")
        log.exception("export job failed", extra={"event": "export_failed", "job_id": job_id, "code": code})
        state.finish("failed", {"code": code, "message": message})
        return 1


def main(argv=None) -> int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    parser = argparse.ArgumentParser(prog="export_job")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    jobs.validate_id(args.job)
    return run(Path(args.data_dir), args.job)


if __name__ == "__main__":
    sys.exit(main())
