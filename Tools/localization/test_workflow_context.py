"""Context lifetime and input-size invariants; synthetic local tasks only."""
import tempfile
import contextlib
import io
import json
from unittest.mock import patch
from pathlib import Path
import unittest

import test_translation_workflow as fixtures
import translation_workflow as workflow


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shh_context_')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.source = self.project / 'context_text.csv'
        self.source.write_text('first\nsecond\n', encoding='utf-8')
        self.flow = workflow.Workflow(self.project)

    def initialize(self, config=None):
        self.flow.init(self.source, {'batch_records': 1, **(config or {})})
        self.flow.prepare('extract')

    def submit_empty_glossary(self, claim):
        self.flow.submit(claim['task_id'], claim['lease_token'], {
            'task_id': claim['task_id'], 'input_hash': claim['payload']['input_hash'],
            'analysis': {'characters': [], 'terms': [], 'non_translate': []}})

    def test_completed_instance_cannot_take_another_batch(self):
        self.initialize()
        first = self.flow.claim('extract', 'real-instance-1')
        self.submit_empty_glossary(first)
        with self.assertRaisesRegex(ValueError, 'Fresh agent context'):
            self.flow.claim('extract', 'real-instance-1')
        second = self.flow.claim('extract', 'real-instance-2')
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertEqual(second['context_policy']['agent_task_limit'], 1)

    def test_compact_cli_keeps_full_text_on_disk_not_in_coordinator_output(self):
        self.initialize()
        output = io.StringIO()
        argv = ['translation_workflow.py', '--project', str(self.project), 'claim', '--stage', 'extract',
                '--worker', 'real-instance-1', '--compact']
        with patch.object(workflow.sys, 'argv', argv), contextlib.redirect_stdout(output):
            self.assertEqual(workflow.main(), 0)
        result = json.loads(output.getvalue())
        self.assertNotIn('payload', result)
        self.assertIn('lease_token', result)
        self.assertEqual(workflow.read_json(result['task_file'])['records'][0]['source'], 'first')

    def test_one_instance_cannot_hold_two_concurrent_batches(self):
        self.initialize()
        self.flow.claim('extract', 'real-instance-1')
        with self.assertRaisesRegex(ValueError, 'Fresh agent context'):
            self.flow.claim('extract', 'real-instance-1')
        self.assertIsNotNone(self.flow.claim('extract', 'real-instance-2'))

    def test_same_batch_retry_is_bounded_then_fresh_instance_can_continue(self):
        self.source.write_text('first\n', encoding='utf-8')
        self.initialize()
        first = self.flow.claim('extract', 'real-instance-1')
        self.flow.fail(first['task_id'], first['lease_token'], 'synthetic_retry', True)
        retry = self.flow.claim('extract', 'real-instance-1')
        self.assertEqual(first['task_id'], retry['task_id'])
        self.flow.fail(retry['task_id'], retry['lease_token'], 'synthetic_retry', True)
        with self.assertRaisesRegex(ValueError, 'Fresh agent context'):
            self.flow.claim('extract', 'real-instance-1')
        fresh = self.flow.claim('extract', 'real-instance-2')
        self.assertEqual(fresh['payload']['attempt'], 3)
        self.submit_empty_glossary(fresh)

    def test_scheduling_limit_change_preserves_completed_work(self):
        self.initialize()
        first = self.flow.claim('extract', 'real-instance-1'); self.submit_empty_glossary(first)
        self.flow.pause(True)
        result = self.flow.configure({'agent_task_limit': 2, 'agent_attempt_limit': 2,
                                      'batch_records': 80, 'review_batch_records': 40}, '调整工作量与批量')
        self.assertFalse(result['semantic_reset'])
        self.flow.pause(False); self.flow.prepare('extract')
        second = self.flow.claim('extract', 'real-instance-1')
        self.assertNotEqual(first['task_id'], second['task_id'])

    def test_full_task_budget_includes_background_and_does_not_consume_lease(self):
        self.initialize({'game_background': '背景' * 1000, 'task_input_chars': 1000})
        with self.assertRaisesRegex(ValueError, 'context budget'):
            self.flow.claim('extract', 'real-instance-1')
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT sum(attempt) FROM tasks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE status="pending"').fetchone()[0], 2)

    def test_review_batches_are_smaller_than_translation_batches(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        other = fixture.project / 'Review'; other.mkdir()
        source = other / 'review_text.csv'; source.write_text('text\n' * 45, encoding='utf-8')
        fixture.flow = workflow.Workflow(other); fixture.flow.init(source, {'batch_records': 60})
        fixture.freeze(); fixture.translate()
        self.assertEqual(len(fixture.flow.prepare('review')['tasks']), 2)
        with fixture.flow.db() as db:
            sizes = [len(workflow.json.loads(r['ids'])) for r in db.execute('SELECT ids FROM tasks WHERE stage="review"')]
        self.assertEqual(sizes, [40, 5])

    def test_body_batches_cross_groups_but_keep_size_and_character_limits(self):
        records = [{'id': str(i), 'source': 'text', 'translation': None,
                    'metadata': {'group': str(i)}} for i in range(161)]
        self.assertEqual([len(b) for b in self.flow.batches(records, workflow.DEFAULT, False)], [80, 80, 1])
        self.assertEqual(len(list(self.flow.batches(records, workflow.DEFAULT))), 161)
        limited = {**workflow.DEFAULT, 'batch_chars': 9}
        groups = list(self.flow.batches(records[:5], limited, False))
        self.assertEqual([len(b) for b in groups], [2, 2, 1])
        self.assertEqual(groups[0][1]['metadata'], {'group': '1'})

    def test_full_translation_preparation_combines_scene_metadata(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        other = fixture.project / 'Groups'; other.mkdir()
        source = other / 'group_text.csv'; source.write_text('text\n' * 85, encoding='utf-8')
        contexts = other / 'contexts.json'
        workflow.write_json(contexts, {str(i): {'group': str(i)} for i in range(1, 86)})
        fixture.flow = workflow.Workflow(other); fixture.flow.init(source, {}, contexts)
        fixture.freeze()
        fixture.flow.prepare('translate')
        with fixture.flow.db() as db:
            tasks = db.execute('SELECT payload FROM tasks WHERE stage="translate"').fetchall()
        rows = [json.loads(t['payload'])['records'] for t in tasks]
        self.assertEqual([len(batch) for batch in rows], [80, 5])
        self.assertEqual(rows[0][1]['metadata']['group'], '2')

    def test_adjacent_text_is_omitted_even_with_legacy_setting(self):
        self.source.write_text('first\nsecond\nthird\n', encoding='utf-8')
        contexts = self.project / 'contexts.json'
        workflow.write_json(contexts, {'1': {'group': 'A'}, '2': {'group': 'B'}, '3': {'group': 'A'}})
        self.flow.init(self.source, {'batch_records': 1, 'context_records': 2}, contexts)
        self.flow.prepare('extract')
        task = self.flow.claim('extract', 'real-instance-1')['payload']
        self.assertEqual(task['records'][0]['ordinal'], 1)
        self.assertEqual(task['context'], [])
        self.assertEqual([r['source'] for r in task['records']], ['first'])

    def test_legacy_prepared_context_requires_regeneration_without_consuming_lease(self):
        self.initialize()
        with self.flow.db() as db:
            task = db.execute('SELECT id,payload FROM tasks ORDER BY id').fetchone()
            payload = json.loads(task['payload'])
            payload['context'] = [{'source': 'legacy neighbour', 'translation': None}]
            db.execute('UPDATE tasks SET payload=? WHERE id=?', (workflow.encoded(payload), task['id']))
        with self.assertRaisesRegex(ValueError, 'Legacy task contains adjacent text'):
            self.flow.claim('extract', 'real-instance-1')
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
