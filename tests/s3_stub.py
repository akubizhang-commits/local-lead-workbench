"""Minimal path-style S3 stand-in for tests (AWS trial, design §5.4). Never talks to AWS.

    python tests/s3_stub.py --port 18854 --dir /tmp/objects [--deny <bucket>]

Supports ``PUT /<bucket>/<key>`` (reads Content-Length bytes, answers Expect: 100-continue,
returns 200 + ETag) and ``HEAD``; a denied bucket gets 403 AccessDenied; anything else 501.
"""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

DENIED = b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>'


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    root: Path = Path(".")
    deny: frozenset = frozenset()

    def log_message(self, *args):  # keep test output quiet
        pass

    def _target(self):
        parts = unquote(urlsplit(self.path).path).lstrip("/").split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1] or ".." in parts[1].split("/"):
            return None, None
        return parts[0], parts[1]

    def _reply(self, status, body=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def do_PUT(self):
        bucket, key = self._target()
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        if bucket is None:
            return self._reply(400)
        if bucket in self.deny:
            return self._reply(403, DENIED, {"Content-Type": "application/xml"})
        target = self.root / bucket / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        (target.parent / f"{target.name}.headers").write_text(
            "\n".join(f"{k}: {v}" for k, v in self.headers.items() if k.lower().startswith("x-amz-server-side")))
        self._reply(200, headers={"ETag": f'"{hashlib.md5(data).hexdigest()}"'})

    def do_HEAD(self):
        bucket, key = self._target()
        target = self.root / (bucket or "") / (key or "")
        if bucket is None or not target.is_file():
            return self._reply(404)
        self._reply(200, headers={"ETag": f'"{hashlib.md5(target.read_bytes()).hexdigest()}"'})

    def _unsupported(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._reply(501)

    do_GET = do_POST = do_DELETE = _unsupported


def start(port, directory, deny=()):
    """Run in a daemon thread; returns the server (call .shutdown() and .server_close())."""
    handler = type("StubHandler", (Handler,), {"root": Path(directory), "deny": frozenset(deny)})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18854)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--deny", action="append", default=[])
    args = parser.parse_args()
    handler = type("StubHandler", (Handler,), {"root": Path(args.dir), "deny": frozenset(args.deny)})
    ThreadingHTTPServer(("0.0.0.0", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
