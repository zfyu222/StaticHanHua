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

    def remember_draft(self, db, task, item, original, translation, instance):
        from translation_workflow import encoded, fingerprint
        self.draft_tables(db)
        source = db.execute('SELECT source FROM records WHERE id=?', (item['id'],)).fetchone()['source']
        db.execute('INSERT OR REPLACE INTO draft_candidates VALUES (?,?,?,?,?,?,?)',
                   (item['id'], encoded(item), encoded(original.get('protection', [])), translation,
                    fingerprint(source), instance, task['id']))

    def draft_audit_key(self, db, config):
        from translation_workflow import fingerprint
        keys = ('source_language', 'target_language', 'case_sensitive', 'whole_word', 'protect_named_curly',
                'placeholder_family_overrides', 'exclusions')
        return fingerprint({'revision': self.meta(db, 'revision'), 'generation': self.meta(db, 'draft_generation', 0),
                            'source': self.meta(db, 'source_hash'), 'glossary': self.meta(db, 'glossary'),
                            'rules': {k: config[k] for k in keys}})

    def collect_draft_issues(self, db, config):
        """Full local checks, including old accepted drafts without new journals."""
        from translation_workflow import encoded, fingerprint
        self.draft_tables(db)
        rows = [dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
        if any(r['stage'] not in ('translate', 'review', 'polish', 'final_review') or r['translation'] is None for r in rows):
            raise ValueError('Complete all production drafts before the full draft scan')
        if db.execute('SELECT 1 FROM tasks WHERE revision=? AND status="running"', (self.meta(db, 'revision'),)).fetchone():
            raise ValueError('Finish in-flight work before the full draft scan')
        originals = {r['record_id']: r for r in db.execute('SELECT * FROM draft_candidates')}
        rules, glossary = self.rules(db, config), self.glossary(db)
        fake_task = {'stage': 'translate', 'payload': encoded({'strict_translation': True})}
        failures = []
        for row in rows:
            journal = originals.get(row['id'])
            item = {'id': row['id'], 'translation': row['translation'], 'exceptions': json.loads(row['exceptions'])}
            protection = []
            if (journal and journal['source_hash'] == fingerprint(row['source']) and journal['translation'] == row['translation']
                    and row['stage'] == 'translate'):
                item, protection = json.loads(journal['raw_item']), json.loads(journal['protection'])
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

    def draft_scan(self, prepare_repairs=False):
        with self.db() as db:
            return self.scan_drafts_in_transaction(db, self.config(db), prepare_repairs)

    def scan_drafts_in_transaction(self, db, config, prepare_repairs):
        from translation_workflow import write_json, encoded, fingerprint
        rows, failures = self.collect_draft_issues(db, config)
        key = self.draft_audit_key(db, config)
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
        write_json(report_file, {'key': key, 'records': len(rows), 'failed_records': len(failures),
                                'clean': clean, 'failures': failures, 'repair_tasks': tasks})
        self.put(db, 'draft_audit', {'key': key, 'clean': clean})
        self.event(db, 'draft_scan', {'key': key, 'failed_records': len(failures), 'repair_tasks': tasks})
        return {'report_file': str(report_file), 'records': len(rows), 'failed_records': len(failures),
                'repair_tasks': tasks, 'clean': clean}
