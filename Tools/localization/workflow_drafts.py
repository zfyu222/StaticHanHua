"""Save production drafts first; audit once at the phase boundary and pool repairs."""
import json
import uuid

import ainiee_bridge as bridge


class DraftMixin:
    @staticmethod
    def is_draft_task(task):
        return task['stage'] == 'translate' and not json.loads(task['payload']).get('strict_translation')

    @staticmethod
    def best_effort_restore(text, protection):
        for item in protection:
            text = text.replace(item['placeholder'], item['original'])
        return text

    def draft_tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS draft_candidates(record_id TEXT PRIMARY KEY,raw_item TEXT,protection TEXT,translation TEXT,source_hash TEXT,instance TEXT,task_id TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS draft_checks(record_id TEXT PRIMARY KEY,policy_key TEXT,signature TEXT,finding TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS draft_check_rounds(id INTEGER PRIMARY KEY,report TEXT)')

    def remember_draft(self, db, task, item, original, translation, instance):
        from translation_workflow import encoded, fingerprint
        self.draft_tables(db)
        source = db.execute('SELECT source FROM records WHERE id=?', (item['id'],)).fetchone()['source']
        db.execute('INSERT OR REPLACE INTO draft_candidates VALUES (?,?,?,?,?,?,?)',
                   (item['id'], encoded(item), encoded(original.get('protection', [])), translation,
                    fingerprint(source), instance, task['id']))

    def draft_audit_key(self, db, config, generation=None):
        from translation_workflow import fingerprint
        keys = ('source_language', 'target_language', 'case_sensitive', 'whole_word', 'protect_named_curly',
                'placeholder_family_overrides', 'exclusions')
        return fingerprint({'revision': self.meta(db, 'revision'), 'generation': self.meta(db, 'draft_generation', 0) if generation is None else generation,
                            'source': self.meta(db, 'source_hash'), 'glossary': self.meta(db, 'glossary'),
                            'rules': {k: config[k] for k in keys}})

    def draft_snapshot(self, db):
        self.draft_tables(db)
        rows = [dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
        if any(r['stage'] not in ('translate', 'review', 'polish', 'final_review') or r['translation'] is None for r in rows):
            raise ValueError('Complete all production drafts before the full draft scan')
        if db.execute('SELECT 1 FROM tasks WHERE revision=? AND status="running"', (self.meta(db, 'revision'),)).fetchone():
            raise ValueError('Finish in-flight work before the full draft scan')
        originals = {r['record_id']: r for r in db.execute('SELECT * FROM draft_candidates')}
        return rows, originals

    @staticmethod
    def draft_candidate(row, journal):
        from translation_workflow import fingerprint
        if (journal and journal['source_hash'] == fingerprint(row['source']) and journal['translation'] == row['translation']
                and row['stage'] == 'translate'):
            return json.loads(journal['raw_item']), json.loads(journal['protection'])
        return {'id': row['id'], 'translation': row['translation'], 'exceptions': json.loads(row['exceptions'])}, []

    def collect_draft_issues(self, db, config, record_ids=None):
        """Validate only this explicit scope; None means a full acceptance scan."""
        from translation_workflow import encoded
        rows, originals = self.draft_snapshot(db)
        if record_ids is not None:
            scope = set(record_ids)
            if scope - {r['id'] for r in rows}:
                raise ValueError('Unknown draft check ID')
            rows = [r for r in rows if r['id'] in scope]
        rules, glossary = self.rules(db, config), self.glossary(db)
        fake_task = {'stage': 'translate', 'payload': encoded({'strict_translation': True})}
        failures = []
        for row in rows:
            item, protection = self.draft_candidate(row, originals.get(row['id']))
            errors = []
            try:
                self.validate_body_item(db, fake_task, {}, item, {'protection': protection}, config, rules, glossary)
            except ValueError as exc:
                errors.append(str(exc))
            # Collect additional mechanical findings now, rather than spending
            # another model round discovering the next error in the same sentence.
            terms = bridge.matched_terms(glossary, [row['source']], config)
            try:
                exceptions = bridge.normalize_exception_sources(item.get('exceptions', []), terms, config['case_sensitive'])
                known = {t['src'] for t in terms}
                exempt = {e['source'] for e in exceptions if e['source'] in known}
            except ValueError as exc:
                errors.append(str(exc)); exempt = set()
            missing = [t['src'] for t in terms if t['src'] not in exempt and t['dst'] not in row['translation']
                       and not bridge.helper().is_keep_original_translation(t['dst'])]
            structure = bridge.structural_issues(row['source'], row['translation'], rules)
            if missing:
                errors.append('terminology mismatch: ' + ', '.join(missing))
            if structure:
                errors.append('structural mismatch: ' + ', '.join(structure))
            if errors:
                failures.append({'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                                 'translation': row['translation'], 'diagnostics': list(dict.fromkeys(errors)),
                                 'missing_terms': missing, 'structural_issues': structure})
        return rows, failures

    def ensure_draft_audit(self, db, config):
        key = self.draft_audit_key(db, config)
        receipt = self.meta(db, 'draft_audit', {})
        if receipt.get('key') == key and receipt.get('clean'):
            return
        report = self.scan_drafts_in_transaction(db, config, prepare_repairs=False)
        if not report['clean']:
            raise ValueError(f'Full draft scan found {report["failed_records"]} affected IDs; run draft-scan --prepare-repairs before independent review. Report: {report["report_file"]}')

    def check_drafts_before_review(self):
        # Commit a failed scan's receipts before blocking review. Otherwise the
        # prepare transaction rolls them back and the next call repeats a full scan.
        with self.db() as db:
            config = self.config(db)
            if db.execute('SELECT 1 FROM records WHERE translation IS NULL OR stage NOT IN '
                          '("translate","review","polish","final_review")').fetchone():
                return  # Preserve prepare's existing preceding-stage error.
            if not self.pilot_approved(db, config):
                return
            receipt = self.meta(db, 'draft_audit', {})
            if receipt.get('key') == self.draft_audit_key(db, config) and receipt.get('clean'):
                return
            report = self.scan_drafts_in_transaction(db, config, prepare_repairs=False)
        if not report['clean']:
            raise ValueError(f'Full draft scan found {report["failed_records"]} affected IDs; run draft-scan --prepare-repairs before independent review. Report: {report["report_file"]}')

    def draft_scan(self, prepare_repairs=False):
        with self.db() as db:
            return self.scan_drafts_in_transaction(db, self.config(db), prepare_repairs)

    def adopt_legacy_draft_scan(self, db, config, rows, signatures, policy):
        """Reuse a bound old full scan, even if its strict repair jobs have finished."""
        from translation_workflow import read_json, write_json, encoded
        if db.execute('SELECT 1 FROM draft_checks').fetchone():
            return None
        path = self.root / 'reports/full_draft_scan.json'
        if not path.exists():
            return None
        report = read_json(path)
        receipt = self.meta(db, 'draft_audit', {})
        if report.get('phase') or not report.get('key') or receipt.get('key') != report['key']:
            return None
        completed = [json.loads(t['ids']) for t in db.execute(
            'SELECT ids,payload FROM tasks WHERE stage="translate" AND revision=? AND status="done"',
            (self.meta(db, 'revision'),)) if json.loads(t['payload']).get('draft_scan_key') == report['key']]
        generation = self.meta(db, 'draft_generation', 0) - len(completed)
        if generation < 0 or report['key'] != self.draft_audit_key(db, config, generation):
            return None  # Some other input changed; the old scan cannot prove this baseline.
        ids = set(signatures)
        found = report.get('failures')
        if (report.get('records') != len(rows) or not isinstance(found, list)
                or report.get('failed_records') != len(found)
                or any(not isinstance(f, dict) or f.get('id') not in ids for f in found)
                or len({f['id'] for f in found}) != len(found)):
            return None
        findings = {f['id']: f for f in found}
        repaired = {i for group in completed for i in group}
        for record_id in ids:
            db.execute('INSERT INTO draft_checks VALUES (?,?,?,?)',
                       (record_id, policy, '' if record_id in repaired else signatures[record_id],
                        encoded(findings.get(record_id))))
        archived = self.root / f'reports/draft_checks/legacy_initial_{report["key"][:12]}.json'
        write_json(archived, {**report, 'phase': 'initial_full', 'scope': 'full', 'execution': 'reused',
                              'checked_ids': [r['id'] for r in rows], 'recheck_required_ids': sorted(repaired)})
        self.put(db, 'draft_check_glossary', {'version': self.meta(db, 'glossary'), 'analysis': self.glossary(db)})
        self.put(db, 'draft_imported_baseline', str(archived))
        self.event(db, 'draft_scan_imported', {'key': report['key'], 'report_file': str(archived),
                                              'recheck_required_ids': sorted(repaired)})
        return str(archived)

    def scan_drafts_in_transaction(self, db, config, prepare_repairs):
        from translation_workflow import write_json, encoded, fingerprint
        rows, originals = self.draft_snapshot(db)
        key = self.draft_audit_key(db, config)
        # Generation changes when a repair is submitted, but must not invalidate
        # the unchanged rows' checks. Policy changes do invalidate their receipts.
        keys = ('source_language', 'target_language', 'case_sensitive', 'whole_word', 'protect_named_curly',
                'placeholder_family_overrides', 'exclusions')
        policy = fingerprint({'validator': 2,
                              'source': self.meta(db, 'source_hash'),
                              'rules': {k: config[k] for k in keys}})
        signatures = {}
        for row in rows:
            journal = originals.get(row['id'])
            raw = ([journal['raw_item'], journal['protection']] if journal
                   and journal['source_hash'] == fingerprint(row['source'])
                   and journal['translation'] == row['translation'] else None)
            # Advancing into semantic review does not change an unchanged text's
            # signature. Keep raw-marker evidence stable across stage changes.
            signatures[row['id']] = fingerprint([row['source'], row['translation'], row['exceptions'], raw])
        self.adopt_legacy_draft_scan(db, config, rows, signatures, policy)
        previous = {r['record_id']: dict(r) for r in db.execute('SELECT * FROM draft_checks WHERE policy_key=?', (policy,))}
        changed = [r['id'] for r in rows if r['id'] not in previous or previous[r['id']]['signature'] != signatures[r['id']]]
        glossary_state = self.meta(db, 'draft_check_glossary', {})
        glossary_version = self.meta(db, 'glossary')
        if previous and glossary_state.get('version') != glossary_version:
            old, new = glossary_state.get('analysis'), self.glossary(db)
            if old is None:
                previous = {}  # Legacy receipts do not prove which terms were checked.
            else:
                def matched(analysis, source):
                    return sorted((t['src'], t['dst']) for t in bridge.matched_terms(analysis, [source], config))
                affected = {r['id'] for r in rows if matched(old, r['source']) != matched(new, r['source'])}
                affected.update(changed)
                changed = [r['id'] for r in rows if r['id'] in affected]
        findings = {r['id']: json.loads(previous[r['id']]['finding']) for r in rows
                    if r['id'] in previous and previous[r['id']]['finding'] != 'null'}
        rounds = []

        def check(phase, ids):
            checked, found = self.collect_draft_issues(db, config, ids)
            by_id = {f['id']: f for f in found}
            for row in checked:
                record_id = row['id']
                findings.pop(record_id, None)
                if record_id in by_id:
                    findings[record_id] = by_id[record_id]
                db.execute('INSERT OR REPLACE INTO draft_checks VALUES (?,?,?,?)',
                           (record_id, policy, signatures[record_id], encoded(by_id.get(record_id))))
            rounds.append({'phase': phase, 'scope': 'issues' if ids is not None else 'full',
                           'checked_ids': [r['id'] for r in checked], 'checked_records': len(checked),
                           'passed_ids': [r['id'] for r in checked if r['id'] not in by_id],
                           'failures': found, 'remaining_failed_ids': list(findings)})

        if not previous:
            check('initial_full', None)
        elif changed:
            check('issue_recheck', changed)
            if not findings:
                # Only after the repair pool has converged do we run the one
                # final full acceptance. Never do this after every repair round.
                check('final_full', None)
        if glossary_state.get('version') != glossary_version:
            self.put(db, 'draft_check_glossary', {'version': glossary_version, 'analysis': self.glossary(db)})
        failures = [findings[r['id']] for r in rows if r['id'] in findings]
        existing = [r for r in db.execute('SELECT * FROM tasks WHERE revision=? AND status IN ("pending","failed")', (self.meta(db, 'revision'),))
                    if json.loads(r['payload']).get('strict_translation')]
        tasks = [r['id'] for r in existing]
        if prepare_repairs and failures and not existing:
            failed_by_id = {r['id']: r for r in failures}
            contexts = self.meta(db, 'contexts', {})
            eligible = []
            for row in rows:
                if row['id'] in failed_by_id:
                    if row['locked']:
                        raise ValueError('Unlock affected confirmed translation before preparing draft repair: ' + row['id'])
                    row['metadata'] = contexts.get(str(row['ordinal']), {})
                    eligible.append(row)
            # Same diagnostic kinds are adjacent; group boundaries and original
            # producer-batch boundaries do not split the repair pool.
            eligible.sort(key=lambda r: (bool(failed_by_id[r['id']]['structural_issues']), bool(failed_by_id[r['id']]['missing_terms']), r['ordinal']))
            glossary, rules = self.glossary(db), self.rules(db, config)
            for group in self.batches(eligible, config, respect_groups=False):
                task_id = 'draft-repair-' + uuid.uuid4().hex[:20]
                payload = {'task_id': task_id, 'stage': 'translate', 'revision': self.meta(db, 'revision'),
                           'source_hash': self.meta(db, 'source_hash'), 'strict_translation': True, 'draft_scan_key': key,
                           'language': {'source': config['source_language'], 'target': config['target_language']},
                           'style': config['style'], 'game_background': '', 'context': [], 'records': [],
                           'glossary_version': self.meta(db, 'glossary'),
                           'terms': bridge.matched_terms(glossary, [r['source'] for r in group], config),
                           'revision_request': '全文草稿已完成；本批为跨批汇总的失败语句。只修复列出的诊断，保留其他已保存译文。'}
                for row in group:
                    protected, protection = bridge.protect(row['source'], row['id'], rules)
                    payload['records'].append({'id': row['id'], 'ordinal': row['ordinal'], 'source': protected,
                        'translation': row['translation'], 'protection': protection, 'metadata': row['metadata'],
                        'exceptions': json.loads(row['exceptions']), 'locked': bool(row['locked']),
                        'diagnostic': failed_by_id[row['id']]['diagnostics'],
                        'candidate': {'id': row['id'], 'translation': row['translation']}})
                signature = fingerprint(payload); payload['input_hash'] = signature
                db.execute('INSERT INTO tasks(id,stage,revision,ids,payload,input_hash,backend,status) VALUES (?,?,?,?,?,?,?,"pending")',
                           (task_id, 'translate', self.meta(db, 'revision'), encoded([r['id'] for r in group]), encoded(payload),
                            signature, config['stage_backends'].get('translate', config['backend'])))
                tasks.append(task_id)
        clean = not failures and not tasks
        report_file = self.root / 'reports/full_draft_scan.json'
        task_states = [dict(r) for r in db.execute('SELECT id,status,attempt,ids FROM tasks WHERE revision=?',
                                                  (self.meta(db, 'revision'),))
                       if r['id'].startswith('draft-repair-')]
        for state in task_states:
            state['ids'] = json.loads(state['ids'])
        round_files = []
        for report in rounds:
            report.update({'key': key, 'policy_key': policy, 'repair_status': task_states,
                           'result': 'passed' if not report['remaining_failed_ids'] else 'needs_repair'})
            cursor = db.execute('INSERT INTO draft_check_rounds(report) VALUES (?)', (encoded(report),))
            report['round'] = cursor.lastrowid
            path = self.root / f'reports/draft_checks/round_{cursor.lastrowid:05d}.json'
            write_json(path, report)
            db.execute('UPDATE draft_check_rounds SET report=? WHERE id=?', (encoded(report), cursor.lastrowid))
            round_files.append(str(path))
        summary = {'key': key, 'records': len(rows), 'failed_records': len(failures),
                   'clean': clean, 'failures': failures, 'repair_tasks': tasks,
                   'phase': rounds[-1]['phase'] if rounds else 'cached',
                   'checked_records': sum(r['checked_records'] for r in rounds),
                   'checked_rounds': [{k: r[k] for k in ('round', 'phase', 'scope', 'checked_records', 'result')} for r in rounds],
                   'round_reports': round_files, 'repair_status': task_states,
                   'imported_baseline': self.meta(db, 'draft_imported_baseline')}
        write_json(report_file, summary)
        self.put(db, 'draft_audit', {'key': key, 'clean': clean})
        self.event(db, 'draft_scan', {k: summary[k] for k in ('key', 'phase', 'checked_records', 'failed_records', 'repair_tasks', 'round_reports')})
        return {k: v for k, v in {**summary, 'report_file': str(report_file)}.items() if k not in ('failures', 'repair_status')}
