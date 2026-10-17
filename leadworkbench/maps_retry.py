"""Per-query attempts, separate from the installation-wide Maps cooldown."""
from datetime import datetime, timezone
from . import maps_policy

EMPTY_RETRY_SECONDS = (600, 1800)
CRASH_RETRY_SECONDS = (120, 600, 1800)


def timestamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def claimed(payload):
    meta = dict(payload.get('maps') or {})
    meta.update(attempts=meta.get('attempts',0)+1,
                cycle_attempts=meta.get('cycle_attempts',0)+1,
                last_attempt_at=timestamp(maps_policy.time.time()), next_attempt_at=None)
    return {**payload, 'maps':meta}


def outcome(payload, result, access):
    meta = dict(payload.get('maps') or {})
    reason = result.get('stop_reason','')
    retry = bool(access and access['auto_retry'])
    next_at = access['not_before'] if retry else None
    # Ambiguous empty output is a local query problem; it must not halt other queries.
    if reason in maps_policy.TRANSIENT:
        meta['transient_failures'] = meta.get('transient_failures',0)+1
    if reason == 'empty_output':
        meta['empty_attempts'] = meta.get('empty_attempts',0)+1
    empty_attempt = meta.get('empty_attempts',0)
    if reason == 'empty_output' and empty_attempt <= len(EMPTY_RETRY_SECONDS) and not access['manual_required']:
        retry = True
        next_at = maps_policy.time.time()+EMPTY_RETRY_SECONDS[empty_attempt-1]
    if reason in maps_policy.SCRAPER_CRASH:
        meta['crash_attempts'] = meta.get('crash_attempts',0)+1
        crash_attempt = meta['crash_attempts']
        if crash_attempt <= len(CRASH_RETRY_SECONDS) and not access['manual_required']:
            retry = True
            next_at = maps_policy.time.time()+CRASH_RETRY_SECONDS[crash_attempt-1]
    history = list(meta.get('history') or [])
    history.append(dict(attempt=meta.get('attempts',1),started_at=meta.get('last_attempt_at'),
                        finished_at=timestamp(maps_policy.time.time()),status=result.get('status'),
                        stop_reason=reason, message='；'.join(result.get('warnings') or [])))
    meta.update(history=history,next_attempt_at=next_at,last_stop_reason=reason,
                retry_exhausted=reason in ({'empty_output'} | maps_policy.SCRAPER_CRASH) and not retry)
    return meta, retry


def reset_cycle(payload):
    """Explicit resume preserves the lifetime attempt history."""
    if payload.get('maps'):
        payload['maps'].update(cycle_attempts=0,transient_failures=0,empty_attempts=0,crash_attempts=0,next_attempt_at=None)
    return payload
