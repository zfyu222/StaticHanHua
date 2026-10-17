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

    def ledger(self):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE tasks(id TEXT,stage TEXT,revision INTEGER,status TEXT,attempt INTEGER,ids TEXT,payload TEXT,result TEXT)')
        flow = ReviewRoundMixin()
        flow.meta = lambda db, key, default=None: default
        return flow, db

    def add_repair(self, db, name, stage, revision, count, scope=None, tier='standard', status='done'):
        payload = {'draft_scan_key': 'scan'} if stage == 'translate' else {'review_repair_origin': 'scan'}
        payload.update(repair_counts={'x': count}, repair_tier=tier)
        if scope:
            payload['repair_budget_scope'] = scope
        db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)',
                   (name, stage, revision, status, 1, '["x"]', json.dumps(payload), '{}'))

    def test_budget_survives_stage_revision_and_completed_task_obsolescence(self):
        flow, db = self.ledger()
        self.add_repair(db, 'old-draft', 'translate', 1, 3, status='obsolete')
        self.add_repair(db, 'old-review', 'review', 2, 2)
        self.add_repair(db, 'new-review', 'final_review', 3, 6, scope='record')
        self.assertEqual(flow.repair_routes(db, {}, 'review'), {'x': (6, 'sol')})
        flow.meta = lambda db, key, default=None: {'x': 6} if key == 'draft_repair_attempts' else default
        self.assertEqual(flow.repair_routes(db, {}, 'translate'), {'x': (6, 'sol')})

    def test_sol_limits_and_no_progress_receipts_are_idempotent(self):
        flow, db = self.ledger()
        for n in range(2):
            self.add_repair(db, str(n), 'review', n + 1, n + 7, scope='record', tier='sol')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (str(n),)).fetchone()
            for _ in range(2):
                flow.record_repair_outcome(db, task, 'x', {'code': 'same_problem'})
        self.assertEqual(db.execute('SELECT count(*) FROM repair_outcomes').fetchone()[0], 2)
        self.assertEqual(flow.repair_routes(db, {}, 'translate')['x'][1], 'stopped')
        self.assertEqual(flow.repair_stops(db, {})['x'], 'sol_no_progress')

    def test_changed_diagnostics_do_not_hide_the_total_sol_limit(self):
        flow, db = self.ledger()
        for n in range(3):
            self.add_repair(db, str(n), 'review', n, n + 7, scope='record', tier='sol')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (str(n),)).fetchone()
            flow.record_repair_outcome(db, task, 'x', {'code': str(n)})
        self.assertEqual(flow.repair_routes(db, {}, 'review')['x'][1], 'stopped')
        self.assertEqual(flow.repair_stops(db, {})['x'], 'sol_round_limit')

    def test_empty_or_omitted_exceptions_inherit_but_explicit_removal_is_audited(self):
        original = {'exceptions': [{'source': 'pagan', 'reason': 'Adjective modifying gods'}]}
        for item in ({}, {'exceptions': []}):
            self.assertEqual(ReviewRoundMixin.inherit_exceptions(item, original)['exceptions'], original['exceptions'])
        changed = {'exception_changes': [{'source': 'pagan', 'action': 'remove', 'reason': 'Different source meaning verified'}]}
        self.assertEqual(ReviewRoundMixin.inherit_exceptions(changed, original)['exceptions'], [])
        with self.assertRaises(ValueError):
            ReviewRoundMixin.inherit_exceptions({'exception_changes': [{'source': 'pagan', 'action': 'remove'}]}, original)

    def test_exception_keys_preserve_distinct_frozen_source_casing(self):
        original = {'exceptions': [{'source': 'Guard', 'reason': 'A title'}, {'source': 'guard', 'reason': 'An ordinary action'}]}
        merged = ReviewRoundMixin.inherit_exceptions({'exceptions': []}, original)
        self.assertEqual(merged['exceptions'], original['exceptions'])
        changed = ReviewRoundMixin.inherit_exceptions({'exception_changes': [
            {'source': 'guard', 'action': 'remove', 'reason': 'Rechecked this exact frozen term'}]}, original)
        self.assertEqual(changed['exceptions'], original['exceptions'][:1])

    def test_exception_removal_requires_text_evidence_and_cannot_silently_readd(self):
        original = {'exceptions': [{'source': 'guard', 'reason': 'Ordinary action'}]}
        for reason in (None, 1, {}, ' '):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, 'requires source'):
                ReviewRoundMixin.inherit_exceptions({'exception_changes': [
                    {'source': 'guard', 'action': 'remove', 'reason': reason}]}, original)
        with self.assertRaisesRegex(ValueError, 'must not also'):
            ReviewRoundMixin.inherit_exceptions({'exceptions': original['exceptions'], 'exception_changes': [
                {'source': 'guard', 'action': 'remove', 'reason': 'Explicit recheck'}]}, original)

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
