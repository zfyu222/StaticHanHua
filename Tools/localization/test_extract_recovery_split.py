import json
from pathlib import Path
import unittest
import test_translation_workflow as fixtures
from translation_workflow import read_json, write_json


class ExtractSplitTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow; self.flow.pause(True)
        self.flow.configure({'backend':'api'}, 'Isolated extraction recovery', impact='none')
        self.flow.pause(False)
        self.session = self.flow.batch_open('extract','old-api-worker',backend='api')

    def test_split_preserves_exact_scope_payload_and_used_attempts(self):
        with self.flow.db() as db:
            old = dict(db.execute('SELECT * FROM tasks WHERE id=?',(self.session['task_id'],)).fetchone())
        self.flow.pause(True)
        result = self.flow.split_extract_recovery(old['id'],1,worker_stopped=True)
        with self.flow.db() as db:
            children = [dict(db.execute('SELECT * FROM tasks WHERE id=?',(i,)).fetchone()) for i in result['children']]
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(old['id'],)).fetchone()[0], 'obsolete')
            self.assertEqual(db.execute('SELECT status FROM batch_runs WHERE id=?',(self.session['session_id'],)).fetchone()[0], 'interrupted')
        self.assertEqual([r for c in children for r in json.loads(c['payload'])['records']],json.loads(old['payload'])['records'])
        self.assertEqual([i for c in children for i in json.loads(c['ids'])],json.loads(old['ids']))
        self.assertTrue(all(c['attempt']==old['attempt'] for c in children))
        self.assertTrue(all(json.loads(c['payload'])['extract_split_lineage'][-1]['task_id']==old['id'] for c in children))

    def test_live_or_unconfirmed_workers_cannot_be_split(self):
        with self.assertRaises(ValueError): self.flow.split_extract_recovery(self.session['task_id'],1,True)
        self.flow.pause(True)
        with self.assertRaises(ValueError): self.flow.split_extract_recovery(self.session['task_id'],1,False)

    def test_noop_split_is_rejected_without_losing_task(self):
        self.flow.pause(True)
        with self.assertRaises(ValueError): self.flow.split_extract_recovery(self.session['task_id'],100,True)
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(self.session['task_id'],)).fetchone()[0], 'running')

    def complete_split(self):
        self.flow.pause(True)
        result = self.flow.split_extract_recovery(self.session['task_id'],1,True)
        with self.assertRaises(ValueError): self.flow.prepare('glossary_review')
        self.flow.pause(False)
        number = 0
        while True:
            packet = self.flow.batch_open('extract','fresh-split-worker-'+str(number),backend='api')
            if packet['status'] != 'ready': break
            value = read_json(Path(packet['result_file']).with_name('result.template.json'))
            write_json(Path(packet['result_file']),value)
            self.flow.batch_finish(packet['session_id']); number += 1
        return result

    def test_completed_children_allow_glossary_review_without_obsolete_parent(self):
        self.complete_split()
        self.flow.prepare('glossary_review')

    def test_obsolete_parent_cannot_hide_a_missing_child_scope(self):
        result = self.complete_split()
        with self.flow.db() as db:
            db.execute('UPDATE tasks SET status="obsolete" WHERE id=?',(result['children'][0],))
        with self.assertRaisesRegex(ValueError,'exactly once'):
            self.flow.prepare('glossary_review')


if __name__=='__main__': unittest.main()
