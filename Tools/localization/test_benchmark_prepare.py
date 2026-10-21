"""Real prepare() on a disposable slice must leave source workflow untouched."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_prepare import benchmark
import test_translation_workflow as fixtures


class PrepareBenchmarkTests(unittest.TestCase):
    def test_slice_has_full_scope_and_original_state_is_unchanged(self):
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.tearDown)
        f.freeze(True)
        f.flow.pause(True)
        f.flow.configure({'backend': 'api'}, 'Isolated benchmark fixture')
        f.flow.pause(False)
        before = f.flow.db_path.read_bytes()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            output = Path(folder) / 'result'
            result = benchmark(f.project, output, 2)
            self.assertEqual(result['full_records'], 4)
            self.assertEqual(result['sample_records'], 2)
            self.assertEqual(result['api_requests'], 0)
            self.assertTrue(result['scope_verified'])
            self.assertGreater(result['timings']['prepare_seconds'], 0)
            self.assertGreater(result['timings']['first_batch_open_seconds'], 0)
            self.assertTrue(Path(result['profile']['profile_log']).is_file())
            self.assertEqual(json.loads((output / 'benchmark.json').read_text(encoding='utf-8'))['sample_records'], 2)
        self.assertEqual(before, f.flow.db_path.read_bytes())


if __name__ == '__main__':
    unittest.main()
