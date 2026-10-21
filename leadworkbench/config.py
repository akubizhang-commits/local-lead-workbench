"""Deployment settings from environment variables (AWS trial, design §1.1–1.2).

``load()`` is the single entry point for the API, worker, admin CLI, serve.py and
healthcheck.py. Errors raise ``ConfigError`` whose message lists the offending
variable names only (never their values, which might be mis-pasted secrets).
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
import ipaddress
import os
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
HOSTNAME = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PREFIX = re.compile(r"^[A-Za-z0-9!_.*'()/-]{0,200}$")
REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
LOOPBACK_ONLY = "未开启登录时只能监听本机地址（LEAD_BIND / LEAD_ALLOWED_HOSTS）"
SERVER_NEEDS_AUTH = "服务器模式必须开启登录（LEAD_AUTH）"


class ConfigError(ValueError):
    """Fatal configuration error; the message names variables, never values."""


@dataclass(frozen=True)
class Settings:
    mode: str = "local"
    auth: bool = False
    server: bool = False
    bind: str = "127.0.0.1"
    port: int = 18765
    public_origin: str = ""
    allowed_hosts: frozenset = LOOPBACK_NAMES
    session_idle: timedelta = timedelta(hours=24)
    session_max: timedelta = timedelta(days=7)
    backup_daily: bool = False
    backup_time: str = "03:30"
    timezone: str = "Asia/Shanghai"
    s3_bucket: str = ""
    s3_prefix: str = "leadworkbench/"
    s3_region: str = ""
    s3_endpoint: str = ""
    cookie_name: str = "lw_session"
    cookie_secure: bool = False

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def valid_host(value):
    """IPv4/IPv6 literal or a 1–253 character host name (no port)."""
    if _is_ip(value):
        return True
    return bool(HOSTNAME.match(value)) and ".." not in value and not value.startswith((".", "-"))


def _loopback_host(value):
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _origin(value, insecure_ok):
    """Normalised scheme://host[:port] or None."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("https", "http") or (parts.scheme == "http" and not insecure_ok):
        return None
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    host = parts.hostname or ""
    if not host or not valid_host(host):
        return None
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")


def _endpoint(value):
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and bool(parts.hostname) and port is not None
            and parts.path in ("", "/") and not parts.query and not parts.username)


def _int(value, low, high):
    if not re.fullmatch(r"\d{1,6}", value or ""):
        return None
    number = int(value)
    return number if low <= number <= high else None


