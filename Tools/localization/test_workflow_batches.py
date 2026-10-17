"""Synthetic batch lifecycle checks; no game-state edits or external model calls."""
import json
import unittest
from unittest.mock import patch
from pathlib import Path

import test_translation_workflow as fixtures
import translation_workflow as workflow


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.f.freeze(True)

    def result(self, session, bad=None):
        with self.flow.db() as db:
            run = db.execute('SELECT * FROM batch_runs WHERE id=?', (session['session_id'],)).fetchone()
            task = db.execute('SELECT * FROM tasks WHERE id=?', (run['task_id'],)).fetchone()
        payload = json.loads(task['payload'])
        scope = json.loads(run['scope'])
        records = []
        for row in payload['records']:
            if row['id'] not in scope:
                continue
            value = row['source'].replace('Alice sees a Wolf.', '爱丽丝看见一只狼。').replace('The Wolf has', '这只狼拥有').replace('gold.', '金币。').replace('text', '文本')
            records.append({'id': row['id'], 'translation': '' if row['id'] in (bad or []) else value})
        return {'task_id': task['id'], 'input_hash': task['input_hash'], 'records': records}

    def write_result(self, session, result):
        workflow.write_json(session['result_file'], result)

    def strict_session(self, worker):
        # Exercise strict validation/partial repair mechanics separately from the
        # round scheduler (whose deferred acceptance is covered in test_workflow_drafts).
        for n in range(2):
            session = self.flow.batch_open('translate', 'seed-' + str(n + 1))
            result = self.result(session)
            if n == 0:
                for item in result['records']:
                    item['translation'] = ''
            self.write_result(session, result)
            self.assertEqual(self.flow.batch_finish(session['session_id'])['status'], 'submitted')
        self.flow.draft_scan(prepare_repairs=True)
        normal = self.flow.is_draft_task
        strict = patch.object(self.flow, 'is_draft_task', side_effect=lambda t: False if json.loads(t['payload']).get('strict_translation') else normal(t))
        strict.start(); self.addCleanup(strict.stop)
        return self.flow.batch_open('translate', worker)

    def test_single_pack_and_finish_are_sufficient_and_idempotent(self):
        session = self.flow.batch_open('translate', 'producer-a')
        pack = Path(session['input_file']).read_text(encoding='utf-8')
        self.assertIn('original_source', pack)
        self.assertIn('protection', pack)
        self.assertIn('"src":"Alice"', pack)
        self.assertNotIn('evidence_rows', pack)
        self.assertNotIn('src_text', pack)
        self.write_result(session, self.result(session))
        receipt = self.flow.batch_finish(session['session_id'])
        self.assertEqual(receipt['status'], 'submitted')
        self.assertEqual(receipt, self.flow.batch_finish(session['session_id']))

    def test_only_failed_ids_repair_and_contributor_identity_survives(self):
        session = self.strict_session('original-writer')
        result = self.result(session); target = result['records'][0]['id']
        self.write_result(session, self.result(session, [target]))
        failed = self.flow.batch_finish(session['session_id'])
        self.assertEqual(failed['failed_ids'], [target])
        self.assertEqual(failed['preserved_records'], 1)
        repair = self.flow.batch_repair(session['session_id'], 'repair-writer')
        self.assertEqual(repair['records'], 1)
        self.assertNotIn(result['records'][1]['id'], Path(repair['input_file']).read_text(encoding='utf-8'))
        self.write_result(repair, self.result(repair))
        self.assertEqual(self.flow.batch_finish(repair['session_id'])['status'], 'submitted')
        with self.flow.db() as db:
            actual = {r['record_id']: r['instance'] for r in db.execute('SELECT record_id,instance FROM batch_items WHERE task_id=?', (session['task_id'],))}
            self.assertEqual(actual[target], 'repair-writer')
            self.assertEqual(actual[result['records'][1]['id']], 'original-writer')
            authors = {r['instance'] for r in db.execute('SELECT instance FROM quality_authors WHERE record_id=? AND stage="translate"', (target,))}
            self.assertEqual(authors, {'seed-1', 'original-writer', 'repair-writer'})
            for instance in authors:
                self.assertFalse(self.flow.independent_worker(db, {'stage': 'review', 'ids': json.dumps([target])}, instance))

    def test_term_omission_is_not_ignored_and_exception_is_explicit(self):
        session = self.strict_session('writer')
        result = self.result(session)
        result['records'][0]['translation'] = result['records'][0]['translation'].replace('爱丽丝', '她')
        self.write_result(session, result)
        failed = self.flow.batch_finish(session['session_id'])
        self.assertEqual(len(failed['failed_ids']), 1)
        repair = self.flow.batch_repair(session['session_id'], 'context-writer')
        fixed = self.result(repair)
        fixed['records'][0] = result['records'][0]
        fixed['records'][0]['exceptions'] = {'src': ' alice ', 'reason': ' 本句测试专名省略，留待独立审校核对。 '}
        self.write_result(repair, fixed)
        self.assertEqual(self.flow.batch_finish(repair['session_id'])['status'], 'submitted')
        with self.flow.db() as db:
            row = db.execute('SELECT exceptions FROM records WHERE id=?', (fixed['records'][0]['id'],)).fetchone()
            self.assertEqual(json.loads(row['exceptions'])[0]['source'], 'Alice')

    def test_parts_are_merged_by_id_without_rereading_or_replacing_good_part(self):
        session = self.strict_session('part-writer')
        result = self.result(session)
        for number, row in enumerate(reversed(result['records'])):
            file = Path(session['result_file']).with_name(f'part-{number}.json')
            workflow.write_json(file, {**result, 'records': [row]})
            self.flow.batch_save(session['session_id'], file)
        self.assertEqual(self.flow.batch_finish(session['session_id'])['status'], 'submitted')

    def test_bulk_handoff_keeps_success_and_next_claim_receipt(self):
        opened = self.flow.batch_many('open', ['bulk-writer', 'bulk-writer'], stage='translate')
        self.assertEqual([r['status'] for r in opened], ['ready', 'needs_attention'])
        session = opened[0]
        self.write_result(session, self.result(session))
        receipt = self.flow.batch_finish(session['session_id'], next_worker='next-writer')
        self.assertEqual(receipt['next']['status'], 'ready')
        self.assertEqual(receipt, self.flow.batch_finish(session['session_id'], next_worker='must-not-claim'))
        finished = self.flow.batch_many('finish', [session['session_id'], 'unknown-session'])
        self.assertEqual([r['status'] for r in finished], ['submitted', 'needs_attention'])

    def test_oversized_pending_review_repair_splits_with_exact_id_and_attempt_preservation(self):
        with self.flow.db() as db:
            self.flow.review_tables(db)
            source_rows = [dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal LIMIT 2')]
            task_id = 'review-round-oversized'
            records = []
            for row in source_rows:
                records.append({'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                    'translation': '候选译文', 'locked': False, 'metadata': {}, 'protection': [],
                    'repair_constraints': {'diagnostic': '需保留'}, 'diagnostic': '证据' * 7000,
                    'candidate': {'translation': '候选译文'}})
            payload = {'task_id': task_id, 'stage': 'review', 'revision': self.flow.meta(db, 'revision'),
                'source_hash': self.flow.meta(db, 'source_hash'), 'review_round': 1,
                'review_repair_origin': 'review-scan', 'repair_tier': 'standard', 'required_model': None,
                'repair_counts': {row['id']: 1 for row in records}, 'language': {'source': '英语', 'target': '简体中文'},
                'style': '忠实准确', 'context': [], 'game_background': '', 'glossary_version': self.flow.meta(db, 'glossary'),
                'records': records, 'terms': [], 'revision_request': '只修本条诊断问题。'}
            payload['input_hash'] = workflow.fingerprint(payload)
            db.execute('INSERT INTO tasks(id,stage,revision,ids,payload,input_hash,backend,status,attempt) '
                       'VALUES (?,?,?,?,?,?,?,"pending",0)',
                       (task_id, 'review', payload['revision'], workflow.encoded([row['id'] for row in records]),
                        workflow.encoded(payload), payload['input_hash'], 'codex'))
            for original, candidate in zip(source_rows, records):
                item = {'id': candidate['id'], 'translation': '候选译文'}
                db.execute('INSERT INTO review_candidates VALUES (?,?,?,?,?,? ,"failed",?)',
                    (candidate['id'], 'review', 'review-origin', workflow.encoded(item),
                     workflow.encoded({'source': original['source'], 'translation': original['translation']}),
                     'independent-reviewer', 'synthetic oversized review failure'))

        split = self.flow.batch_split(task_id)
        self.assertEqual(split['status'], 'split')
        self.assertEqual(split['records'], 2)
        self.assertTrue(all(child['input_chars'] <= split['input_limit_chars'] for child in split['children']))
        self.assertEqual([i for child in split['children'] for i in child['record_ids']], [r['id'] for r in records])
        self.assertEqual([child['records'] for child in split['children']], [1, 1])
        with self.flow.db() as db:
            parent = db.execute('SELECT status,attempt FROM tasks WHERE id=?', (task_id,)).fetchone()
            children = [db.execute('SELECT * FROM tasks WHERE id=?', (child['task_id'],)).fetchone()
                        for child in split['children']]
        self.assertEqual(tuple(parent), ('obsolete', 0))
        for child, receipt in zip(children, split['children']):
            payload = json.loads(child['payload'])
            self.assertEqual(json.loads(child['ids']), receipt['record_ids'])
            self.assertEqual(payload['repair_counts'], {receipt['record_ids'][0]: 1})
            self.assertEqual(payload['records'][0]['diagnostic'], '证据' * 7000)
            self.assertEqual(child['status'], 'pending')
            self.assertEqual(child['attempt'], 0)

    def test_oversized_sol_carried_finding_can_split_after_review_candidate_passed(self):
        with self.flow.db() as db:
            self.flow.review_tables(db)
            source_rows = [dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal LIMIT 2')]
            task_id = 'review-round-sol-oversized'
            records = []
            for row in source_rows:
                records.append({'id': row['id'], 'ordinal': row['ordinal'], 'source': row['source'],
                    'translation': '候选译文', 'locked': False, 'metadata': {}, 'protection': [],
                    'repair_constraints': {}, 'diagnostic': 'Sol遗留问题' * 5000,
                    'candidate': None})
            payload = {'task_id': task_id, 'stage': 'review', 'revision': self.flow.meta(db, 'revision'),
                'source_hash': self.flow.meta(db, 'source_hash'), 'review_round': 2,
                'review_repair_origin': 'sol-round-2', 'repair_tier': 'standard', 'required_model': None,
                'repair_counts': {row['id']: 1 for row in records}, 'language': {'source': '英语', 'target': '简体中文'},
                'style': '忠实准确', 'context': [], 'game_background': '', 'glossary_version': self.flow.meta(db, 'glossary'),
                'records': records, 'terms': [], 'revision_request': '只修本条Sol裁定的问题。'}
            payload['input_hash'] = workflow.fingerprint(payload)
            db.execute('INSERT INTO tasks(id,stage,revision,ids,payload,input_hash,backend,status,attempt) '
                       'VALUES (?,?,?,?,?,?,?,"pending",0)',
                       (task_id, 'review', payload['revision'], workflow.encoded([row['id'] for row in records]),
                        workflow.encoded(payload), payload['input_hash'], 'codex'))
            for original, candidate in zip(source_rows, records):
                item = {'id': candidate['id'], 'translation': '候选译文'}
                db.execute('INSERT INTO review_candidates VALUES (?,?,?,?,?,? ,"passed",NULL)',
                    (candidate['id'], 'review', 'review-origin', workflow.encoded(item),
                     workflow.encoded({'source': original['source'], 'translation': original['translation']}),
                     'independent-reviewer'))

        split = self.flow.batch_split(task_id)
        self.assertEqual(split['status'], 'split')
        self.assertEqual(split['records'], 2)
        self.assertEqual([child['records'] for child in split['children']], [1, 1])
        self.assertTrue(all(child['input_chars'] <= split['input_limit_chars'] for child in split['children']))

    def test_old_untouched_initial_review_can_split_on_output_budget_without_spending_attempts(self):
        self.f.translate()
        self.flow.prepare('review')
        with self.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE stage="review" AND status="pending" ORDER BY id').fetchone()
            task_id, ids = task['id'], json.loads(task['ids'])
        original_config = self.flow.config
        with patch.object(self.flow, 'config', side_effect=lambda db: {**original_config(db), 'review_output_tokens': 800}):
            split = self.flow.batch_split(task_id)
        self.assertEqual([i for child in split['children'] for i in child['record_ids']], ids)
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE id=?', (task_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts WHERE task_id=?', (task_id,)).fetchone()[0], 0)

    def test_unrelated_id_and_second_repair_are_rejected(self):
        session = self.strict_session('writer')
        result = self.result(session)
        wrong = {**result, 'records': [{'id': 'wrong', 'translation': '错'}]}
        self.write_result(session, wrong)
        with self.assertRaisesRegex(ValueError, 'IDs'):
            self.flow.batch_finish(session['session_id'])
        self.write_result(session, self.result(session, [result['records'][0]['id']]))
        self.flow.batch_finish(session['session_id'])
        repair = self.flow.batch_repair(session['session_id'], 'fixer')
        with self.assertRaisesRegex(ValueError, 'already dispatched'):
            self.flow.batch_repair(session['session_id'], 'another-fixer')
        self.write_result(repair, self.result(repair, [result['records'][0]['id']]))
        self.assertEqual(self.flow.batch_finish(repair['session_id'])['status'], 'needs_attention')
        with self.assertRaisesRegex(ValueError, 'One consolidated'):
            self.flow.batch_repair(repair['session_id'], 'extra-fixer')
        directed = self.flow.batch_repair(repair['session_id'], 'sol-directed-fixer', reason='Sol identified a specific missing placeholder; repair only this ID.')
        self.write_result(directed, self.result(directed))
        self.assertEqual(self.flow.batch_finish(directed['session_id'])['status'], 'submitted')
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE id=?', (session['task_id'],)).fetchone()['attempt'], 3)

    def test_reliability_repair_is_split_before_dispatch_and_merges_after_all_parts(self):
        session = self.strict_session('split-source-worker')
        bad = self.result(session)
        for item in bad['records']:
            item['translation'] = ''
        self.write_result(session, bad)
        self.assertEqual(self.flow.batch_finish(session['session_id'])['status'], 'repair_required')
        with self.flow.db() as db:
            db.execute('UPDATE batch_items SET error=? WHERE task_id=? AND valid=0',
                       ('diagnostic ' * 400, session['task_id']))
            config = self.flow.config(db)
        with patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': 16000}):
            first = self.flow.batch_repair(session['session_id'], 'split-fix-a')
            self.assertEqual(first['repair_parts'], 2)
            self.assertLessEqual(first['input_chars'], 16000)
            self.write_result(first, self.result(first))
            receipt = self.flow.batch_finish(first['session_id'])
            self.assertEqual(receipt['status'], 'repair_part_saved')
            second = self.flow.batch_repair(session['session_id'], 'split-fix-b')
            self.assertLessEqual(second['input_chars'], 16000)
            self.write_result(second, self.result(second))
            self.assertEqual(self.flow.batch_finish(second['session_id'])['status'], 'submitted')
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE id=?', (session['task_id'],)).fetchone()[0], 2)

    def test_review_provenance_is_per_record_after_repair(self):
        self.f.translate()
        session = self.flow.batch_open('review', 'review-original')
        with self.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
        rows = json.loads(task['payload'])['records']
        records = [{'id': r['id'], 'translation': r['translation'], 'reviewed': True,
                    'reason': 'Independent semantic review', **self.f.review_evidence(r)} for r in rows]
        result = {'task_id': task['id'], 'input_hash': task['input_hash'], 'records': records}
        records[0]['reason'] = ''
        self.write_result(session, result)
        self.flow.batch_finish(session['session_id'])
        while other := self.flow.claim('review', self.f.fresh_worker('review')):
            self.flow.submit(other['task_id'], other['lease_token'], {**self.f.envelope(other), 'records': [
                {'id': r['id'], 'translation': r['translation'], 'reviewed': True, 'reason': 'Independent checks', **self.f.review_evidence(r)}
                for r in other['payload']['records']]})
        self.flow.review_scan(prepare_repairs=True)
        repair = self.flow.batch_open('review', 'review-repair')
        fixed = dict(records[0]); fixed['reason'] = 'Rechecked the assigned sentence'
        with self.flow.db() as db:
            repair_task = db.execute('SELECT * FROM tasks WHERE id=?', (repair['task_id'],)).fetchone()
        self.write_result(repair, {'task_id': repair_task['id'], 'input_hash': repair_task['input_hash'], 'records': [fixed]})
        self.assertEqual(self.flow.batch_finish(repair['session_id'])['status'], 'submitted')
        self.assertTrue(self.flow.review_scan()['clean'])
        with self.flow.db() as db:
            reviews = self.flow.latest_reviews(db, 'final')
        self.assertEqual(reviews[records[0]['id']]['instance'], 'review-repair')
        self.assertEqual(reviews[records[1]['id']]['instance'], 'review-original')

    def test_exception_normalization_never_invents_an_exception(self):
        terms = [{'src': 'move', 'dst': '移动'}, {'src': 'Charge', 'dst': '冲锋'}, {'src': 'Poison', 'dst': '中毒'}]
        self.assertEqual(workflow.bridge.normalize_exception_sources([], terms, False), [])
        result = workflow.bridge.normalize_exception_sources([{'src': ' charge ', 'reason': ' 本句是收费含义。 '}], terms, False)
        self.assertEqual(result, [{'source': 'Charge', 'reason': '本句是收费含义。'}])
        self.assertEqual(workflow.bridge.normalize_exception_sources([{'source': 'Unknown', 'reason': '普通义'}], terms, False)[0]['source'], 'Unknown')
        with self.assertRaisesRegex(ValueError, 'source and reason'):
            workflow.bridge.normalize_exception_sources([{'source': 'Poison'}], terms, False)
        ambiguous = [{'src': 'Charge', 'dst': '冲锋'}, {'src': 'charge', 'dst': '费用'}]
        self.assertEqual(workflow.bridge.normalize_exception_sources([{'source': 'CHARGE', 'reason': 'ordinary'}], ambiguous, False)[0]['source'], 'CHARGE')

    def test_compact_pilot_repair_keeps_each_reviewers_summary(self):
        from workflow_quality import REVIEW_CHECKS
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.tearDown)
        self.flow = f.flow; f.freeze(True, pilot=False)
        translated = self.flow.batch_open('pilot_translate', 'pilot-writer')
        self.write_result(translated, self.result(translated))
        self.flow.batch_finish(translated['session_id'])
        reviewed = self.flow.batch_open('pilot_review', 'pilot-reviewer')
        with self.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (reviewed['task_id'],)).fetchone()
        ids = json.loads(task['ids'])
        summary = {key: {'status': 'pass', 'evidence': 'Explicit synthetic source comparison'} for key in REVIEW_CHECKS}
        result = {'task_id': task['id'], 'input_hash': task['input_hash'], 'review_summary': summary,
                  'records': [{'id': i, 'reviewed': i != ids[0], 'verdict': 'pass'} for i in ids]}
        self.write_result(reviewed, result); self.flow.batch_finish(reviewed['session_id'])
        repaired = self.flow.batch_repair(reviewed['session_id'], 'pilot-repair-reviewer')
        other = {key: {'status': 'pass', 'evidence': 'Explicit repaired sentence source comparison'} for key in REVIEW_CHECKS}
        self.write_result(repaired, {**result, 'review_summary': other,
                                    'records': [{'id': ids[0], 'reviewed': True, 'verdict': 'pass'}]})
        self.assertEqual(self.flow.batch_finish(repaired['session_id'])['status'], 'submitted')
        report = workflow.read_json(self.flow.quality_report('pilot')['report_file'])
        self.assertEqual(len(report['pilot_batch_summaries']), 2)
        with self.flow.db() as db:
            evidence = self.flow.latest_reviews(db, 'pilot')
        self.assertEqual(evidence[ids[0]]['batch_summary'], other)
        self.assertEqual(evidence[ids[1]]['batch_summary'], summary)
        f.approve('pilot')


