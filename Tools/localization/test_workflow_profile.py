"""Profile arithmetic, throttling, thread safety and runner propagation; no API."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

import ainiee_bridge as bridge

from run_api_round import execute
from workflow_profile import RunProfile, profiled, usage_totals


class ProfileTests(unittest.TestCase):
    def test_phase_times_accumulate_across_cycles(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stderr(io.StringIO()), \
                patch('workflow_profile.time.perf_counter', return_value=0) as clock:
            profile = RunProfile(Path(folder), 'review')
            profile.emit('phase_started', phase='prepare')
            clock.return_value = 2
            profile.emit('phase_finished', phase='prepare')
            clock.return_value = 3
            profile.emit('phase_started', phase='prepare')
            clock.return_value = 7
            profile.emit('phase_finished', phase='prepare')
            self.assertEqual(profile.finish('completed')['phase_seconds']['prepare'], 6)

    def test_empty_api_reply_retains_reported_usage(self):
        api = bridge.AINieeAPI.__new__(bridge.AINieeAPI)
        api.settings = {'model_name': 'mock'}
        api.parser = Mock()
        api.parser.apply_platform_thinking_params.side_effect = lambda params, _: params
        api.client = Mock()
        api.client.chat.completions.with_raw_response.create.return_value.parse.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=''))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        with self.assertRaises(bridge.ApiFailure) as error:
            api.request('', 'fixture')
        self.assertEqual(error.exception.usage['total_tokens'], 15)

    def test_failed_prepare_still_has_summary_and_incomplete_phase(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stderr(io.StringIO()):
            import translation_workflow as workflow
            class Broken:
                root = Path(folder)
            def fail(*args):
                args[-1].emit('phase_started', phase='prepare')
                raise ValueError('fixture-only')
            with self.assertRaises(ValueError), patch.object(workflow, '_run_api', side_effect=fail):
                workflow.run_api(Broken(), 'translate', continuous=False)
            summary_file = next((Path(folder) / 'reports/api_runs').glob('*.profile.json'))
            summary = json.loads(summary_file.read_text(encoding='utf-8'))
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual(summary['error_type'], 'ValueError')
            self.assertEqual(summary['requests_started'], 0)
            self.assertEqual(summary['unfinished_phases'], ['prepare'])
            self.assertNotIn('fixture-only', summary_file.read_text(encoding='utf-8'))

    def test_tokens_include_retries_without_double_counting_detail_tokens(self):
        result = usage_totals([
            {'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18,
             'prompt_tokens_details': {'cached_tokens': 4}, 'completion_tokens_details': {'reasoning_tokens': 3}},
            {'input_tokens': 5, 'output_tokens': 2}, {}, None,
            {'prompt_tokens': 0, 'completion_tokens': 0, 'usage_reported': False}])
        self.assertEqual(result['total_tokens'], 25)
        self.assertEqual(result['prompt_tokens'], 15)
        self.assertEqual(result['completion_tokens'], 10)
        self.assertEqual(result['cached_prompt_tokens'], 4)
        self.assertEqual(result['reasoning_tokens'], 3)
        self.assertEqual(result['unknown_requests'], 3)
        self.assertEqual(result['reported_requests'], 2)

    def test_progress_is_throttled_and_each_completed_phase_has_wall_time(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stderr(io.StringIO()):
            with patch('workflow_profile.time.perf_counter', return_value=0) as clock:
                profile = RunProfile(Path(folder), 'translate')
                profile.emit('phase_started', phase='prepare')
                profile.progress('prepare', 0, 100, force=True)
                profile.progress('prepare', 9, 100)
                profile.progress('prepare', 10, 100)
                clock.return_value = 31
                profile.progress('prepare', 10, 100)
                profile.emit('phase_finished', phase='prepare')
                summary = profile.finish('completed')
            events = [json.loads(line) for line in profile.log_path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([e['completed'] for e in events if e['event'] == 'progress'], [0, 10, 10])
            self.assertEqual(summary['phase_seconds']['prepare'], 31)
            self.assertEqual(summary['elapsed_seconds'], 31)
            self.assertIsNone(summary['first_request_seconds'])
            self.assertEqual(events[-1]['event'], 'run_finished')

    def test_concurrent_usage_and_timers_are_not_lost(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stderr(io.StringIO()):
            profile = RunProfile(Path(folder), 'translate')
            @profiled('mock_work')
            def calculate():
                return 1
            def worker(i):
                with profile.activate():
                    calculate()
                    profile.request_started(i)
                    profile.response(i, 0.1, {'prompt_tokens': 3, 'completion_tokens': 2}, 'received')
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(worker, range(40)))
            summary = profile.finish('completed')
            self.assertEqual(summary['requests_started'], 40)
            self.assertEqual(summary['run_tokens']['total_tokens'], 200)
            self.assertEqual(summary['timings']['mock_work']['count'], 40)
            events = [json.loads(line) for line in profile.log_path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(sum(e['event'] == 'request_finished' for e in events), 40)

    def test_runner_receipt_carries_child_profile_and_keeps_stdout_machine_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'TranslateFiles'
            (root / 'state').mkdir(parents=True)
            summary = dict(accepted=1, failed=0, paused=False, needs_attention=[], pending_handoffs=[])
            code = ('import sys,json\n'
                    f'sys.path.insert(0, {str(Path(__file__).parent)!r})\n'
                    'from workflow_profile import RunProfile\n'
                    f'p=RunProfile({str(root)!r}, "translate")\n'
                    'p.request_started(1)\n'
                    'p.response(1,0.1,dict(prompt_tokens=10,completion_tokens=5),"received")\n'
                    f'summary={summary!r}\n'
                    'summary["performance"]=p.finish("completed",summary)\n'
                    'print(json.dumps(summary))\n')
            receipt = execute(folder, 'translate', [sys.executable, '-c', code])
            self.assertEqual(receipt['status'], 'completed')
            self.assertEqual(receipt['performance']['run_id'], receipt['run_id'])
            self.assertEqual(receipt['performance']['run_tokens']['total_tokens'], 15)
            self.assertGreater(receipt['elapsed_seconds'], 0)
            self.assertTrue(Path(receipt['profile_log']).exists())
            stdout = json.loads(Path(receipt['stdout_log']).read_text(encoding='utf-8'))
            self.assertEqual(stdout['accepted'], 1)
            stderr = Path(receipt['stderr_log']).read_text(encoding='utf-8')
            self.assertIn('total_tokens=15', stderr)


if __name__ == '__main__':
    unittest.main()
