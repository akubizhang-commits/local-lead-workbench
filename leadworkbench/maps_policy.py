"""Durable, installation-wide Maps pacing. Never sleeps while holding worker turns."""
import math
import json
import random
import time
from datetime import datetime, timezone

QUERY_GAP_SECONDS = (120, 240)
# Transient (chiefly time-budget) backoff. Timeouts here are usually benign — a
# dense tile that returns results but can't finish in one pass — so the backoff is
# kept short; the per-query subdivision (market_worker) converges faster this way.
# Genuine platform throttling has its own path (RESTRICTIONS -> manual cooldown).
BACKOFF_SECONDS = (120, 300, 900)
MANUAL_COOLDOWN_SECONDS = 1800
RESTRICTIONS = {'captcha', 'rate_limited', 'access_denied'}
TRANSIENT = {'timeout', 'nonzero_exit', 'adapter_error', 'inactivity_timeout'}
# Local Chromium exits are not platform throttling: retried per query (maps_retry), no global backoff.
SCRAPER_CRASH = {'browser_closed'}
CONFIG_ERRORS = {'missing_binary', 'launch_failed'}
# Another Maps process still holds maps.lock: retry the query later, no policy change.
BUSY = frozenset({'maps_busy'})
DDL = """CREATE TABLE IF NOT EXISTS maps_access_policy(
 id INTEGER PRIMARY KEY CHECK(id=1), not_before REAL NOT NULL DEFAULT 0,
 manual_required INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL DEFAULT '');
 INSERT OR IGNORE INTO maps_access_policy(id) VALUES(1);"""


def status(con, at=None):
    row = con.execute('SELECT not_before,manual_required,failures,reason FROM maps_access_policy WHERE id=1').fetchone()
    until, manual, failures, reason = row
    remaining = max(0, math.ceil(until - (time.time() if at is None else at)))
    wait_text = '不到1分钟' if remaining < 60 else f'约{math.ceil(remaining / 60)}分钟'
    in_flight = bool(con.execute("SELECT 1 FROM work_items WHERE stage='discovery' AND state='running' LIMIT 1").fetchone())
    phase = ('manual' if manual else 'probing' if in_flight and failures else 'running' if in_flight
             else 'cooldown' if remaining else 'probe' if failures else 'ready')
    if manual:
        message = f'地图采集保护暂停：{reason}。请确认访问已恢复后解除暂停，未执行查询已保留'
        if remaining:
            message += f'；冷却剩余{wait_text}'
    elif in_flight and failures:
        message = '正在单次试探恢复地图访问；本次成功后才会继续其他查询'
    elif remaining and failures:
        message = f'地图访问暂时异常：{wait_text}后自动试探恢复，未执行查询已保留'
    elif remaining:
        message = f'地图采集低速等待：{wait_text}后继续，已有结果已保存'
    elif failures:
        message = '地图冷却已结束，等待调度一个查询试探恢复'
    else:
        message = '地图采集低速模式：单并发，查询结束后间隔1～2分钟'
    return dict(allowed=not manual and remaining == 0 and not in_flight, manual_required=bool(manual),
                retry_after_seconds=remaining, not_before=until, failures=failures,
                phase=phase, reason=reason, message=message,
                retries_completed=max(0, failures-1), max_auto_retries=len(BACKOFF_SECONDS),
                next_attempt_at=datetime.fromtimestamp(until, timezone.utc).isoformat()
                    if remaining and not manual else None)


def observe(con, result, at=None, *, query_failures=0):
    at = time.time() if at is None else at
    if result.get('stop_reason') in BUSY:
        return dict(auto_retry=True, **status(con, at))  # not_before / failures / reason unchanged
    prior = status(con, at)
    reason = result.get('stop_reason', '')
    failures = prior['failures']
    delay, manual, explanation, retry = random.uniform(*QUERY_GAP_SECONDS), False, '', False
    if reason in RESTRICTIONS:
        manual, delay = True, MANUAL_COOLDOWN_SECONDS
        explanation = {'captcha':'检测到验证码或异常流量', 'rate_limited':'平台限制访问频率',
                       'access_denied':'平台拒绝访问'}[reason]
    elif reason in {'timeout', 'inactivity_timeout'} and (result.get('records') or []):
        # A timeout that still returned results is Maps' normal "too many results
        # to finish in one pass" — a benign partial, not installation throttling.
        # It keeps the ordinary inter-query gap and does NOT raise the global
        # failure counter, so a dense query can never, by timing out while
        # producing data, drive the whole installation into cooldown/manual stop.
        # The per-tile subdivision (market_worker) handles coverage instead.
        failures = 0
        retry = False
    elif reason in TRANSIENT:
        failures = max(failures+1, query_failures)
        if failures <= len(BACKOFF_SECONDS):
            delay, retry = BACKOFF_SECONDS[failures - 1], True
        else:
            manual, delay, explanation = True, MANUAL_COOLDOWN_SECONDS, '三次自动恢复尝试仍未成功'
        if not manual:
            explanation = '地图查询超时' if reason in {'timeout','inactivity_timeout'} else '地图连接或采集进程异常'
    elif reason in CONFIG_ERRORS:
        manual, delay, explanation = True, 0, '地图采集程序不可用，需要检查运行环境'
    elif result.get('status') == 'completed':
        failures = 0
    # Observations must never silently clear an existing manual stop.
    manual = manual or prior['manual_required']
    explanation = explanation or (prior['reason'] if manual or failures else '')
    con.execute('UPDATE maps_access_policy SET not_before=?,manual_required=?,failures=?,reason=? WHERE id=1',
                (max(prior['not_before'], at + delay), int(manual), failures, explanation))
    return dict(auto_retry=retry and not manual, **status(con, at))


def resume(con, at=None):
    current = status(con, at)
    if current['manual_required']:
        if current['retry_after_seconds']:
            raise ValueError(current['message'])
        con.execute("UPDATE maps_access_policy SET manual_required=0,failures=0,reason='' WHERE id=1")
        # Resume only attempted, recoverable errors. Untouched queries already remain pending.
        for row in con.execute("""SELECT w.id,w.result FROM work_items w JOIN runs r ON r.id=w.run_id
                WHERE w.stage='discovery' AND w.state='blocked' AND r.status IN ('queued','running')
                """).fetchall():
            payload = json.loads(row['result'] or '{}')
            if payload.get('stop_reason') not in RESTRICTIONS | TRANSIENT | SCRAPER_CRASH | CONFIG_ERRORS:
                continue
            meta = payload.setdefault('maps', {})
            meta.update(cycle_attempts=0,transient_failures=0,empty_attempts=0,crash_attempts=0,next_attempt_at=None)
            con.execute("UPDATE work_items SET state='pending',result=? WHERE id=?",
                        (json.dumps(payload,ensure_ascii=False),row['id']))