def load(environ=None) -> Settings:
    env = os.environ if environ is None else environ
    get = lambda name, default="": (env.get(name) or default).strip()  # noqa: E731
    bad: list[str] = []
    notes: list[str] = []

    mode = get("LEAD_MODE", "local")
    if mode not in ("local", "server"):
        bad.append("LEAD_MODE")
        mode = "local"
    server = mode == "server"
    auth_raw = get("LEAD_AUTH", "on" if server else "off")
    if auth_raw not in ("on", "off"):
        bad.append("LEAD_AUTH")
        auth_raw = "on" if server else "off"
    auth = auth_raw == "on"
    if server and not auth:
        notes.append(SERVER_NEEDS_AUTH)
    insecure_ok = get("LEAD_ALLOW_INSECURE_HTTP") == "1"

    origin_raw = get("LEAD_PUBLIC_ORIGIN")
    public_origin = ""
    if origin_raw:
        public_origin = _origin(origin_raw, insecure_ok) or ""
        if not public_origin:
            bad.append("LEAD_PUBLIC_ORIGIN")
    elif server:
        bad.append("LEAD_PUBLIC_ORIGIN")

    hosts_raw = get("LEAD_ALLOWED_HOSTS")
    configured = [h.strip() for h in hosts_raw.split(",") if h.strip()] if hosts_raw else []
    if hosts_raw and (not configured or len(configured) > 10 or any(h == "*" or not valid_host(h) for h in configured)):
        bad.append("LEAD_ALLOWED_HOSTS")
        configured = []

    bind = get("LEAD_BIND", "127.0.0.1")
    if not _is_ip(bind):
        bad.append("LEAD_BIND")
        bind = "127.0.0.1"
    port = _int(get("LEAD_PORT", "8000" if server else "18765"), 1024, 65535)
    if port is None:
        bad.append("LEAD_PORT")
        port = 8000 if server else 18765

    if not auth and (not ipaddress.ip_address(bind).is_loopback or any(not _loopback_host(h) for h in configured)):
        notes.append(LOOPBACK_ONLY)

    idle_hours, max_days = 24, 7
    if auth:
        idle_hours = _int(get("LEAD_SESSION_IDLE_HOURS", "24"), 1, 720)
        max_days = _int(get("LEAD_SESSION_MAX_DAYS", "7"), 1, 90)
        if idle_hours is None:
            bad.append("LEAD_SESSION_IDLE_HOURS")
            idle_hours = 24
        if max_days is None or max_days * 24 < idle_hours:
            bad.append("LEAD_SESSION_MAX_DAYS")
            max_days = 7

    daily_raw = get("LEAD_BACKUP_DAILY", "on" if server else "off")
    if daily_raw not in ("on", "off"):
        bad.append("LEAD_BACKUP_DAILY")
        daily_raw = "off"
    backup_daily = daily_raw == "on"
    backup_time, timezone = "03:30", "Asia/Shanghai"
    bucket, prefix, region, endpoint = "", "leadworkbench/", "", ""
    if backup_daily:
        backup_time = get("LEAD_BACKUP_TIME", "03:30")
        if not CLOCK.match(backup_time):
            bad.append("LEAD_BACKUP_TIME")
        timezone = get("LEAD_TIMEZONE", "Asia/Shanghai")
        try:
            ZoneInfo(timezone)
        except Exception:  # ZoneInfoNotFoundError, ValueError for odd keys
            bad.append("LEAD_TIMEZONE")
            timezone = "Asia/Shanghai"
        bucket = get("LEAD_BACKUP_S3_BUCKET")
        if bucket and (not BUCKET.match(bucket) or ".." in bucket or _is_ip(bucket)):
            bad.append("LEAD_BACKUP_S3_BUCKET")
        prefix = env.get("LEAD_BACKUP_S3_PREFIX", "leadworkbench/").strip()
        if not PREFIX.match(prefix) or prefix.startswith("/") or ".." in prefix:
            bad.append("LEAD_BACKUP_S3_PREFIX")
        elif prefix and not prefix.endswith("/"):
            prefix += "/"
        region = get("LEAD_BACKUP_S3_REGION")
        if (bucket or region) and not REGION.match(region):
            bad.append("LEAD_BACKUP_S3_REGION")
        endpoint = get("LEAD_BACKUP_S3_ENDPOINT")
        if endpoint and not _endpoint(endpoint):
            bad.append("LEAD_BACKUP_S3_ENDPOINT")

    if bad or notes:
        names = "、".join(dict.fromkeys(bad))
        lines = list(dict.fromkeys(notes))
        if names:
            lines.append(f"以下环境变量无效或缺失：{names}（详见 .env.example）")
        raise ConfigError("配置错误：" + "；".join(lines))

    allowed = set(configured) | set(LOOPBACK_NAMES)
    public_host = urlsplit(public_origin).hostname if public_origin else None
    if public_host:
        allowed.add(public_host)
    cookie_secure = public_origin.startswith("https://")
    return Settings(
        mode=mode, auth=auth, server=server, bind=bind, port=port, public_origin=public_origin,
        allowed_hosts=frozenset(allowed), session_idle=timedelta(hours=idle_hours),
        session_max=timedelta(days=max_days), backup_daily=backup_daily, backup_time=backup_time,
        timezone=timezone, s3_bucket=bucket, s3_prefix=prefix, s3_region=region, s3_endpoint=endpoint,
        cookie_name="__Host-lw_session" if cookie_secure else "lw_session", cookie_secure=cookie_secure)
