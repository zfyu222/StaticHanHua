"""Build complete tasks and enforce package budgets before making them pending."""
import copy
import json
import uuid
from contextvars import ContextVar
from functools import wraps

import ainiee_bridge as bridge
import glossary_families
from workflow_profile import profiled
from glossary_merge import review_evidence_rows
from workflow_tokens import count_tokens, record_limit, batch_limit, record_tokens


_PREPARATION_INPUTS = ContextVar('preparation_inputs', default=None)


def allocation_session(function):
    """A cache bounded to one prepare call, isolated across threads/nesting."""
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        token = _PREPARATION_INPUTS.set((self, {}))
        try:
            return function(self, *args, **kwargs)
        finally:
            _PREPARATION_INPUTS.reset(token)
    return wrapped


class AllocationMixin:
    def prepare_task_input(self, db, value, config, backend):
        """Reject broken producer input before any worker sees it; no semantic claims."""
        from translation_workflow import STAGES
        stage = value.get('stage')
        if stage not in STAGES or backend not in ('subagent', 'api'):
            raise ValueError('Task allocation: unsupported stage/backend')
        if value.get('revision') != self.meta(db, 'revision'):
            raise ValueError('Task allocation: stale revision')
        expected_source = self.meta(db, 'source_hash')
        if value.get('source_hash', expected_source) != expected_source:
            raise ValueError('Task allocation: source snapshot mismatch')
        value['source_hash'] = expected_source
        value.setdefault('protocol', 1)
        language = value.get('language', {})
        if not isinstance(language, dict):
            raise ValueError('Task allocation: language must be an object')
        value['language'] = {'source': language.get('source', config['source_language']),
                             'target': language.get('target', config['target_language'])}
        value.setdefault('style', config['style'])
        if not value.get('game_background') and (value.get('strict_translation') or value.get('review_repair_origin')):
            value['game_background'] = config.get('game_background', '')
        else:
            value.setdefault('game_background', config.get('game_background', ''))
        if (any(not isinstance(v, str) or not v.strip() for v in value['language'].values()) or
                not isinstance(value['style'], str) or not isinstance(value['game_background'], str)):
            raise ValueError('Task allocation: invalid language/style/background fields')
        if not isinstance(value.get('records'), list):
            raise ValueError('Task allocation: records must be a list')
        review_readings = self.meta(db, 'review_readings', {}) if stage in ('review', 'final_review') else {}
        seen = set()
        for row in value['records']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in seen:
                raise ValueError('Task allocation: invalid or duplicate record ID')
            seen.add(row['id'])
            original = db.execute('SELECT * FROM records WHERE id=?', (row['id'],)).fetchone()
            if not original or row.get('ordinal', original['ordinal']) != original['ordinal']:
                raise ValueError('Task allocation: unknown ID or mismatched source ordinal')
            row.setdefault('ordinal', original['ordinal'])
            if not isinstance(row.get('source'), str):
                raise ValueError('Task allocation: complete source text required')
            protection = row.setdefault('protection', [])
            if (not isinstance(protection, list) or any(not isinstance(p, dict) or
                    not isinstance(p.get('placeholder'), str) or not isinstance(p.get('original'), str) for p in protection)):
                raise ValueError('Task allocation: invalid protection mapping')
            restored = bridge.restore(row['source'], protection) if protection else row['source']
            if restored != original['source']:
                raise ValueError('Task allocation: source/protection does not match the source snapshot')
            self.add_review_reading(db, row, original['source'], stage, review_readings)
            row['locked'] = bool(original['locked'])
            if row['locked'] and row.get('translation') != original['translation']:
                raise ValueError('Task allocation: prepared input conflicts with a locked translation')
            row.setdefault('metadata', {})
            if not isinstance(row['metadata'], dict) or any(not isinstance(v, str) for v in row['metadata'].values()):
                raise ValueError('Task allocation: metadata must contain strings')
            if stage in ('review', 'pilot_review', 'final_review', 'polish') and not isinstance(row.get('translation'), str):
                raise ValueError('Task allocation: review/polish requires an existing translation')
            if row.get('translation') is not None and not isinstance(row['translation'], str):
                raise ValueError('Task allocation: translation must be text')
            row.setdefault('exceptions', json.loads(original['exceptions']))
            if (not isinstance(row['exceptions'], list) or any(not isinstance(e, dict) or
                    not isinstance(e.get('source'), str) or not isinstance(e.get('reason'), str) or
                    not e['source'].strip() or not e['reason'].strip() for e in row['exceptions'])):
                raise ValueError('Task allocation: invalid inherited context exceptions')
            if row.get('diagnostic') is not None and not (isinstance(row['diagnostic'], str) or
                    isinstance(row['diagnostic'], list) and all(isinstance(x, str) for x in row['diagnostic'])):
                raise ValueError('Task allocation: invalid repair diagnostic')
            for name in ('candidate', 'diagnostic_detail', 'repair_constraints'):
                if row.get(name) is not None and not isinstance(row[name], dict):
                    raise ValueError('Task allocation: invalid ' + name)
        if 'repair_counts' in value and (not isinstance(value['repair_counts'], dict) or
                set(value['repair_counts']) != seen or any(type(n) is not int or n < 1 for n in value['repair_counts'].values())):
            raise ValueError('Task allocation: repair counters must cover every assigned ID')
        api_escalation = backend == 'api' and value.get('escalation_backend') == 'api' and not value.get('required_tier')
        if value.get('repair_tier') == 'standard' and not api_escalation and (backend != 'subagent' or value.get('required_tier') != 'standard'):
            raise ValueError('Task allocation: standard-tier repair requires a subagent worker tier')
        if stage == 'glossary_review':
            entries = value.get('entries', [])
            refs = [e['ref'] for e in entries]
            if len(refs) != len(set(refs)):
                raise ValueError('Task allocation: duplicate glossary ref')
            ordinals = {r['ordinal'] for r in value['records']}
            for entry in entries + value.get('related_entries', []):
                evidence = review_evidence_rows(entry['row'])
                if not evidence or not set(evidence).issubset(ordinals):
                    raise ValueError('Task allocation: missing required glossary source evidence')
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError('Task allocation: input must be valid JSON data') from exc
        return value

    @staticmethod
    def allocation_tables(db):
        db.execute('CREATE TABLE IF NOT EXISTS task_allocations(task_id TEXT PRIMARY KEY,input_chars INTEGER,compact_chars INTEGER,input_limit INTEGER,contract_version INTEGER DEFAULT 0)')
        if 'contract_version' not in {r[1] for r in db.execute('PRAGMA table_info(task_allocations)')}:
            db.execute('ALTER TABLE task_allocations ADD COLUMN contract_version INTEGER DEFAULT 0')
        for column in ('input_tokens', 'compact_tokens', 'token_limit'):
            if column not in {r[1] for r in db.execute('PRAGMA table_info(task_allocations)')}:
                db.execute(f'ALTER TABLE task_allocations ADD COLUMN {column} INTEGER')

    @profiled('task_allocate')
    def plan_task_allocation(self, db, payload, backend, fixed_identity=False, *, reliability_limit=None):
        from translation_workflow import fingerprint, prompt, read_json, STAGES
        config = self.config(db)
        if (not isinstance(payload, dict) or payload.get('stage') not in STAGES or
                not isinstance(payload.get('task_id'), str) or not payload['task_id']):
            raise ValueError('Task allocation requires a supported stage and explicit task ID')
        stage = payload['stage']
        glossary_stage = stage == 'glossary_review'
        units = payload.get('entries' if glossary_stage else 'records')
        if not isinstance(units, list) or any(not isinstance(r, dict) for r in units):
            raise ValueError('Task allocation requires structured entries/records')
        cap = record_limit(config, stage)
        if reliability_limit is not None:
            if type(reliability_limit) is not int or reliability_limit < 1:
                raise ValueError('Reliability split requires a positive record limit')
            cap = min(cap, reliability_limit) if cap is not None else reliability_limit
        active = _PREPARATION_INPUTS.get()
        cache = active[1] if active is not None and active[0] is self else None
        context_key = (id(db), payload['revision'], 'contexts')
        if cache is not None and context_key in cache:
            contexts = cache[context_key]
        else:
            contexts = self.meta(db, 'contexts', {})
            if cache is not None:
                cache[context_key] = contexts
        glossary = self.glossary(db) if stage not in ('extract', 'glossary_review') else None
        rules = self.rules(db, config) if glossary is not None else ()
        if glossary_stage:
            key = (id(db), payload['revision'], 'glossary_inputs')
            if cache is not None and key in cache:
                entries, audit, families, source_records = cache[key]
            else:
                folder = self.root / f'glossary/r{payload["revision"]}'
                snapshot = read_json(folder / 'glossary_original.json')
                entries = [{'ref': f'{table}:{i}', 'table': table, 'index': i, 'row': row}
                           for table in ('characters', 'terms', 'non_translate') for i, row in enumerate(snapshot[table])]
                audit = read_json(folder / 'audit/glossary_audit.json')
                families_file = folder / 'family_candidates.json'
                families = read_json(families_file)['relations'] if families_file.is_file() else []
                source_records = [dict(row) for row in db.execute('SELECT * FROM records ORDER BY ordinal')]
                if cache is not None:
                    cache[key] = entries, audit, families, source_records
        planned = []

        def build(group, task_id):
            value = copy.deepcopy(payload)
            value.pop('input_hash', None)
            value['task_id'] = task_id
            if glossary_stage:
                value['entries'] = group
                refs = {e['ref'] for e in group}
                findings = [f for f in audit['findings'] if refs.intersection(f['refs'])]
                related_refs = {ref for f in findings for ref in f['refs']} - refs
                related = [e for e in entries if e['ref'] in related_refs]
                value['related_entries'] = related
                value['audit'] = {**audit, 'findings': findings}
                value['term_families'] = glossary_families.batch_hints(group, entries, families)
                evidence_ids = {n for e in group + related for n in review_evidence_rows(e['row'])}
                value['records'] = []
                for row in source_records:
                    if row['ordinal'] in evidence_ids:
                        value['records'].append({'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                            'translation': row['translation'], 'locked': bool(row['locked']),
                            'metadata': contexts.get(str(row['ordinal']), {}),
                            'exceptions': json.loads(row['exceptions']), 'protection': []})
            else:
                value['records'] = group
                if glossary is not None:
                    texts = []
                    for row in group:
                        source_row = db.execute('SELECT source FROM records WHERE id=?', (row.get('id'),)).fetchone()
                        if source_row is None:
                            raise ValueError('Task allocation: unknown source record ID')
                        raw = source_row['source']
                        texts.extend((raw, row.get('metadata', {}).get('speaker', '')))
                    value['terms'] = bridge.matched_terms(glossary, texts, config, rules=rules)
            if isinstance(value.get('repair_counts'), dict):
                ids = {r['id'] for r in value['records']}
                value['repair_counts'] = {i: n for i, n in value['repair_counts'].items() if i in ids}
            self.prepare_task_input(db, value, config, backend)
            value['input_hash'] = payload['input_hash'] if fixed_identity else fingerprint(value)
            return value

        def partition(group, task_id):
            value = build(group, task_id)
            # Reserve the runtime identity/lease envelope at allocation, so claim
            # does not render or measure the model input again.
            envelope = {**value, 'worker': 'w' * 128, 'lease_token': '0' * 32, 'attempt': 999999}
            system, user = prompt(envelope)
            raw_tokens = count_tokens(system) + count_tokens(user)
            from workflow_task_contract import render_protocol_input
            path = self.root / 'results' / task_id / ('0' * 32) / 'result.json'
            raw_tokens = max(raw_tokens, count_tokens(render_protocol_input(envelope, path)))
            compact_tokens = 0
            if stage not in ('extract', 'glossary_review'):
                path = self.root / 'tasks' / task_id / 'handoffs' / ('0' * 32) / 'result.json'
                compact_tokens = count_tokens(self.render_batch_input(db, envelope, [r['id'] for r in value['records']], path, '0' * 32))
                if backend == 'api':
                    from workflow_task_contract import api_id_transport
                    scope = [row['id'] for row in value['records']]
                    transport = api_id_transport(envelope, scope, '0' * 32)
                    compact_tokens = max(compact_tokens, count_tokens(self.render_batch_input(
                        db, envelope, scope, path, '0' * 32, api=True, id_transport=transport)))
            elif backend == 'api':
                compact_tokens = count_tokens(render_protocol_input(envelope, path, api=True))
            if glossary_stage:
                content_tokens = sum(record_tokens(row, stage) for row in group)
            else:
                content_tokens = sum(record_tokens({**row, 'source':
                    bridge.restore(row['source'], row.get('protection', []))}, stage) for row in group)
            output_ok = (stage not in ('review', 'pilot_review', 'final_review') or
                         sum(self.estimate_review_output(r, stage, config) for r in value['records']) <= self.review_output_budget(config, stage, backend))
            # Like AINiee, a source item larger than the content budget stands
            # alone. The complete input and estimated-output limits stay strict.
            # API sends the compact protocol, not the internal canonical JSON.
            # Keep both measurements for audit, but budget the selected executor.
            input_tokens = compact_tokens if backend == 'api' else max(raw_tokens, compact_tokens)
            if (input_tokens <= config.get('task_input_tokens', 16384) and output_ok
                    and (cap is None or len(group) <= cap)
                    and (len(group) == 1 or content_tokens <= batch_limit(config, stage))):
                planned.append((value, raw_tokens, compact_tokens))
                return
            if len(group) < 2:
                label = group[0].get('ref', group[0].get('id')) if group else 'shared instructions'
                raise ValueError(f'Task allocation exceeds input/output budget for {label}: '
                                 f'{input_tokens} input tokens, limit {config.get("task_input_tokens", 16384)}; '
                                 'single item and required evidence cannot be split; no tasks allocated or evidence truncated')
            middle = len(group) // 2
            for part in (group[:middle], group[middle:]):
                partition(part, payload['task_id'] if fixed_identity else payload['task_id'] + '-s' + uuid.uuid4().hex[:8])

        partition(units, payload['task_id'])
        return planned

    def allocate_task(self, db, payload, backend, attempt=0, planned=None):
        from translation_workflow import encoded
        planned = planned if planned is not None else self.plan_task_allocation(db, payload, backend)
        self.allocation_tables(db)
        limit = self.config(db).get('task_input_tokens', 16384)
        ids = []
        for value, raw_tokens, compact_tokens in planned:
            task_id = value['task_id']
            db.execute('INSERT INTO tasks(id,stage,revision,ids,payload,input_hash,backend,status,attempt) VALUES (?,?,?,?,?,?,?,"pending",?)',
                       (task_id, value['stage'], value['revision'], encoded([r['id'] for r in value['records']]),
                        encoded(value), value['input_hash'], backend, attempt))
            db.execute('INSERT OR REPLACE INTO task_allocations(task_id,input_tokens,compact_tokens,token_limit,contract_version) VALUES (?,?,?,?,4)',
                       (task_id, raw_tokens, compact_tokens, limit))
            ids.append(task_id)
        return ids

    def allocate_legacy_pending(self, db, stage):
        """Prepare upgrades untouched legacy tasks; completed/in-flight tasks stay intact."""
        self.allocation_tables(db)
        tasks = db.execute('SELECT t.* FROM tasks t LEFT JOIN task_allocations a ON a.task_id=t.id '
                           'WHERE t.stage=? AND t.revision=? AND t.status="pending" AND t.attempt=0 AND (a.task_id IS NULL OR a.contract_version<4)',
                           (stage, self.meta(db, 'revision'))).fetchall()
        for task in tasks:
            payload = json.loads(task['payload'])
            planned = self.plan_task_allocation(db, payload, task['backend'])
            if len(planned) == 1:
                value, raw_tokens, compact_tokens = planned[0]
                from translation_workflow import encoded
                db.execute('UPDATE tasks SET ids=?,payload=?,input_hash=? WHERE id=?',
                           (encoded([r['id'] for r in value['records']]), encoded(value), value['input_hash'], task['id']))
                db.execute('INSERT OR REPLACE INTO task_allocations(task_id,input_tokens,compact_tokens,token_limit,contract_version) VALUES (?,?,?,?,4)',
                           (task['id'], raw_tokens, compact_tokens, self.config(db).get('task_input_tokens', 16384)))
                self.event(db, 'allocate_legacy_pending', {'task': task['id'], 'replacement_tasks': [task['id']],
                                                          'input_tokens': max(raw_tokens, compact_tokens)})
            else:
                db.execute('UPDATE tasks SET status="obsolete" WHERE id=?', (task['id'],))
                # Planning is deterministic in content; child IDs do not encode progress.
                replacement = self.allocate_task(db, payload, task['backend'], planned=planned)
                self.event(db, 'allocate_legacy_pending', {'task': task['id'], 'replacement_tasks': replacement})
