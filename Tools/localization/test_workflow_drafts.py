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
        with self.assertRaisesRegex(ValueError, 'repair round'):
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

    def test_repair_rounds_check_only_changed_ids_then_one_final_full_scan(self):
        self.flow.pause(True)
        self.flow.configure({'batch_records': 1}, 'Exercise two separate pooled repair jobs')
        self.flow.pause(False)
        bad = []
        for n in range(4):
            session = self.flow.batch_open('translate', f'draft-{n}')
            result = self.result(session)
            if n < 2:
                bad.append(result['records'][0]['id'])
                result['records'][0]['translation'] = ''
            self.assertEqual(self.save(session, result)['status'], 'submitted')
        initial = self.flow.draft_scan(prepare_repairs=True)
        self.assertEqual(initial['phase'], 'initial_full')
        self.assertEqual(initial['checked_records'], 4)
        self.assertEqual(len(initial['repair_tasks']), 2)
        first = self.flow.batch_open('translate', 'fix-first')
        fixed_id = self.result(first)['records'][0]['id']
        unfinished_fix = self.result(first)
        unfinished_fix['records'][0]['translation'] = ''  # Same failed text must still be rechecked at round end.
        self.assertEqual(self.save(first, unfinished_fix)['status'], 'submitted')
        # Reopen the Workflow object to prove receipts survive context/process changes.
        self.flow = workflow.Workflow(self.f.project)
        with patch.object(self.flow, 'collect_draft_issues', wraps=self.flow.collect_draft_issues) as validator:
            partial = self.flow.draft_scan(prepare_repairs=True)
            validator.assert_not_called()
            self.assertEqual(partial['phase'], 'awaiting_round')
            self.assertEqual(partial['checked_records'], 0)
            self.assertEqual(len(partial['repair_tasks']), 1)
            again = self.flow.draft_scan(prepare_repairs=True)
            validator.assert_not_called()
            self.assertEqual((again['phase'], again['checked_records']), ('awaiting_round', 0))
            with self.assertRaisesRegex(ValueError, 'repair round'):
                self.flow.prepare('review')
            validator.assert_not_called()
        second = self.flow.batch_open('translate', 'fix-second')
        waiting = self.flow.draft_scan(prepare_repairs=True)
        self.assertEqual(waiting['unfinished_batches']['running'], 1)
        self.assertEqual(waiting['checked_records'], 0)
        last_id = self.result(second)['records'][0]['id']
        self.assertNotEqual(fixed_id, last_id)
        self.assertEqual(self.save(second, self.result(second))['status'], 'submitted')
        with patch.object(self.flow, 'collect_draft_issues', wraps=self.flow.collect_draft_issues) as validator:
            next_round = self.flow.draft_scan(prepare_repairs=True)
            self.assertEqual([c.args[2] for c in validator.call_args_list], [bad])
            self.assertEqual(next_round['failed_records'], 1)
            self.assertEqual(len(next_round['repair_tasks']), 1)
        third = self.flow.batch_open('translate', 'fix-next-round')
        self.assertEqual(third['records'], 1)
        self.assertEqual(self.save(third, self.result(third))['status'], 'submitted')
        with patch.object(self.flow, 'collect_draft_issues', wraps=self.flow.collect_draft_issues) as validator:
            final = self.flow.draft_scan()
            self.assertEqual([c.args[2] for c in validator.call_args_list], [[fixed_id], None])
            self.assertTrue(final['clean'])
            self.assertEqual(final['phase'], 'final_full')
            history = [workflow.read_json(p) for p in final['round_reports']]
            self.assertEqual([r['scope'] for r in history], ['issues', 'full'])
            self.assertEqual([r['checked_records'] for r in history], [1, 4])
            self.assertEqual(history[0]['checked_ids'], [fixed_id])
            self.assertEqual(history[0]['result'], 'passed')
            self.assertTrue(all(t['status'] == 'done' for t in history[-1]['repair_status']))
            validator.reset_mock()
            self.assertTrue(self.flow.draft_scan()['clean'])
            validator.assert_not_called()

    def test_minor_configuration_change_reuses_unchanged_record_checks(self):
        self.f.translate()
        self.assertTrue(self.flow.draft_scan()['clean'])
        self.flow.pause(True)
        self.flow.configure({'batch_records': 1}, 'Scheduling change preserves validated text')
        self.flow.pause(False)
        with patch.object(self.flow, 'collect_draft_issues', side_effect=AssertionError('Unexpected rescan')):
            self.assertTrue(self.flow.draft_scan()['clean'])

    def test_final_full_scan_catches_a_previously_unreported_failure(self):
        for n in range(2):
            session = self.flow.batch_open('translate', f'draft-{n}')
            result = self.result(session)
            result['records'][0]['translation'] = ''
            self.assertEqual(self.save(session, result)['status'], 'submitted')
        original = self.flow.collect_draft_issues
        def missed_finding(*args, **kwargs):
            rows, found = original(*args, **kwargs)
            return rows, found[:1]  # Simulate a missed finding in the initial report.
        with patch.object(self.flow, 'collect_draft_issues', side_effect=missed_finding):
            self.assertEqual(self.flow.draft_scan(prepare_repairs=True)['failed_records'], 1)
        repair = self.flow.batch_open('translate', 'fix-first')
        self.assertEqual(self.save(repair, self.result(repair))['status'], 'submitted')
        final = self.flow.draft_scan(prepare_repairs=True)
        self.assertEqual(final['phase'], 'final_full')
        self.assertFalse(final['clean'])
        self.assertEqual(final['failed_records'], 1)
        self.assertEqual(len(final['repair_tasks']), 1)
        with self.assertRaisesRegex(ValueError, 'repair round'):
            self.flow.prepare('review')

    def test_failed_review_gate_persists_checks_instead_of_rescanning(self):
        first = self.flow.batch_open('translate', 'draft-first')
        result = self.result(first); result['records'][0]['translation'] = ''
        self.save(first, result)
        self.f.translate()
        with patch.object(self.flow, 'collect_draft_issues', wraps=self.flow.collect_draft_issues) as validator:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'Full draft scan'):
                    self.flow.prepare('review')
            self.assertEqual(validator.call_count, 1)
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM draft_checks').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT count(*) FROM draft_check_rounds').fetchone()[0], 1)

    def test_legacy_full_scan_is_reused_after_bound_repairs_have_finished(self):
        first = self.flow.batch_open('translate', 'draft-first')
        result = self.result(first)
        for item in result['records']:
            item['translation'] = ''
        self.save(first, result); self.f.translate()
        initial = self.flow.draft_scan(prepare_repairs=True)
        old = workflow.read_json(initial['report_file'])
        legacy = {k: old[k] for k in ('key', 'records', 'failed_records', 'clean', 'failures', 'repair_tasks')}
        with self.flow.db() as db:
            db.execute('DELETE FROM draft_checks')
            db.execute('DELETE FROM draft_check_rounds')
        workflow.write_json(initial['report_file'], legacy)
        repair = self.flow.batch_open('translate', 'legacy-fixer')
        fixed = self.result(repair)
        self.assertEqual(self.save(repair, fixed)['status'], 'submitted')
        with patch.object(self.flow, 'collect_draft_issues', wraps=self.flow.collect_draft_issues) as validator:
            scan = self.flow.draft_scan()
            self.assertEqual([c.args[2] for c in validator.call_args_list], [[r['id'] for r in fixed['records']], None])
            self.assertTrue(scan['clean'])
            baseline = workflow.read_json(scan['imported_baseline'])
            self.assertEqual(baseline['execution'], 'reused')
            self.assertEqual(baseline['failed_records'], 2)
            self.assertEqual(len(baseline['checked_ids']), 4)

    def test_round_attempt_budget_survives_new_tasks_and_identical_failed_text(self):
        session = self.flow.batch_open('translate', 'draft-first')
        result = self.result(session)
        target = result['records'][0]['id']; result['records'][0]['translation'] = ''
        self.save(session, result); self.f.translate()
        self.flow.draft_scan(prepare_repairs=True)
        for n in range(3):
            repair = self.flow.batch_open('translate', f'round-fixer-{n}')
            bad = self.result(repair); bad['records'][0]['translation'] = ''
            receipt = self.save(repair, bad)
            self.assertEqual(receipt['status'], 'submitted')
            self.assertIn('repair_saved', receipt['quality'])
            self.assertEqual(receipt['remaining_round_batches'], 0)
            scan = self.flow.draft_scan(prepare_repairs=True)
            self.assertEqual(scan['checked_records'], 1)
            self.assertEqual(scan['failed_records'], 1)
            if n < 2:
                self.assertEqual(len(scan['repair_tasks']), 1)
        self.assertEqual(scan['needs_attention_ids'], [target])
        self.assertEqual(scan['repair_tasks'], [])
        self.assertFalse(scan['clean'])

    def test_latest_legacy_rejected_round_candidate_can_be_saved_without_rewriting(self):
        session = self.flow.batch_open('translate', 'draft-first')
        result = self.result(session); result['records'][0]['translation'] = ''
        self.save(session, result); self.f.translate()
        self.flow.draft_scan(prepare_repairs=True)
        repair = self.flow.batch_open('translate', 'old-round-worker')
        bad = self.result(repair); bad['records'][0]['translation'] = ''
        workflow.write_json(repair['result_file'], bad)
        # Simulate the old immediate strict branch, preserving its real handoff,
        # candidate, lease and author instead of manufacturing a new task.
        with patch.object(self.flow, 'is_draft_task', return_value=False):
            self.assertEqual(self.flow.batch_finish(repair['session_id'])['status'], 'repair_required')
        self.assertEqual(self.flow.batch_finish(repair['session_id'])['status'], 'submitted')
        self.assertEqual(self.flow.draft_scan()['failed_records'], 1)

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
        payload.update(strict_translation=True, draft_scan_key='synthetic-round-key')
        workflow.write_json(task_file, payload)
        round_receipt = check()
        self.assertEqual(round_receipt['failures'], 0)
        self.assertEqual(round_receipt['quality'], 'deferred_to_round_check')
        result['records'][0]['id'] = 'wrong-id'
        workflow.write_json(session['result_file'], result)
        self.assertGreater(check()['failures'], 0)


if __name__ == '__main__':
    unittest.main()
