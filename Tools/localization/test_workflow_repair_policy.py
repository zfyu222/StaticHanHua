"""Repair budget migration and routing without model or project access."""
import json
import sqlite3
from unittest.mock import patch
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

    def test_empty_or_omitted_exceptions_inherit_but_explicit_removal_is_audited(self):
        original = {'exceptions': [{'source': 'pagan', 'reason': 'Adjective modifying gods'}]}
        for item in ({}, {'exceptions': []}):
            self.assertEqual(ReviewRoundMixin.inherit_exceptions(item, original)['exceptions'], original['exceptions'])
        changed = {'exception_changes': [{'source': 'pagan', 'action': 'remove', 'reason': 'Different source meaning verified'}]}
        self.assertEqual(ReviewRoundMixin.inherit_exceptions(changed, original)['exceptions'], [])
        with self.assertRaises(ValueError):
            ReviewRoundMixin.inherit_exceptions({'exception_changes': [{'source': 'pagan', 'action': 'remove'}]}, original)

    def test_regression_count_is_task_deduplicated_and_forces_sol(self):
        flow = ReviewRoundMixin(); state = {}
        flow.meta = lambda db, key, default=None: state.get(key, default)
        flow.put = lambda db, key, value: state.update({key: value})
        with sqlite3.connect(':memory:') as db:
            original = {'source': 'pagan gods', 'repair_constraints': {'satisfied_terms': [{'src': 'gods', 'dst': '神祇'}]}}
            with patch.object(flow, 'repair_constraints', return_value={'missing_terms': ['gods']}):
                for task in ('repair-a', 'repair-a'):
                    self.assertEqual(flow.repair_regression(db, {}, 'review', 'x', original, '异教徒之神', [], {}, task), ['gods'])
                self.assertNotIn('repair_sol_ids', state)
                flow.repair_regression(db, {}, 'review', 'x', original, '异教徒之神', [], {}, 'repair-b')
            self.assertEqual(flow.repair_routes(db, {}, 'review', []), {'x': (0, 'sol')})
