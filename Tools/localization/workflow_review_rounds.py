"""Persist independent reviews, then validate and regroup only at round boundaries."""
import json
import uuid

import ainiee_bridge as bridge


class ReviewRoundMixin:
    @staticmethod
    def is_deferred_review(task):
        return task['stage'] in ('review', 'final_review') and not json.loads(task['payload']).get('strict_review')

    def review_tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS review_candidates(record_id TEXT,stage TEXT,task_id TEXT,item TEXT,original TEXT,instance TEXT,status TEXT,diagnostic TEXT,PRIMARY KEY(record_id,stage))')
        db.execute('CREATE TABLE IF NOT EXISTS review_round_checks(id INTEGER PRIMARY KEY,report TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS review_proof_checks(signature TEXT PRIMARY KEY)')

    @staticmethod
    def review_proof_key(item, source, before, after):
        from translation_workflow import fingerprint
        return fingerprint([source, before, after, {k: item.get(k) for k in ('source_excerpt', 'checks', 'changes')}])

    def unfinished_reviews(self, db, stage):
        return [dict(t) for t in db.execute('SELECT id,status FROM tasks WHERE stage=? AND revision=? AND status IN ("pending","running","failed")',
                                           (stage, self.meta(db, 'revision')))]

    def save_review_candidate(self, db, task, item, original, instance):
        from translation_workflow import encoded
        self.review_tables(db)
        db.execute('INSERT OR REPLACE INTO review_candidates VALUES (?,?,?,?,?,?,"pending",NULL)',
                   (item['id'], task['stage'], task['id'], encoded(item), encoded(original), instance))

    def require_review_round_complete(self, db, stage):
        self.review_tables(db)
        if self.unfinished_reviews(db, stage) or db.execute(
                'SELECT 1 FROM review_candidates c JOIN tasks t ON t.id=c.task_id '
                'WHERE c.stage=? AND t.revision=? AND t.status!="obsolete" AND c.status!="passed"',
                (stage, self.meta(db, 'revision'))).fetchone():
            raise ValueError('Complete the independent review round and run review-scan before proceeding')

    def plan_review_repairs(self, db, config, stage, problems, origin):
        """Pool across producer batches; retain each ID's attempts and provenance."""
        from translation_workflow import encoded, fingerprint
        contexts = self.meta(db, 'contexts', {})
        glossary = self.glossary(db)
        prior = [dict(t) for t in db.execute('SELECT * FROM tasks WHERE stage=? AND revision=?',
                                            (stage, self.meta(db, 'revision')))]
        round_number = 1 + max((json.loads(t['payload']).get('review_round', 1) for t in prior), default=0)
        rows, blocked = [], []
        for record_id, diagnostic in problems.items():
            row = dict(db.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone())
            used = max((t['attempt'] for t in prior if record_id in json.loads(t['ids'])), default=0)
            if row['locked'] or used >= config['max_attempts']:
                blocked.append(record_id); continue
            row.update(used=used, diagnostic=diagnostic, metadata=contexts.get(str(row['ordinal']), {}))
            rows.append(row)
        tasks = []
        for used in sorted({r['used'] for r in rows}):
            for group in self.batches([r for r in rows if r['used'] == used],
                                     {**config, 'batch_records': config['review_batch_records']}, respect_groups=False):
                task_id = stage + '-round-' + uuid.uuid4().hex[:16]
                payload = {'task_id': task_id, 'stage': stage, 'revision': self.meta(db, 'revision'),
                           'source_hash': self.meta(db, 'source_hash'), 'review_round': round_number,
                           'review_repair_origin': origin, 'language': {'source': config['source_language'], 'target': config['target_language']},
                           'style': config['style'], 'context': [], 'game_background': '',
                           'glossary_version': self.meta(db, 'glossary'), 'records': [],
                           'terms': bridge.matched_terms(glossary, [r['source'] for r in group], config),
                           'revision_request': '整轮检查已结束；只修复本批诊断及审校证据，不重写无关表达。保存后等待全轮统一复查。'}
                for row in group:
                    candidate = db.execute('SELECT item FROM review_candidates WHERE record_id=? AND stage=?', (row['id'], stage)).fetchone()
                    payload['records'].append({'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                        'translation': row['translation'], 'exceptions': json.loads(row['exceptions']),
                        'locked': bool(row['locked']), 'metadata': row['metadata'],
                        'protection': [],  # Review source/translation use original variables, not SHH translation markers.
                        'diagnostic': row['diagnostic'], 'candidate': json.loads(candidate['item']) if candidate else None})
                    db.execute('UPDATE records SET stage=? WHERE id=?', ('translate' if stage == 'review' else 'polish', row['id']))
                value = fingerprint(payload); payload['input_hash'] = value
                db.execute('INSERT INTO tasks(id,stage,revision,ids,payload,input_hash,backend,status,attempt) VALUES (?,?,?,?,?,?,?,"pending",?)',
                           (task_id, stage, self.meta(db, 'revision'), encoded([r['id'] for r in group]), encoded(payload), value,
                            config['stage_backends'].get(stage, config['backend']), used))
                tasks.append(task_id)
        approvals = self.meta(db, 'quality_approvals', {}); approvals.pop('final', None)
        self.put(db, 'quality_approvals', approvals)
        return tasks, blocked

    def review_scan(self, stage='review', prepare_repairs=False):
        from translation_workflow import encoded, write_json
        if stage not in ('review', 'final_review'):
            raise ValueError('Review rounds support review and final_review; pilot remains strict')
        with self.db() as db:
            config = self.config(db); self.review_tables(db)
            unfinished = self.unfinished_reviews(db, stage)
            path = self.root / f'reports/{stage}_round_status.json'
            if unfinished:
                report = {'stage': stage, 'phase': 'awaiting_round', 'clean': False, 'checked_records': 0,
                          'unfinished_batches': unfinished, 'repair_tasks': [], 'next_action': 'Finish the entire current review round'}
                write_json(path, report); return {**report, 'report_file': str(path)}
            candidates = [dict(r) for r in db.execute(
                'SELECT c.* FROM review_candidates c JOIN tasks t ON t.id=c.task_id '
                'WHERE c.stage=? AND t.revision=? AND t.status="done" AND c.status="pending"',
                (stage, self.meta(db, 'revision')))]
            rules, glossary = self.rules(db, config), self.glossary(db)
            checked, passed = [], []
            for row in candidates:
                task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone())
                payload = json.loads(task['payload']); payload['strict_review'] = True
                task['payload'] = encoded(payload)
                item, original = json.loads(row['item']), json.loads(row['original'])
                error = None
                try:
                    value, exceptions = self.validate_body_item(db, task, {}, item, original, config, rules, glossary)
                except ValueError as exc:
                    error = str(exc)
                if error is None:
                    db.execute('UPDATE records SET translation=?,exceptions=?,stage=? WHERE id=?',
                               (value, encoded(exceptions), stage, row['record_id']))
                    passed.append(row['record_id'])
                    db.execute('INSERT OR IGNORE INTO review_proof_checks VALUES (?)',
                               (self.review_proof_key(item, original['source'], original['translation'], value),))
                db.execute('UPDATE review_candidates SET status=?,diagnostic=? WHERE record_id=? AND stage=?',
                           ('failed' if error else 'passed', error, row['record_id'], stage))
                checked.append({'id': row['record_id'], 'instance': row['instance'], 'task_id': row['task_id'],
                                'result': 'failed' if error else 'passed', 'diagnostic': error})
            problems = {r['record_id']: r['diagnostic'] for r in db.execute(
                'SELECT c.* FROM review_candidates c JOIN tasks t ON t.id=c.task_id '
                'WHERE c.stage=? AND t.revision=? AND t.status!="obsolete" AND c.status="failed"',
                (stage, self.meta(db, 'revision')))}
            tasks, blocked = self.plan_review_repairs(db, config, stage, problems, 'review-scan') if prepare_repairs and problems else ([], [])
            required = {'review': ('review', 'polish', 'final_review'), 'final_review': ('final_review',)}[stage]
            complete = all(r['stage'] in required for r in db.execute('SELECT stage FROM records'))
            report = {'stage': stage, 'phase': 'round_checked' if checked else 'cached',
                      'scope': 'review_round', 'checked_records': len(checked), 'checks': checked,
                      'passed_ids': passed,
                      'failed_records': len(problems), 'problems': problems, 'repair_tasks': tasks,
                      'needs_attention_ids': blocked, 'clean': complete and not problems and not tasks}
            if checked:
                cursor = db.execute('INSERT INTO review_round_checks(report) VALUES (?)', (encoded(report),))
                report['round'] = cursor.lastrowid
                archive = self.root / f'reports/review_rounds/{stage}_{cursor.lastrowid:05d}.json'
                write_json(archive, report); report['round_report'] = str(archive)
                self.put(db, 'review_generation', self.meta(db, 'review_generation', 0) + 1)
                self.event(db, 'review_round_checked', {'stage': stage, 'checked_ids': [r['id'] for r in checked], 'repair_tasks': tasks})
            write_json(path, report)
            return {k: v for k, v in {**report, 'report_file': str(path)}.items() if k not in ('checks', 'problems')}
