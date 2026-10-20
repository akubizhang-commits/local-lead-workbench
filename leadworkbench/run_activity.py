"""Read projections for task > company refresh > refresh detail navigation."""
import json
from .errors import NotFound


class RunActivityMixin:
    def task_activity(self, task_id, *, page=1, status='', run_id=''):
        self.task(task_id)  # Missing task must not look like an empty task.
        with self.connect() as con:
            con.execute("BEGIN")  # deferred read transaction: rounds, page COUNT/rows and workflow from one snapshot
            rounds = [self.row(r) for r in con.execute(
                'SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC,id DESC', (task_id,))]
            for run in rounds:
                if self._market_run(con, run["id"]):
                    run["coverage"] = self._market_coverage(con, run["id"])
            where = """(r.task_id=? OR (r.kind='refresh' AND EXISTS
                (SELECT 1 FROM market_memberships m WHERE m.market_id=? AND m.company_id=w.target)))"""
            result = self._refresh_page(con, where, [task_id, task_id], page, status, run_id)
            result['rounds'] = rounds
            from .task_workflow import workflow
            selected = next((r for r in rounds if r['id']==run_id), None) if run_id else (rounds[0] if rounds else None)
            result['workflow'] = workflow(con, selected) if selected else None
            return result

    def refresh_activity(self, *, page=1, status='', run_id=''):
        with self.connect() as con:
            con.execute("BEGIN")  # deferred read transaction: COUNT and rows from one snapshot
            return self._refresh_page(con, "r.kind='refresh'", [], page, status, run_id)

    def _refresh_page(self, con, where, args, page, status, run_id):
        where = "w.stage='refresh' AND " + where
        if status:
            where += ' AND w.state=?'
            args.append(status)
        if run_id:
            where += ' AND r.id=?'
            args.append(run_id)
        joined = ' FROM work_items w JOIN runs r ON r.id=w.run_id LEFT JOIN companies c ON c.id=w.target WHERE ' + where
        total = con.execute('SELECT count(*)' + joined, args).fetchone()[0]
        rows = con.execute('SELECT w.*, c.name AS company_name,r.task_id,r.kind AS run_kind,r.status AS run_status,r.created_at,r.finished_at' + joined +
                           ' ORDER BY r.created_at DESC,r.id DESC,w.id LIMIT 50 OFFSET ?', [*args, (page - 1) * 50])
        return {'items': [self._refresh_brief(r) for r in rows], 'total': total, 'page': page, 'page_size': 50}

    @staticmethod
    def _refresh_brief(row):
        item = dict(row)
        payload = json.loads(item.pop('result') or '{}')
        item['company_id'] = item.pop('target')
        item['summary'] = payload.get('summary') or {}
        item['message'] = payload.get('message') or ''
        item['source'] = 'market' if item['task_id'] else 'standalone'
        # work_items do not have timestamps; label the parent's times explicitly in UI.
        return item

    def refresh_detail(self, run_id, item_id):
        with self.connect() as con:
            row = con.execute('''SELECT w.*,c.name AS company_name,c.website,r.task_id,r.kind AS run_kind,
                r.status AS run_status,r.created_at,r.finished_at FROM work_items w
                JOIN runs r ON r.id=w.run_id LEFT JOIN companies c ON c.id=w.target
                WHERE w.id=? AND w.run_id=? AND w.stage='refresh' ''', (item_id, run_id)).fetchone()
            if row is None:
                raise NotFound("记录不存在")
            result = self._refresh_brief(row)
            payload = json.loads(row['result'] or '{}')
            checkpoint = payload.get('checkpoint') or {}
            # Return diagnostics only; avoid shipping crawler queue, contacts or credentials.
            result['logs'] = checkpoint.get('logs') or []
            result['warnings'] = checkpoint.get('warnings') or []
            result['files_unparsed'] = checkpoint.get('files_unparsed') or []
            result['run'] = self.run(run_id)
            return result