class LargeBatchTest(unittest.TestCase):
    def test_move_charge_poison_have_row_specific_terms_and_explicit_exceptions(self):
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.tearDown)
        project = f.project / 'Ordinary'; project.mkdir()
        source = project / 'ordinary_text.csv'
        source.write_text('Move the chair.\nCharge the customer.\nPoison spreads.\n', encoding='utf-8')
        flow = f.flow = workflow.Workflow(project); flow.init(source)
        flow.prepare('extract'); extraction = flow.claim('extract', 'term-extractor')
        analysis = {'characters': [], 'terms': [
            {'source': src, 'recommended_translation': dst, 'category_path': '其他', 'evidence_rows': [n]}
            for n, src, dst in [(1, 'move', '移动'), (2, 'Charge', '冲锋'), (3, 'Poison', '中毒')]], 'non_translate': []}
        flow.submit(extraction['task_id'], extraction['lease_token'], {**f.envelope(extraction), 'analysis': analysis})
        f.freeze(pilot=False)
        session = flow.batch_open('pilot_translate', 'ordinary-writer')
        text = Path(session['input_file']).read_text(encoding='utf-8')
        rows = [json.loads(line) for line in text.split('```jsonl\n')[1].split('\n```')[0].splitlines()]
        self.assertEqual([[t['src'].casefold() for t in r['terms']] for r in rows], [['move'], ['charge'], ['poison']])
        self.assertEqual([r['source'] for r in rows], ['Move the chair.', 'Charge the customer.', 'Poison spreads.'])
        with flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
            frozen_before = flow.meta(db, 'glossary')
        values = ['挪动椅子。', '向顾客收费。', '毒药蔓延。']
        result = {'task_id': task['id'], 'input_hash': task['input_hash'],
                  'records': [{'id': r['id'], 'translation': value} for r, value in zip(rows, values)]}
        workflow.write_json(session['result_file'], result)
        self.assertEqual(len(flow.batch_finish(session['session_id'])['failed_ids']), 3)
        repair = flow.batch_repair(session['session_id'], 'ordinary-fixer')
        reasons = ['原句说搬动椅子，是普通动作，不是移动机制。', '原句说向顾客收费，不是冲锋技能。', '原句指毒药物质蔓延，不是中毒状态。']
        for item, src, reason in zip(result['records'], ['MOVE', 'charge', 'Poison'], reasons):
            item['exceptions'] = [{'src': src, 'reason': reason}]
        workflow.write_json(repair['result_file'], result)
        self.assertEqual(flow.batch_finish(repair['session_id'])['status'], 'submitted')
        with flow.db() as db:
            self.assertEqual(flow.meta(db, 'glossary'), frozen_before)

    def test_eighty_rows_two_failures_keep_seventy_eight(self):
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.tearDown)
        project = f.project / 'Large'; project.mkdir()
        source = project / 'large_text.csv'; source.write_text('text\n' * 80, encoding='utf-8')
        f.flow = workflow.Workflow(project); f.flow.init(source); f.freeze()
        session = f.flow.batch_open('translate', 'original-80')
        with f.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
        ids = json.loads(task['ids'])
        result = {'task_id': task['id'], 'input_hash': task['input_hash'], 'records': [
            {'id': i, 'translation': '' if i in ids[:2] else '文本'} for i in ids]}
        workflow.write_json(session['result_file'], result)
        receipt = f.flow.batch_finish(session['session_id'])
        self.assertEqual(receipt['status'], 'submitted')
        self.assertIn('draft_saved', receipt['quality'])
        scan = f.flow.draft_scan(prepare_repairs=True)
        self.assertEqual(scan['failed_records'], 2)
        self.assertEqual(len(scan['repair_tasks']), 1)
        repair = f.flow.batch_open('translate', 'repair-2')
        self.assertEqual(repair['records'], 2)
        with f.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM records WHERE stage=?', ('translate',)).fetchone()[0], 80)
            task = db.execute('SELECT * FROM tasks WHERE id=?', (repair['task_id'],)).fetchone()
        workflow.write_json(repair['result_file'], {'task_id': task['id'], 'input_hash': task['input_hash'],
                                                  'records': [{'id': i, 'translation': '文本'} for i in ids[:2]]})
        self.assertEqual(f.flow.batch_finish(repair['session_id'])['status'], 'submitted')
        with f.flow.db() as db:
            last = {r['record_id']: r['instance'] for r in db.execute('SELECT record_id,instance FROM record_lineage ORDER BY id')}
            owners = {owner: list(last.values()).count(owner) for owner in set(last.values())}
        self.assertEqual(owners, {'original-80': 78, 'repair-2': 2})
        self.assertTrue(f.flow.draft_scan()['clean'])


