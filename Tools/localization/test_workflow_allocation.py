"""Allocation owns package budgets; claiming only acquires a prepared task."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import translation_workflow as workflow


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shh_allocation_')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.source = self.project / 'sample_text.csv'
        self.source.write_text(''.join(f'Item{i} ' + 'x' * 1600 + '\n' for i in range(4)), encoding='utf-8')
        self.flow = workflow.Workflow(self.project)
        self.flow.init(self.source, {'batch_records': 80, 'extract_batch_records': 80, 'batch_chars': 100000})

    def test_extract_is_split_at_allocation_and_claim_does_not_measure_inputs(self):
        with self.flow.db() as db:
            config = self.flow.config(db)
        with patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': 5000}):
            tasks = self.flow.prepare('extract')['tasks']
            self.assertGreater(len(tasks), 1)
            with self.flow.db() as db:
                allocated = list(db.execute('SELECT * FROM task_allocations'))
                self.assertTrue(all(r['input_chars'] <= r['input_limit'] for r in allocated))
                covered = [i for t in db.execute('SELECT ids FROM tasks WHERE status="pending"') for i in json.loads(t['ids'])]
                self.assertEqual(len(covered), 4)
                self.assertEqual(len(set(covered)), 4)
            with (patch('translation_workflow.prompt', side_effect=AssertionError('claim rendered input')),
                  patch.object(self.flow, 'render_batch_input', side_effect=AssertionError('claim rendered package')),
                  patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': 1})):
                claim = self.flow.claim('extract', 'actual-worker')
            self.assertIn(claim['task_id'], tasks)
            self.assertIsNotNone(claim['context_policy']['input_chars'])
            self.assertEqual(claim['context_policy']['input_limit_chars'], 5000)
            self.assertEqual(claim['context_policy']['checked_at'], 'allocation')

    def glossary_payload(self, db):
        rows = [dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
        entries = [{'ref': f'terms:{i}', 'table': 'terms', 'index': i,
                    'row': {'source': f'Item{i}', 'recommended_translation': f'术语{i}', 'evidence_rows': [i + 1]}}
                   for i in range(4)]
        folder = self.flow.root / 'glossary/r1'
        workflow.write_json(folder / 'glossary_original.json',
                            {'characters': [], 'terms': [e['row'] for e in entries], 'non_translate': []})
        workflow.write_json(folder / 'audit/glossary_audit.json', {'findings': [
            {'id': 'pair-a', 'refs': ['terms:0', 'terms:1']},
            {'id': 'pair-b', 'refs': ['terms:2', 'terms:3']}]})
        workflow.write_json(folder / 'family_candidates.json', {'relations': []})
        return {'task_id': 'glossary-review-allocation', 'stage': 'glossary_review', 'revision': 1,
                'entries': entries, 'records': rows, 'language': {}, 'style': '忠实自然', 'context': []}

    def test_glossary_split_rebuilds_required_related_entries_and_evidence(self):
        with self.flow.db() as db:
            payload = self.glossary_payload(db)
            config = self.flow.config(db)
            full = self.flow.plan_task_allocation(db, payload, 'codex')[0][1]
            with patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': full - 100}):
                planned = self.flow.plan_task_allocation(db, payload, 'codex')
            self.assertGreater(len(planned), 1)
            self.assertEqual([e['ref'] for p, _, _ in planned for e in p['entries']], [e['ref'] for e in payload['entries']])
            for value, raw_chars, _ in planned:
                self.assertLessEqual(raw_chars, full - 100)
                refs = {e['ref'] for e in value['entries']}
                required = {ref for f in value['audit']['findings'] for ref in f['refs']} - refs
                self.assertEqual({e['ref'] for e in value['related_entries']}, required)
                evidence = {n for e in value['entries'] + value['related_entries'] for n in e['row']['evidence_rows']}
                self.assertEqual({r['ordinal'] for r in value['records']}, evidence)

    def test_unsplittable_item_rolls_back_without_pending_tasks(self):
        with self.flow.db() as db:
            config = self.flow.config(db)
        with patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': 1000}):
            with self.assertRaisesRegex(ValueError, 'single item'):
                self.flow.prepare('extract')
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0], 0)

    def test_prepare_upgrades_only_untouched_legacy_pending_tasks(self):
        old = self.flow.prepare('extract')['tasks'][0]
        with self.flow.db() as db:
            config = self.flow.config(db)
            db.execute('DELETE FROM task_allocations')
        with patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': 5000}):
            replacement = self.flow.prepare('extract')['tasks']
        self.assertNotIn(old, replacement)
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?', (old,)).fetchone()[0], 'obsolete')
            self.assertEqual(db.execute('SELECT sum(attempt) FROM tasks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0], 0)

    def test_review_repair_allocation_counts_diagnostics_and_preserves_budgets(self):
        with self.flow.db() as db:
            config = self.flow.config(db)
            rows = [{'id': r['id'], 'ordinal': r['ordinal'], 'source': r['source'],
                     'translation': '已有译文', 'metadata': {}, 'locked': False, 'protection': [],
                     'diagnostic': '待核对的具体诊断' * 700, 'candidate': {'translation': '候选译文'}}
                    for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
            payload = {'task_id': 'review-repair-allocation', 'stage': 'review', 'revision': 1,
                       'review_repair_origin': 'review-scan', 'repair_budget_scope': 'record',
                       'repair_counts': {r['id']: 7 for r in rows}, 'records': rows,
                       'style': '忠实准确', 'context': []}
            glossary = {'characters': [], 'terms': [], 'non_translate': []}
            with (patch.object(self.flow, 'glossary', return_value=glossary),
                  patch.object(self.flow, 'config', return_value={**config, 'task_input_chars': 22000})):
                tasks = self.flow.allocate_task(db, payload, 'codex', attempt=2)
            self.assertGreater(len(tasks), 1)
            covered = []
            for task_id in tasks:
                task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                value = json.loads(task['payload']); ids = json.loads(task['ids'])
                covered.extend(ids)
                self.assertEqual(value['repair_counts'], {i: 7 for i in ids})
                self.assertEqual(task['attempt'], 2)
                receipt = db.execute('SELECT * FROM task_allocations WHERE task_id=?', (task_id,)).fetchone()
                self.assertLessEqual(max(receipt['input_chars'], receipt['compact_chars']), 22000)
            self.assertEqual(covered, [r['id'] for r in rows])

    def test_corrupted_source_is_rejected_before_worker_dispatch(self):
        task_id = self.flow.prepare('extract')['tasks'][0]
        with self.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            payload = json.loads(task['payload'])
            payload['records'][0]['source'] = 'Wrong source injected by a broken producer'
            with self.assertRaisesRegex(ValueError, 'source/protection'):
                self.flow.plan_task_allocation(db, payload, 'codex')
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0], 0)

    def test_glossary_and_review_templates_never_fabricate_semantic_approval(self):
        from workflow_task_contract import output_template
        with self.flow.db() as db:
            payload = self.glossary_payload(db)
        template = output_template(payload)
        self.assertFalse(template['decisions']['review_complete'])
        self.assertTrue(all(check['status'] is None and not check['evidence']
                            for r in template['decisions']['entry_reviews'] for check in r['checks'].values()))
        template = output_template({'stage': 'review', 'task_id': 'review-task', 'input_hash': 'version',
                                    'records': [{'id': 'real-id', 'translation': '既有译文'}]})
        self.assertEqual(template['task_id'], 'review-task')
        self.assertEqual(template['input_hash'], 'version')
        self.assertFalse(template['records'][0]['reviewed'])
        self.assertEqual(template['records'][0]['translation'], '既有译文')

    def test_glossary_handoff_is_a_complete_single_file_with_a_program_template(self):
        self.flow.prepare('extract')
        claim = self.flow.claim('extract', 'extract-worker')
        paths = self.flow.task_prompt(claim['task_file'])
        text = Path(paths['input_file']).read_text(encoding='utf-8')
        self.assertIn('output_template', text)
        self.assertIn(claim['task_id'], text)
        self.assertIn('不运行任务校验', text)
        self.assertEqual(workflow.read_json(paths['result_template_file'])['input_hash'], claim['payload']['input_hash'])

    def test_sol_packets_are_ready_and_never_marked_over_budget(self):
        with self.flow.db() as db:
            config = {**self.flow.config(db), 'task_input_chars': 3000}
        report = {'report_hash': 'real-report', 'records_to_check': [{'id': str(i), 'source': 'x' * 1700} for i in range(2)],
                  'consistency': [], 'glossary_checks': []}
        self.flow.write_sol_inputs(report, 1, config)
        manifest = workflow.read_json(self.flow.root / 'reports/sol_rounds/round_00001.inputs.json')
        self.assertEqual(len(manifest), 2)
        for part in manifest:
            self.assertFalse(part['over_budget'])
            self.assertLessEqual(part['input_chars'], 3000)
            packet = workflow.read_json(part['file'])
            self.assertEqual([i['id'] for i in packet['output_template']['decisions']], part['check_ids'])
            self.assertTrue(all(i['disposition'] == 'pending' for i in packet['output_template']['decisions']))
        report['records_to_check'][0]['source'] = 'x' * 10000
        with self.assertRaisesRegex(ValueError, 'single check'):
            self.flow.write_sol_inputs(report, 2, config)
        self.assertFalse((self.flow.root / 'reports/sol_rounds/round_00002.inputs.json').exists())

    def test_old_contract_is_refreshed_only_for_unclaimed_tasks(self):
        tasks = self.flow.prepare('extract')['tasks']
        with self.flow.db() as db:
            db.execute('UPDATE task_allocations SET contract_version=0')
        self.flow.prepare('extract')
        claim = self.flow.claim('extract', 'old-contract-worker')
        self.assertEqual(claim['context_policy']['input_contract_version'], 1)
        with self.flow.db() as db:
            db.execute('UPDATE task_allocations SET contract_version=0 WHERE task_id=?', (claim['task_id'],))
        self.flow.prepare('extract')
        with self.flow.db() as db:
            task = db.execute('SELECT status,attempt,input_hash FROM tasks WHERE id=?', (claim['task_id'],)).fetchone()
            self.assertEqual((task['status'], task['attempt'], task['input_hash']), ('running', 1, claim['payload']['input_hash']))
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE id=?', (tasks[0],)).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
