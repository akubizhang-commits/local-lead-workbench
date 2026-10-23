"""Explicit, idempotent repair of legacy broadcast-blocked Maps queues.

Call under the worker locks and an IMMEDIATE transaction, after taking a backup.
Unknown historical attempts remain unknown; never invent a lifetime attempt count.
"""
import json
from pathlib import Path
from . import maps_policy


def repair_legacy_maps(con, directory, at=None):
    at = maps_policy.time.time() if at is None else at
    directory = Path(directory)
    summary = dict(runs=[],unattempted_restored=0,attempted_requeued=0)
    runs = con.execute("""SELECT r.id,r.status FROM runs r WHERE r.kind='discovery'
        AND r.status IN ('running','queued','partial','blocked','failed')
        AND NOT EXISTS(SELECT 1 FROM runs newer WHERE newer.task_id=r.task_id AND newer.created_at>r.created_at)""").fetchall()
    for run in runs:
        rows = con.execute("SELECT * FROM work_items WHERE run_id=? AND stage='discovery' AND state='blocked'",(run['id'],)).fetchall()
        unattempted = []
        for row in rows:
            payload = json.loads(row['result'] or '{}')
            if (set(payload)<= {'message','status'} and '连续三次地图访问失败' in payload.get('message','')
                    and not (directory/'runs'/run['id']/row['id']).exists()):
                unattempted.append(row['id'])
        if not unattempted:
            continue
        for row in rows:
            payload = json.loads(row['result'] or '{}')
            if 'maps' in payload:
                continue
            reason = payload.get('stop_reason')
            if row['id'] in unattempted:
                payload['maps'] = dict(attempts=0,cycle_attempts=0,next_attempt_at=None)
                payload['legacy_message'] = payload.get('message')
                payload['message'] = '尚未发起查询；已恢复为待执行，等待地图冷却结束'
                summary['unattempted_restored'] += 1
            elif reason in {'timeout','empty_output'}:
                payload['maps'] = dict(attempts=0,legacy_attempted=True,legacy_attempts_unknown=True,
                    cycle_attempts=3 if reason=='timeout' else 1,
                    transient_failures=3 if reason=='timeout' else 0,
                    empty_attempts=1 if reason=='empty_output' else 0,
                    next_attempt_at=at+(maps_policy.BACKOFF_SECONDS[-1] if reason=='timeout' else 600))
                summary['attempted_requeued'] += 1
            else:
                continue
            con.execute("UPDATE work_items SET state='pending',result=? WHERE id=?",
                        (json.dumps(payload,ensure_ascii=False),row['id']))
        # A terminal round is reopened only because there is verified unexecuted work.
        con.execute("UPDATE runs SET status='queued',finished_at=NULL,error=NULL,message=? WHERE id=?",
                    ('已恢复未执行的地图查询，按低频计划继续',run['id']))
        summary['runs'].append(run['id'])
    prior = maps_policy.status(con,at)
    if summary['runs'] and prior['manual_required'] and prior['reason']=='连续三次地图访问失败':
        con.execute("""UPDATE maps_access_policy SET manual_required=0,failures=3,
            not_before=?,reason='历史查询超时，冷却后再进行一次自动恢复尝试' WHERE id=1""",
            (max(prior['not_before'],at+maps_policy.BACKOFF_SECONDS[-1]),))
    return summary
