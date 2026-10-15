"""Whole-text draft collection, pooled repair, compatibility and strict final gates."""
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_translation_workflow as fixtures
import translation_workflow as workflow


class DraftPhaseTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow; self.f.freeze(True)

    def result(self, session):
        with self.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
        payload = json.loads(task['payload'])
        rows = []
        for row in payload['records']:
            value = row['source'].replace('Alice sees a Wolf.', '爱丽丝看见一只狼。').replace('The Wolf has', '这只狼拥有').replace('gold.', '金币。').replace('text', '文本')
            rows.append({'id': row['id'], 'translation': value})
        return {'task_id': task['id'], 'input_hash': task['input_hash'], 'records': rows}

    def save(self, session, result):
        workflow.write_json(session['result_file'], result)
        return self.flow.batch_finish(session['session_id'])

    def test_quality_errors_are_saved_then_pooled_across_original_batches(self):
        first = self.flow.batch_open('translate', 'draft-a'); one = self.result(first)
        bad_one = one['records'][0]['id']; good_one = one['records'][1]['id']
        one['records'][0]['translation'] = '暂存的草稿，遗漏变量和术语。'
        self.assertEqual(self.save(first, one)['status'], 'submitted')
        with self.assertRaisesRegex(ValueError, 'Complete all production drafts'):
            self.flow.draft_scan(prepare_repairs=True)
        second = self.flow.batch_open('translate', 'draft-b'); two = self.result(second)
        bad_two = two['records'][0]['id']; good_two = two['records'][1]['id']
        two['records'][0]['translation'] = ''
        self.assertEqual(self.save(second, two)['status'], 'submitted')
        with self.flow.db() as db:
            before = {r['id']: (r['translation'], r['exceptions']) for r in db.execute('SELECT id,translation,exceptions FROM records')}
        plan = self.flow.draft_scan(prepare_repairs=True)
        self.assertEqual(plan['failed_records'], 2)
        self.assertEqual(len(plan['repair_tasks']), 1)
        self.assertEqual(self.flow.draft_scan(prepare_repairs=True)['repair_tasks'], plan['repair_tasks'])
        report = workflow.read_json(plan['report_file'])
        self.assertGreaterEqual(len(next(r for r in report['failures'] if r['id'] == bad_one)['diagnostics']), 2)
        with self.assertRaisesRegex(ValueError, 'Full draft scan'):
            self.flow.prepare('review')
        repair = self.flow.batch_open('translate', 'pooled-fixer')
        self.assertEqual(repair['records'], 2)
        fixed = self.result(repair)
        self.assertEqual({r['id'] for r in fixed['records']}, {bad_one, bad_two})
        self.assertEqual(self.save(repair, fixed)['status'], 'submitted')
        self.assertTrue(self.flow.draft_scan()['clean'])
        with self.flow.db() as db:
            after = {r['id']: (r['translation'], r['exceptions']) for r in db.execute('SELECT id,translation,exceptions FROM records')}
            self.assertEqual(after[good_one], before[good_one]); self.assertEqual(after[good_two], before[good_two])
            sources = {r['id']: {a['instance'] for a in db.execute('SELECT instance FROM quality_authors WHERE record_id=? AND stage="translate"', (r['id'],))}
                       for r in db.execute('SELECT id FROM records')}
            self.assertEqual(sources[bad_one], {'draft-a', 'pooled-fixer'})
            self.assertEqual(sources[bad_two], {'draft-b', 'pooled-fixer'})
            self.assertEqual(sources[good_one], {'draft-a'})
            self.assertEqual(sources[good_two], {'draft-b'})
        self.assertTrue(self.flow.prepare('review')['tasks'])
        with self.assertRaisesRegex(ValueError, 'semantic review'):
            self.flow.export()

    def test_old_drafts_without_journal_are_scanned_without_retranslation(self):
        self.f.translate()
        with self.flow.db() as db:
            before = [tuple(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
            db.execute('DROP TABLE draft_candidates')  # Simulate a pre-upgrade accepted draft database.
        self.assertTrue(self.flow.draft_scan()['clean'])
        with self.flow.db() as db:
            self.assertEqual(before, [tuple(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')])

    def test_unparseable_or_wrong_id_output_is_still_rejected_immediately(self):
        session = self.flow.batch_open('translate', 'draft-writer')
        result = self.result(session); result['records'][0]['id'] = 'wrong-id'
        workflow.write_json(session['result_file'], result)
        with self.assertRaisesRegex(ValueError, 'IDs'):
            self.flow.batch_finish(session['session_id'])

    def test_malformed_exception_is_kept_for_full_scan(self):
        session = self.flow.batch_open('translate', 'draft-writer')
        result = self.result(session)
        bad_id = result['records'][0]['id']
        result['records'][0]['exceptions'] = [{'source': 'Alice'}]
        self.assertEqual(self.save(session, result)['status'], 'submitted')
        self.f.translate()
        report = workflow.read_json(self.flow.draft_scan()['report_file'])
        self.assertIn(bad_id, [r['id'] for r in report['failures']])

    def test_clean_scan_receipt_is_reused_for_review_preparation(self):
        self.f.translate()
        self.assertTrue(self.flow.draft_scan()['clean'])
        with patch.object(self.flow, 'collect_draft_issues', side_effect=AssertionError('Unexpected full rescan')):
            self.flow.prepare('review')
            self.flow.prepare('review')

    def test_legacy_helper_defers_quality_but_still_rejects_wrong_ids(self):
        session = self.flow.batch_open('translate', 'draft-writer')
        result = self.result(session)
        result['records'][0]['translation'] = ''
        workflow.write_json(session['result_file'], result)
        with self.flow.db() as db:
            payload = json.loads(db.execute('SELECT payload FROM tasks WHERE id=?', (session['task_id'],)).fetchone()[0])
        scripts = self.flow.root / 'scripts'; scripts.mkdir(exist_ok=True)
        source = workflow.bridge.ROOT / 'Projects/expeditions-conquistador/TranslateFiles/scripts/single_task.py'
        helper = scripts / source.name
        shutil.copy2(source, helper)
        shutil.copy2(source.with_name('diagnostic_restore.py'), scripts / 'diagnostic_restore.py')
        task_file, assignment = scripts / 'task.json', scripts / 'assignment.json'
        workflow.write_json(task_file, payload)
        workflow.write_json(assignment, {'task_file': str(task_file), 'result_file': session['result_file']})
        def check():
            process = subprocess.run([sys.executable, '-X', 'utf8', str(helper), 'check', '--assignment', str(assignment)],
                                     capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        receipt = check()
        self.assertEqual(receipt['failures'], 0)
        self.assertEqual(receipt['quality'], 'deferred_to_full_draft_scan')
        result['records'][0]['id'] = 'wrong-id'
        workflow.write_json(session['result_file'], result)
        self.assertGreater(check()['failures'], 0)


if __name__ == '__main__':
    unittest.main()
