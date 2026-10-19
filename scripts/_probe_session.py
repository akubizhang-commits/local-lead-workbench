"""Readiness probe for start.sh: prints "<status> <X-Lead-Unavailable or ->", or "down"."""
import sys
import urllib.error
import urllib.request

port = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(f"http://127.0.0.1:{port}/api/session", timeout=2) as response:
        print(response.status, response.headers.get("X-Lead-Unavailable") or "-")
except urllib.error.HTTPError as exc:
    print(exc.code, exc.headers.get("X-Lead-Unavailable") or "-")
except Exception:
    print("down")
