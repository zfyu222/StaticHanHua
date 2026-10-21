"""Pilot retry, acceptance reuse and legacy configuration compatibility."""
import unittest
import uuid

import test_translation_workflow as fixtures
import translation_workflow as workflow


class PilotCostTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.flow.pause(True); self.flow.configure({'pilot_call_limit': 6}, 'Verify a legacy six-call field is ignored'); self.flow.pause(False)
        self.f.freeze(True, pilot=False)

    def test_legacy_cost_fields_do_not_cap_or_force_batches(self):
        self.flow.pause(True)
        self.flow.configure({'pilot_call_limit': 1, 'pilot_batch_count': 6}, 'Ignore retired pilot costs')
        self.flow.pause(False)
        plan = self.flow.pilot_plan()
        self.assertEqual((plan['translation_batches'], plan['review_batches']), (2, 0))
        self.f.pilot()
        self.assertGreater(self.flow.pilot_plan()['executions_used'], 1)

    def test_retries_use_the_production_task_execution_limit(self):
        self.flow.prepare('pilot_translate')
        for _ in range(3):
            claim = self.flow.claim('pilot_translate', 'translator-' + uuid.uuid4().hex)
            task_id = claim['task_id']
            self.flow.fail(task_id, claim['lease_token'], 'synthetic_transport_failure', True)
        with self.flow.db() as db:
            task = db.execute('SELECT status,attempt FROM tasks WHERE id=?', (task_id,)).fetchone()
        self.assertEqual((task['status'], task['attempt']), ('failed', 3))
        self.assertIsNone(self.flow.pilot_plan()['execution_limit'])

    def test_reduce_sample_reuses_completed_translation(self):
        project = self.f.project / 'Legacy'; project.mkdir()
        source = project / 'legacy_text.csv'; source.write_text('text\n' * 100, encoding='utf-8')
        self.f.flow = flow = workflow.Workflow(project)
        flow.init(source, {'pilot_records': 100, 'batch_records': 50})
        self.f.freeze(pilot=False); flow.prepare('pilot_translate')
        claim = flow.claim('pilot_translate', 'first-legacy-batch')
        flow.submit(claim['task_id'], claim['lease_token'], {**self.f.envelope(claim),
            'records': [{'id': r['id'], 'translation': '文本'} for r in claim['payload']['records']]})
        flow.pause(True); result = flow.reduce_pilot(30, 'Adopt cheaper sample while retaining work'); flow.pause(False)
        self.assertEqual(result['reused_translations'], 30)
        plan = flow.pilot_plan()
        self.assertEqual((plan['translation_batches'], plan['review_batches']), (0, 0))
        self.assertEqual(plan['executions_used'], 1)
        self.assertEqual(flow.status()['records'], {'pilot_translate': 30, 'source': 70})
        with flow.db() as db:
            ordinals = flow.config(db)['pilot_ordinals']
        self.assertTrue(all(n <= 50 for n in ordinals))


if __name__ == '__main__':
    unittest.main()
