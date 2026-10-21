"""Explicit fresh production scope, retaining paid execution and authorship history."""
import json
import sqlite3
import uuid

from run_api_round import pid_alive


class RestartMixin:
    def restart_body(self, reason, stopped_pids):
        from translation_workflow import write_json
        if not reason.strip() or not stopped_pids or any(pid_alive(p) for p in stopped_pids):
            raise ValueError('Restart requires a reason and confirmed stopped executor PIDs')
        lock = self.root / 'state/api_round.lock'
        lock_text = lock.read_text(encoding='utf-8') if lock.exists() else None
        if lock_text:
            owner = json.loads(lock_text)
            receipt = json.loads((self.root / 'reports/api_runs' / (owner['run_id'] + '.json')).read_text(encoding='utf-8'))
            if pid_alive(owner.get('owner_pid')) or pid_alive(receipt.get('pid')):
                raise ValueError('Previous API runner or child is still alive')
        for path in (self.root / 'reports/api_runs').glob('*.json'):
            receipt = json.loads(path.read_text(encoding='utf-8'))
            if receipt.get('status') == 'running':
                if pid_alive(receipt.get('pid')):
                    raise ValueError('A recorded API executor is still alive: ' + str(path))
                if receipt.get('pid') not in stopped_pids:
                    raise ValueError('Include the stopped child PID from: ' + str(path))
        # Read-only SQLite backup MUST precede db(), whose BEGIN IMMEDIATE holds
        # a write transaction. Backing up that connection can wait indefinitely.
        folder = self.root / 'backups' / ('restart_body_' + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        backup = folder / 'workflow.sqlite3'
        source = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        with self.db() as db:
            config = self.config(db)
            if not self.meta(db, 'paused'):
                raise ValueError('Pause before restarting production')
            locked = [r[0] for r in db.execute('SELECT id FROM records WHERE locked=1')]
            if locked:
                raise ValueError('Locked translations prevent restart: ' + ', '.join(locked))
            if not self.pilot_approved(db, config):
                raise ValueError('An accepted pilot is required to preserve the calibration baseline')
            self.glossary(db)
            body = ('translate', 'review', 'polish', 'final_review')
            if db.execute('SELECT 1 FROM tasks WHERE status="running" AND stage NOT IN (?,?,?,?)', body).fetchone():
                raise ValueError('Non-production work is still running')
            reserved = [dict(r) for r in db.execute('SELECT * FROM api_calls WHERE status="reserved"')]
            for call in reserved:
                task = db.execute('SELECT stage FROM tasks WHERE id=?', (call['task_id'],)).fetchone()
                if not task or task['stage'] not in body:
                    raise ValueError('Reserved non-production request prevents restart')
            old_revision = self.meta(db, 'revision')
            stage_counts = {r['stage']: r['n'] for r in db.execute('SELECT stage,count(*) n FROM records GROUP BY stage')}
            known_stages = ('source', 'pilot_translate', 'pilot_review', 'translate', 'review', 'polish', 'final_review')
            before_records = {stage: count for stage, count in stage_counts.items() if stage in known_stages}
            unexpected_records = sum(count for stage, count in stage_counts.items() if stage not in known_stages)
            before_drafts = db.execute('SELECT count(*) FROM draft_candidates').fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='draft_candidates'").fetchone() else 0
            revision = old_revision + 1
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            cleared = {}
            for table in ('draft_candidates', 'draft_checks', 'draft_check_rounds', 'review_candidates',
                          'review_round_checks', 'review_proof_checks', 'checks', 'repair_regressions'):
                if table in tables:
                    where = ' WHERE stage NOT LIKE "pilot_%"' if table in ('review_candidates', 'repair_regressions') else ''
                    cleared[table] = db.execute('DELETE FROM ' + table + where).rowcount
            approvals = self.meta(db, 'quality_approvals', {})
            self.put(db, 'quality_approvals', {k: v for k, v in approvals.items() if k == 'pilot'})
            checkpoints = self.meta(db, 'quality_checkpoints', {})
            self.put(db, 'quality_checkpoints', {k: v for k, v in checkpoints.items() if k == 'pilot'})
            reset_keys = [r[0] for r in db.execute('SELECT key FROM meta')
                          if r[0].startswith('draft_') or r[0] in ('review_generation', 'sol_pending_findings', 'last_export', 'in_game_check')]
            for key in reset_keys:
                db.execute('DELETE FROM meta WHERE key=?', (key,))
            forced = self.meta(db, 'repair_sol_ids', {})
            self.put(db, 'repair_sol_ids', {k: v for k, v in forced.items() if k.startswith('pilot_')})
            obsolete = db.execute('UPDATE tasks SET status="obsolete",lease_token=NULL,lease_until=NULL WHERE stage IN (?,?,?,?)', body).rowcount
            db.execute('UPDATE api_calls SET status="interrupted_restart" WHERE status="reserved"')
            if 'batch_runs' in tables:
                db.execute('UPDATE batch_runs SET status="obsolete" WHERE task_id IN (SELECT id FROM tasks WHERE stage IN (?,?,?,?))', body)
            if 'batch_repair_plans' in tables:
                db.execute('UPDATE batch_repair_plans SET status="obsolete" WHERE task_id IN (SELECT id FROM tasks WHERE stage IN (?,?,?,?))', body)
            count = db.execute('UPDATE records SET stage=?,translation=NULL,exceptions=?', ('source', '[]')).rowcount
            self.put(db, 'revision', revision)
            self.put(db, 'body_restart_revision', revision)
            result = {'status': 'restarted', 'revision': revision, 'previous_revision': old_revision,
                      'paused': True, 'source_records': count, 'obsolete_tasks': obsolete,
                      'previous_record_stages': before_records, 'previous_draft_candidates': before_drafts,
                      'previous_unrecognized_stage_records': unexpected_records,
                      'interrupted_reserved_calls': [r['id'] for r in reserved], 'usage_of_interrupted_calls': 'unknown',
                      'backup': str(backup), 'cleared': cleared, 'reason': reason,
                      'stopped_pids': stopped_pids, 'glossary': self.meta(db, 'glossary'),
                      'pilot_report_hash': approvals['pilot']['report_hash']}
            self.event(db, 'restart_body', result)
        if lock_text:
            if not lock.exists() or lock.read_text(encoding='utf-8') != lock_text:
                raise ValueError('Runner lock changed; restart committed but lock requires inspection')
            lock.unlink()
        write_json(folder / 'restart_report.json', result)
        write_json(self.root / 'reports' / f'restart_body_r{revision}.json', result)
        return result
