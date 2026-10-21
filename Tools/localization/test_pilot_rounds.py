"""Random calibration uses production batching without a second review round."""
import unittest
import test_translation_workflow as fixtures
import translation_workflow as workflow
from workflow_tokens import record_limit


class PilotRoundTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow

    def test_150_sample_can_execute_more_than_twelve_batches(self):
        project = self.f.project / 'Large'; project.mkdir()
        source = project / 'large_text.csv'; source.write_text('text\n' * 170, encoding='utf-8')
        self.f.flow = flow = workflow.Workflow(project)
        flow.init(source, {'batch_records': 10, 'pilot_call_limit': 1, 'pilot_batch_count': 6})
        self.f.freeze(pilot=False)
        plan = flow.pilot_plan()
        self.assertEqual(plan['sample_records'], 150)
        self.assertEqual((plan['translation_batches'], plan['review_batches']), (15, 0))
        self.assertIsNone(plan['execution_limit'])
        self.f.pilot()
        self.assertEqual(flow.pilot_plan()['executions_raw'], 15)
        self.assertEqual(flow.status()['records'], {'source': 170})
        self.assertEqual(workflow.read_json(flow.root / 'reports/pilot_calibration.json')['statistics']['sample_records'], 150)
        self.assertFalse(any(t['stage'] == 'pilot_review' for t in flow.status()['tasks']))

    def test_identical_translation_batching(self):
        rows = [{'id': str(i), 'source': 'text'} for i in range(150)]
        config = {**workflow.DEFAULT, 'batch_records': None}
        self.assertIsNone(record_limit(config, 'pilot_translate'))
        self.assertEqual(list(self.flow.batches(rows, config, stage='pilot_translate')),
                         list(self.flow.batches(rows, config, stage='translate')))


if __name__ == '__main__':
    unittest.main()
