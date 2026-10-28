"""Executor interchangeability against real shared batches; no paid model calls."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import ainiee_bridge as bridge
import test_translation_workflow as fixtures
import translation_workflow as workflow


class ExecutorParityTests(unittest.TestCase):
    def setUp(self):
        # Provenance is constant throughout these isolated fixtures.
        self.provenance = patch.object(bridge, 'provenance', return_value=bridge.provenance())
        self.provenance.start(); self.addCleanup(self.provenance.stop)
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow

    def api(self, **settings):
        self.flow.pause(True)
        self.flow.configure({'backend': 'api', 'api': {
            'api_url': 'https://example.invalid/v1', 'model_name': 'fixture',
            'max_completion_tokens': 16384, 'max_requests': 100,
            'requests_per_minute': 1000000, 'concurrency': 1, **settings}}, 'Executor parity fixture')
        self.flow.pause(False)

    @staticmethod
    def translated(payload):
        return {k: payload[k] for k in ('task_id', 'input_hash') if k in payload} | {'records': [
            {'id': r['id'], 'translation': r['source'].replace('Alice sees a Wolf.', '爱丽丝看见一只狼。')
                .replace('The Wolf has', '这只狼拥有').replace('gold.', '金币。').replace('text', '文本')}
            for r in payload['records']]}

    def run_model(self, stage, reply, session_ids=None):
        packs = []
        flow = self.flow
        class Mock:
            def request(self, system, user):
                # Assert the adapter sends the actual shared input file byte for byte.
                assert system == ''
                with flow.db() as db:
                    sessions = db.execute('SELECT * FROM batch_runs WHERE status="waiting"').fetchall()
                matches = [s for s in sessions if Path(s['input_file']).read_text(encoding='utf-8') == user]
                assert len(matches) == 1
                packs.append((dict(matches[0]), user))
                result = reply(fixtures.model_input(user), len(packs))
                return json.dumps(result, ensure_ascii=False), {'prompt_tokens': 1, 'completion_tokens': 1}
            def close(self): pass
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock-only'}):
            summary = workflow.run_api(flow, stage, lambda *_: Mock(), session_ids=session_ids, continuous=False)
        return summary, packs

    def test_api_draft_and_round_repair_use_same_files_and_deferred_checks(self):
        self.f.freeze(True); self.api()
        def first(payload, number):
            result = self.translated(payload)
            if number == 1:
                result['records'][0]['translation'] = ''
            return result
        summary, packs = self.run_model('translate', first)
        self.assertEqual(summary['accepted'], 2)
        self.assertEqual(len(packs), 2)  # No immediate semantic repair.
        self.assertTrue(all(Path(s['result_file']).exists() for s, _ in packs))
        checked = self.flow.draft_scan(prepare_repairs=True)
        self.assertFalse(checked['clean'])
        summary, repairs = self.run_model('translate', lambda p, _: self.translated(p))
        self.assertEqual(summary['accepted'], 1)
        self.assertEqual(len(fixtures.model_input(repairs[0][1])['records']), 1)
        self.assertTrue(self.flow.draft_scan(prepare_repairs=True)['clean'])

    def test_api_compact_input_preserves_shared_data_and_output_contract(self):
        self.f.freeze(True); self.api()
        session = self.flow.batch_open('translate', 'api-compact-fixture', backend='api')
        compact = Path(session['input_file']).read_text(encoding='utf-8')
        with self.flow.db() as db:
            payload = json.loads(db.execute('SELECT payload FROM tasks WHERE id=?',
                                           (session['task_id'],)).fetchone()[0])
            full = self.flow.render_batch_input(db, payload, [r['id'] for r in payload['records']],
                                               session['result_file'], session['session_id'])
        expected = fixtures.model_input(full)
        for row in expected['records']:
            row.pop('ordinal', None)
            for field in ('metadata', 'locked'):
                if not row.get(field):
                    row.pop(field, None)
        from workflow_task_contract import transport_id_fields
        transport = json.loads(Path(session['input_file']).with_name('api_id_transport.v2.json').read_text(encoding='utf-8'))
        expected = transport_id_fields(expected, {v: k for k, v in transport['short_to_canonical'].items()})
        expected.pop('task_id'); expected.pop('input_hash')
        self.assertEqual(fixtures.model_input(compact), expected)
        self.assertLess(len(compact), len(full))
        for text in ('不执行检查脚本', '不轮询', 'result_file', '只读本文件', '不扫描全库'):
            self.assertNotIn(text, compact)
        self.assertIn('repair_constraints', compact)
        self.assertIn('exception_changes', compact)

    def test_api_prompts_keep_review_evidence_and_term_decisions(self):
        for stage in ('extract', 'glossary_review', 'pilot_translate', 'pilot_review',
                      'translate', 'review', 'polish', 'final_review'):
            with self.subTest(stage=stage):
                full, data = workflow.prompt({'stage': stage})
                compact, compact_data = workflow.prompt({'stage': stage}, api=True)
                full_data, compact_value = json.loads(data), json.loads(compact_data)
                self.assertEqual(full_data['stage'], compact_value['stage'])
                expected = copy.deepcopy(full_data['output_template'])
                if stage in ('extract', 'glossary_review'):
                    expected.pop('task_id'); expected.pop('input_hash')
                if stage == 'glossary_review':
                    for field in ('snapshot_sha256', 'source_sha256', 'reviewed_refs'):
                        expected['decisions'].pop(field)
                self.assertEqual(expected, compact_value['output_template'])
                self.assertLess(len(compact), len(full))
                self.assertNotIn('不运行任务校验', compact)
                if stage in ('review', 'final_review', 'pilot_review'):
                    for field in ('accuracy', 'completeness', 'terminology', 'tone', 'logic',
                                  'placeholders', 'source_excerpt', 'changes'):
                        self.assertIn(field, compact)
                if stage == 'glossary_review':
                    self.assertIn('entry_reviews', compact)
                    self.assertIn('evidence_rows', compact)

    def test_api_shared_terms_preserve_membership_and_template_scope(self):
        self.f.freeze(True)
        with self.flow.db() as db:
            records = [dict(r) for r in db.execute('SELECT id,ordinal,source FROM records ORDER BY ordinal')]
            records[0].update(metadata={'speaker': 'Alice'}, locked=True,
                              protection=[{'token': 'example', 'original': '{0}'}],
                              exceptions=[{'source': 'Wolf', 'reason': '原句依据'}])
            records[-1]['translation'] = ''
            payload = {'stage': 'translate', 'task_id': 'compact-scope', 'input_hash': 'a' * 64,
                       'records': records}
            wolf = {'src': 'Wolf', 'dst': '狼', 'info': '动物'}
            # Same spelling with different semantics must not collapse.
            other = {'src': 'Wolf', 'dst': '沃尔夫', 'src_state': 'case_sensitive'}
            with patch.object(bridge, 'matched_terms', side_effect=[[wolf], [wolf, other], [], []]):
                text = self.flow.render_batch_input(db, payload, [r['id'] for r in records],
                                                   'result.json', api=True)
        decoded = fixtures.model_input(text)
        self.assertEqual([r['id'] for r in decoded['records']], [r['id'] for r in records])
        self.assertEqual([r['terms'] for r in decoded['records']], [[wolf], [wolf, other], [], []])
        for key in ('metadata', 'locked', 'protection'):
            self.assertEqual(decoded['records'][0][key], records[0][key])
        self.assertEqual(decoded['records'][0]['existing_exceptions'],
                         [dict(records[0]['exceptions'][0],exception_ref='x1')])
        self.assertEqual(decoded['records'][-1]['source'], '')
        self.assertEqual(decoded['records'][-1]['translation'], '')
        self.assertEqual(text.count('"info":"动物"'), 1)
        template = json.loads(text.rsplit('```json\n', 1)[1].split('\n```', 1)[0])
        self.assertEqual(len(template['records']), 1)
        self.assertEqual(template['input_hash'], payload['input_hash'])
        self.assertIn('全部 ID 各一次', text)
        self.assertNotIn('"ordinal":', text)
        self.assertNotIn('"protection":[]', text)
        self.assertNotIn('审校修订的 before', text)

    def test_local_batching_applies_before_allocation_without_saving_key(self):
        self.api()
        workflow.write_json(workflow.API_CONFIG_FILE, {
            'api_url': 'https://example.invalid', 'api_key': 'fixture-local-secret',
            'model_name': 'fixture', 'batching': {'batch_records': 1, 'batch_tokens': 5000}})
        summary, packs = self.run_model('extract', lambda p, _: copy.deepcopy(p['output_template']))
        self.assertEqual(summary['accepted'], 4)
        self.assertTrue(all(len(fixtures.model_input(text)['records']) == 1 for _, text in packs))
        with self.flow.db() as db:
            config = self.flow.config(db)
            self.assertEqual(config['batch_records'], 1)
            self.assertEqual(config['batch_tokens'], 5000)
            self.assertNotIn('api_key', config['api'])
        self.assertNotIn('fixture-local-secret',
                         (self.flow.root / 'config/workflow.json').read_text(encoding='utf-8'))
        with self.flow.db() as db:
            revision = self.flow.meta(db, 'revision')
        workflow.sync_api_batching(self.flow)
        with self.flow.db() as db:
            self.assertEqual(self.flow.meta(db, 'revision'), revision)
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE status="done"').fetchone()[0], 4)

    def test_local_batching_change_rejects_live_task_without_rewriting_it(self):
        self.api()
        session = self.flow.batch_open('extract', 'live-api-fixture', backend='api')
        original = Path(session['input_file']).read_bytes()
        workflow.write_json(workflow.API_CONFIG_FILE, {'batching': {'batch_records': 1}})
        with self.assertRaisesRegex(ValueError, 'in-flight tasks'):
            workflow.sync_api_batching(self.flow)
        self.assertEqual(Path(session['input_file']).read_bytes(), original)
        with self.flow.db() as db:
            self.assertFalse(self.flow.meta(db, 'paused'))
            self.assertEqual(self.flow.config(db)['batch_records'], 2)

    def test_api_pilot_repairs_only_bad_ids_and_preserves_real_authors(self):
        self.f.freeze(True, pilot=False); self.api()
        bad = []
        def reply(payload, number):
            result = self.translated(payload)
            if number == 1:
                bad.append(result['records'][0]['id'])
                result['records'][0]['translation'] = ''
            return result
        summary, packs = self.run_model('pilot_translate', reply)
        self.assertEqual(summary['needs_attention'], [])
        self.assertFalse(any(s['round'] == 1 for s, _ in packs))
        self.assertEqual(self.flow.pilot_report()['statistics']['mechanical_failed_records'], 1)
        transport = json.loads(Path(packs[0][0]['input_file']).with_name('api_id_transport.v2.json').read_text(encoding='utf-8'))
        bad = [transport['short_to_canonical'][i] for i in bad]
        self.flow.pilot_retry(bad, 'Verify the identified calibration problem')
        _, repaired = self.run_model('pilot_translate', lambda p, _: self.translated(p))
        repair_packs = [s for s, _ in repaired]
        self.assertEqual(len(repair_packs), 1)
        self.assertEqual(json.loads(repair_packs[0]['scope']), bad)
        with self.flow.db() as db:
            authors = db.execute('SELECT DISTINCT instance FROM quality_authors WHERE record_id=? AND stage="pilot_translate"', (bad[0],)).fetchall()
            self.assertEqual(len(authors), 2)
            self.assertEqual(db.execute('SELECT count(*) FROM record_lineage WHERE record_id=?', (bad[0],)).fetchone()[0], 2)

    def test_api_review_repairs_protocol_before_round_quality_scan(self):
        self.f.freeze(True); self.f.translate(); self.flow.draft_scan(); self.api()
        def review(payload, number):
            return {k: payload[k] for k in ('task_id', 'input_hash') if k in payload} | {'records': [
                {'id': r['id'], 'keep': True,
                 'reason': '核对原文与译文。', **self.f.review_evidence(r)} for r in payload['records']]}
        def broken(payload, number):
            result = review(payload, number)
            if number == 1:
                result['records'][0]['checks'] = {}
            return result
        summary, packs = self.run_model('review', broken)
        self.assertEqual(summary['accepted'], 2)
        self.assertEqual(summary['needs_attention'], [])
        self.assertEqual(len(packs),3)
        self.assertEqual(len(fixtures.model_input(packs[1][1])['records']), 1)
        self.assertTrue(self.flow.review_scan('review', prepare_repairs=True)['clean'])

    def test_glossary_review_uses_shared_handoff_and_provenance(self):
        self.f.extract(True); self.api()
        def review(payload, number):
            result = copy.deepcopy(payload['output_template'])
            d = result['decisions']
            d.update(review_complete=True, review_summary='核对全部术语出处和译名。',
                     acknowledged_findings=[f['id'] for f in payload['audit']['findings']],
                     entry_reviews=self.f.glossary_evidence(payload))
            return result
        summary, packs = self.run_model('glossary_review', review)
        self.assertGreater(summary['accepted'], 0)
        self.assertEqual(summary['needs_attention'], [])
        self.assertEqual(self.flow.freeze_glossary()['status'], 'frozen')
        self.assertTrue(all(Path(s['result_file']).exists() for s, _ in packs))

    def test_saved_minimal_glossary_reply_resumes_without_model_or_hash_copying(self):
        self.f.extract(True); self.api()
        session = self.flow.batch_open('glossary_review', 'saved-review', backend='api')
        packet = Path(session['input_file']).read_text(encoding='utf-8')
        payload = fixtures.model_input(packet)
        self.assertNotIn('input_hash', payload)
        decisions = copy.deepcopy(payload['output_template']['decisions'])
        decisions.update(review_complete=True, review_summary='核对出处、译名和分类。',
            acknowledged_findings=[f['id'] for f in payload['audit']['findings']],
            entry_reviews=self.f.glossary_evidence(payload))
        self.assertNotIn('reviewed_refs', decisions)
        Path(session['result_file']).with_name('response.raw.txt').write_text(
            json.dumps({'decisions': decisions}, ensure_ascii=False), encoding='utf-8')
        restarted = workflow.Workflow(self.f.project)
        self.assertEqual(workflow.resume_api_tasks(restarted, 'glossary_review'), 1)
        stored = workflow.read_json(session['result_file'])
        self.assertEqual(stored['task_id'], session['task_id'])
        self.assertTrue(all(':' in r for r in stored['decisions']['reviewed_refs']))
        self.assertEqual(restarted.freeze_glossary()['status'], 'frozen')

    def test_review_changed_text_is_output_once_then_shared_validation_passes(self):
        self.f.freeze(True); self.f.translate(); self.flow.draft_scan(); self.api()
        changed = []
        def reply(payload, number):
            rows = []
            for row in payload['records']:
                item = {'id': row['id'], 'keep': True, 'reason': '逐项核对原文。',
                        **self.f.review_evidence(row)}
                if '看见' in row['translation']:
                    item.pop('keep')
                    item['translation'] = row['translation'].replace('看见', '看到')
                    item['checks']['accuracy']['status'] = 'corrected'
                    item['changes'] = [{'category': 'accuracy', 'severity': 'minor',
                        'source_excerpt': row['source'], 'reason': '合成修订用于验证全文还原。'}]
                    changed.append((row['translation'], item['translation']))
                rows.append(item)
            return {'records': rows}
        summary, packs = self.run_model('review', reply)
        self.assertEqual(summary['failed'], 0)
        self.assertEqual(len(changed), 1)
        self.assertTrue(self.flow.review_scan('review')['clean'])
        stored = [row for session, _ in packs for row in workflow.read_json(session['result_file'])['records']]
        edit = next(row for row in stored if row['changes'])
        self.assertEqual((edit['changes'][0]['before'], edit['changes'][0]['after']), changed[0])

    def test_allocation_is_backend_independent_and_standard_remains_subagent_only(self):
        task_id = self.flow.prepare('extract')['tasks'][0]
        with self.flow.db() as db:
            payload = json.loads(db.execute('SELECT payload FROM tasks WHERE id=?', (task_id,)).fetchone()[0])
            a = self.flow.plan_task_allocation(db, payload, 'subagent')
            b = self.flow.plan_task_allocation(db, payload, 'api')
            self.assertEqual([v[:2] for v in a], [v[:2] for v in b])
            self.assertEqual(a[0][2],0)
            self.assertGreater(b[0][2],0)  # Complete API presentation gets its own token budget.
            payload['repair_tier'] = 'standard'; payload['required_tier'] = 'standard'
            with self.assertRaisesRegex(ValueError, 'requires a subagent worker tier'):
                self.flow.plan_task_allocation(db, payload, 'api')

    def test_transport_retry_of_round_repair_preserves_good_items_and_scope(self):
        self.f.freeze(True, pilot=False); self.api()
        def initial(payload, number):
            result = self.translated(payload)
            if number == 1:
                result['records'][0]['translation'] = ''
            return result
        self.run_model('pilot_translate', initial)
        report = workflow.read_json(self.flow.pilot_report()['report_file'])
        self.flow.pilot_retry([r['id'] for r in report['failures']], 'Verify the calibration fix')
        def reply(payload, number):
            if number == 1:
                raise bridge.ApiFailure('synthetic_connection', True)
            return self.translated(payload)
        summary, packs = self.run_model('pilot_translate', reply)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(summary['needs_attention'], [])
        self.assertEqual(summary['pending_handoffs'], [])
        self.assertEqual(len(packs), 2)
        self.assertEqual(packs[0][0]['scope'], packs[1][0]['scope'])
        self.assertEqual(len(json.loads(packs[0][0]['scope'])), 1)
        self.assertEqual(self.flow.pilot_report()['statistics']['mechanical_failed_records'], 0)
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE id=?', (packs[-1][0]['task_id'],)).fetchone()[0], 2)

    def test_api_budget_pause_preserves_completed_drafts_and_resumes_remaining_work(self):
        self.f.freeze(True, pilot=False); self.api(max_requests=1)
        def broken(payload, number):
            result = self.translated(payload); result['records'][0]['translation'] = ''
            return result
        summary, packs = self.run_model('pilot_translate', broken)
        self.assertTrue(summary['paused'])
        self.assertEqual(summary['pending_handoffs'], [])
        with self.flow.db() as db:
            before = dict(db.execute('SELECT * FROM tasks WHERE id=?', (packs[0][0]['task_id'],)).fetchone())
            attempts = self.flow.pilot_spent(db)
        changed = self.flow.configure({'api': {'max_requests': 10}}, 'Raise common API request budget')
        self.assertFalse(changed['semantic_reset'])
        with self.flow.db() as db:
            self.assertEqual(attempts, self.flow.pilot_spent(db))
            saved = db.execute('SELECT result FROM tasks WHERE id=?', (before['id'],)).fetchone()
            self.assertEqual(saved['result'], before['result'])
        self.flow.pause(False)
        summary, remaining = self.run_model('pilot_translate', lambda p, _: self.translated(p))
        self.assertEqual(summary['needs_attention'], [])
        self.assertEqual(len(remaining), 1)
        report = workflow.read_json(self.flow.pilot_report()['report_file'])
        self.assertEqual(report['statistics']['mechanical_failed_records'], 1)
        self.flow.pilot_retry([r['id'] for r in report['failures']], 'Verify after raising the shared API budget')
        _, repairs = self.run_model('pilot_translate', lambda p, _: self.translated(p))
        self.assertEqual(len(fixtures.model_input(repairs[0][1])['records']), 1)
        self.assertEqual(self.flow.pilot_report()['statistics']['mechanical_failed_records'], 0)

    def test_format_error_has_only_one_automatic_retry(self):
        self.api()
        summary, packs = self.run_model('extract', lambda *_: [])
        self.assertEqual(len(packs), 4)  # Two independent tasks, one retry per task.
        self.assertEqual(summary['accepted'], 0)
        self.assertEqual(len(summary['needs_attention']), 2)
        self.assertIn('One automatic format retry', summary['needs_attention'][0]['error'])
        self.assertEqual(len(summary['pending_handoffs']), 2)

    def test_interrupted_api_handoff_is_not_replayed_implicitly(self):
        self.api()
        session = self.flow.batch_open('extract', 'api-interrupted', backend='api')
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock-only'}):
            with self.assertRaisesRegex(ValueError, 'Unfinished API handoff'):
                workflow.run_api(self.flow, 'extract', lambda *_: self.fail('Must not replay an unknown request'))
        retry = self.flow.batch_retry(session['session_id'], 'new-verified-instance', 'Old request verified stopped')
        self.assertNotEqual(retry['session_id'], session['session_id'])
        summary, packs = self.run_model('extract', lambda p, _: copy.deepcopy(p['output_template']),
                                        session_ids=[retry['session_id']])
        self.assertEqual(summary['accepted'], 1)
        self.assertEqual(len(packs), 1)
        self.assertEqual(summary['pending_handoffs'], [])

    def test_glossary_retry_keeps_attempt_history_and_actual_reviewer(self):
        self.f.extract(True); self.api()
        def reply(payload, number):
            if number == 1:
                raise bridge.ApiFailure('synthetic_timeout', True)
            result = copy.deepcopy(payload['output_template'])
            result['decisions'].update(review_complete=True, review_summary='已核对全部出处与术语。',
                reviewed_refs=[e['ref'] for e in payload['entries']],
                acknowledged_findings=[f['id'] for f in payload['audit']['findings']],
                entry_reviews=self.f.glossary_evidence(payload))
            return result
        summary, packs = self.run_model('glossary_review', reply)
        self.assertEqual(summary['needs_attention'], [])
        first, retry = packs[0][0], packs[1][0]
        with self.flow.db() as db:
            owner = db.execute('SELECT instance FROM quality_attempts WHERE task_id=? AND token=?',
                               (first['task_id'], first['token'])).fetchone()[0]
            self.assertEqual(owner, first['worker'])
            self.assertEqual(db.execute('SELECT instance FROM quality_results WHERE task_id=?',
                                       (first['task_id'],)).fetchone()[0], retry['worker'])
        frozen = self.flow.freeze_glossary()
        folder = Path(self.flow.root / frozen['glossary']['path']).parent.parent
        decisions = workflow.read_json(folder / 'glossary_decisions.json')
        self.assertNotIn(first['worker'], decisions['review_instances'].values())
        self.assertIn(retry['worker'], decisions['review_instances'].values())

    def test_api_capacity_is_checked_without_altering_shared_review_batches(self):
        self.api(max_completion_tokens=100)
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock-only'}):
            with self.assertRaisesRegex(ValueError, 'shared review_output_tokens'):
                workflow.run_api(self.flow, 'review', lambda *_: self.fail('Must not call a model'))
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
