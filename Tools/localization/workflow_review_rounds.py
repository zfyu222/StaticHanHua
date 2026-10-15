"""Persist independent reviews, then validate and regroup only at round boundaries."""
import json
import uuid

import ainiee_bridge as bridge


class ReviewRoundMixin:
    @staticmethod
    def inherit_exceptions(item, original):
        """Omission/empty output must not silently erase a context decision."""
        inherited = original.get('exceptions', [])
        incoming = item.get('exceptions', [])
        if isinstance(incoming, dict):
            incoming = [incoming]
        try:
            incoming = bridge.normalize_exception_sources(incoming, [], True)
        except ValueError:
            return dict(item)
        if not isinstance(inherited, list) or not isinstance(incoming, list):
            return dict(item)  # Preserve malformed evidence for boundary validation.
        removed = set()
        changes = item.get('exception_changes', [])
        if not isinstance(changes, list):
            raise ValueError('exception_changes must be a list')
        for change in changes:
            if (not isinstance(change, dict) or change.get('action') != 'remove'
                    or not isinstance(change.get('source'), str) or not str(change.get('reason', '')).strip()):
                raise ValueError('Removing a context exception requires source, action=remove and reason')
            removed.add(change['source'].casefold())
        merged = {e['source'].casefold(): e for e in inherited
                  if isinstance(e, dict) and isinstance(e.get('source'), str) and e['source'].casefold() not in removed}
        for entry in incoming:
            if not isinstance(entry, dict) or not isinstance(entry.get('source'), str):
                return dict(item)
            merged[entry['source'].casefold()] = entry
        if not inherited and not incoming and 'exceptions' not in item:
            return dict(item)
        return {**item, 'exceptions': list(merged.values())}

    def repair_constraints(self, source, translation, exceptions, glossary, config):
        terms = bridge.matched_terms(glossary, [source], config)
        exceptions = exceptions if isinstance(exceptions, list) else []
        exempt = {e['source'].casefold() for e in exceptions if isinstance(e, dict) and isinstance(e.get('source'), str)}
        return {'satisfied_terms': [{'src': t['src'], 'dst': t['dst']} for t in terms
                                    if t['dst'] in (translation or '') and not bridge.helper().is_keep_original_translation(t['dst'])],
                'context_exceptions': exceptions,
                'missing_terms': [t['src'] for t in terms if t['src'].casefold() not in exempt
                                  and t['dst'] not in (translation or '') and not bridge.helper().is_keep_original_translation(t['dst'])]}

    def repair_regression(self, db, config, stage, record_id, original, translation, exceptions, glossary, task_id):
        """Called only by whole-round checks, never by batch saving."""
        from translation_workflow import encoded
        before = original.get('repair_constraints')
        if not before:
            before = self.repair_constraints(original['source'], original.get('translation'), original.get('exceptions', []), glossary, config)
        after = self.repair_constraints(original['source'], translation, exceptions, glossary, config)
        lost = sorted(set(after['missing_terms']) & {t['src'] for t in before['satisfied_terms']})
        if not lost:
            return []
        db.execute('CREATE TABLE IF NOT EXISTS repair_regressions(record_id TEXT,stage TEXT,task_id TEXT,detail TEXT,PRIMARY KEY(record_id,stage,task_id))')
        db.execute('INSERT OR IGNORE INTO repair_regressions VALUES (?,?,?,?)', (record_id, stage, task_id, encoded({'regressed_terms': lost})))
        count = db.execute('SELECT count(*) FROM repair_regressions WHERE record_id=? AND stage=?', (record_id, stage)).fetchone()[0]
        if count >= 2:
            forced = self.meta(db, 'repair_sol_ids', {})
            forced[stage] = sorted(set(forced.get(stage, [])) | {record_id})
            self.put(db, 'repair_sol_ids', forced)
        return lost

    def repair_routes(self, db, config, stage, prior=None):
        """Index completed per-ID rounds once; request retries are separate.

        Preserve legacy counters conservatively, excluding the initial review.
        """
        prior = prior if prior is not None else db.execute(
            'SELECT * FROM tasks WHERE stage=? AND revision=?', (stage, self.meta(db, 'revision')))
        counts, used = {}, {}
        for task in prior:
            if task['status'] != 'done':
                continue
            payload = json.loads(task['payload'])
            if not (payload.get('draft_scan_key') or payload.get('review_repair_origin')):
                continue
            for record_id in json.loads(task['ids']):
                counts[record_id] = counts.get(record_id, 0) + 1
                legacy = max(0, task['attempt'] - (stage != 'translate'))
                used[record_id] = max(used.get(record_id, 0), counts[record_id],
                                      payload.get('repair_counts', {}).get(record_id, legacy))
        if stage == 'translate':
            for record_id, count in self.meta(db, 'draft_repair_attempts', {}).items():
                used[record_id] = max(used.get(record_id, 0), count)
        forced = set(self.meta(db, 'repair_sol_ids', {}).get(stage, []))
        return {i: (used.get(i, 0), 'sol' if i in forced or used.get(i, 0) >= config.get('body_repair_round_limit', 6) else 'standard')
                for i in set(used) | forced}

    @staticmethod
    def repair_dispatch(db, task_ids):
        dispatch = []
        for task_id in task_ids:
            task = db.execute('SELECT payload,backend FROM tasks WHERE id=?', (task_id,)).fetchone()
            payload = json.loads(task['payload'])
            dispatch.append({'task_id': task_id, 'backend': task['backend'],
                             **{k: payload[k] for k in ('repair_tier', 'required_model', 'repair_counts', 'automatic_escalation') if k in payload}})
        return dispatch

    @staticmethod
    def repair_routing(config, tier):
        return {'repair_tier': tier, 'required_model': config.get('sol_repair_model', 'gpt-6-sol') if tier == 'sol' else None,
                'repair_round_limit': config.get('body_repair_round_limit', 6),
                'automatic_escalation': tier == 'sol'}

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
        routes = self.repair_routes(db, config, stage, prior)
        rows, blocked = [], []
        for record_id, diagnostic in problems.items():
            row = dict(db.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone())
            used, tier = routes.get(record_id, (0, 'standard'))
            if row['locked']:
                blocked.append(record_id); continue
            row.update(used=used, tier=tier, diagnostic=diagnostic, metadata=contexts.get(str(row['ordinal']), {}))
            rows.append(row)
        tasks = []
        for used, tier in sorted({(r['used'], r['tier']) for r in rows}):
            for group in self.batches([r for r in rows if (r['used'], r['tier']) == (used, tier)],
                                     {**config, 'batch_records': config['review_batch_records']}, respect_groups=False):
                task_id = stage + '-round-' + uuid.uuid4().hex[:16]
                payload = {'task_id': task_id, 'stage': stage, 'revision': self.meta(db, 'revision'),
                           'source_hash': self.meta(db, 'source_hash'), 'review_round': round_number,
                           'review_repair_origin': origin,
                           **self.repair_routing(config, group[0]['tier']),
                           'repair_counts': {r['id']: r['used'] + 1 for r in group}, 'language': {'source': config['source_language'], 'target': config['target_language']},
                           'style': config['style'], 'context': [], 'game_background': '',
                           'glossary_version': self.meta(db, 'glossary'), 'records': [],
                           'terms': bridge.matched_terms(glossary, [r['source'] for r in group], config),
                           'revision_request': '整轮检查已结束；只修复本批诊断及审校证据，不重写无关表达。保存后等待全轮统一复查。'}
                for row in group:
                    candidate = db.execute('SELECT item FROM review_candidates WHERE record_id=? AND stage=?', (row['id'], stage)).fetchone()
                    candidate_item = json.loads(candidate['item']) if candidate else {}
                    inherited = self.inherit_exceptions(candidate_item, {'exceptions': json.loads(row['exceptions'])})
                    context_exceptions = inherited.get('exceptions', [])
                    if not isinstance(context_exceptions, list):
                        context_exceptions = json.loads(row['exceptions'])
                    payload['records'].append({'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                        'translation': row['translation'], 'exceptions': context_exceptions,
                        'locked': bool(row['locked']), 'metadata': row['metadata'],
                        'protection': [],  # Review source/translation use original variables, not SHH translation markers.
                        'repair_constraints': self.repair_constraints(row['source'], candidate_item.get('translation', row['translation']), context_exceptions, glossary, config),
                        'diagnostic': row['diagnostic'], 'candidate': candidate_item if candidate else None})
                    db.execute('UPDATE records SET stage=? WHERE id=?', ('translate' if stage == 'review' else 'polish', row['id']))
                value = fingerprint(payload); payload['input_hash'] = value
                db.execute('INSERT INTO tasks(id,stage,revision,ids,payload,input_hash,backend,status,attempt) VALUES (?,?,?,?,?,?,?,"pending",?)',
                           (task_id, stage, self.meta(db, 'revision'), encoded([r['id'] for r in group]), encoded(payload), value,
                            'codex' if group[0]['tier'] == 'sol' else config['stage_backends'].get(stage, config['backend']), 0))
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
                regression = self.repair_regression(db, config, stage, row['record_id'], original, item.get('translation', ''),
                                                    item.get('exceptions', original.get('exceptions', [])), glossary, row['task_id'])
                if regression and error:
                    error += '; Regression: previously satisfied terms lost: ' + ', '.join(regression)
                if error is None:
                    db.execute('UPDATE records SET translation=?,exceptions=?,stage=? WHERE id=?',
                               (value, encoded(exceptions), stage, row['record_id']))
                    passed.append(row['record_id'])
                    db.execute('INSERT OR IGNORE INTO review_proof_checks VALUES (?)',
                               (self.review_proof_key(item, original['source'], original['translation'], value),))
                db.execute('UPDATE review_candidates SET status=?,diagnostic=? WHERE record_id=? AND stage=?',
                           ('failed' if error else 'passed', error, row['record_id'], stage))
                checked.append({'id': row['record_id'], 'instance': row['instance'], 'task_id': row['task_id'],
                                'result': 'failed' if error else 'passed', 'diagnostic': error, 'regressed_terms': regression})
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
                      'repair_dispatch': self.repair_dispatch(db, tasks),
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
