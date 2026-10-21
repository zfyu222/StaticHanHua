"""Cross-entry-point protocol checks with synthetic data, no external calls."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import test_translation_workflow as fixtures
import translation_workflow as workflow


class ContractPipelineTests(unittest.TestCase):
    def test_direct_and_batch_pilot_keep_original_output_exceptions_and_provenance(self):
        for mode in ('direct', 'batch'):
            with self.subTest(mode=mode):
                fixture = fixtures.WorkflowTests(); fixture.setUp()
                try:
                    flow = fixture.flow
                    flow.pause(True); flow.configure({'batch_records': None}, 'Single synthetic batch'); flow.pause(False)
                    fixture.freeze(True, pilot=False)
                    if mode == 'batch':
                        session = flow.batch_open('pilot_translate', 'batch-producer')
                        with flow.db() as db:
                            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
                        claim = {'task_id': task['id'], 'lease_token': task['lease_token'], 'payload': json.loads(task['payload'])}
                    else:
                        flow.prepare('pilot_translate')
                        claim = flow.claim('pilot_translate', 'direct-producer')
                    rows = claim['payload']['records']
                    records = [{'id': r['id'], 'translation': r['source'].replace('Alice sees a Wolf.', '爱丽丝看见一只狼。')
                        .replace('The Wolf has', '这只狼拥有').replace('gold.', '金币。').replace('text', '文本')}
                        for r in rows]
                    records[0]['exceptions'] = [{'source': 'Wolf', 'reason': 'Synthetic retained context exception.'}]
                    result = {**fixture.envelope(claim), 'records': records}
                    if mode == 'batch':
                        workflow.write_json(session['result_file'], result)
                        self.assertEqual(flow.batch_finish(session['session_id'])['status'], 'submitted')
                    else:
                        flow.submit(claim['task_id'], claim['lease_token'], result)
                    report = workflow.read_json(flow.pilot_report()['report_file'])
                    self.assertEqual(report['records'][0]['exceptions'], records[0]['exceptions'])
                    self.assertEqual(report['records'][0]['producer']['instance'], mode + '-producer')
                    fixture.approve('pilot')
                finally:
                    fixture.tearDown()

    def test_legacy_repair_input_retains_run_background_without_mutating_payload(self):
        flow = workflow.Workflow('.')
        payload = {'stage': 'review', 'task_id': 'synthetic', 'input_hash': 'unchanged',
                   'game_background': '', 'review_repair_origin': 'review-scan',
                   'records': [{'id': 'x', 'source': 'House', 'translation': '房屋'}]}
        before = json.dumps(payload)
        db = Mock(); db.execute.return_value.fetchone.return_value = {'source': 'House'}
        with patch.object(flow, 'config', return_value={'game_background': 'Synthetic historical background'}), \
                patch.object(flow, 'glossary', return_value={}), \
                patch('workflow_batches.bridge.matched_terms', return_value=[]):
            packet = flow.render_batch_input(db, payload, ['x'], Path('not-written.json'))
        self.assertIn('Synthetic historical background', packet)
        self.assertEqual(json.dumps(payload), before)


if __name__ == '__main__':
    unittest.main()
