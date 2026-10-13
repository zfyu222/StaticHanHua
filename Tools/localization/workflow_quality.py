"""Evidence-backed pilot, independent review, Sol acceptance and runtime checks.

Reports propose checks; only the orchestrating Sol writes acceptance decisions.
Worker IDs are actual agent instance IDs supplied by the trusted orchestrator.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

REVIEW_CHECKS = ('accuracy', 'completeness', 'terminology', 'tone', 'logic', 'placeholders')
GLOSSARY_CHECKS = ('source', 'translation', 'category', 'consistency')


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def check_dimensions(checks, expected):
    if not isinstance(checks, dict) or set(checks) != set(expected):
        raise ValueError('Structured review checks must cover: ' + ', '.join(expected))
    for name, item in checks.items():
        if not isinstance(item, dict) or item.get('status') not in ('pass', 'corrected', 'not_applicable') or not nonempty(item.get('evidence')):
            raise ValueError('Each review check requires status and specific evidence: ' + name)


def validate_review(item, source, before, after):
    check_dimensions(item.get('checks'), REVIEW_CHECKS)
    if source.strip() and any(item['checks'][name]['status'] == 'not_applicable' for name in ('accuracy', 'completeness', 'logic')):
        raise ValueError('Meaning/completeness/logic checks cannot be skipped for non-empty text')
    excerpt = item.get('source_excerpt')
    if not isinstance(excerpt, str) or (source and not excerpt.strip()) or excerpt not in source:
        raise ValueError('Review source_excerpt must cite the assigned original text')
    changes = item.get('changes', [])
    if not isinstance(changes, list):
        raise ValueError('Review changes must be a list')
    if before != after and not changes:
        raise ValueError('Review correction requires before/after and an error category')
    for change in changes:
        if (not isinstance(change, dict) or change.get('before') != before or change.get('after') != after
                or change.get('category') not in REVIEW_CHECKS or change.get('severity') not in ('minor', 'major', 'critical')
                or not nonempty(change.get('reason')) or change.get('source_excerpt') != excerpt or before == after):
            raise ValueError('Invalid review change evidence')
    if before != after and not any(c['status'] == 'corrected' for c in item['checks'].values()):
        raise ValueError('Corrected translation must identify a corrected check')


def validate_glossary_reviews(decisions, payload):
    reviews = decisions.get('entry_reviews')
    refs = {e['ref']: e for e in payload['entries']}
    if (not isinstance(reviews, list) or any(not isinstance(r, dict) for r in reviews)
            or len(reviews) != len(refs) or {r.get('ref') for r in reviews} != set(refs)):
        raise ValueError('Glossary entry_reviews must cover each assigned ref exactly once')
    contexts = {r['ordinal']: r['source'] for r in payload['records']}
    for review in reviews:
        check_dimensions(review.get('checks'), GLOSSARY_CHECKS)
        rows = review.get('evidence_rows')
        excerpt = review.get('source_excerpt')
        entry = refs[review['ref']]['row']
        if (not isinstance(rows, list) or not rows or any(type(n) is not int or n not in entry.get('evidence_rows', []) for n in rows)
                or not nonempty(excerpt) or not any(excerpt in contexts.get(n, '') for n in rows)
                or not nonempty(review.get('reason'))):
            raise ValueError('Glossary review requires cited source context and specific reason')


def risk_reasons(row):
    """Heuristic candidates, never a semantic pass/fail verdict."""
    text, metadata = row['source'], row.get('metadata', {})
    flags = []
    patterns = {
        'numbers': r'\d',
        'negation': r"\b(?:not|never|no|without|unless|cannot|can't|don't|doesn't|won't)\b",
        'conditions': r'\b(?:if|unless|only|until|before|after|must|either|except)\b',
        'relationships': r'\b(?:father|mother|brother|sister|son|daughter|husband|wife|master|servant)\b',
        'variables': r'\{[^{}]+\}|\[[^\[\]]+\]|%[sdif]|\$\w+',
    }
    for name, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            flags.append(name)
    if len(text) >= 600:
        flags.append('long_text')
    if str(metadata.get('text_type', '')).casefold() in ('quest', 'objective', 'choice', 'ui', '任务', '任务目标', '选项'):
        flags.append('interaction')
    if text.strip() and len(text) <= 30 and not (metadata.get('scene') or metadata.get('speaker') or metadata.get('note')):
        flags.append('short_without_context')
    if row.get('exceptions') not in (None, '[]', []):
        flags.append('term_exception')
    return flags


def evenly(rows, count):
    if not rows or count <= 0:
        return []
    if len(rows) <= count:
        return rows
    return [rows[i * (len(rows) - 1) // max(1, count - 1)] for i in range(count)]


class QualityMixin:
    def restore_pilot_baseline(self, db):
        for row in self.meta(db, 'pilot_prior', []) or []:
            db.execute('UPDATE records SET stage=?,translation=?,exceptions=? WHERE id=?',
                       (row['stage'], row['translation'], row['exceptions'], row['id']))
        self.put(db, 'pilot_prior', None)

    def quality_rows(self, db):
        contexts = self.meta(db, 'contexts', {})
        return [{**dict(r), 'metadata': contexts.get(str(r['ordinal']), {})}
                for r in db.execute('SELECT * FROM records ORDER BY ordinal')]

    def pilot_ids(self, db, records, config):
        selected = self.meta(db, 'pilot_selection')
        if selected is not None:
            return set(selected)
        ordinals = config['pilot_ordinals']
        if ordinals:
            if any(n > len(records) for n in ordinals):
                raise ValueError('pilot_ordinals exceeds source record count')
            picks = [r for r in records if r['ordinal'] in ordinals]
        else:
            # Round robin across text types, speakers, scenes and risk dimensions.
            buckets = defaultdict(list)
            for row in records:
                metadata = row.get('metadata', {})
                keys = [('type', str(metadata.get('text_type', 'unknown'))),
                        ('speaker', str(metadata.get('speaker', 'unknown'))),
                        ('scene', str(metadata.get('scene', metadata.get('group', 'unknown'))))]
                keys.extend(('risk', flag) for flag in risk_reasons(row))
                keys.append(('length', 'long' if len(row['source']) >= 300 else 'short'))
                if not row['source'].strip():
                    keys.append(('special', 'empty'))
                for key in keys:
                    buckets[key].append(row)
            queues = [evenly(rows, config['pilot_records']) for _, rows in sorted(buckets.items())]
            picks, seen = [], set()
            for offset in range(config['pilot_records']):
                for queue in queues:
                    if offset < len(queue) and queue[offset]['id'] not in seen:
                        picks.append(queue[offset]); seen.add(queue[offset]['id'])
                        if len(picks) >= config['pilot_records']:
                            break
                if len(picks) >= config['pilot_records']:
                    break
        selected = [r['id'] for r in picks]
        self.put(db, 'pilot_selection', selected)
        return set(selected)

    def pilot_key(self, db, config):
        keys = ('style', 'game_background', 'source_language', 'target_language', 'case_sensitive',
                'whole_word', 'protect_named_curly', 'placeholder_family_overrides', 'exclusions',
                'pilot_records', 'pilot_ordinals', 'quality_version')
        return signature({'source': self.meta(db, 'source_hash'), 'glossary': self.meta(db, 'glossary'),
                          'rules': {k: config[k] for k in keys}})

    def pilot_approved(self, db, config):
        receipt = self.meta(db, 'quality_approvals', {}).get('pilot', {})
        if receipt.get('pilot_key') != self.pilot_key(db, config):
            return False
        path = self.root / f'reports/accepted/pilot_{receipt["report_hash"][:16]}/report.json'
        from translation_workflow import read_json
        if not path.is_file():
            return False
        report = read_json(path)
        stored = report.pop('report_hash', None)
        return stored == receipt['report_hash'] == signature(report)

    def independent_worker(self, db, task, instance):
        stage = task['stage']
        preceding = {'glossary_review': ('extract',), 'pilot_review': ('pilot_translate',),
                     'review': ('translate', 'pilot_translate'),
                     'final_review': ('polish', 'translate', 'pilot_translate')}.get(stage)
        if not preceding:
            return True
        ids = set(json.loads(task['ids']))
        if stage == 'glossary_review':
            payload = json.loads(task['payload'])
            ordinals = {n for e in payload.get('entries', []) for n in e['row'].get('evidence_rows', [])}
            ids.update(r['id'] for r in db.execute('SELECT id,ordinal FROM records') if r['ordinal'] in ordinals)
        authored = db.execute('SELECT record_id,stage FROM quality_authors WHERE instance=?', (instance,)).fetchall()
        return not any(r['record_id'] in ids and r['stage'] in preceding for r in authored)

    def latest_reviews(self, db, kind):
        reviews = {}
        # A downstream batch may contain both repaired and unaffected records.
        # Retain completed evidence for untouched records even when that task is obsolete.
        # Submission order, not task creation order, makes a reopened task supersede old evidence.
        submitted = {}
        for event in db.execute('SELECT id,detail FROM events WHERE kind="submit" ORDER BY id'):
            submitted[json.loads(event['detail'])['task']] = event['id']
        tasks = sorted(db.execute('SELECT * FROM tasks WHERE status IN ("done","obsolete") AND result IS NOT NULL '
                                  'AND stage IN ("pilot_review","review","final_review")').fetchall(),
                       key=lambda t: submitted.get(t['id'], 0))
        for task in tasks:
            if (kind == 'pilot') != (task['stage'] == 'pilot_review'):
                continue
            payload = json.loads(task['payload'])
            owner = db.execute('SELECT instance FROM quality_attempts WHERE task_id=? AND token=?', (task['id'], task['lease_token'])).fetchone()
            originals = {r['id']: r for r in payload['records']}
            for item in json.loads(task['result'])['records']:
                reviews[item['id']] = {'task_id': task['id'], 'instance': owner['instance'] if owner else None,
                                      'stage': task['stage'], 'before': originals[item['id']]['translation'], **item}
        return reviews

    def build_quality_report(self, db, kind, config):
        if kind not in ('pilot', 'final'):
            raise ValueError('Quality report kind must be pilot or final')
        self.glossary(db)
        all_rows = self.quality_rows(db)
        pilot = self.pilot_ids(db, all_rows, config)
        rows = [r for r in all_rows if r['id'] in pilot] if kind == 'pilot' else all_rows
        required_stage = 'pilot_review' if kind == 'pilot' else ('final_review' if config['polish'] else 'review')
        if any(r['stage'] != required_stage for r in rows):
            raise ValueError('Complete the required semantic review before quality report: ' + required_stage)
        if db.execute('SELECT 1 FROM tasks WHERE revision=? AND status IN ("running","pending","failed")', (self.meta(db, 'revision'),)).fetchone():
            raise ValueError('Finish all current tasks before Sol review')
        reviews = self.latest_reviews(db, kind)
        risks, ordinary = [], []
        for row in rows:
            review = reviews.get(row['id'])
            if not review or not review['instance'] or review['translation'] != row['translation']:
                raise ValueError('Missing current review provenance: ' + row['id'])
            validate_review(review, row['source'], review['before'], row['translation'])
            reasons = risk_reasons(row)
            if review.get('changes'):
                reasons.append('review_correction')
            if review.get('issues'):
                reasons.append('review_warning')
            record = {'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                      'translation': row['translation'], 'metadata': row['metadata'],
                      'risk_reasons': reasons, 'review': review}
            (risks if reasons else ordinary).append(record)
        selected = risks + (ordinary if kind == 'pilot' else evenly(ordinary, 20))
        groups = defaultdict(list)
        speakers = defaultdict(list)
        for row in rows:
            groups[row['source']].append(row)
            if row['metadata'].get('speaker'):
                speakers[str(row['metadata']['speaker'])].append(row)
        consistency = []
        for source, group in sorted(groups.items()):
            if len({r['translation'] for r in group}) > 1:
                consistency.append({'id': 'C:' + signature(source)[:16], 'kind': 'same_source_multiple_translations',
                                    'source': source, 'records': group})
        for speaker, group in sorted(speakers.items()):
            consistency.append({'id': 'S:' + signature(speaker)[:16], 'kind': 'speaker_voice',
                                'speaker': speaker, 'record_ids': [r['id'] for r in group],
                                'examples': evenly(group, 12)})
        # Include glossary findings and actual semantic changes in the pilot acceptance.
        glossary_checks = []
        glossary_info = self.meta(db, 'glossary')
        if kind == 'pilot' and glossary_info:
            built = (self.root / glossary_info['path']).parent
            from translation_workflow import read_json
            changes_path = built / 'glossary_review_changes.json'
            if changes_path.exists():
                glossary_checks.append({'id': 'G:review_changes', 'report': read_json(changes_path),
                                        'reviewed_glossary': glossary_info['path']})
        required = [r['id'] for r in selected] + [g['id'] for g in consistency + glossary_checks]
        report = {'schema': 2, 'kind': kind, 'pilot_key': self.pilot_key(db, config),
                  'source_sha256': self.meta(db, 'source_hash'), 'glossary': glossary_info,
                  'snapshot_hash': signature({'rows': rows, 'reviews': {r['id']: reviews[r['id']] for r in rows},
                                              'polish': config['polish']}),
                  'coverage': {'records': len(rows), 'text_types': dict(Counter(str(r['metadata'].get('text_type', 'unknown')) for r in rows)),
                               'missing_context': sum(not r['metadata'] for r in rows),
                               'risk_records': len(risks), 'ordinary_sample': len(selected) - len(risks)},
                  'risk_detection': 'Heuristic candidates plus reviewer findings; absence of flags is not proof of correctness.',
                  'records_to_check': selected, 'consistency': consistency, 'glossary_checks': glossary_checks,
                  'required_decisions': required}
        report['report_hash'] = signature(report)
        return report

    def quality_report(self, kind):
        from translation_workflow import write_json
        with self.db() as db:
            config = self.config(db)
            report = self.build_quality_report(db, kind, config)
            path = self.root / f'reports/{kind}_quality_review.json'
            write_json(path, report)
            write_json(self.root / f'reports/{kind}_sol_decisions.template.json', {
                'report_hash': report['report_hash'], 'reviewer': 'sol', 'summary': '',
                'decisions': [{'id': record_id, 'disposition': 'pending', 'reason': ''} for record_id in report['required_decisions']]})
            return {'report_file': str(path), 'report_hash': report['report_hash'], 'coverage': report['coverage']}

    def approve_quality(self, kind, decisions):
        from translation_workflow import write_json
        with self.db() as db:
            config = self.config(db)
            report = self.build_quality_report(db, kind, config)
            entries = decisions.get('decisions', [])
            if (decisions.get('report_hash') != report['report_hash'] or decisions.get('reviewer') != 'sol'
                    or not nonempty(decisions.get('summary')) or not isinstance(entries, list)
                    or any(not isinstance(e, dict) for e in entries)
                    or len(entries) != len(report['required_decisions'])
                    or {e.get('id') for e in entries} != set(report['required_decisions'])):
                raise ValueError('Sol decisions must bind the current report and cover every requested check exactly once')
            if any(e.get('disposition') not in ('pass', 'contextual') or not nonempty(e.get('reason')) for e in entries):
                raise ValueError('Unresolved Sol checks; return defective tasks for revision before approval')
            receipt = {'report_hash': report['report_hash'], 'snapshot_hash': report['snapshot_hash'],
                       'pilot_key': report['pilot_key'], 'reviewer': 'sol', 'decisions': decisions}
            approvals = self.meta(db, 'quality_approvals', {})
            approvals[kind] = receipt
            self.put(db, 'quality_approvals', approvals)
            folder = self.root / f'reports/accepted/{kind}_{report["report_hash"][:16]}'
            write_json(folder / 'report.json', report)
            write_json(folder / 'sol_decisions.json', decisions)
            if kind == 'pilot':
                # Pilot never replaces the production baseline, including unaffected revisions.
                self.restore_pilot_baseline(db)
            self.event(db, 'approve_quality', {'kind': kind, 'report_hash': report['report_hash']})
            return {'status': 'approved', 'kind': kind, 'report_hash': report['report_hash']}

    def require_final_approval(self, db, config):
        if not self.pilot_approved(db, config):
            raise ValueError('Sol pilot approval required')
        report = self.build_quality_report(db, 'final', config)
        receipt = self.meta(db, 'quality_approvals', {}).get('final', {})
        if receipt.get('report_hash') != report['report_hash']:
            raise ValueError('Current Sol risk/consistency approval required before export')
        return {'report_hash': receipt['report_hash'], 'reviewer': 'sol'}

    def reopen_body(self, task_id, reason):
        """Return a rejected batch to a subagent, preserving attempt budgets and locks."""
        with self.db() as db:
            config = self.config(db)
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not nonempty(reason) or not task or task['status'] != 'done' or task['stage'] not in ('pilot_review', 'review', 'final_review') or task['revision'] != self.meta(db, 'revision'):
                raise ValueError('Only completed current body review tasks can be reopened with reason')
            if db.execute('SELECT 1 FROM tasks WHERE status="running"').fetchone():
                raise ValueError('Finish/recover in-flight tasks before reopening a review')
            if task['stage'] == 'pilot_review' and self.pilot_approved(db, config):
                raise ValueError('Accepted pilot is immutable; revise configuration/glossary and run a new pilot')
            if task['attempt'] >= config['max_attempts']:
                raise ValueError('Attempt limit reached')
            ids = set(json.loads(task['ids']))
            # Later stage tasks touching these records must not override the repair.
            order = {'pilot_review': 0, 'review': 1, 'polish': 2, 'final_review': 3}
            for other in db.execute('SELECT * FROM tasks WHERE revision=?', (task['revision'],)).fetchall():
                if other['id'] != task_id and order.get(other['stage'], -1) > order[task['stage']] and ids.intersection(json.loads(other['ids'])):
                    db.execute('UPDATE tasks SET status="obsolete" WHERE id=?', (other['id'],))
            payload = json.loads(task['payload'])
            for row in payload['records']:
                current = db.execute('SELECT translation,locked,exceptions FROM records WHERE id=?', (row['id'],)).fetchone()
                row.update(translation=current['translation'], locked=bool(current['locked']), exceptions=json.loads(current['exceptions']))
                previous = {'pilot_review': 'pilot_translate', 'review': 'translate', 'final_review': 'polish'}[task['stage']]
                db.execute('UPDATE records SET stage=? WHERE id=?', (previous, row['id']))
            payload['revision_request'] = reason
            payload.pop('input_hash', None)
            input_hash = signature(payload)
            payload['input_hash'] = input_hash
            db.execute('UPDATE tasks SET payload=?,input_hash=?,status="pending",lease_token=NULL,lease_until=NULL,ready_at=0,result=NULL WHERE id=?',
                       (json.dumps(payload, ensure_ascii=False), input_hash, task_id))
            approvals = self.meta(db, 'quality_approvals', {})
            approvals.pop('final', None)
            if task['stage'] == 'pilot_review':
                approvals.pop('pilot', None)
            self.put(db, 'quality_approvals', approvals)
            self.event(db, 'reopen_body', {'task': task_id, 'reason': reason})

    def write_game_check_template(self, export):
        from translation_workflow import write_json
        write_json(self.root / 'reports/in_game_check.template.json', {
            'translation_sha256': export['translation_sha256'], 'game_version': '', 'platform': '',
            'payload_files': [], 'evidence_files': [],
            'checks': [{'category': category, 'status': 'pending', 'scene': '', 'notes': ''}
                       for category in ('fonts', 'layout', 'ui', 'dialogue', 'quest')]})

    def checked_files(self, files):
        if not isinstance(files, list) or not files:
            raise ValueError('At least one payload/evidence file is required')
        checked = []
        for entry in files:
            if not isinstance(entry, dict) or not nonempty(entry.get('path')):
                raise ValueError('File evidence requires path and sha256')
            relative = Path(entry['path'])
            path = (self.project / relative).resolve()
            if relative.is_absolute() or '..' in relative.parts or (relative.parts and relative.parts[0].casefold() == 'game') or not path.is_relative_to(self.project) or not path.is_file():
                raise ValueError('Payload/evidence must be saved inside the localization project, outside Game links')
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if entry.get('sha256') != actual:
                raise ValueError('Payload/evidence file hash mismatch')
            record = {'path': relative.as_posix(), 'sha256': actual}
            if 'game_path' in entry:
                destination = PurePosixPath(str(entry['game_path']).replace('\\', '/'))
                if destination.is_absolute() or '..' in destination.parts or ':' in str(destination) or str(destination) in ('', '.'):
                    raise ValueError('game_path must be a safe game-root relative destination')
                record['game_path'] = str(destination)
            checked.append(record)
        return checked

    def record_game_check(self, report):
        from translation_workflow import digest, write_json
        with self.db() as db:
            config = self.config(db)
            self.require_final_approval(db, config)
            export = self.meta(db, 'last_export')
            if not export or report.get('translation_sha256') != export['translation_sha256'] or digest(export['path']) != export['translation_sha256']:
                raise ValueError('Game check must bind the current exported translation')
            if not nonempty(report.get('game_version')) or not nonempty(report.get('platform')):
                raise ValueError('Actual tested game version and platform required')
            checks = report.get('checks', [])
            expected = {'fonts', 'layout', 'ui', 'dialogue', 'quest'}
            if (not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks)
                    or len(checks) != len(expected) or {c.get('category') for c in checks} != expected):
                raise ValueError('Game checks must cover fonts, layout, ui, dialogue and quest')
            for check in checks:
                if (check.get('status') not in ('pass', 'not_applicable') or not nonempty(check.get('notes'))
                        or (check['status'] == 'pass' and not nonempty(check.get('scene')))
                        or (check['category'] in ('fonts', 'layout') and check['status'] != 'pass')):
                    raise ValueError('Unverified/failed game checks cannot be recorded as passed')
            report = {**report, 'payload_files': self.checked_files(report.get('payload_files')),
                      'evidence_files': self.checked_files(report.get('evidence_files')),
                      'quality_report_hash': export['quality_review']['report_hash'], 'status': 'passed'}
            if any(not nonempty(e.get('game_path')) for e in report['payload_files']):
                raise ValueError('Every tested payload file requires its game_path installation destination')
            if len({e['game_path'] for e in report['payload_files']}) != len(report['payload_files']):
                raise ValueError('Duplicate tested payload destinations')
            write_json(self.root / 'reports/in_game_check.json', report)
            self.put(db, 'in_game_check', report)
            self.event(db, 'record_game_check', {'translation_sha256': report['translation_sha256']})
            return {'status': 'passed', 'report_file': str(self.root / 'reports/in_game_check.json')}

    def release_check(self):
        from translation_workflow import digest
        with self.db() as db:
            config = self.config(db)
            self.require_final_approval(db, config)
            export, check = self.meta(db, 'last_export'), self.meta(db, 'in_game_check')
            if (not export or not check or check.get('status') != 'passed'
                    or check['translation_sha256'] != export['translation_sha256']
                    or check['quality_report_hash'] != export['quality_review']['report_hash']
                    or digest(export['path']) != check['translation_sha256']):
                raise ValueError('Current in-game validation required before release')
            self.checked_files(check['payload_files']); self.checked_files(check['evidence_files'])
            return {'status': 'passed', 'game_version': check['game_version'], 'platform': check['platform'],
                    'payload_files': check['payload_files']}
