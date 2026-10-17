"""Cross-entry-point protocol checks with synthetic data, no external calls."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import test_translation_workflow as fixtures
import translation_workflow as workflow
from workflow_quality import REVIEW_CHECKS


class ContractPipelineTests(unittest.TestCase):
    def test_direct_and_batch_pilot_keep_exceptions_and_per_record_summaries(self):
        for mode in ('direct', 'batch'):
            with self.subTest(mode=mode):
                fixture = fixtures.WorkflowTests(); fixture.setUp()
                try:
                    flow = fixture.flow; fixture.freeze(True, pilot=False)
                    original_submit = flow.submit

                    def seed(task_id, token, result):
                        result['records'][0]['exceptions'] = [{'source': 'Wolf', 'reason': 'Synthetic retained context exception.'}]
                        return original_submit(task_id, token, result)

                    with patch.object(flow, 'submit', side_effect=seed):
                        fixture.translate('pilot_translate')
                    if mode == 'batch':
                        session = flow.batch_open('pilot_review', 'batch-independent-reviewer')
                        with flow.db() as db:
                            task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
                        claim = {'task_id': task['id'], 'lease_token': task['lease_token'], 'input_hash': task['input_hash'],
                                 'payload': json.loads(task['payload'])}
                    else:
                        flow.prepare('pilot_review')
                        claim = flow.claim('pilot_review', 'direct-independent-reviewer')
                    rows = claim['payload']['records']; target = rows[0]
                    records = [{'id': r['id'], 'reviewed': True, 'verdict': 'pass'} for r in rows]
                    records[0] = {'id': target['id'], 'translation': target['translation'], 'reviewed': True,
                                  'verdict': 'corrected', 'reason': 'Explicitly checked retained context exception.',
                                  **fixture.review_evidence(target)}
                    # Deliberately omit exceptions: both entry points must inherit.
                    summaries = {r['id']: {k: {'status': 'pass', 'evidence': 'Synthetic record ' + r['id']}
                                           for k in REVIEW_CHECKS} for r in rows}
                    result = {'task_id': claim['task_id'], 'input_hash': claim['payload']['input_hash'],
                              'records': records, 'review_summaries': summaries}
                    if mode == 'batch':
                        workflow.write_json(session['result_file'], result)
                        self.assertEqual(flow.batch_finish(session['session_id'])['status'], 'submitted')
                    else:
                        flow.submit(claim['task_id'], claim['lease_token'], result)
                    with flow.db() as db:
                        saved = json.loads(db.execute('SELECT result FROM tasks WHERE id=?', (claim['task_id'],)).fetchone()[0])
                    self.assertEqual(saved['review_summaries'], summaries)
                    self.assertEqual(saved['records'][0]['exceptions'], target['exceptions'])
                    self.assertTrue(flow.quality_report('pilot')['report_hash'])
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
