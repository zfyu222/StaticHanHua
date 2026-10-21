"""Real workflow dispatch with blocked mock requests; no external API calls."""
import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import ainiee_bridge as bridge
import test_translation_workflow as fixtures
import translation_workflow as workflow


class APIConcurrencyTests(unittest.TestCase):
    def setUp(self):
        provenance = patch.object(bridge, 'provenance', return_value=bridge.provenance())
        provenance.start(); self.addCleanup(provenance.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='shh_api_concurrency_')
        self.addCleanup(self.temp.cleanup)
        local_api = patch.object(workflow, 'API_CONFIG_FILE', Path(self.temp.name) / 'api.local.jsonc')
        local_api.start(); self.addCleanup(local_api.stop)
        self.project = Path(self.temp.name)

    def exercise(self, records, expected_peak, api_patch=None):
        source = self.project / 'sample_text.csv'
        with source.open('w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows([[f'Synthetic source {i}.'] for i in range(records)])
        flow = workflow.Workflow(self.project)
        flow.init(source, {'backend': 'api', 'batch_records': 1, 'api': {
            'api_url': 'https://example.invalid/v1', 'model_name': 'mock', **(api_patch or {})}})
        lock, release = threading.Lock(), threading.Event()
        state = {'active': 0, 'peak': 0, 'ids': [], 'closed': False}

        class Mock:
            def request(self, system, user):
                payload = fixtures.model_input(user)
                with lock:
                    state['active'] += 1
                    state['peak'] = max(state['peak'], state['active'])
                    # Task IDs are program-owned and intentionally absent from
                    # API input. Extraction keeps source ordinals for coverage.
                    state['ids'].append(tuple(r['ordinal'] for r in payload['records']))
                    if state['active'] == expected_peak:
                        release.set()
                try:
                    if not release.wait(30):
                        raise AssertionError('Configured simultaneous requests never started')
                    return json.dumps(payload['output_template']), {'prompt_tokens': 1, 'completion_tokens': 1}
                finally:
                    with lock:
                        state['active'] -= 1

            def close(self):
                state['closed'] = True

        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock-only'}):
            summary = workflow.run_api(flow, 'extract', lambda *_: Mock())
        self.assertTrue(state['closed'])
        self.assertEqual(state['peak'], expected_peak)
        self.assertEqual(summary['peak_in_flight'], expected_peak)
        self.assertEqual(len(state['ids']), len(set(state['ids'])))
        self.assertEqual(summary['failed'], 0)
        self.assertEqual(summary['needs_attention'], [])
        self.assertEqual(summary['pending_handoffs'], [])
        self.assertEqual(summary['performance']['run_tokens']['total_tokens'], 2 * summary['accepted'])
        self.assertEqual(summary['performance']['run_tokens']['unknown_requests'], 0)
        self.assertEqual(summary['performance']['timings']['api_request']['count'], summary['accepted'])
        with flow.db() as db:
            done = db.execute('SELECT count(*) FROM tasks WHERE status="done"').fetchone()[0]
            self.assertEqual(done, summary['accepted'])
            self.assertEqual(db.execute('SELECT count(*) FROM api_calls WHERE status!="cancelled"').fetchone()[0], done)
        return summary

    def test_default_reaches_fifty_simultaneous_requests_without_duplicates(self):
        summary = self.exercise(55, 50)
        self.assertEqual(summary['concurrency_limit'], 50)
        self.assertEqual(summary['accepted'], 55)

    def test_project_concurrency_override_sets_actual_ceiling(self):
        summary = self.exercise(17, 7, {'concurrency': 7})
        self.assertEqual(summary['concurrency_limit'], 7)
        self.assertEqual(summary['accepted'], 17)

    def test_request_budget_is_not_exceeded_with_fifty_workers(self):
        summary = self.exercise(12, 3, {'max_requests': 3})
        self.assertEqual(summary['accepted'], 3)
        self.assertTrue(summary['paused'])

    def test_rate_limit_zero_disables_pacing_and_invalid_values_are_rejected(self):
        for value in (0, 30):
            self.assertEqual(workflow.merge_config(workflow.DEFAULT, {'api': {'requests_per_minute': value}})['api']['requests_per_minute'], value)
        for value in (-1, True, 1.5):
            with self.assertRaisesRegex(ValueError, 'nonnegative integer'):
                workflow.merge_config(workflow.DEFAULT, {'api': {'requests_per_minute': value}})


if __name__ == '__main__':
    unittest.main()
