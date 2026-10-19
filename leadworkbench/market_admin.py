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
MARKET_ARCHIVED = "市场已归档，请先恢复后再操作。"
MARKET_EXISTS = "已有相同地区和关键词的目标市场「{name}」，请直接使用该市场。"
KEYWORDS_REASON = "用户修改关键词；下一轮起按新关键词检索，已有企业保留"


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

    @staticmethod
    def _archived_at(con, task_id):
        row = con.execute("SELECT archived_at FROM task_state WHERE task_id=?", (task_id,)).fetchone()
        return row[0] if row else None

    def _ensure_not_archived(self, con, task_id):
        if task_id and self._archived_at(con, task_id):
            raise Conflict(MARKET_ARCHIVED, code="market_archived")

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

    # ---- round summaries (design §7.4) -------------------------------------------

    @staticmethod
    def _best_rank_sql():
        from .market_store import COMPANY_RANK_SQL, USABLE_FROM, usable_contact_sql
        return f"""SELECT m.company_id,COALESCE(s.best_rank,{COMPANY_RANK_SQL}) rank
            FROM market_memberships m JOIN companies c ON c.id=m.company_id
            LEFT JOIN (SELECT f.company_id,min(f.tier_rank) best_rank FROM {USABLE_FROM}
                       WHERE {usable_contact_sql()} GROUP BY f.company_id) s ON s.company_id=m.company_id
            WHERE m.market_id=? AND c.merged_into IS NULL"""

    def _write_round_baseline(self, con, run_id, market_id):
        """Entry tiers at round start, plus the pending marker meaning "this round has a baseline"."""
        if self._facets_enabled:
            self._refresh_facets_locked(con)
        con.execute(f"INSERT OR IGNORE INTO market_round_baselines(run_id,company_id,rank) "
                    f"SELECT ?,company_id,rank FROM ({self._best_rank_sql()})", (run_id, market_id))
        con.execute("INSERT OR IGNORE INTO market_round_summaries(run_id,market_id,payload,created_at) VALUES(?,?,?,?)",
                    (run_id, market_id, canonical({"pending": True}), stamp()))

    def _round_summary_payload(self, con, run, market_id, with_tiers):
        from .contact_tiers import RANK_TIER
        stats = self._market_stats(con, market_id, run["id"])
        coverage = self._market_coverage(con, run["id"])
        entries = 0
        for (result,) in con.execute("SELECT result FROM work_items WHERE run_id=? AND stage='refresh'", (run["id"],)):
            try:
                entries += int((json.loads(result or "{}").get("summary") or {}).get("entries_added") or 0)
            except (ValueError, TypeError, AttributeError):
                continue
        round_no = con.execute("""SELECT count(*) FROM runs WHERE task_id=? AND kind='discovery'
            AND created_at<=?""", (market_id, run["created_at"])).fetchone()[0]
        payload = {"run_id": run["id"], "round": round_no, "status": run["status"],
                   "finished_at": run["finished_at"], **{k: stats[k] for k in ("new", "existing", "changed", "not_seen", "total")},
                   "entries_added": entries, "queries_done": coverage["done"], "queries_planned": coverage["planned"],
                   "tiers_available": False, "tier_improved": None, "tier_worsened": None, "tier_delta": None}
        if with_tiers:
            baseline = dict(con.execute("SELECT company_id,rank FROM market_round_baselines WHERE run_id=?",
                                        (run["id"],)).fetchall())
            current = dict(con.execute(self._best_rank_sql(), (market_id,)).fetchall())
            delta = {}
            for ranks, sign in ((current, 1), (baseline, -1)):
                for rank in ranks.values():
                    code = RANK_TIER.get(rank, "none")
                    delta[code] = delta.get(code, 0) + sign
            payload.update(tiers_available=True,
                           tier_improved=sum(1 for c, r in current.items() if c in baseline and r < baseline[c]),
                           tier_worsened=sum(1 for c, r in current.items() if c in baseline and r > baseline[c]),
                           tier_delta={k: v for k, v in sorted(delta.items()) if v})
        return payload

    @staticmethod
    def _summary_has_baseline(row):
        if not row:
            return False
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            return False
        return bool(payload.get("pending") or payload.get("tiers_available"))

    def _write_round_summary(self, con, run_id, state):
        """Final summary of a market discovery round; a failure never blocks the round itself."""
        con.execute("SAVEPOINT round_summary")
        try:
            run = dict(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            run["status"] = state
            market_id = self._market_run(con, run_id)["market_id"]
            stored = con.execute("SELECT payload FROM market_round_summaries WHERE run_id=?", (run_id,)).fetchone()
            with_tiers = self._summary_has_baseline(stored)
            if with_tiers and self._facets_enabled:
                self._refresh_facets_locked(con)
            payload = self._round_summary_payload(con, run, market_id, with_tiers)
            con.execute("INSERT OR REPLACE INTO market_round_summaries(run_id,market_id,payload,created_at) VALUES(?,?,?,?)",
                        (run_id, market_id, canonical(payload), stamp()))
            con.execute("RELEASE round_summary")
        except Exception as exc:
            con.execute("ROLLBACK TO round_summary")
            con.execute("RELEASE round_summary")
            log.warning("round summary failed for %s", run_id,
                        extra={"event": "round_summary_failed", "run_id": run_id, "error_type": type(exc).__name__})

    def _round_summary(self, con, market_id):
        """Summary of the latest finished discovery round; read-only (never writes)."""
        run = con.execute("""SELECT * FROM runs WHERE task_id=? AND kind='discovery'
            AND status IN ('completed','partial','blocked','failed','cancelled')
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (market_id,)).fetchone()
        if not run or not self._market_run(con, run["id"]):
            return None
        stored = con.execute("SELECT payload FROM market_round_summaries WHERE run_id=?", (run["id"],)).fetchone()
        if stored:
            payload = json.loads(stored["payload"])
            if not payload.get("pending"):
                return payload
        return self._round_summary_payload(con, dict(run), market_id, False)

    # ---- keyword edits (design §6.1) ----------------------------------------------

    def _stored_market_payload(self, con, task_row, keywords):
        """Identity payload from stored data: tasks row plus task_geography.place_id (no validate_selection)."""
        row = con.execute("SELECT payload FROM task_geography WHERE task_id=?", (task_row["id"],)).fetchone()
        try:
            geography = json.loads(row[0]) if row else None
        except ValueError:
            geography = None
        return {"country": task_row["country"], "state": task_row["state"], "city": task_row["city"],
                "company_types": json.loads(task_row["company_types"] or "[]"), "keywords": keywords,
                "place_id": (geography or {}).get("place_id"), "geography": geography}

    def _other_market(self, con, identities, market_id):
        """First other market owning one of these identities, as {id, name, archived}."""
        for identity in identities:
            row = con.execute("""SELECT t.id,t.name FROM market_keys k JOIN tasks t ON t.id=k.task_id
                WHERE k.identity=? AND k.task_id<>?""", (identity, market_id)).fetchone()
            if row:
                return {"id": row["id"], "name": row["name"], "archived": bool(self._archived_at(con, row["id"]))}
        return None

    def _expanded_keywords(self, task_row, keywords, industry):
        from .industry_presets import MAX_KEYWORDS, TOO_MANY, merge_keywords
        # The preset expands with the market's own stored company types (R3-1).
        merged = merge_keywords(keywords, industry, json.loads(task_row["company_types"] or "[]"))
        if len(merged) > MAX_KEYWORDS:
            raise Invalid(TOO_MANY)
        return merged

    def update_market_keywords(self, id, keywords, industry=None):
        from .market_store import normalized
        with self.connect() as con:
            self._begin(con)
            task = con.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
            if not task:
                raise NotFound("市场不存在")
            self._ensure_not_archived(con, id)
            new = self._expanded_keywords(task, keywords, industry)
            old = json.loads(task["keywords"] or "[]")
            if new == sorted({normalized(v) for v in old if normalized(v)}):
                unchanged = True
            else:
                unchanged = False
                self._replace_keywords(con, task, old, new)
        if not unchanged:
            log.info("market keywords updated", extra={"event": "market_keywords_updated", "market_id": id})
        return self.task(id)


    def _replace_keywords(self, con, task, old, new):
        from .market_store import market_name
        id = task["id"]
        payload = self._stored_market_payload(con, task, new)
        identities = self._market_identities(con, payload)
        other = self._other_market(con, identities, id)
        if other:
            log.info("market keyword edit conflicts with another market",
                     extra={"event": "market_exists", "market_id": id})
            raise Conflict(MARKET_EXISTS.format(name=other["name"]), code="market_exists",
                           existing_id=other["id"], archived=other["archived"])
        previous = [r[0] for r in con.execute("SELECT identity FROM market_keys WHERE task_id=? ORDER BY identity", (id,))]
        # market_keys is derived from region + keywords: replaced as a whole, old rows kept in market_changes.
        con.execute("DELETE FROM market_keys WHERE task_id=?", (id,))
        con.executemany("INSERT OR IGNORE INTO market_keys(identity,task_id) VALUES(?,?)", [(k, id) for k in identities])
        con.execute("UPDATE tasks SET keywords=?,name=? WHERE id=?", (canonical(new), market_name(payload), id))
        con.execute("""INSERT INTO market_changes(id,market_id,field,old_value,new_value,reason,created_at)
            VALUES(?,?,?,?,?,?,?)""", (ident(), id, "keywords", canonical({"keywords": old, "identities": previous}),
                                       canonical(new), KEYWORDS_REASON, stamp()))

    def preview_market_edit(self, market_id, keywords, industry=None):
        """Preview of an edit: region and company types come from the market; this market is excluded."""
        from .market_plan import estimate_queries
        with self.connect() as con:
            task = con.execute("SELECT * FROM tasks WHERE id=?", (market_id,)).fetchone()
            if not task:
                raise NotFound("市场不存在")
            new = self._expanded_keywords(task, keywords, industry)
            payload = self._stored_market_payload(con, task, new)
            other = self._other_market(con, self._market_identities(con, payload), market_id)
        return {**estimate_queries(payload), "keywords": new, "existing_market": other,
                "existing_market_scope": "edit"}
