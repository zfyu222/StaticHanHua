"""Local process-only tests: no API credentials, models or real projects."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from run_api_round import execute


class APIRoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'TranslateFiles/state').mkdir(parents=True)

    def run_child(self, **patch):
        summary = {'accepted': 3, 'failed': 0, 'paused': False,
                   'needs_attention': [], 'pending_handoffs': [], **patch}
        code = 'import json,time; time.sleep(0.05); print(json.dumps(' + repr(summary) + '))'
        return execute(self.project, 'translate', [sys.executable, '-c', code])

    def test_single_process_finishes_and_returns_only_compact_summary(self):
        receipt = self.run_child()
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['accepted'], 3)
        self.assertEqual(json.loads(Path(receipt['receipt_file']).read_text(encoding='utf-8')), receipt)
        self.assertFalse((self.project / 'TranslateFiles/state/api_round.lock').exists())

    def test_cycle_handoff_and_external_model_are_visible_in_receipt(self):
        task = {'task_id': 'repair', 'backend': 'codex', 'required_model': 'gpt-6-sol'}
        receipt = self.run_child(continuous=True, stage_clean=False, next_action='external_executor_required',
            api_rounds=[{'cycle': 1, 'accepted': 3}],
            needs_attention=[{'reason': 'external_executor_required', 'repair_dispatch': [task]}])
        self.assertEqual(receipt['status'], 'needs_attention')
        self.assertEqual(receipt['next_action'], 'external_executor_required')
        self.assertFalse(receipt['stage_clean'])
        self.assertEqual(receipt['repair_dispatch'], [task])

    def test_translation_receipt_preserves_draft_check_boundary(self):
        receipt = self.run_child(continuous=True, stage_clean=False,
            drafts_complete=True, next_action='draft_check')
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(receipt['drafts_complete'])
        self.assertFalse(receipt['stage_clean'])
        self.assertEqual(receipt['next_action'], 'draft_check')

    def test_execution_failure_is_not_replayed(self):
        receipt = execute(self.project, 'translate', [sys.executable, '-c', 'raise SystemExit(4)'])
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['exit_code'], 4)
        self.assertEqual(len(list((self.project / 'TranslateFiles/reports/api_runs').glob('*.json'))), 1)

    def test_paused_or_unresolved_execution_is_not_called_completed(self):
        for patch in ({'paused': True}, {'needs_attention': [{'error': 'fixture detail'}]},
                      {'pending_handoffs': [{'session_id': 'fixture'}]}):
            with self.subTest(patch=patch):
                receipt = self.run_child(**patch)
                self.assertEqual(receipt['status'], 'needs_attention')
                self.assertNotIn('fixture detail', json.dumps(receipt))

    def test_invalid_reply_is_failure_with_logs_kept_on_disk(self):
        receipt = execute(self.project, 'review', [sys.executable, '-c', 'print("fixture log")'])
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(Path(receipt['stdout_log']).read_text(encoding='utf-8').strip(), 'fixture log')

    def test_existing_lock_does_not_start_another_process(self):
        lock = self.project / 'TranslateFiles/state/api_round.lock'
        lock.write_text('existing worker', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already locked'):
            self.run_child()
        self.assertEqual(lock.read_text(encoding='utf-8'), 'existing worker')

    def test_resume_reclaims_lock_only_after_both_processes_stopped(self):
        lock = self.project / 'TranslateFiles/state/api_round.lock'
        reports = self.project / 'TranslateFiles/reports/api_runs'
        reports.mkdir(parents=True)
        lock.write_text(json.dumps({'run_id': 'old', 'owner_pid': 111}), encoding='utf-8')
        (reports / 'old.json').write_text(json.dumps({'pid': 222}), encoding='utf-8')
        command = [sys.executable, '-c', 'import json; print(json.dumps(dict(accepted=0,failed=0,paused=False,needs_attention=[],pending_handoffs=[])))']
        with patch('run_api_round.pid_alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'still alive'):
                execute(self.project, 'translate', command, resume=True)
        self.assertTrue(lock.exists())
        with patch('run_api_round.pid_alive', return_value=False) as alive:
            receipt = execute(self.project, 'translate', command, resume=True)
        self.assertEqual([call.args[0] for call in alive.call_args_list], [111, 222])
        self.assertEqual(receipt['status'], 'completed')
        self.assertFalse(lock.exists())


if __name__ == '__main__':
    unittest.main()
