"""Container entry point: ``python -m leadworkbench.serve`` (design §1.3).

Loads the configuration, holds ``{data}/api.lock`` for the process lifetime (one
API per data volume; lets ``admin restore`` prove the API is stopped) and runs
uvicorn. ``scripts/start.sh`` does not use this module.
"""
import fcntl
import os
from pathlib import Path
import sys

from . import config

ROOT = Path(__file__).resolve().parent.parent
API_LOCK_BUSY = "已有 API 进程在使用该数据目录"


def data_dir(environ=None):
    env = os.environ if environ is None else environ
    return Path(env.get("LEAD_DATA_DIR") or str(ROOT / "data")).expanduser().resolve()


def hold_api_lock(directory):
    """Non-blocking flock on {data}/api.lock; the returned handle must stay open."""
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "api.lock").open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def main():
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
    handle = hold_api_lock(data_dir())
    if handle is None:
        print(API_LOCK_BUSY, file=sys.stderr)
        raise SystemExit(2)
    import uvicorn
    try:
        uvicorn.run("leadworkbench.api:create_app", factory=True, host=cfg.bind, port=cfg.port,
                    access_log=False, server_header=False, proxy_headers=cfg.server,
                    forwarded_allow_ips="*" if cfg.server else None, timeout_keep_alive=5)
    finally:
        handle.close()


if __name__ == "__main__":
    main()
