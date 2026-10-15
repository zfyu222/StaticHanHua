"""Repair budget migration and routing without model or project access."""
import json
import unittest
from workflow_review_rounds import ReviewRoundMixin


class RepairPolicyTests(unittest.TestCase):
    def routes(self, tasks, stage='translate', counters=None):
        flow = ReviewRoundMixin()
        flow.meta = lambda db, key, default=None: (counters or {}) if key == 'draft_repair_attempts' else default
        return flow.repair_routes(None, {}, stage, tasks)

    def task(self, attempt, count=None, stage='translate', status='done'):
        payload = {'draft_scan_key': 'key'} if stage == 'translate' else {'review_repair_origin': 'scan'}
        if count is not None:
            payload['repair_counts'] = {'x': count}
        return {'attempt': attempt, 'ids': '["x"]', 'payload': json.dumps(payload), 'status': status}

    def test_legacy_attempts_preserved_and_sixth_round_escalates(self):
        self.assertEqual(self.routes([self.task(3)]), {'x': (3, 'standard')})
        self.assertEqual(self.routes([], counters={'x': 6}), {'x': (6, 'sol')})

    def test_request_retries_do_not_consume_extra_repair_rounds(self):
        self.assertEqual(self.routes([self.task(3, 1)]), {'x': (1, 'standard')})
        self.assertEqual(self.routes([self.task(3, 6)]), {'x': (6, 'sol')})

    def test_initial_review_and_unfinished_tasks_are_not_repairs(self):
        initial = self.task(3); initial['payload'] = '{}'
        self.assertEqual(self.routes([initial, self.task(3, status='running')]), {})
        self.assertEqual(self.routes([self.task(3, stage='review')], stage='review'), {'x': (2, 'standard')})

    def test_sol_routing_uses_explicit_model(self):
        route = ReviewRoundMixin.repair_routing({}, 'sol')
        self.assertEqual(route['required_model'], 'gpt-6-sol')
        self.assertTrue(route['automatic_escalation'])
