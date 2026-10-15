"""Collect the entire Sol inspection before dispatching any semantic repairs."""
import json


class SolRoundMixin:
    def sol_watch(self, db):
        from translation_workflow import fingerprint
        return fingerprint([self.meta(db, k) for k in ('revision', 'source_hash', 'glossary', 'draft_generation', 'review_generation', 'quality_policy_key')])

    def sol_tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS sol_review_rounds(id INTEGER PRIMARY KEY,watch TEXT,report TEXT,decisions TEXT,status TEXT,result TEXT)')

    def open_sol_round(self, db, config):
        from translation_workflow import write_json, encoded
        self.sol_tables(db)
        active = db.execute('SELECT * FROM sol_review_rounds WHERE status="open" ORDER BY id DESC LIMIT 1').fetchone()
        watch = self.sol_watch(db)
        if active and active['watch'] == watch:
            report = json.loads(active['report'])
            return report, active['id']
        report = self.build_quality_report(db, 'final', config)
        if active:
            # Only reuse actual passed decisions whose item signatures still match.
            old_report, old_decisions = json.loads(active['report']), json.loads(active['decisions'])
            checkpoints = self.meta(db, 'quality_checkpoints', {})
            for record_id, entry in old_decisions.items():
                if entry['disposition'] in ('pass', 'contextual') and old_report['check_signatures'].get(record_id) == report['check_signatures'].get(record_id):
                    checkpoints.setdefault('final', {})[record_id] = {'signature': report['check_signatures'][record_id],
                        'report_hash': old_report['report_hash'], 'decision': entry}
            self.put(db, 'quality_checkpoints', checkpoints)
            db.execute('UPDATE sol_review_rounds SET status="superseded" WHERE id=?', (active['id'],))
            report = self.build_quality_report(db, 'final', config)
        if self.meta(db, 'quality_approvals', {}).get('final', {}).get('report_hash') == report['report_hash']:
            return report, None
        cursor = db.execute('INSERT INTO sol_review_rounds(watch,report,decisions,status) VALUES (?,?,?,"open")',
                            (watch, encoded(report), '{}'))
        write_json(self.root / f'reports/sol_rounds/round_{cursor.lastrowid:05d}.report.json', report)
        items = [i for key in ('records_to_check', 'consistency', 'glossary_checks') for i in report[key]]
        groups, group, chars = [], [], 0
        for item in items:
            size = len(encoded(item))
            if group and (len(group) >= config['review_batch_records'] or chars + size > max(1, config['task_input_chars'] - 512)):
                groups.append(group); group, chars = [], 0
            group.append(item); chars += size
        if group:
            groups.append(group)
        manifest = []
        for index, batch in enumerate(groups, 1):
            path = self.root / f'reports/sol_rounds/round_{cursor.lastrowid:05d}.input_{index:04d}.json'
            packet = {'round': cursor.lastrowid, 'report_hash': report['report_hash'], 'checks': batch,
                      'instruction': 'Sol 主对话逐项检查并记录 pass/contextual/repair；全部检查结束前不得派发修订。'}
            write_json(path, packet)
            manifest.append({'file': str(path), 'check_ids': [i['id'] for i in batch],
                             'input_chars': len(encoded(packet)), 'over_budget': len(encoded(packet)) > config['task_input_chars']})
        write_json(self.root / f'reports/sol_rounds/round_{cursor.lastrowid:05d}.inputs.json', manifest)
        self.event(db, 'sol_round_open', {'round': cursor.lastrowid, 'report_hash': report['report_hash'], 'scope': report['required_decisions']})
        return report, cursor.lastrowid

    def active_sol_round(self, db):
        self.sol_tables(db)
        row = db.execute('SELECT * FROM sol_review_rounds WHERE status="open" ORDER BY id DESC LIMIT 1').fetchone()
        if not row:
            raise ValueError('Open a final quality-report before recording or finishing a Sol round')
        if row['watch'] != self.sol_watch(db):
            raise ValueError('Stale Sol round; request the current quality-report before continuing')
        self.require_review_round_complete(db, 'final_review' if self.config(db)['polish'] else 'review')
        return row

    def record_sol_checks(self, db, decisions):
        from translation_workflow import encoded, write_json
        if not isinstance(decisions, dict):
            raise ValueError('Sol decisions must be a JSON object')
        row = self.active_sol_round(db)
        report, saved = json.loads(row['report']), json.loads(row['decisions'])
        entries = decisions.get('decisions')
        if (decisions.get('report_hash') != report['report_hash'] or decisions.get('reviewer') != 'sol'
                or not isinstance(decisions.get('summary'), str) or not decisions['summary'].strip()
                or not isinstance(entries, list) or not entries or any(not isinstance(e, dict) or not isinstance(e.get('id'), str) for e in entries)
                or len({e.get('id') for e in entries}) != len(entries)):
            raise ValueError('Sol checks require unique explicit decisions bound to the current round report')
        items = {i['id']: i for key in ('records_to_check', 'consistency', 'glossary_checks') for i in report[key]}
        for entry in entries:
            record_id = entry.get('id')
            if (record_id not in report['required_decisions'] or entry.get('disposition') not in ('pass', 'contextual', 'repair')
                    or not isinstance(entry.get('reason'), str) or not entry['reason'].strip()):
                raise ValueError('Every Sol decision requires an assigned ID, disposition and evidence/reason')
            if entry['disposition'] == 'repair':
                item = items[record_id]
                allowed = ({record_id} if 'translation' in item else
                           set(item.get('record_ids', [])) | {r['id'] for r in item.get('records', [])})
                ids = entry.get('record_ids', [record_id] if 'translation' in item else [])
                if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not set(ids).issubset(allowed):
                    raise ValueError('Sol repair must name exact affected record IDs within this check')
                entry = {**entry, 'record_ids': ids}
            saved[record_id] = entry
        db.execute('UPDATE sol_review_rounds SET decisions=? WHERE id=?', (encoded(saved), row['id']))
        write_json(self.root / f'reports/sol_rounds/round_{row["id"]:05d}.progress.json',
                   {'round': row['id'], 'report_hash': report['report_hash'], 'decisions': saved,
                    'remaining_checks': [i for i in report['required_decisions'] if i not in saved], 'repairs_dispatched': False})
        self.event(db, 'sol_round_checks', {'round': row['id'], 'decisions': entries})
        return {'status': 'checks_saved', 'round': row['id'], 'saved_checks': len(saved),
                'remaining_checks': len(set(report['required_decisions']) - set(saved)), 'repairs_dispatched': False, 'stage_approved': False}

    def record_sol_round(self, decisions):
        with self.db() as db:
            self.config(db)
            return self.record_sol_checks(db, decisions)

    def finish_sol_round(self):
        from translation_workflow import encoded, write_json
        with self.db() as db:
            config = self.config(db); row = self.active_sol_round(db)
            report, decisions = json.loads(row['report']), json.loads(row['decisions'])
            missing = set(report['required_decisions']) - set(decisions)
            if missing:
                raise ValueError(f'Complete the entire Sol inspection round before dispatching repairs: {len(missing)} checks remain')
            problems = {}
            for entry in decisions.values():
                if entry['disposition'] == 'repair':
                    for record_id in entry['record_ids']:
                        problems.setdefault(record_id, []).append(entry['reason'])
            if problems:
                checkpoints = self.meta(db, 'quality_checkpoints', {})
                for record_id, entry in decisions.items():
                    if entry['disposition'] in ('pass', 'contextual'):
                        checkpoints.setdefault('final', {})[record_id] = {'signature': report['check_signatures'][record_id],
                            'report_hash': report['report_hash'], 'decision': entry}
                self.put(db, 'quality_checkpoints', checkpoints)
                tasks, blocked = self.plan_review_repairs(db, config, 'final_review' if config['polish'] else 'review',
                                                       problems, f'sol-round-{row["id"]}')
                result = {'status': 'repair_round_prepared', 'round': row['id'], 'repair_tasks': tasks,
                          'repair_records': len(problems), 'needs_attention_ids': blocked, 'stage_approved': False}
            else:
                supplied = {'report_hash': report['report_hash'], 'reviewer': 'sol', 'summary': 'Completed Sol inspection round; explicit per-item decisions archived.',
                            'decisions': [decisions[i] for i in report['required_decisions']]}
                result = self.accept_quality_in_transaction(db, 'final', report, supplied)
                result['round'] = row['id']
            db.execute('UPDATE sol_review_rounds SET status="finished",result=? WHERE id=?', (encoded(result), row['id']))
            write_json(self.root / f'reports/sol_rounds/round_{row["id"]:05d}.result.json',
                       {'report_hash': report['report_hash'], 'decisions': decisions, **result})
            self.event(db, 'sol_round_finished', result)
            return result

    def guard_sol_dispatch(self, db):
        self.sol_tables(db)
        if db.execute('SELECT 1 FROM sol_review_rounds WHERE status="open"').fetchone():
            raise ValueError('Finish the entire Sol inspection round before dispatching semantic repairs; use sol-round-record / sol-round-finish')
