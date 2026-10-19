"""Bounded local gosom adapter. Raw evidence never enters the business database here."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import logging
import signal
import sys
import time
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
BUSY_MESSAGE = "上一个地图查询的进程尚未退出，本查询稍后自动重试"
REGISTRY_MESSAGE = "采集程序异常：无法写入运行记录（磁盘空间不足或目录不可写）"
BINARY = ROOT / "tools/bin/google-maps-scraper"


# Fixed texts for an early stop (reason is only cancelled or timeout here).
STOPPED_MESSAGES = {
    "cancelled": "已暂停：采集按用户操作停止，已取得的结果会保留",
    "timeout": "采集程序异常：单次查询达到 180 秒时间上限后停止，结果不完整",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def listing_aliases(source_key, url, place_id=""):
    """Stable listing identifiers, never a shared search query or brand domain."""
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    aliases = {source_key}
    for value in [place_id, *params.get("query_place_id", []), *params.get("place_id", [])]:
        if value:
            aliases.add("google_maps:place_id:" + value)
    for value in [*params.get("cid", []), *params.get("ludocid", [])]:
        if value.isdigit() and 0 < int(value) < 2**64:
            aliases.add(f"google_maps:cid:{int(value)}")
    match = re.search(r"0x[0-9a-fA-F]+:(0x[0-9a-fA-F]+)", unquote(url))
    if match and 0 < int(match[1], 16) < 2**64:
        aliases.add(f"google_maps:cid:{int(match[1], 16)}")
    # A place URL can bridge a legacy URL-only identity to a newly exposed ID.
    # Generic /maps/search URLs are shared by results and are not identities.
    if "/maps/place/" in parts.path or any(k in params for k in ("query_place_id", "place_id", "cid", "ludocid")):
        aliases.add("google_maps:url:" + hashlib.sha256(url.split("#", 1)[0].encode()).hexdigest())
    return sorted(aliases)


def _record(row, observed_at):
    if not isinstance(row, dict):
        raise ValueError("JSONL record must be an object")
    url = _text(row.get("link"))
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "google.com" or host.endswith(".google.com") or host == "maps.app.goo.gl"
    ) or (host != "maps.app.goo.gl" and not parsed.path.startswith("/maps")):
        raise ValueError("missing or invalid Google Maps listing link")
    name = _text(row.get("title"))
    if not name:
        raise ValueError("missing title")
    # Prefer CID across decimal/hex data_id and URL representations. Never use input_id:
    # that identifies the query, not the listing. Never merge by name/address alone.
    cid = row.get("cid")
    if isinstance(cid, bool):
        cid = None
    cid = str(cid).strip() if isinstance(cid, (str, int)) else ""
    params = parse_qs(parsed.query)
    candidates = [cid, *(params.get("cid", [])), *(params.get("ludocid", []))]
    data_id = _text(row.get("data_id"))
    match = re.search(r"0x[0-9a-fA-F]+:(0x[0-9a-fA-F]+)", data_id or unquote(url))
    if match:
        candidates.append(match.group(1))
    key = None
    for candidate in candidates:
        if re.fullmatch(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)", candidate):
            number = int(candidate, 16 if candidate.lower().startswith("0x") else 10)
            if 0 < number < 2**64:
                key = f"google_maps:cid:{number}"
                break
    place_id = _text(row.get("place_id")) or next(iter(params.get("query_place_id", [])), "")
    if not key and place_id:
        key = f"google_maps:place_id:{place_id}"
    if not key:
        # Keep query parameters: dropping them would collapse distinct ?q= listings.
        key = "google_maps:url:" + hashlib.sha256(url.split("#", 1)[0].encode()).hexdigest()
    def coordinate(field, bound):
        value = row.get(field)
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError(f"invalid {field}")
        value = float(value)
        if not math.isfinite(value) or abs(value) > bound:
            raise ValueError(f"invalid {field}")
        return value
    status_raw = _text(row.get("status"))
    status = {"permanently closed": "permanently_closed", "temporarily closed": "temporarily_closed",
              "永久停业": "permanently_closed", "永久歇业": "permanently_closed", "暂时停业": "temporarily_closed",
              "temporarily_closed": "temporarily_closed", "closed_permanently": "permanently_closed",
              "operational": "operational"}.get(status_raw.casefold(), "")
    if re.match(r"^open(?:\s|$)", status_raw, re.I):
        status = "operational"
    aliases = listing_aliases(key, url, place_id)
    # Keep the established record shape unless the payload supplies an identity
    # that cannot be reconstructed from its primary key and URL.
    extra = {"source_aliases": aliases} if aliases != listing_aliases(key, url) else {}
    return dict(operating_status=status, status_evidence=status_raw, source_key=key, **extra,
                source_url=url, name=name,
                address=_text(row.get("address")), phone=_text(row.get("phone")),
                website=_text(row.get("web_site") or row.get("website")), latitude=coordinate("latitude", 90),
                longitude=coordinate("longitude", 180), category=_text(row.get("category")),
                observed_at=observed_at)


async def _terminate(process):
    # A child can outlive an already-exited group leader. Always signal the group.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            await asyncio.sleep(0.2)
    await process.wait()


CRASH_TERMS = ("playwright: target closed", "target page, context or browser has been closed",
               "browser has been closed", "browser has disconnected", "target crashed", "page crashed")
# Top-level "error" field near the start of an oversized line (browser logs follow it).
OVERSIZED_ERROR = re.compile(rb'^\s*\{[^\n]{0,4096}?"error"\s*:\s*"([^"\n]{0,512})', re.I)


def _classify_error(error: str) -> str | None:
    if any(term in error for term in CRASH_TERMS):
        return "browser_closed"
    if "inactivity timeout" in error:
        return "inactivity_timeout"
    return None


def _stderr_stop_reason(path):
    """Recognize audited worker-stop errors regardless of JSON log severity.

    Read only the adapter's own stderr, without copying browser command lines
    into warnings. Oversized lines (crash errors embed full browser logs) are
    classified from their first 8 KB only; non-JSON lines and successful
    progress events are ignored.
    """
    with path.open("rb") as stream:
        while line := stream.readline(65_537):
            if len(line) > 65_536:
                prefix = line[:8192]
                while line and not line.endswith(b"\n"):
                    line = stream.readline(65_537)
                match = OVERSIZED_ERROR.match(prefix)
                found = _classify_error(match.group(1).decode(errors="replace").casefold()) if match else None
                if found:
                    return found
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            error = _text(event.get("error")).casefold() if isinstance(event, dict) else ""
            found = _classify_error(error)
            if found:
                return found
            if error and event.get("message") == "job finished" and event.get("status") == "failed":
                # The CLI can exit zero after failed seed/detail jobs. With its
                # internal retries disabled, the durable queue must see the failure.
                if any(term in error for term in ("timeout", "timed out", "deadline exceeded")):
                    return "timeout"
                return "adapter_error"
    return None


class RestrictionLog:
    """Incremental adapter-log reader; never scan listing titles or query fields."""
    def __init__(self, path):
        self.path, self.offset, self.pending = path, 0, b''
        self.discarding = False

    def _lines(self, final):
        if not self.path.exists():
            return
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            start = self.offset
            # Live polls have a byte budget. After exit, scan to EOF with bounded
            # lines so a late restriction cannot be mistaken for normal exhaustion.
            while final or self.offset - start < 262144:
                part = stream.readline(65537)
                self.offset = stream.tell()
                if not part:
                    if final and self.pending:
                        line, self.pending = self.pending, b''
                        yield line
                    break
                if not self.discarding:
                    self.pending += part
                    if len(self.pending) > 65536:
                        self.pending, self.discarding = b'', True
                if part.endswith(b'\n'):
                    line, self.pending = self.pending, b''
                    discard, self.discarding = self.discarding, False
                    if not discard:
                        yield line

    def poll(self, final=False):
        for line in self._lines(final):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict):
                continue
            # Error fields may appear on info-level worker-stop events.
            text = _text(event.get('error'))
            if event.get('level') in {'error', 'warn', 'warning', 'fatal'}:
                text += ' ' + _text(event.get('message'))
            text = text.casefold()
            # Negate only the stated check, not other errors in the same event.
            text = re.sub(r'\bno captcha\b|\bcaptcha (?:false\b|not (?:detected|present|found|required)\b)'
                          r'|\brate limit(?:ed)?[:= ]+false\b', '', text)
            if any(term in text for term in ('captcha', 'unusual traffic', '验证码')):
                return 'captcha'
            if re.search(r'(?:http|status(?: code)?)[ :=]+429\b|too many requests|rate limit', text):
                return 'rate_limited'
            if re.search(r'(?:http|status(?: code)?)[ :=]+403\b|access denied', text):
                return 'access_denied'
        return None


async def discover_maps(query: str, output_dir: Path, *, limit: int = 20,
                        timeout_seconds: int = 300,
                        should_stop: Callable[[], bool] | None = None,
                        geo: tuple | list | None = None, zoom: int = 15,
                        depth: int = 1, data_dir: Path | None = None) -> dict:
    """Bound one source job. Market callers use limit=120, timeout=180, depth=10.

    saturated means the internal cap was reached, so the worker must subdivide
    or mark an unresolved leaf. exhausted means this CLI job finished below cap;
    it does not establish full geographic coverage or an explicit Maps end marker.
    """
    if not isinstance(query, str) or not query.strip() or any(c in query for c in "\r\n") or "#!#" in query:
        raise ValueError("query must be one nonempty plain input line")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    if isinstance(zoom, bool) or not isinstance(zoom, int) or not 1 <= zoom <= 21:
        raise ValueError("zoom must be an integer between 1 and 21")
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise ValueError("depth must be a positive integer")
    if geo is not None:
        if not isinstance(geo, (tuple, list)) or len(geo) != 2:
            raise ValueError("geo must contain latitude and longitude")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or abs(v) > bound for v, bound in zip(geo, (90, 180))):
            raise ValueError("geo coordinates must be finite and in range")
        geo = [float(v) for v in geo]
    run_dir = Path(output_dir).resolve() / ("maps-" + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    raw = run_dir / "results.jsonl"
    input_path = run_dir / "query.txt"
    input_path.write_text(query + "\n", encoding="utf-8")
    binary = Path(os.environ.get("MAPS_SCRAPER_BIN", str(BINARY))).expanduser().resolve()
    command = [str(binary), "-input", str(input_path), "-results", str(raw),
               "-json", "-c", "1", "-depth", str(depth), "-zoom", str(zoom),
               "-email=false", "-extra-reviews=false",
               "-exit-on-inactivity", "0"]
    if geo is not None:
        command.extend(["-geo", ",".join(str(v) for v in geo)])
    metadata = dict(query=query, started_at=_now(), command=command, binary=str(binary),
                    limit=limit, timeout_seconds=timeout_seconds, telemetry=False,
                    output_dir=str(run_dir), returncode=None, geo=geo, zoom=zoom, depth=depth)
    manifest = binary.with_name("google-maps-scraper.build.json")
    if manifest.is_file():
        try:
            metadata["build"] = json.loads(manifest.read_text())
        except (ValueError, OSError):
            metadata["build"] = "unreadable manifest"
    records, warnings, seen = [], [], set()
    def finish(status, reason):
        saturated = len(records) >= limit
        exhausted = status == "completed" and reason == "finished" and not saturated
        result = dict(status=status, records=records, warnings=warnings, stop_reason=reason,
                      saturated=saturated, exhausted=exhausted)
        metadata.update(finished_at=_now(), status=status, stop_reason=reason, records_count=len(records), warnings=warnings)
        metadata.update(saturated=saturated, exhausted=exhausted)
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (run_dir / "normalized.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    if not binary.is_file() or not os.access(binary, os.X_OK):
        warnings.append("采集程序异常：未找到可执行的地图采集程序，请运行 scripts/install_maps.sh")
        return finish("blocked", "missing_binary")
    if should_stop and should_stop():
        return finish("partial", "cancelled")
    # Dedicated Playwright installation; browser creates a fresh context.
    # Do not alter the user's HOME or forward service credentials.
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_DRIVER_PATH"
    }}
    for folder in ("home", "tmp"):
        (run_dir / folder).mkdir()
    env.update(DISABLE_TELEMETRY="1", TMPDIR=str(run_dir / "tmp"))
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "tools/bin/playwright-browsers"))
    env.setdefault("PLAYWRIGHT_DRIVER_PATH", str(ROOT / "tools/bin/playwright-driver"))
    offset, pending, invalid = 0, b"", 0
    def ingest(final=False):
        nonlocal offset, pending, invalid
        if not raw.exists():
            return
        with raw.open("rb") as stream:
            stream.seek(offset)
            pending += stream.read()
            offset = stream.tell()
        lines = pending.split(b"\n")
        pending = lines.pop()
        if final and pending:
            lines.append(pending)
            pending = b""
        for line in lines:
            if not line.strip() or len(records) >= limit:
                continue
            try:
                record = _record(json.loads(line), _now())
            except (ValueError, TypeError, OverflowError):
                invalid += 1
                continue
            if record["source_key"] not in seen:
                records.append(record)
                seen.add(record["source_key"])
    process = None
    cleanup_task = None
    # The guard owns maps.lock and the registry; without a data directory
    # (direct adapter calls) the registry lives next to the run directory.
    from .. import maps_guard
    registry = maps_guard.registry_dir(data_dir) if data_dir is not None else run_dir
    async def cleanup():
        nonlocal cleanup_task
        if process is not None:
            # Reuse one shielded cleanup even if the caller is cancelled while
            # waiting. Never signal a successfully cleaned/reaped group again.
            if cleanup_task is None:
                cleanup_task = asyncio.create_task(_terminate(process))
            await asyncio.shield(cleanup_task)
            # A SIGKILLed guard cannot remove its own registry entry.
            (registry / f"{process.pid}.json").unlink(missing_ok=True)
    async def finish_exception(status, stop_reason):
        try:
            await cleanup()
        except Exception as exc:
            warnings.append(f"采集程序异常：Process cleanup failed: {str(exc).strip() or type(exc).__name__}")
        finally:
            if process is not None:
                metadata["returncode"] = process.returncode
            try:
                ingest(final=True)
            except Exception as exc:
                warnings.append(f"采集程序异常：Final output read failed: {str(exc).strip() or type(exc).__name__}")
            finish(status, stop_reason)

    reason = None
    source_reason = None
    restriction_logs = [RestrictionLog(run_dir / name) for name in ('stderr.log', 'stdout.log')]
    last_log_scan = -1.0
    start = time.monotonic()
    try:
        with (run_dir / "stderr.log").open("wb") as stderr, (run_dir / "stdout.log").open("wb") as stdout:
            try:
                # The guard leads the new session; the scraper shares its group, so
                # _terminate's killpg(process.pid) still ends everything (§9.3).
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "leadworkbench.maps_guard", "--registry-dir", str(registry),
                    "--run-dir", str(run_dir), "--", *command, cwd=run_dir, env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=stdout, stderr=stderr, start_new_session=True)
                log.info("maps query started", extra={
                    "event": "maps_query", "query_len": len(query),
                    "query_sha8": hashlib.sha256(query.encode()).hexdigest()[:8], "guard_pid": process.pid})
            except OSError as exc:
                warnings.append(str(exc).strip() or type(exc).__name__)
                return finish("blocked", "launch_failed")
            while True:
                ingest()
                if time.monotonic() - last_log_scan >= 1:
                    last_log_scan = time.monotonic()
                    reason = next((found for reader in restriction_logs if (found := reader.poll())), None)
                    if reason:
                        break
                if process.returncode is not None:
                    break
                if should_stop and should_stop():
                    reason = "cancelled"
                elif time.monotonic() - start >= timeout_seconds:
                    reason = "timeout"
                elif len(records) >= limit:
                    reason = "company_limit"
                if reason:
                    break
                await asyncio.sleep(0.05)
            await cleanup()
            ingest(final=True)
            metadata["returncode"] = process.returncode
        # Catch restriction errors also on fast/nonzero exits and preserve records.
        for reader in restriction_logs:
            found = reader.poll(final=True)
            if found:
                reason = found
        # Deliberate cancellation/timeout/cap termination can itself close the
        # browser. Only reinterpret an otherwise natural, zero-exit completion.
        if reason is None and process.returncode == 0:
            source_reason = _stderr_stop_reason(run_dir / "stderr.log")
    except asyncio.CancelledError:
        await finish_exception("partial", "cancelled")
        raise
    except Exception as exc:
        warnings.append(f"采集程序异常：Maps adapter failed: {str(exc).strip() or type(exc).__name__}")
        await finish_exception("failed", "adapter_error")
        raise
    if reason is None and process.returncode == maps_guard.BUSY_EXIT and (run_dir / "guard.json").exists():
        warnings.append(BUSY_MESSAGE)
        log.warning("maps lock busy", extra={"event": "maps_busy"})
        return finish("partial", "maps_busy")
    if reason is None and process.returncode == maps_guard.REGISTRY_EXIT:
        warnings.append(REGISTRY_MESSAGE)
        return finish("blocked", "launch_failed")
    if reason in {'captcha', 'rate_limited', 'access_denied'}:
        warnings.append({'captcha':'检测到验证码或异常流量，停止地图访问',
                         'rate_limited':'平台限制访问频率，停止地图访问',
                         'access_denied':'平台拒绝访问，停止地图访问'}[reason])
        return finish('blocked', reason)
    if invalid:
        warnings.append(f"采集程序异常：跳过 {invalid} 条无法解析的结果")
    if reason == "company_limit":
        return finish("partial" if invalid else "completed", reason)
    if reason:
        warnings.append(STOPPED_MESSAGES.get(reason, "采集程序异常：采集提前结束，结果不完整"))
        return finish("partial" if records or reason == "cancelled" else "blocked", reason)
    if process.returncode != 0:
        warnings.append(f"采集程序异常：采集程序异常退出（退出码 {process.returncode}），详见运行目录 stderr.log")
        return finish("partial" if records else "failed", "nonzero_exit")
    if invalid:
        return finish("partial" if records else "failed", "invalid_output")
    if source_reason:
        warnings.append({
            "browser_closed": "采集程序异常：地图页面被意外关闭，本次未完成",
            "inactivity_timeout": "采集程序异常：采集程序长时间无响应后停止，结果不完整",
            "timeout": "采集程序异常：内部查询超时，结果不完整",
            "adapter_error": "采集程序异常：采集程序报告查询失败，结果不完整",
        }[source_reason])
        return finish("partial" if records else "blocked", source_reason)
    if len(records) >= limit:
        return finish("completed", "company_limit")
    if not records:
        warnings.append("没有找到结果：采集程序正常结束，但没有返回可用的企业；这不代表该地区一定没有此类企业")
        return finish("blocked", "empty_output")
    return finish("completed", "finished")
