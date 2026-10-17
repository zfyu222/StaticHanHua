"""Persist independent reviews, then validate and regroup only at round boundaries."""
import json
import uuid

import ainiee_bridge as bridge
from collections import Counter
from workflow_quality import review_failure_detail, nonempty


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
                    or not nonempty(change.get('source')) or not nonempty(change.get('reason'))):
                raise ValueError('Removing a context exception requires source, action=remove and reason')
            removed.add(change['source'])
        # Exception keys are exact frozen src values. Case-folding here silently
        # merged distinct glossary entries even in a case-sensitive project.
        merged = {e['source']: e for e in inherited
                  if isinstance(e, dict) and isinstance(e.get('source'), str) and e['source'] not in removed}
        for entry in incoming:
            if not isinstance(entry, dict) or not isinstance(entry.get('source'), str):
                return dict(item)
            if entry['source'] in removed:
                raise ValueError('Removed context exception must not also be returned in exceptions: ' + entry['source'])
            merged[entry['source']] = entry
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
        """Count each completed repair once across stages and revisions.

        Old stage-local counters are lower bounds; new payloads carry a global
        counter. Obsoleting a completed task must not erase its consumption.
        """
        prior = (db.execute('SELECT * FROM tasks ORDER BY rowid') if db is not None
                 and db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone() else (prior or []))
        counts, floors, global_floors, sol_counts = {}, {}, {}, {}
        for task in prior:
            task = dict(task)
            if task['status'] != 'done' and not task.get('result'):
                continue
            payload = json.loads(task['payload'])
            if not (payload.get('draft_scan_key') or payload.get('review_repair_origin')):
                continue
            task_stage = task.get('stage', stage)
            for record_id in json.loads(task['ids']):
                key = (record_id, task_stage)
                bucket = 'global' if payload.get('repair_budget_scope') == 'record' else task_stage
                counts[(record_id, bucket)] = counts.get((record_id, bucket), 0) + 1
                if bucket == 'global':
                    global_floors[record_id] = max(global_floors.get(record_id, 0), payload.get('repair_counts', {}).get(record_id, 0))
                else:
                    legacy = max(0, task['attempt'] - (task_stage != 'translate'))
                    floors[key] = max(floors.get(key, 0), payload.get('repair_counts', {}).get(record_id, legacy))
                if payload.get('repair_tier') == 'sol':
                    sol_counts[record_id] = sol_counts.get(record_id, 0) + 1
        for record_id, count in self.meta(db, 'draft_repair_attempts', {}).items():
            if record_id in global_floors:
                global_floors[record_id] = max(global_floors[record_id], count)
            else:
                floors[(record_id, 'translate')] = max(floors.get((record_id, 'translate'), 0), count)
        used = {}
        for key in set(counts) | set(floors):
            record_id, _ = key
            used[record_id] = used.get(record_id, 0) + max(counts.get(key, 0), floors.get(key, 0))
        for record_id, count in global_floors.items():
            used[record_id] = max(used.get(record_id, 0), count)
        forced = {i for ids in self.meta(db, 'repair_sol_ids', {}).values() for i in ids}
        stopped = self.repair_stops(db, config, sol_counts) if db is not None else {}
        return {i: (used.get(i, 0), 'stopped' if i in stopped else
                    'sol' if i in forced or i in sol_counts or used.get(i, 0) >= config.get('body_repair_round_limit', 6) else 'standard')
                for i in set(used) | forced | set(stopped)}

    def record_repair_outcome(self, db, task, record_id, diagnostic):
        """One immutable receipt per checked Sol repair, only at round boundaries."""
        from translation_workflow import encoded
        payload = json.loads(task['payload'])
        if payload.get('repair_tier') != 'sol':
            return
        db.execute('CREATE TABLE IF NOT EXISTS repair_outcomes(task_id TEXT,record_id TEXT,diagnostic TEXT,PRIMARY KEY(task_id,record_id))')
        # A corrected contract field / removed missing term changes this key.
        db.execute('INSERT OR IGNORE INTO repair_outcomes VALUES (?,?,?)',
                   (task['id'], record_id, encoded(diagnostic)))

    def repair_stops(self, db, config, sol_counts=None):
        if sol_counts is None:
            sol_counts = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone():
                for task in db.execute('SELECT * FROM tasks'):
                    if task['status'] != 'done' and not task['result']:
                        continue
                    payload = json.loads(task['payload'])
                    if payload.get('repair_tier') == 'sol':
                        for record_id in json.loads(task['ids']):
                            sol_counts[record_id] = sol_counts.get(record_id, 0) + 1
        stops = {i: 'sol_round_limit' for i, n in (sol_counts or {}).items()
                 if n >= config.get('sol_repair_round_limit', 3)}
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_outcomes'").fetchone():
            return stops
        history = {}
        for row in db.execute('SELECT * FROM repair_outcomes ORDER BY rowid'):
            history.setdefault(row['record_id'], []).append(row['diagnostic'])
        limit = config.get('sol_no_progress_limit', 2)
        for record_id, outcomes in history.items():
            recent = outcomes[-limit:]
            if len(recent) == limit and recent[0] != 'null' and len(set(recent)) == 1:
                stops[record_id] = 'sol_no_progress'
        return stops

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
                'repair_budget_scope': 'record',
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

    def plan_review_repairs(self, db, config, stage, problems, origin, diagnostic_details=None):
        """Pool across producer batches; retain each ID's attempts and provenance."""
        from translation_workflow import encoded, fingerprint
        total_records = len(problems)
        print(f'repair-plan progress: start; stage={stage}; origin={origin}; records={total_records}', flush=True)
        contexts = self.meta(db, 'contexts', {})
        glossary = self.glossary(db)
        prior = [dict(t) for t in db.execute('SELECT * FROM tasks WHERE stage=? AND revision=?',
                                            (stage, self.meta(db, 'revision')))]
        round_number = 1 + max((json.loads(t['payload']).get('review_round', 1) for t in prior), default=0)
        print('repair-plan progress: computing repair routes', flush=True)
        routes = self.repair_routes(db, config, stage, prior)
        print(f'repair-plan progress: loaded {len(prior)} prior tasks; routed {len(routes)} records', flush=True)
        rows, blocked = [], []
        route_interval = max(1, min(100, total_records // 20 or 1))
        for index, (record_id, diagnostic) in enumerate(problems.items(), 1):
            row = dict(db.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone())
            used, tier = routes.get(record_id, (0, 'standard'))
            if row['locked'] or tier == 'stopped':
                blocked.append(record_id); continue
            row.update(used=used, tier=tier, diagnostic=diagnostic, metadata=contexts.get(str(row['ordinal']), {}))
            rows.append(row)
            if index % route_interval == 0 or index == total_records:
                print(f'repair-plan progress: routed {index}/{total_records}; ready={len(rows)}; locked={len(blocked)}', flush=True)
        tasks = []
        batch_groups = []
        route_groups = sorted({(r['used'], r['tier']) for r in rows})
        for used, tier in route_groups:
            batch_groups.extend(self.review_batches([r for r in rows if (r['used'], r['tier']) == (used, tier)],
                                                    config, stage, 'codex' if tier == 'sol' else None))
        print(f'repair-plan progress: prepared {len(batch_groups)} task groups; building payloads', flush=True)
        for task_index, group in enumerate(batch_groups, 1):
            task_id = stage + '-round-' + uuid.uuid4().hex[:16]
            print(f'repair-plan progress: task {task_index}/{len(batch_groups)}; preparing {len(group)} records', flush=True)
            payload = {'task_id': task_id, 'stage': stage, 'revision': self.meta(db, 'revision'),
                       'source_hash': self.meta(db, 'source_hash'), 'review_round': round_number,
                       'review_repair_origin': origin,
                       **self.repair_routing(config, group[0]['tier']),
                       'repair_counts': {r['id']: r['used'] + 1 for r in group}, 'language': {'source': config['source_language'], 'target': config['target_language']},
                       'style': config['style'], 'context': [], 'game_background': config.get('game_background', ''),
                       'glossary_version': self.meta(db, 'glossary'), 'records': [],
                       'terms': bridge.matched_terms(glossary, [r['source'] for r in group], config),
                       'revision_request': '整轮检查已结束；只修复本批诊断及审校证据，不重写无关表达。保存后等待全轮统一复查。'}
            item_interval = max(1, min(10, len(group) // 4 or 1))
            for item_index, row in enumerate(group, 1):
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
                    'diagnostic': row['diagnostic'],
                    'diagnostic_detail': (diagnostic_details or {}).get(row['id'], review_failure_detail(row['diagnostic'])),
                    'candidate': candidate_item if candidate else None})
                db.execute('UPDATE records SET stage=? WHERE id=?', ('translate' if stage == 'review' else 'polish', row['id']))
                if item_index % item_interval == 0 or item_index == len(group):
                    print(f'repair-plan progress: task {task_index}/{len(batch_groups)}; records {item_index}/{len(group)}', flush=True)
            value = fingerprint(payload); payload['input_hash'] = value
            tasks.extend(self.allocate_task(db, payload,
                'codex' if group[0]['tier'] == 'sol' else config['stage_backends'].get(stage, config['backend'])))
        approvals = self.meta(db, 'quality_approvals', {}); approvals.pop('final', None)
        self.put(db, 'quality_approvals', approvals)
        print(f'repair-plan progress: complete; tasks={len(tasks)}; ready={len(rows)}; locked={len(blocked)}', flush=True)
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
            total_candidates = len(candidates)
            progress_every = max(1, min(250, total_candidates // 20 or 1))
            if total_candidates:
                print(f'review-scan progress: 0/{total_candidates}; passed=0; failed=0', flush=True)
            for index, row in enumerate(candidates, 1):
                task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone())
                payload = json.loads(task['payload']); payload['strict_review'] = True
                task['payload'] = encoded(payload)
                item, original = json.loads(row['item']), json.loads(row['original'])
                error = None
                detail = None
                try:
                    value, exceptions = self.validate_body_item(db, task, {}, item, original, config, rules, glossary)
                except ValueError as exc:
                    error = str(exc)
                    detail = review_failure_detail(exc)
                self.record_repair_outcome(db, task, row['record_id'], detail)
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
                                'result': 'failed' if error else 'passed', 'diagnostic': error,
                                'diagnostic_detail': detail, 'regressed_terms': regression})
                if index % progress_every == 0 or index == total_candidates:
                    print(f'review-scan progress: {index}/{total_candidates}; passed={len(passed)}; failed={len(checked) - len(passed)}', flush=True)
            problems = {r['record_id']: r['diagnostic'] for r in db.execute(
                'SELECT c.* FROM review_candidates c JOIN tasks t ON t.id=c.task_id '
                'WHERE c.stage=? AND t.revision=? AND t.status!="obsolete" AND c.status="failed"',
                (stage, self.meta(db, 'revision')))}
            diagnostic_details = {}
            # Include cached failures when a later scan prepares repairs without new candidates.
            for prior_report in db.execute('SELECT report FROM review_round_checks ORDER BY id'):
                previous = json.loads(prior_report['report'])
                if previous.get('stage') != stage:
                    continue
                for check in previous.get('checks', []):
                    if check.get('diagnostic_detail'):
                        diagnostic_details[check['id']] = check['diagnostic_detail']
            diagnostic_details.update({r['id']: r['diagnostic_detail'] for r in checked if r['diagnostic_detail']})
            tasks, blocked = self.plan_review_repairs(db, config, stage, problems, 'review-scan', diagnostic_details) if prepare_repairs and problems else ([], [])
            stop_reasons = self.repair_stops(db, config)
            blocked = sorted(set(blocked) | (set(problems) & set(stop_reasons)))
            required = {'review': ('review', 'polish', 'final_review'), 'final_review': ('final_review',)}[stage]
            complete = all(r['stage'] in required for r in db.execute('SELECT stage FROM records'))
            report = {'stage': stage, 'phase': 'round_checked' if checked else 'cached',
                      'scope': 'review_round', 'checked_records': len(checked), 'checks': checked,
                      'passed_ids': passed,
                      'failure_summary': {
                          'basis': 'first_failing_gate_in_this_scan; later checks may be blocked; not translation accuracy',
                          'checked_failures': dict(Counter(r['diagnostic_detail']['category'] for r in checked if r['diagnostic_detail'])),
                          'outstanding_failures': dict(Counter(
                              diagnostic_details.get(i, review_failure_detail(error))['category'] for i, error in problems.items())),
                          'regression_overlap': sum(bool(r['regressed_terms']) for r in checked)},
                      'failed_records': len(problems), 'problems': problems, 'repair_tasks': tasks,
                      'repair_dispatch': self.repair_dispatch(db, tasks),
                      'needs_attention_ids': blocked, 'clean': complete and not problems and not tasks}
            report['repair_stop_reasons'] = {i: reason for i, reason in stop_reasons.items() if i in blocked}
            report['status'] = 'needs_attention' if blocked else ('clean' if report['clean'] else 'needs_repair')
            if checked:
                cursor = db.execute('INSERT INTO review_round_checks(report) VALUES (?)', (encoded(report),))
                report['round'] = cursor.lastrowid
                archive = self.root / f'reports/review_rounds/{stage}_{cursor.lastrowid:05d}.json'
                write_json(archive, report); report['round_report'] = str(archive)
                self.put(db, 'review_generation', self.meta(db, 'review_generation', 0) + 1)
                self.event(db, 'review_round_checked', {'stage': stage, 'checked_ids': [r['id'] for r in checked], 'repair_tasks': tasks})
            write_json(path, report)
            return {k: v for k, v in {**report, 'report_file': str(path)}.items() if k not in ('checks', 'problems')}
