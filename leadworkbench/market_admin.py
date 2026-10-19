"""Market administration: one enabled plan per market, round summaries, keyword edits,
archiving and batch website re-checks (P1 batch 2, design §6, §7)."""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
import json
import logging
import sqlite3

from .errors import Conflict, Invalid, NotFound
from .market_store import canonical, ident, stamp

log = logging.getLogger(__name__)
UTC = dt_timezone.utc
DEFAULT_MONTHS = {"discovery": 1, "refresh": 3}
DEFAULT_TIMEZONE = "Asia/Shanghai"
SCHEDULE_EXISTS = "该市场已有启用中的定时计划（每 {n} 个月，下次 {date}）。请直接修改这个计划。"
SCHEDULE_FILLED = "补齐计划缺失的设置后重新计算下次时间"


def _default_months(kind):
    return DEFAULT_MONTHS["refresh" if kind == "refresh" else "discovery"]


class MarketAdminMixin:
    # ---- schedules (design §7.1) -------------------------------------------------

    def _schedule_conflict(self, con, task_id, exclude_id=None):
        row = con.execute("""SELECT id,interval_months,next_due_at FROM schedules
            WHERE task_id=? AND enabled=1 AND id<>? ORDER BY anchor IS NULL,anchor,rowid LIMIT 1""",
                          (task_id, exclude_id or "")).fetchone()
        if not row:
            return None
        return Conflict(SCHEDULE_EXISTS.format(n=row["interval_months"] or 1, date=str(row["next_due_at"] or "")[:10]),
                        code="schedule_exists", existing_id=row["id"])

    def _schedule_write(self, con, task_id, schedule_id, sql, params):
        """Run one schedules write; the partial unique index answers races with 409, never 500."""
        try:
            con.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed: schedules.task_id" not in str(exc):
                raise
            # A constraint conflict only aborts the statement; the transaction can still read.
            conflict = self._schedule_conflict(con, task_id, schedule_id)
            raise conflict or Conflict(SCHEDULE_EXISTS.format(n=1, date=""), code="schedule_exists",
                                       existing_id=None) from exc

    def _reschedule_if_overdue(self, con, schedule, now, force=False):
        """Fill missing anchor/interval/timezone, then move an overdue (or forced) next_due_at
        to the next calendar occurrence after now; never a catch-up run (D7, R3-5)."""
        from .db import calendar_next
        s = dict(schedule)
        filled = {}
        if not s.get("anchor"):
            filled["anchor"] = now.isoformat()
        if not s.get("interval_months"):
            filled["interval_months"] = _default_months(s.get("kind"))
        if not s.get("timezone"):
            filled["timezone"] = DEFAULT_TIMEZONE
        if filled:
            con.execute(f"UPDATE schedules SET {','.join(f'{k}=?' for k in filled)} WHERE id=?",
                        (*filled.values(), s["id"]))
            if s.get("task_id"):
                con.execute("""INSERT INTO market_changes(id,market_id,field,old_value,new_value,reason,created_at)
                    VALUES(?,?,?,?,?,?,?)""", (ident(), s["task_id"], "schedule",
                    canonical({"schedule_id": s["id"], **{k: s.get(k) for k in filled}}),
                    canonical(filled), SCHEDULE_FILLED, stamp()))
            s.update(filled)
        if force or not s.get("next_due_at") or s["next_due_at"] <= now.isoformat():
            due = calendar_next(datetime.fromisoformat(s["anchor"]), now, s["interval_months"], s["timezone"])
            con.execute("UPDATE schedules SET next_due_at=? WHERE id=?", (due.isoformat(), s["id"]))

    def create_schedule(self, *, task_id=None, company_id=None, kind, interval_months=None,
                        timezone=DEFAULT_TIMEZONE, enabled=True):
        from .db import calendar_next
        if bool(task_id) == bool(company_id) or kind not in {"discovery", "refresh"}:
            raise Invalid("计划必须指定唯一目标")
        interval_months = interval_months or DEFAULT_MONTHS[kind]
        if not 1 <= interval_months <= 24 or (kind == "discovery") != bool(task_id):
            raise Invalid("计划类型或周期错误")
        if task_id:
            self.task(task_id)
        else:
            self.company(company_id)
        anchor = datetime.now(UTC)
        next_due = calendar_next(anchor, anchor, interval_months, timezone).isoformat()
        id = ident()
        with self.connect() as con:
            self._begin(con)
            if task_id and enabled:
                self._ensure_not_archived(con, task_id)
                conflict = self._schedule_conflict(con, task_id)
                if conflict:
                    raise conflict
            self._schedule_write(con, task_id, id, """INSERT INTO schedules(id,task_id,company_id,kind,
                interval_months,timezone,next_due_at,enabled,anchor) VALUES(?,?,?,?,?,?,?,?,?)""",
                (id, task_id, company_id, kind, interval_months, timezone, next_due, int(enabled), anchor.isoformat()))
        return self.schedule(id)

    def update_schedule(self, id, *, enabled=None, interval_months=None, timezone=None):
        now = datetime.now(UTC)
        with self.connect() as con:
            self._begin(con)
            row = con.execute("SELECT * FROM schedules WHERE id=?", (id,)).fetchone()
            if not row:
                raise NotFound("计划不存在")
            current = dict(row)
            enabling = enabled is True and not current["enabled"]
            if enabling and current["task_id"]:
                self._ensure_not_archived(con, current["task_id"])
                conflict = self._schedule_conflict(con, current["task_id"], id)
                if conflict:
                    raise conflict
            changes = {}
            if enabled is not None and bool(current["enabled"]) != enabled:
                changes["enabled"] = int(enabled)
            if interval_months is not None and interval_months != current["interval_months"]:
                changes["interval_months"] = interval_months
            if timezone is not None and timezone != current["timezone"]:
                changes["timezone"] = timezone
            if changes:
                self._schedule_write(con, current["task_id"], id,
                                     f"UPDATE schedules SET {','.join(f'{k}=?' for k in changes)} WHERE id=?",
                                     (*changes.values(), id))
            timing = "interval_months" in changes or "timezone" in changes
            if timing or enabling:
                fresh = con.execute("SELECT * FROM schedules WHERE id=?", (id,)).fetchone()
                self._reschedule_if_overdue(con, fresh, now, force=timing)
        return self.schedule(id)

    def _ensure_not_archived(self, con, task_id):
        """Hook for archive checks (design §6.2); archiving lands with task_state."""

    def schedule(self, id):
        found = [s for s in self.schedules(_ids=(id,))]
        if not found:
            raise NotFound("计划不存在")
        return found[0]

    def schedules(self, task_id=None, *, _ids=None):
        """Global list by next_due_at; per market: enabled first, then anchor IS NULL, anchor, rowid."""
        from .db import now
        where, params, order = "", (), "s.next_due_at"
        if task_id is not None:
            where, params, order = "WHERE s.task_id=?", (task_id,), "s.enabled DESC,s.anchor IS NULL,s.anchor,s.rowid"
        elif _ids:
            where, params = f"WHERE s.id IN ({','.join('?' * len(_ids))})", tuple(_ids)
        with self.connect() as con:
            result = [self.row(r) for r in con.execute(f"""SELECT s.*,t.name task_name FROM schedules s
                LEFT JOIN tasks t ON t.id=s.task_id {where} ORDER BY {order}""", params)]
            for schedule in result:
                paused = con.execute("""SELECT id FROM runs WHERE status='paused' AND
                    COALESCE(task_id,'')=? AND COALESCE(company_id,'')=? LIMIT 1""",
                    (schedule["task_id"] or "", schedule["company_id"] or "")).fetchone()
                schedule["waiting_reason"] = "旧任务已暂停，本期更新将延后；继续或取消旧任务后再调度" if paused and schedule["enabled"] and schedule["next_due_at"] <= now() else ""
            return result

