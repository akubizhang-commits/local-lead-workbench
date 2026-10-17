"""Read-only workflow projection. Counts come from work items, not cached percentages."""
import json
from datetime import datetime, timezone
from .maps_policy import status as maps_status, TRANSIENT, SCRAPER_CRASH, CONFIG_ERRORS, RESTRICTIONS
from .maps_retry import timestamp

TERMINAL = {'completed', 'partial', 'failed', 'blocked', 'cancelled'}


def failure_kind(stop_reason):
    """Separate scraper faults from genuine empty results for the UI."""
    if stop_reason in TRANSIENT | SCRAPER_CRASH | CONFIG_ERRORS | {'invalid_output'}:
        return 'scraper_error'
    if stop_reason == 'empty_output':
        return 'no_results'
    if stop_reason in RESTRICTIONS:
        return 'restricted'
    return None


def workflow(con, run):
    rows = [dict(r) for r in con.execute('''SELECT w.id,w.stage,w.state,w.target,w.result,c.name company_name
        FROM work_items w LEFT JOIN companies c ON w.stage='refresh' AND c.id=w.target
        WHERE w.run_id=? ORDER BY w.rowid''', (run['id'],))]
    stages = []
    query_details = []
    for key, title in [('planning','规划检索范围'), ('discovery','Google Maps 检索'), ('refresh','官网资料补全')]:
        items = [r for r in rows if r['stage'] == key]
        counts = {state:sum(r['state']==state for r in items)
                  for state in ['pending','running','succeeded','partial','failed','blocked']}
        processed = sum(counts[s] for s in ['succeeded','partial','failed','blocked'])
        details = []
        text_only = False
        if key == 'planning':
            for item in items:
                result = json.loads(item['result'] or '{}')
                text_only = text_only or result.get('mode') == 'text_only'
                warnings = result.get('warnings') or []
                if isinstance(warnings, str):
                    warnings = [warnings]
                for message in [result.get('message'), *warnings]:
                    if not isinstance(message, str) or not message.strip():
                        continue
                    if result.get('mode') == 'text_only' or (item['state'] == 'partial' and 'root searches only' in message):
                        text_only = True
                        detail = '未取得地区范围坐标，无法进行坐标分区检索；已改用地区名称文字检索。'
                    else:
                        detail = message.strip()
                    if detail not in details:
                        details.append(detail)
        stage = dict(key=key,title=title,total=len(items),processed=processed,
                     mode='text_only' if text_only else None,details=details,**counts)
        if key == 'refresh':
            from .website_workflow import website_summary
            stage['website_summary'] = website_summary(items)
        if key == 'discovery':
            for item in items:
                payload = json.loads(item['result'] or '{}')
                meta = payload.get('maps') or {}
                target = item['target']
                try:
                    parsed = json.loads(target)
                    if isinstance(parsed,dict): target = parsed.get('query',target)
                except (ValueError,TypeError):
                    pass
                attempts = None if meta.get('legacy_attempts_unknown') else meta.get('attempts')
                attempted = bool(meta.get('attempts') or meta.get('legacy_attempted'))
                stop_reason = meta.get('last_stop_reason') or payload.get('stop_reason')
                history = [{**h, 'failure_kind': failure_kind(h.get('stop_reason'))} for h in meta.get('history', [])]
                query_details.append(dict(id=item['id'],query=target,state=item['state'],
                    stop_reason=stop_reason,failure_kind=failure_kind(stop_reason),
                    attempts=attempts,attempted=attempted,retries=max(0,attempts-1) if attempts is not None else None,
                    last_attempt_at=meta.get('last_attempt_at'),
                    next_attempt_at=timestamp(meta['next_attempt_at']) if meta.get('next_attempt_at') else None,
                    reason=payload.get('message',''),history=history))
            stage['retry_pending'] = sum(q['state']=='pending' and q['attempted'] for q in query_details)
            stage['unattempted'] = counts['pending']-stage['retry_pending']
        stages.append(stage)
    active = [r for r in rows if r['state']=='running']
    policy = maps_status(con)
    pending_maps = stages[1]['pending'] > 0
    terminal = run['status'] in TERMINAL
    recovery = None
    if not terminal and run['status']!='paused' and (pending_maps or stages[1]['running']):
        recovery = dict(policy)
        pending = [((maps := json.loads(r['result'] or '{}').get('maps',{})).get('next_attempt_at') or 0,
                    maps.get('last_stop_reason'))
                   for r in rows if r['stage']=='discovery' and r['state']=='pending']
        if pending and not policy['manual_required'] and not stages[1]['running']:
            from .maps_policy import time
            first_at, first_reason = min(pending, key=lambda p: p[0])
            earliest = max(policy['not_before'],first_at)
            if earliest > time.time():
                recovery['next_attempt_at'] = timestamp(earliest)
                if policy['allowed']:
                    message = ('采集程序异常，等待自动重试，已保存查询与已有结果' if first_reason in SCRAPER_CRASH
                               else '等待空结果复核时间，已保存查询与已有结果')
                    recovery.update(phase='query_wait',message=message)
            else:
                recovery['next_attempt_at'] = None
    maps_wait = not terminal and run['status']!='paused' and pending_maps and not policy['allowed']
    if terminal:
        current = {'completed':'本轮处理结束，已取得的企业和资料已保存',
                   'partial':'本轮已结束，部分来源未能获取资料', 'failed':'本轮执行失败，请查看原因',
                   'blocked':'本轮受阻，需要处理后继续', 'cancelled':'本轮已取消，已取得结果仍保留'}[run['status']]
    elif run['status']=='paused':
        current = '任务已暂停，等待手动继续'
    elif active:
        r = active[0]
        if r['stage']=='discovery':
            try:
                parsed=json.loads(r['target'])
                target=parsed.get('query','') if isinstance(parsed,dict) else r['target']
            except (ValueError,TypeError): target=r['target']
            current='正在检索：'+str(target)[:300]
        elif r['stage']=='refresh': current='正在读取企业官网：'+(r['company_name'] or '企业资料补全')
        else: current='正在规划关键词和地区范围'
    elif recovery and (maps_wait or recovery['phase']=='query_wait'): current=recovery['message']
    elif any(r['state']=='pending' for r in rows): current='等待后台调度，本任务与其他任务轮流执行'
    else: current='正在汇总本轮结果'
    start=datetime.fromisoformat(run['created_at'])
    end=datetime.fromisoformat(run['finished_at']) if run.get('finished_at') else datetime.now(timezone.utc)
    elapsed=max(0,int((end-start).total_seconds()))
    companies=con.execute("SELECT count(DISTINCT company_id) FROM observations WHERE run_id=? AND method='maps'",(run['id'],)).fetchone()[0]
    summaries=[json.loads(r['result'] or '{}').get('summary',{}) for r in rows if r['stage']=='refresh']
    query_issues = [q for q in query_details if q['stop_reason'] != 'skipped_legacy_query' and (
                    q['state'] in {'partial','failed','blocked'}
                    or (q['attempted'] and (q['state'] in {'pending','running'} or (q['attempts'] or 0)>1)))]
    query_issues.sort(key=lambda q:q['last_attempt_at'] or '', reverse=True)
    from .market_social import social_workflow
    social_stage, social = social_workflow(con, run)
    social_only=run.get('snapshot',{}).get('social_only',False)
    if social_stage:
        stages=[social_stage] if social_only else [*stages,social_stage]
        if social_only:
            companies=social_stage['total']
            recovery=None
            maps_wait=False
        if not terminal and run['status']!='paused':
            if social['current']:
                current=(current+'；社媒：' if active else '正在处理社媒：')+social['current']
            elif not active and social_stage['pending']:
                waiting=[p for p in social['platforms'] if p['pending']]
                current='社媒任务等待登录或连接处理' if any(p['manual_required'] for p in waiting) else '社媒任务等待调度或平台访问间隔'
    if run['status'] == 'partial':
        has_results = companies or any(s.get('pages_checked', 0) or s.get('contacts_found', 0) for s in summaries)
        has_results = has_results or bool(social and (social.get('accounts_found') or social.get('posts_found')))
        if any(s['pending'] or s['running'] for s in stages):
            current = '本轮仍有待处理任务，已取得的结果已保存'
        else:
            current = ('本轮已结束，已取得的结果已保存；部分来源未能获取资料'
                       if has_results else '本轮已结束，未取得可用结果；可展开阶段明细查看原因')
    return dict(run_id=run['id'],status=run['status'],created_at=run['created_at'],elapsed_seconds=elapsed,
                current=current,stages=stages,companies_found=companies,social=social,social_only=social_only,
                website_pages=sum(s.get('pages_checked',0) for s in summaries),
                contacts_found=sum(s.get('contacts_found',0) for s in summaries),
                website_links_remaining=sum(s.get('remaining',0) for s in summaries),
                auto_enrich=run.get('snapshot',{}).get('auto_enrich',True),
                maps_recovery=recovery,maps_queries=query_issues[:20],maps_query_issue_count=len(query_issues),
                maps_wait=policy['message'] if maps_wait else None,
                updated_at=datetime.now(timezone.utc).isoformat())
