"""One-time, user-triggered cleanup of obvious duplicate reviews (design §4.6; D3).

Pending field updates that only differ in format, propose a page title or slogan as
the name, or add no enum business type are closed as ``auto_closed``. Companies are
never changed and no row is deleted; every closure is recorded in review_resolutions.
Nothing here runs at startup.
"""
from __future__ import annotations
import hashlib
import logging
import re
from urllib.parse import urlsplit
from . import review_policy
from .errors import DomainError
from .market_store import stamp
log = logging.getLogger(__name__)
PLAN_CHANGED = "待审核内容已变化，请重新预览后再确认清理。"
RULES = (
    ("phone_format", "电话仅格式不同"),
    ("name_title", "网页标题或口号不是企业名称"),
    ("business_type_enum", "业务类型不在批发商 / 零售商 / 进口贸易商范围内"),
)
LABELS = dict(RULES)
SAMPLES = 5
_GOOGLE_HOST = re.compile(r"(?:www\.)?google(?:\.[a-z]{2,3}){1,2}")
_MAPS_GOOGLE_HOST = re.compile(r"maps\.google(?:\.[a-z]{2,3}){1,2}")


def is_maps_url(url):
    """Google Maps links: google.*/maps, maps.google.*, maps.app.goo.gl and goo.gl/maps short links."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    path = parts.path.lower()
    maps_path = path == "/maps" or path.startswith("/maps/")
    return bool(host == "maps.app.goo.gl" or _MAPS_GOOGLE_HOST.fullmatch(host)
                or (host == "goo.gl" and maps_path) or (_GOOGLE_HOST.fullmatch(host) and maps_path))


def _rule(review):
    """The cleanup rule a pending field update matches, or None (same functions as the write paths)."""
    field, old, new = review["field"], review["old_value"], review["new_value"]
    if field == "phone" and review_policy.phones_equivalent(old, new, review["country"]):
        return "phone_format"
    if (field == "name" and not is_maps_url(review["source_url"])
            and review_policy.website_name_verdict(old, new) in {"equivalent", "unrelated"}):
        return "name_title"
    if field == "business_types" and review_policy.business_types_add_nothing(old, new):
        return "business_type_enum"
    return None


def plan_id(candidates):
    return hashlib.sha256("\n".join(sorted(f"{r['id']}:{rule}" for r, rule in candidates)).encode()).hexdigest()


class ReviewCleanupMixin:
    def _cleanup_candidates(self, con):
        rows = con.execute("""SELECT r.id,r.field,r.old_value,r.new_value,r.source_url,r.evidence,r.status,
                c.name company_name,c.country FROM reviews r JOIN companies c ON c.id=r.company_id
            WHERE r.status='pending' AND r.kind='field_update' AND r.field IN ('phone','name','business_types')
            ORDER BY r.created_at,r.rowid""").fetchall()
        return [(row, rule) for row in rows if (rule := _rule(row))]

    def review_cleanup_preview(self):
        """Read-only plan: rule counts with up to five samples each."""
        with self.connect() as con:
            candidates = self._cleanup_candidates(con)
        rules = []
        for rule, label in RULES:
            matched = [row for row, r in candidates if r == rule]
            if matched:
                rules.append({"rule": rule, "label": label, "count": len(matched), "samples": [
                    {"review_id": row["id"], "company_name": row["company_name"], "field": row["field"],
                     "old_value": row["old_value"], "new_value": row["new_value"]} for row in matched[:SAMPLES]]})
        return {"plan_id": plan_id(candidates), "total": len(candidates), "rules": rules}

    def review_cleanup(self, expected_plan):
        with self.connect() as con:
            self._begin(con)
            candidates = self._cleanup_candidates(con)
            if not candidates:
                return {"closed": 0, "rules": {}}  # Idempotent: a repeated confirmation succeeds.
            if plan_id(candidates) != expected_plan:
                log.info("review cleanup plan changed", extra={"event": "review_cleanup_plan_changed"})
                raise DomainError(PLAN_CHANGED, code="plan_changed", status=409)
            created, counts = stamp(), {}
            for row, rule in candidates:
                note = f"；审核清理：{LABELS[rule]}，未修改企业资料"
                con.execute("UPDATE reviews SET status='auto_closed',evidence=? WHERE id=? AND status='pending'",
                            ((row["evidence"] or "") + note, row["id"]))
                con.execute("""INSERT INTO review_resolutions(review_id,previous_status,status,rule,batch_id,created_at)
                    VALUES(?,?,?,?,?,?)""", (row["id"], row["status"], "auto_closed", rule, expected_plan, created))
                counts[rule] = counts.get(rule, 0) + 1
        log.info("review cleanup", extra={"event": "review_cleanup", **counts})
        return {"closed": len(candidates), "rules": counts}
