"""Container health checks without curl (design §1.4).

``python -m leadworkbench.healthcheck api``    → GET http://127.0.0.1:$LEAD_PORT/healthz
``python -m leadworkbench.healthcheck worker`` → worker-heartbeat.json younger than 60 s
Exit 0 when healthy, 1 otherwise.
"""
from datetime import datetime, timezone
import json
import os
import sys
import urllib.request

from .serve import data_dir

HEARTBEAT_MAX_AGE = 60


def check_api(environ=None):
    env = os.environ if environ is None else environ
    port = env.get("LEAD_PORT") or "8000"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


def check_worker(environ=None, now=None):
    try:
        payload = json.loads((data_dir(environ) / "worker-heartbeat.json").read_text())
        seen = datetime.fromisoformat(payload["last_seen"])
    except (OSError, ValueError, KeyError, TypeError):
        return 1
    age = ((now or datetime.now(timezone.utc)) - seen).total_seconds()
    return 0 if 0 <= age < HEARTBEAT_MAX_AGE else 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["api"]:
        return check_api()
    if args == ["worker"]:
        return check_worker()
    print("用法：python -m leadworkbench.healthcheck api|worker", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
