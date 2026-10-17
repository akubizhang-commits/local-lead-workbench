"""Current account view from immutable observations.

Missing data is not a deletion. Each field uses its newest non-empty successful
observation, while different older values retain their own evidence and dates.
This module performs no database writes or network operations.
"""
from collections import defaultdict
import re
from urllib.parse import urlsplit

from .contact_quality import invalid_email_reason, non_outreach_email_reason
from .social import classify_link


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def social_target(value):
    """Narrow known profile shapes before discovery or automatic reads."""
    entry = classify_link(value)
    if entry["kind"] == "excluded":
        return entry
    parts = [part for part in urlsplit(entry["url"]).path.split("/") if part]
    invalid = any(part in {".", ".."} for part in parts)
    if entry["platform"] == "instagram" and entry["kind"] == "account":
        name = parts[0]
        invalid |= (not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_.]{0,28}[a-z0-9_])?", name)
                    or ".." in name)
    if entry["platform"] == "facebook":
        invalid |= parts[0] in {"login.php", "checkpoint", "recover"}
    if invalid:
        return {**entry, "kind": "excluded", "url": "", "reason": "账号地址格式无效或不是公开账号页面"}
    return entry


def usable_field(key, value):
    """Conservative display checks; the immutable observation remains intact."""
    if not isinstance(key, str) or not _nonempty(value):
        return False
    value = value.strip()
    if key == "email":
        return not (invalid_email_reason(value) or non_outreach_email_reason(value)
                    or re.search(r"@\d+x\.(?:png|jpe?g|gif|webp|svg)$", value, re.I))
    if key == "phone":
        digits = re.sub(r"\D", "", value)
        return bool(re.fullmatch(r"\+?[\d(). \-]+", value) and
                    7 <= len(digits) <= 15 and len(set(digits)) > 1)
    if key in {"website", "website_text"}:
        try:
            parsed = urlsplit(value if key == "website" or "://" in value else "https://" + value)
            return bool(parsed.scheme in {"http", "https"} and parsed.hostname
                        and "." in parsed.hostname and not parsed.username and not parsed.password
                        and not re.search(r"[\s\x00-\x1f\x7f]", value)
                        and parsed.port in {None, 80, 443})
        except ValueError:
            return False
    return True


def _records(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def project_account(observations):
    """Project observations supplied newest first as (payload, observed_at)."""
    attempt_at = observations[0][1]
    # Older releases wrote a new snapshot when another branch reused a result.
    # These rows remain in the audit trail but are not fresh source evidence.
    observations = [
        (payload, at) for payload, at in observations
        if not payload.get("cache_reused")
    ]
    if not observations:
        return {
            "status": "unreadable",
            "reason": "cache_source_missing",
            "message": "仅有缓存记录，缺少原始采集来源与时间，请重新读取账号资料",
            "fields": [], "field_history": [], "posts": [],
            "content_sources": {}, "observed_at": attempt_at,
        }
    latest, latest_at = observations[0]
    result = dict(latest)
    successful = [
        (payload, at) for payload, at in observations
        if payload.get("status") == "preview_read"
    ]
    # Never treat content from a failed/login response as account-owned data.
    for key in ("title", "description", "source_url", "provider"):
        result.pop(key, None)
    result.pop("content_observed_at", None)
    result["content_sources"] = {}
    current = {}
    history = {}
    posts = {}
    for payload, at in successful:
        source = payload.get("source_url", "")
        fields = [f for f in _records(payload.get("fields"))
                  if usable_field(f.get("key"), f.get("value"))]
        valid_posts = [p for p in _records(payload.get("posts"))
                       if _nonempty(p.get("text"))
                       and (not p.get("url") or social_target(p["url"])["kind"] == "post")]
        if "content_observed_at" not in result and (
            _nonempty(payload.get("title"))
            or _nonempty(payload.get("description"))
            or fields or valid_posts
        ):
            result["content_observed_at"] = at
        for key in ("title", "description", "source_url", "provider"):
            if key not in result and _nonempty(payload.get(key)):
                result[key] = payload[key]
                if key in {"title", "description"}:
                    result["content_sources"][key] = {
                        "observed_at": at, "source_url": source,
                        "retained": payload is not latest,
                    }
        groups = defaultdict(dict)
        for field in fields:
            key, value = field.get("key"), field.get("value")
            if not key or not _nonempty(value):
                continue
            groups[key].setdefault(value.strip(), {
                **field,
                "source_url": field.get("source_url") or source,
                "observed_at": at,
                "retained": payload is not latest,
            })
        for key, values in groups.items():
            if key not in current:
                current[key] = values
            else:
                for value, field in values.items():
                    if value not in current[key]:
                        history.setdefault((key, value), field)
        for post in valid_posts:
            key = post.get("url") or post.get("text")
            if key and _nonempty(post.get("text")):
                posts.setdefault(key, {**post, "observed_at": at})

    result["fields"] = [field for values in current.values() for field in values.values()]
    result["field_history"] = list(history.values())
    result["posts"] = list(posts.values())
    result["observed_at"] = latest_at
    if latest.get("status") != "preview_read" and successful:
        result["message"] = (
            latest.get("message", "")
            + "；保留上次成功读取的预览，未视为本次新资料"
        )
    return result
