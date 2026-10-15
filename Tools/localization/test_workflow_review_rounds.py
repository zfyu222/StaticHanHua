"""Whole-round semantic review and Sol acceptance using synthetic local data."""
import json
import unittest
from unittest.mock import patch

import test_translation_workflow as fixtures
import translation_workflow as workflow


class ReviewRoundTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow; self.f.freeze(True); self.f.translate()

    def result(self, session, bad_ids=()):
        with self.flow.db() as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
        rows = json.loads(task['payload'])['records']
        records = [{'id': r['id'], 'translation': r['translation'], 'reviewed': True,
                    'reason': 'Synthetic independent source comparison', **self.f.review_evidence(r)} for r in rows]
        for item in records:
            if item['id'] in bad_ids:
                item.pop('checks')
        return {'task_id': task['id'], 'input_hash': task['input_hash'], 'records': records}

    def save(self, session, result):
        workflow.write_json(session['result_file'], result)
        return self.flow.batch_finish(session['session_id'])

    def complete_review(self):
        for n in range(2):
            session = self.flow.batch_open('review', f'reviewer-{n}')
            self.assertEqual(self.save(session, self.result(session))['status'], 'submitted')
        self.assertTrue(self.flow.review_scan()['clean'])

    def test_six_review_repair_rounds_then_automatic_sol(self):
        first = self.flow.batch_open('review', 'initial-a')
        result = self.result(first); target = result['records'][0]['id']
        result['records'][0].pop('checks'); self.save(first, result)
        second = self.flow.batch_open('review', 'initial-b'); self.save(second, self.result(second))
        self.flow.review_scan(prepare_repairs=True)
        for n in range(6):
            repair = self.flow.batch_open('review', f'repair-{n}')
            self.assertEqual(repair['repair_tier'], 'standard')
            self.assertEqual(repair['repair_counts'], {target: n + 1})
            self.save(repair, self.result(repair, [target]))
            report = self.flow.review_scan(prepare_repairs=True)
            self.assertEqual(report['checked_records'], 1)
            self.assertEqual(report['needs_attention_ids'], [])
        sol = self.flow.batch_open('review', 'sol-review-fix', worker_model='gpt-6-sol')
        self.assertEqual(sol['required_model'], 'gpt-6-sol')
        self.assertTrue(sol['automatic_escalation'])
        self.save(sol, self.result(sol))
        self.assertTrue(self.flow.review_scan()['clean'])

    def test_no_partial_acceptance_or_early_repairs_and_cross_batch_regrouping(self):
        first = self.flow.batch_open('review', 'review-a'); one = self.result(first)
        bad_a = one['records'][0]['id']; good_a = one['records'][1]['id']
        one['records'][0].pop('checks')
        corrected = one['records'][1]
        before_text = corrected['translation']; corrected['translation'] += '！'
        corrected['checks']['tone']['status'] = 'corrected'
        corrected['changes'] = [{'category': 'tone', 'severity': 'minor', 'source_excerpt': corrected['source_excerpt'],
                                 'before': before_text, 'after': corrected['translation'], 'reason': 'Synthetic correction for staging test'}]
        before = self.flow.status()['records']
        self.assertEqual(self.save(first, one)['status'], 'submitted')
        self.assertEqual(self.flow.status()['records'], before)
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT translation FROM records WHERE id=?', (good_a,)).fetchone()[0], before_text)
        waiting = self.flow.review_scan(prepare_repairs=True)
        self.assertEqual((waiting['phase'], waiting['checked_records']), ('awaiting_round', 0))
        with self.assertRaises(ValueError):
            self.flow.quality_report('final')
        second = self.flow.batch_open('review', 'review-b'); two = self.result(second)
        bad_b = two['records'][0]['id']; two['records'][0].pop('checks')
        self.save(second, two)
        checked = self.flow.review_scan(prepare_repairs=True)
        self.assertEqual((checked['checked_records'], checked['failed_records']), (4, 2))
        self.assertEqual(len(checked['repair_tasks']), 1)
        self.assertEqual(self.flow.status()['records'], {'translate': 2, 'review': 2})
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT translation FROM records WHERE id=?', (good_a,)).fetchone()[0], corrected['translation'])
        self.assertEqual(self.flow.review_scan()['checked_records'], 0)
        repaired = self.flow.batch_open('review', 'review-fix')
        fixed = self.result(repaired)
        self.assertEqual({r['id'] for r in fixed['records']}, {bad_a, bad_b})
        self.save(repaired, fixed)
        scan = self.flow.review_scan()
        self.assertEqual(scan['checked_records'], 2); self.assertTrue(scan['clean'])
        with self.flow.db() as db:
            reviews = self.flow.latest_reviews(db, 'final')
        self.assertEqual(reviews[good_a]['instance'], 'review-a')
        self.assertEqual(reviews[bad_a]['instance'], 'review-fix')
        self.assertEqual(reviews[bad_b]['instance'], 'review-fix')

    def test_sol_collects_entire_scope_before_dispatch_and_reuses_passed_checks(self):
        self.complete_review()
        opened = self.flow.quality_report('final'); report = workflow.read_json(opened['report_file'])
        ids = report['required_decisions']; self.assertGreater(len(ids), 1)
        target = report['records_to_check'][0]['id']
        problem = {'id': target, 'disposition': 'repair', 'reason': 'Synthetic source-based issue for this exact record.'}
        envelope = {'report_hash': report['report_hash'], 'reviewer': 'sol', 'summary': 'Synthetic inspected evidence'}
        before_tasks = self.flow.status()['tasks']
        self.flow.record_sol_round({**envelope, 'decisions': [problem]})
        self.assertEqual(self.flow.status()['tasks'], before_tasks)
        with self.assertRaisesRegex(ValueError, 'entire Sol'):
            self.flow.finish_sol_round()
        with self.flow.db() as db:
            task_id = self.flow.latest_reviews(db, 'final')[target]['task_id']
        with self.assertRaisesRegex(ValueError, 'entire Sol'):
            self.flow.reopen_body(task_id, 'Do not dispatch during collection', [target])
        with patch.object(self.flow, 'build_quality_report', side_effect=AssertionError('Unnecessary report rebuild')):
            same = self.flow.quality_report('final')
        self.assertEqual(same['report_hash'], report['report_hash'])
        self.assertNotIn(target, workflow.read_json(same['report_file'])['required_decisions'])
        rest = [{'id': i, 'disposition': 'pass', 'reason': 'Explicitly inspected unchanged check'} for i in ids if i != target]
        self.flow.record_sol_round({**envelope, 'decisions': rest})
        finished = self.flow.finish_sol_round()
        self.assertEqual(finished['repair_records'], 1)
        self.assertEqual(len(finished['repair_tasks']), 1)
        with self.assertRaises(ValueError):
            self.flow.quality_report('final')
        repair = self.flow.batch_open('review', 'sol-repair-reviewer')
        self.assertEqual(repair['records'], 1)
        self.save(repair, self.result(repair)); self.assertTrue(self.flow.review_scan()['clean'])
        next_round = self.flow.quality_report('final'); new_report = workflow.read_json(next_round['report_file'])
        self.assertIn(target, new_report['required_decisions'])
        self.assertTrue(new_report['reused_decisions'])
        self.assertTrue(set(i['id'] for i in rest) - set(new_report['required_decisions']))
        self.flow.record_sol_round({'report_hash': new_report['report_hash'], 'reviewer': 'sol', 'summary': 'Inspected repaired evidence and affected groups',
            'decisions': [{'id': i, 'disposition': 'pass', 'reason': 'Explicitly verified repaired check'} for i in new_report['required_decisions']]})
        self.assertEqual(self.flow.finish_sol_round()['status'], 'approved')
        self.assertEqual(self.flow.export()['records'], 4)

    def test_stale_unknown_and_uninspected_sol_checks_cannot_create_jobs(self):
        self.complete_review()
        opened = self.flow.quality_report('final'); report = workflow.read_json(opened['report_file'])
        for key, record_id in [('stale', report['required_decisions'][0]), (report['report_hash'], 'unknown')]:
            with self.assertRaises(ValueError):
                self.flow.record_sol_round({'report_hash': key, 'reviewer': 'sol', 'summary': 'Synthetic',
                    'decisions': [{'id': record_id, 'disposition': 'repair', 'reason': 'Synthetic error'}]})
        with self.assertRaises(ValueError):
            self.flow.finish_sol_round()
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE status="pending"').fetchone()[0], 0)

    def test_sol_input_batches_have_complete_fixed_scope(self):
        self.complete_review()
        self.flow.pause(True); self.flow.configure({'review_batch_records': 1}, 'Small synthetic Sol inspection packets'); self.flow.pause(False)
        opened = self.flow.quality_report('final'); report = workflow.read_json(opened['report_file'])
        manifest = workflow.read_json(opened['inspection_manifest'])
        self.assertEqual([i for b in manifest for i in b['check_ids']], report['required_decisions'])
        self.assertTrue(all(len(b['check_ids']) == 1 for b in manifest))
        for batch in manifest:
            packet = workflow.read_json(batch['file'])
            self.assertEqual(packet['report_hash'], report['report_hash'])
            self.assertEqual([i['id'] for i in packet['checks']], batch['check_ids'])

    def test_sol_partial_passes_do_not_bypass_stale_round_or_implicit_approval(self):
        self.complete_review()
        opened = self.flow.quality_report('final'); report = workflow.read_json(opened['report_file'])
        entry = {'id': report['required_decisions'][0], 'disposition': 'pass', 'reason': 'Actually inspected synthetic evidence'}
        self.flow.checkpoint_quality('final', {'report_hash': report['report_hash'], 'reviewer': 'sol',
                                             'summary': 'Only first check inspected', 'decisions': [entry]})
        with self.assertRaises(ValueError):
            self.flow.export()
        with self.assertRaises(ValueError):
            self.flow.finish_sol_round()
        self.flow.pause(True); self.flow.configure({'review_batch_records': 1}, 'Changed scheduling while preserving text'); self.flow.pause(False)
        with self.assertRaisesRegex(ValueError, 'Stale Sol'):
            self.flow.record_sol_round({'report_hash': report['report_hash'], 'reviewer': 'sol', 'summary': 'Stale', 'decisions': [entry]})
        current = self.flow.quality_report('final'); view = workflow.read_json(current['report_file'])
        self.assertNotIn(entry['id'], view['required_decisions'])
        self.assertIn(entry['id'], view['reused_decisions'])

    def test_sol_report_reuses_unchanged_validated_review_evidence(self):
        self.complete_review()
        with patch('workflow_quality.validate_review', side_effect=AssertionError('Repeated proof validation')):
            self.assertIsNotNone(self.flow.quality_report('final')['sol_round'])


if __name__ == '__main__':
    unittest.main()