class FrozenMatcherTests(unittest.TestCase):
    def test_compiled_patterns_are_reused_between_record_checks(self):
        helper = workflow.bridge.helper()
        helper.build_search_pattern.cache_clear()
        glossary = {'characters': [], 'terms': [
            {'source': 'cache-token', 'recommended_translation': '缓存词'}], 'non_translate': []}
        workflow.bridge.matched_terms(glossary, ['cache-token'], workflow.DEFAULT)
        workflow.bridge.matched_terms(glossary, ['unrelated text'], workflow.DEFAULT)
        self.assertEqual(helper.build_search_pattern.cache_info().misses, 1)

    def test_matching_does_not_unify_distinct_frozen_translations(self):
        glossary = {'characters': [], 'terms': [
            {'source': 'Guard', 'recommended_translation': '守卫', 'category_path': '身份'},
            {'source': 'Guards', 'recommended_translation': '警卫', 'category_path': '身份'}], 'non_translate': []}
        singular = workflow.bridge.matched_terms(glossary, ['Guard'], workflow.DEFAULT)
        plural = workflow.bridge.matched_terms(glossary, ['Guards'], workflow.DEFAULT)
        self.assertEqual([(t['src'], t['dst']) for t in singular], [('Guard', '守卫')])
        self.assertEqual([(t['src'], t['dst']) for t in plural], [('Guards', '警卫')])


if __name__ == '__main__':
    unittest.main()
