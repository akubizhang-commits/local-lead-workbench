"""Platform-scoped pacing shared by manual and market social work."""
import math
import random
import time
from datetime import datetime, timezone

from .social_projection import social_target

PLATFORMS = ("facebook", "instagram", "x")
DDL = """
CREATE TABLE IF NOT EXISTS social_platform_gates(
 platform TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'ready',
 not_before REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
 failures INTEGER NOT NULL DEFAULT 0);
"""


def platform_for(item):
    if item["stage"] == "search":
        return "searxng"
    if item["stage"] == "platform_search":
        return item["target"] if item["target"] in PLATFORMS else ""
    if item["stage"] not in {"read", "profile"}:
        return ""
    entry = social_target(item["target"])
    return entry["platform"] if entry["kind"] in {"account", "post"} else ""


def gate(con, platform):
    row = con.execute("SELECT * FROM social_platform_gates WHERE platform=?", (platform,)).fetchone()
    return dict(row) if row else dict(platform=platform, state="ready", not_before=0, reason="", failures=0)


def allowed(con, platform):
    value = gate(con, platform)
    return value["state"] not in {"waiting_login", "waiting_configuration"} and value["not_before"] <= time.time()


def observe(con, item, result):
    """Return True only when this item should remain pending for later recovery."""
    platform = platform_for(item)
    # Cache reuse and website work make no request to this social source.
    if not platform or result.get("cache_reused"):
        return False
    old = gate(con, platform)
    status, reason = result.get("status"), result.get("reason", "")
    message = result.get("message") or reason or "平台访问需要处理"
    failures = old["failures"]
    retry = False
    state, delay = "ready", random.uniform(15, 30) if platform == "searxng" else random.uniform(60, 120)
    if status in {"auth_required", "challenge", "login_required"} or reason in {"login_required", "challenge", "access_denied"}:
        state, delay, retry = "waiting_login", 0, True
    elif reason == "session_busy":
        state, delay, retry = "waiting_session", 60, True
    elif reason in {"missing_dependency", "dependency_missing", "unsupported", "unsupported_url", "not_configured",
                    "search_unrecognized", "invalid_settings", "unsupported_proxy", "invalid_response",
                    "unsafe_redirect", "robots_denied", "response_too_large"}:
        state, delay, retry = "waiting_configuration", 0, True
    elif reason in {"network_error", "rate_limited", "worker_error", "timeout", "provider_unavailable"} or status in {"rate_limited", "timeout"}:
        failures += 1
        state = "waiting_recovery" if failures <= 3 else "waiting_configuration"
        delay = (600, 1800, 7200)[min(failures - 1, 2)]
        retry = True
        if failures > 3:
            message = "连续恢复未成功，请检查连接后手动继续：" + message
    elif status == "unreadable" or reason in {"provider_error", "read_error", "profile_unrecognized"} or (
        status in {"blocked", "failed"} and not reason
    ):
        state, delay, retry = "waiting_configuration", 0, True
    else:
        failures = 0
    access = result.get("website_access") or {}
    if result.get("manual_required") or access.get("manual_required"):
        retry = True
        if state != "waiting_login":
            state = "waiting_configuration"
    deadline = time.time() + delay if delay else 0
    # A source's Retry-After (including HTTP-date) and an already observed wait
    # can only extend the platform backoff, never shorten it.
    for value in (old["not_before"], result.get("not_before"), access.get("not_before")):
        if isinstance(value, (int, float)) and math.isfinite(value):
            deadline = max(deadline, value)
    con.execute("""INSERT INTO social_platform_gates VALUES(?,?,?,?,?)
        ON CONFLICT(platform) DO UPDATE SET state=excluded.state,not_before=excluded.not_before,
        reason=excluded.reason,failures=excluded.failures""",
        (platform, state, deadline, message if retry else "", failures))
    return retry


def public_gate(con, platform):
    value = gate(con, platform)
    waiting = value["not_before"] > time.time()
    manual = value["state"] in {"waiting_login", "waiting_configuration"}
    state = value["state"] if manual or waiting else "pending"
    if state == "ready":
        state = "pending"
    return dict(state=state,
        reason=value["reason"] or ("按平台访问间隔等待，优先保证稳定" if waiting else ""), manual_required=manual,
        next_attempt_at=datetime.fromtimestamp(value["not_before"], timezone.utc).isoformat() if waiting else None)


def resume(con, platform):
    from .sources.social_discovery import PLATFORMS as readable_platforms
    if platform not in {*readable_platforms, "searxng"}:
        raise ValueError("无效社媒平台")
    value = gate(con, platform)
    if value["not_before"] > time.time():
        raise ValueError("平台仍在冷却，请到期后再继续")
    con.execute("DELETE FROM social_platform_gates WHERE platform=?", (platform,))
