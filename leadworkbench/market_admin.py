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
MARKET_BUSY = "该市场还有未结束的轮次（排队、运行或暂停中），请先取消后再归档。已取得的企业会保留。"
RECHECK_RUNNING = "该市场已有批量官网复核在进行中（已完成 {done} / {total} 家），请等待完成或取消后再发起。"
ACTIVE = ("queued", "running", "paused")
RECHECK_LIMIT = 20  # D2
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

    # ---- archive and restore (design §6.2) -------------------------------------

    def _active_recheck(self, con, market_id, exclude_run=None):
        return con.execute(f"""SELECT r.* FROM market_recheck_runs b JOIN runs r ON r.id=b.run_id
            WHERE b.market_id=? AND r.status IN ({','.join('?' * len(ACTIVE))}) AND r.id<>?
            ORDER BY b.created_at DESC LIMIT 1""", (market_id, *ACTIVE, exclude_run or "")).fetchone()

    def _recheck_conflict(self, run):
        progress = json.loads(run["progress"] or "{}")
        return Conflict(RECHECK_RUNNING.format(done=progress.get("enrichment_done", 0),
                                               total=progress.get("enrichment_total", 0)), code="recheck_running")

    def _ensure_recheck_resumable(self, con, run):
        """Resuming a batch re-check: its market must be active and have no other running batch."""
        snapshot = run["snapshot"] if isinstance(run["snapshot"], dict) else json.loads(run["snapshot"] or "{}")
        if snapshot.get("batch") != "website_recheck":
            return
        self._ensure_not_archived(con, snapshot.get("market_id"))
        other = self._active_recheck(con, snapshot.get("market_id"), run["id"])
        if other:
            raise self._recheck_conflict(other)

    def _market_change(self, con, market_id, field, old, new, reason):
        con.execute("""INSERT INTO market_changes(id,market_id,field,old_value,new_value,reason,created_at)
            VALUES(?,?,?,?,?,?,?)""", (ident(), market_id, field, old, new, reason, stamp()))

    def archive_market(self, id):
        with self.connect() as con:
            self._begin(con)
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (id,)).fetchone():
                raise NotFound("市场不存在")
            if not self._archived_at(con, id):
                busy = con.execute(f"""SELECT 1 FROM runs WHERE task_id=? AND status IN ({','.join('?' * len(ACTIVE))})
                    LIMIT 1""", (id, *ACTIVE)).fetchone() or self._active_recheck(con, id)
                if busy:
                    raise Conflict(MARKET_BUSY, code="market_busy")
                now = stamp()
                con.execute("""INSERT INTO task_state(task_id,archived_at,updated_at) VALUES(?,?,?)
                    ON CONFLICT(task_id) DO UPDATE SET archived_at=excluded.archived_at,updated_at=excluded.updated_at""",
                            (id, now, now))
                self._market_change(con, id, "archive", "active", "archived", "用户归档市场；企业和历史记录保留")
                log.info("market archived", extra={"event": "market_archived", "market_id": id})
        return self.task(id)

    def restore_market(self, id):
        now = datetime.now(UTC)
        with self.connect() as con:
            self._begin(con)
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (id,)).fetchone():
                raise NotFound("市场不存在")
            if self._archived_at(con, id):
                con.execute("UPDATE task_state SET archived_at=NULL,updated_at=? WHERE task_id=?", (now.isoformat(), id))
                # No catch-up run: an overdue enabled plan moves to its next period (D7).
                for schedule in con.execute("SELECT * FROM schedules WHERE task_id=? AND enabled=1", (id,)).fetchall():
                    self._reschedule_if_overdue(con, schedule, now)
                self._market_change(con, id, "archive", "archived", "active", "用户恢复市场")
                log.info("market restored", extra={"event": "market_restored", "market_id": id})
        return self.task(id)

    # ---- batch website re-check (design §6.3; D2, D10) ---------------------------

    def _queued_refresh_run(self, con, company_id):
        """The unfinished run whose refresh queue already holds this company, or None."""
        return con.execute(f"""SELECT r.* FROM work_items w JOIN runs r ON r.id=w.run_id
            WHERE w.stage='refresh' AND w.target=? AND w.state IN ('pending','running')
            AND r.status IN ({','.join('?' * len(ACTIVE))}) ORDER BY r.created_at,r.rowid LIMIT 1""",
                           (company_id, *ACTIVE)).fetchone()

    def _never_attempted(self, con, market_id):
        hidden = ""
        if self._materialize_exclusions(con, [market_id]):
            hidden = "AND NOT EXISTS(SELECT 1 FROM temp.excluded_ids x WHERE x.market_id=m.market_id AND x.company_id=c.id)"
        return [r[0] for r in con.execute(f"""SELECT c.id FROM market_memberships m JOIN companies c ON c.id=m.company_id
            WHERE m.market_id=? AND c.merged_into IS NULL AND c.status!='do_not_contact' AND COALESCE(c.website,'')<>''
            AND c.last_checked_at IS NULL AND NOT EXISTS (
                SELECT 1 FROM work_items w WHERE w.stage='refresh' AND w.target=c.id
                AND w.state NOT IN ('pending','running','cancelled','skipped'))
            {hidden} ORDER BY m.first_seen,c.id LIMIT {RECHECK_LIMIT}""", (market_id,))]

    def create_website_recheck(self, id, company_ids=None, select=None):
        with self.connect() as con:
            self._begin(con)
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (id,)).fetchone():
                raise NotFound("市场不存在")
            self._ensure_not_archived(con, id)
            active = self._active_recheck(con, id)
            if active:
                raise self._recheck_conflict(active)
            ids = self._never_attempted(con, id) if select == "never_attempted" else list(dict.fromkeys(company_ids or []))
            if not ids:
                raise Conflict("所选企业都不需要复核：没有尚未读取过官网的企业。", code="nothing_to_recheck")
            queued, skipped = [], []
            for company_id in ids:
                company = con.execute("SELECT id,name,website,status FROM companies WHERE id=?", (company_id,)).fetchone()
                member = company and con.execute("SELECT 1 FROM market_memberships WHERE market_id=? AND company_id=?",
                                                 (id, company_id)).fetchone()
                reason = ("不属于此市场" if not member else "无官网" if not company["website"]
                          else "已标记拒绝联系" if company["status"] == "do_not_contact"
                          else "已在复核队列中" if self._queued_refresh_run(con, company_id) else None)
                if reason:
                    skipped.append({"company_id": company_id, "name": company["name"] if company else "", "reason": reason})
                else:
                    queued.append(company_id)
            if not queued:
                counts = {}
                for item in skipped:
                    counts[item["reason"]] = counts.get(item["reason"], 0) + 1
                summary = "、".join(f"{reason} {n} 家" for reason, n in counts.items())
                raise Conflict(f"所选企业都不需要复核：{summary}。", code="nothing_to_recheck")
            run_id, now = ident(), stamp()
            snapshot = {"batch": "website_recheck", "market_id": id, "company_ids": queued}
            progress = {"done": 0, "total": len(queued), "discovered": 0, "enrichment_done": 0,
                        "enrichment_total": len(queued)}
            con.execute("""INSERT INTO runs(id,task_id,kind,company_id,status,progress,message,error,created_at,finished_at,snapshot)
                VALUES(?,NULL,'refresh',NULL,'queued',?,?,NULL,?,NULL,?)""",
                        (run_id, canonical(progress), "等待本地 worker", now, canonical(snapshot)))
            con.executemany("INSERT INTO work_items(id,run_id,stage,target,state,result) VALUES(?,?,'refresh',?,'pending',NULL)",
                            [(ident(), run_id, company_id) for company_id in queued])
            con.execute("INSERT INTO market_recheck_runs(run_id,market_id,requested,skipped,created_at) VALUES(?,?,?,?,?)",
                        (run_id, id, len(ids), canonical(skipped), now))
        log.info("website recheck created", extra={"event": "website_recheck_created", "market_id": id,
                                                   "queued": len(queued), "skipped": len(skipped)})
        return {"run_id": run_id, "queued": len(queued), "skipped": skipped}

    def website_recheck(self, id):
        """Latest batch re-check of a market with its progress, or None (read-only)."""
        with self.connect() as con:
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (id,)).fetchone():
                raise NotFound("市场不存在")
            batch = con.execute("""SELECT b.skipped,b.created_at,r.id,r.status,r.finished_at FROM market_recheck_runs b
                JOIN runs r ON r.id=b.run_id WHERE b.market_id=? ORDER BY b.created_at DESC,b.rowid DESC LIMIT 1""",
                                (id,)).fetchone()
            if not batch:
                return None
            items = con.execute("SELECT state,result FROM work_items WHERE run_id=? AND stage='refresh'",
                                (batch["id"],)).fetchall()
        summaries = [(json.loads(r["result"] or "{}").get("summary") or {}) for r in items]
        return {"run_id": batch["id"], "status": batch["status"], "total": len(items),
                "done": sum(r["state"] in {"succeeded", "partial", "failed", "blocked"} for r in items),
                "skipped": json.loads(batch["skipped"] or "[]"), "created_at": batch["created_at"],
                "finished_at": batch["finished_at"], "pages_checked": sum(s.get("pages_checked", 0) for s in summaries),
                "entries_added": sum(s.get("entries_added", 0) for s in summaries)}
