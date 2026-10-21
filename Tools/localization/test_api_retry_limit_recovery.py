"""Recover an exhausted stopped API run without replacing its tasks or attempts."""
import unittest
from pathlib import Path
import test_translation_workflow as fixtures

class RetryLimitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.flow.pause(True)
        self.flow.configure({'backend':'api'}, 'fixture API backend', impact='none')
        self.flow.pause(False)
        self.flow.prepare('extract')
        self.run = self.flow.batch_open('extract','api-fixture',backend='api')
        self.flow.pause(True)

    def snapshot(self):
        with self.flow.db() as db:
            return self.flow.meta(db,'revision'), [tuple(r) for r in db.execute('select id,status,attempt,lease_token from tasks order by id')]

    def test_preserves_tasks_attempts_and_revision(self):
        before = self.snapshot()
        result = self.flow.configure({'max_attempts':4}, 'verified stopped runner; single bounded recovery', impact='none')
        self.assertTrue(result['retry_limit_only']); self.assertEqual(before,self.snapshot())
        with self.flow.db() as db: self.assertEqual(self.flow.config(db)['max_attempts'],4)

    def test_refuses_runner_lock(self):
        (self.flow.root/'state/api_round.lock').write_text('{}')
        with self.assertRaisesRegex(ValueError,'no runner'):
            self.flow.configure({'max_attempts':4}, 'fixture', impact='none')

    def test_refuses_unpaused_or_decreasing_limit(self):
        with self.assertRaises(ValueError): self.flow.configure({'max_attempts':2}, 'fixture', impact='none')
        self.flow.pause(False)
        with self.assertRaises(ValueError): self.flow.configure({'max_attempts':4}, 'fixture', impact='none')

    def test_refuses_reserved_request(self):
        with self.flow.db() as db:
            db.execute('insert into api_calls(task_id,time,status,usage) values (?,0,"reserved",NULL)',(self.run['task_id'],))
        with self.assertRaisesRegex(ValueError,'idle API'):
            self.flow.configure({'max_attempts':4}, 'fixture', impact='none')

if __name__=='__main__':unittest.main()
