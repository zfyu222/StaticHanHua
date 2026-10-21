"""Small calibration runs: translate, observe, then let the main AI decide."""
from collections import Counter
import json
import uuid


class PilotMixin:
    def build_pilot_report(self, db, config):
        from translation_workflow import fingerprint, read_json
        self.glossary(db)
        if self.pilot_approved(db, config):
            receipt = self.meta(db, 'quality_approvals')['pilot']
            return read_json(self.root / f'reports/accepted/pilot_{receipt["report_hash"][:16]}/report.json')
        rows = self.quality_rows(db)
        selected = self.pilot_ids(db, rows, config)
        rows = [r for r in rows if r['id'] in selected]
        tasks = [dict(t) for t in db.execute(
            'SELECT id,stage,status,attempt,backend,ids,error FROM tasks WHERE revision=? '
            'AND stage IN ("pilot_translate","pilot_review") AND status!="obsolete" ORDER BY id',
            (self.meta(db, 'revision'),))]
        unfinished = [t for t in tasks if t['status'] != 'done']
        complete = not unfinished and all(r['stage'] in ('pilot_translate', 'pilot_review')
                                           and isinstance(r['translation'], str) for r in rows)
        failures = []
        if complete:
            _, failures = self.collect_draft_issues(db, {**config, '_draft_stage': 'pilot_translate'})
        findings = {r['id']: r for r in failures}
        self.draft_tables(db, 'pilot_translate')
        candidates = {r['record_id']: dict(r) for r in db.execute('SELECT * FROM pilot_draft_candidates')}
        records = []
        for row in rows:
            raw = candidates.get(row['id'])
            item = json.loads(raw['raw_item']) if raw else {}
            records.append({k: row[k] for k in ('id', 'ordinal', 'source', 'translation', 'metadata')} | {
                'exceptions': json.loads(row['exceptions']),
                'issues': item.get('issues', []),
                'diagnostics': findings.get(row['id'], {}).get('diagnostics', []),
                'producer': {'task_id': raw['task_id'], 'instance': raw['instance']} if raw else None})
        requests = {r['status']: r['n'] for r in db.execute(
            'SELECT c.status,count(*) n FROM api_calls c JOIN tasks t ON t.id=c.task_id '
            'WHERE t.stage="pilot_translate" GROUP BY c.status')}
        report = {'schema': 1, 'kind': 'pilot', 'purpose': 'calibration',
                  'pilot_key': self.pilot_key(db, config),
                  'source_sha256': self.meta(db, 'source_hash'), 'glossary': self.meta(db, 'glossary'),
                  'config': config, 'records': records, 'tasks': tasks,
                  'config_note': 'Current configuration. Saved outputs retain their producer task/input snapshots; '
                                 'a settings change alone does not mean these outputs were regenerated.',
                  'ready_for_assessment': complete,
                  'statistics': {'sample_records': len(rows),
                                 'translated_records': sum(r['translation'] is not None for r in rows),
                                 'mechanical_failed_records': len(failures) if complete else None,
                                 'task_statuses': dict(Counter(t['status'] for t in tasks)),
                                 'api_request_statuses_cumulative': requests},
                  'failures': failures,
                  'note': 'Mechanical findings are diagnostic, not a semantic error rate. '
                          'Main AI assesses pipeline reliability, style and recurring quality problems. '
                          'Individual errors need not be cleared before accepting calibration.'}
        report['snapshot_hash'] = fingerprint([records, tasks, config])
        report['report_hash'] = fingerprint(report)
        return report

    def pilot_report(self):
        from translation_workflow import write_json
        with self.db() as db:
            config = self.config(db)
            report = self.build_pilot_report(db, config)
            approved = self.pilot_approved(db, config)
            path = self.root / 'reports/pilot_calibration.json'
            write_json(path, report)
            write_json(self.root / f'reports/pilot_rounds/{report["report_hash"]}.json', report)
            template = self.root / 'reports/pilot_assessment.template.json'
            write_json(template, {'report_hash': report['report_hash'], 'reviewer': 'main',
                                 'decision': 'accept|retry|stop', 'summary': '', 'retry_ids': []})
            return {'status': 'already_approved' if approved else
                             'awaiting_main' if report.get('ready_for_assessment') else 'incomplete',
                    'report_file': str(path), 'report_hash': report['report_hash'],
                    'assessment_template': str(template), 'statistics': report.get('statistics', {}),
                    'next_action': None if approved else 'main_assessment', 'repair_tasks': []}

    def retry_pilot_in_transaction(self, db, config, record_ids, reason):
        if self.pilot_approved(db, config):
            raise ValueError('Accepted pilot requires an explicit configuration impact before retry')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Pilot retry requires a reason')
        if db.execute('SELECT 1 FROM tasks WHERE status="running"').fetchone():
            raise ValueError('Finish/recover in-flight tasks before retrying pilot')
        rows = self.quality_rows(db)
        sample = self.pilot_ids(db, rows, config)
        if record_ids is not None and (not isinstance(record_ids, list)
                or any(not isinstance(i, str) for i in record_ids)):
            raise ValueError('Retry IDs must be a list of record IDs')
        ids = sample if record_ids is None else set(record_ids)
        if (not ids or not ids.issubset(sample)
                or (record_ids is not None and len(ids) != len(record_ids))):
            raise ValueError('Retry IDs must be a nonempty unique subset of the saved pilot sample')
        if any(r['locked'] for r in rows if r['id'] in ids):
            raise ValueError('Unlock confirmed translations before including them in a pilot retry')
        if self.meta(db, 'pilot_prior') is None:
            self.put(db, 'pilot_prior', [{k: r[k] for k in ('id', 'stage', 'translation', 'exceptions')}
                                       for r in rows if r['id'] in sample])
        for task in db.execute('SELECT id,ids FROM tasks WHERE revision=? '
                               'AND stage IN ("pilot_translate","pilot_review") AND status!="obsolete"',
                               (self.meta(db, 'revision'),)).fetchall():
            if ids.intersection(json.loads(task['ids'])):
                db.execute('UPDATE tasks SET status="obsolete" WHERE id=?', (task['id'],))
        for record_id in ids:
            db.execute('UPDATE records SET stage=?,translation=NULL,exceptions=? WHERE id=?',
                       ('source', '[]', record_id))
        self.event(db, 'pilot_retry', {'record_ids': sorted(ids), 'reason': reason})
        return {'status': 'retry_prepared', 'retry_records': len(ids),
                'next_action': 'prepare --stage pilot_translate'}

    def pilot_retry(self, record_ids, reason):
        with self.db() as db:
            config = self.config(db)
            report = self.build_pilot_report(db, config)
            result = self.retry_pilot_in_transaction(db, config, record_ids, reason)
            assessment = {'report_hash': report['report_hash'], 'reviewer': 'main', 'decision': 'retry',
                          'summary': reason, 'retry_ids': record_ids}
            path = self.save_pilot_feedback(report, assessment)
            return {**result, 'assessment_file': str(path)}

    def save_pilot_feedback(self, report, assessment):
        from translation_workflow import write_json
        path = self.root / f'reports/pilot_feedback/{report["report_hash"]}_{uuid.uuid4().hex[:8]}.json'
        write_json(self.root / f'reports/pilot_rounds/{report["report_hash"]}.json', report)
        write_json(path, assessment)
        return path

    def pilot_decide(self, assessment):
        if (not isinstance(assessment, dict) or assessment.get('reviewer') != 'main'
                or assessment.get('decision') not in ('accept', 'retry', 'stop')
                or not isinstance(assessment.get('summary'), str) or not assessment['summary'].strip()):
            raise ValueError('Main pilot assessment requires accept/retry/stop and a meaningful summary')
        with self.db() as db:
            config = self.config(db)
            report = self.build_pilot_report(db, config)
            if assessment.get('report_hash') != report['report_hash']:
                raise ValueError('Stale pilot assessment; read the current pilot-report')
            if db.execute('SELECT 1 FROM tasks WHERE status="running"').fetchone():
                raise ValueError('Finish/recover in-flight tasks before deciding pilot')
            decision = assessment['decision']
            if decision == 'accept':
                if self.pilot_approved(db, config):
                    return {'status': 'already_approved', 'kind': 'pilot', 'report_hash': report['report_hash']}
                if not report['ready_for_assessment']:
                    raise ValueError('Complete all pilot translation tasks before accepting calibration')
                result = self.accept_quality_in_transaction(db, 'pilot', report, assessment)
            elif decision == 'retry':
                ids = assessment.get('retry_ids')
                if ids is not None and not isinstance(ids, list):
                    raise ValueError('retry_ids must be a list')
                result = self.retry_pilot_in_transaction(db, config, ids or None, assessment['summary'])
            else:
                self.put(db, 'paused', True)
                result = {'status': 'stopped', 'kind': 'pilot'}
            path = self.save_pilot_feedback(report, assessment)
            self.event(db, 'pilot_decision', {'decision': decision, 'report_hash': report['report_hash'],
                                             'assessment_file': str(path)})
            return {**result, 'assessment_file': str(path)}
