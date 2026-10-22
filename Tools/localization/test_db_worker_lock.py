"""Shared API workers queue before SQLite's cross-process busy timeout."""
import sqlite3
import threading
import unittest
from unittest.mock import patch
import test_translation_workflow as fixtures


class WorkerLockTests(unittest.TestCase):
    def test_workers_wait_and_exception_releases_lock(self):
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.tearDown)
        flow = f.flow
        opened, release, finished = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def holder():
            try:
                with flow.db():
                    opened.set()
                    release.wait(5)
                    raise ValueError('rollback fixture')
            except ValueError:
                pass
        def waiter():
            try:
                with flow.db() as db:
                    self.assertIsNotNone(db.execute('select 1').fetchone())
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()
        original = sqlite3.connect
        def short_timeout(*args, **kwargs):
            kwargs['timeout'] = 0.01
            return original(*args, **kwargs)
        with patch('translation_workflow.sqlite3.connect', short_timeout):
            first = threading.Thread(target=holder); second = threading.Thread(target=waiter)
            first.start(); self.assertTrue(opened.wait(5)); second.start()
            try:
                self.assertFalse(finished.wait(0.1))
            finally:
                release.set(); first.join(5); second.join(5)
            self.assertTrue(finished.is_set())
            self.assertEqual(errors, [])
        with flow.db() as db:
            self.assertIsNotNone(db.execute('select 1').fetchone())


if __name__ == '__main__':
    unittest.main()
