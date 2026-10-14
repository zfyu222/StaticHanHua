"""Cheap calibration invariants. Synthetic data, no external model calls."""
import json
import unittest
import uuid
import shutil
import subprocess
import sys

import test_translation_workflow as fixtures
import translation_workflow as workflow
from workflow_quality import REVIEW_CHECKS


class PilotCostTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.f.freeze(True, pilot=False)

    @staticmethod
    def summary():
        return {key: {'status': 'pass', 'evidence': 'Synthetic check of assigned source and translation.'} for key in REVIEW_CHECKS}

    def compact_claim(self):
        self.f.translate('pilot_translate'); self.flow.prepare('pilot_review')
        claim = self.flow.claim('pilot_review', 'review-' + uuid.uuid4().hex)
        result = {**self.f.envelope(claim), 'review_summary': self.summary(),
                  'records': [{'id': row['id'], 'reviewed': True, 'verdict': 'pass'} for row in claim['payload']['records']]}
        return claim, result

    def test_default_plan_is_one_translator_and_one_independent_reviewer(self):
        plan = self.flow.pilot_plan()
        self.assertEqual(workflow.DEFAULT['pilot_records'], 30)
        self.assertEqual((plan['translation_batches'], plan['review_batches']), (1, 1))
        self.assertEqual(plan['execution_limit'], 6)
        self.assertTrue(plan['within_budget'])

    def test_many_distinct_scenes_do_not_fragment_calibration(self):
        project = self.f.project / 'Scenes'; project.mkdir()
        source = project / 'scene_text.csv'; source.write_text('text\n' * 30, encoding='utf-8')
        context = project / 'context.json'
        workflow.write_json(context, {str(n): {'group': f'scene-{n}', 'scene': f'scene-{n}'} for n in range(1, 31)})
        self.f.flow = flow = workflow.Workflow(project); flow.init(source, context_path=context)
        self.f.freeze(pilot=False)
        tasks = flow.prepare('pilot_translate')['tasks']
        self.assertEqual(len(tasks), 1)
        claim = flow.claim('pilot_translate', 'scene-translator')
        self.assertEqual(len(claim['payload']['records']), 30)
        self.assertEqual(len({r['metadata']['scene'] for r in claim['payload']['records']}), 30)

    def test_oversized_task_count_is_rejected_before_creating_tasks(self):
        self.flow.pause(True); self.flow.configure({'pilot_batch_count': 4}, 'Synthetic over-budget split'); self.flow.pause(False)
        with self.assertRaisesRegex(ValueError, '6-execution budget'):
            self.flow.prepare('pilot_translate')
        with self.flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE stage LIKE "pilot_%"').fetchone()[0], 0)

    def test_retry_and_configuration_do_not_refresh_execution_budget(self):
        self.flow.pause(True); self.flow.configure({'max_attempts': 10}, 'Synthetic retry exercise'); self.flow.pause(False)
        self.f.translate('pilot_translate'); self.flow.prepare('pilot_review')
        for _ in range(5):
            claim = self.flow.claim('pilot_review', 'review-' + uuid.uuid4().hex)
            self.flow.fail(claim['task_id'], claim['lease_token'], 'synthetic_failure', True)
        with self.assertRaisesRegex(ValueError, 'budget exhausted'):
            self.flow.claim('pilot_review', 'extra-reviewer')
        self.flow.pause(True); self.flow.configure({'lease_seconds': 1000}, 'Scheduling only'); self.flow.pause(False)
        self.assertEqual(self.flow.pilot_plan()['executions_used'], 6)
        self.assertFalse(self.flow.pilot_plan()['within_budget'])

    def test_hard_limit_cannot_be_configured_above_six(self):
        with self.assertRaisesRegex(ValueError, 'cannot exceed 6'):
            workflow.merge_config(workflow.DEFAULT, {'pilot_call_limit': 7})

    def test_compact_review_keeps_one_batch_summary_and_all_ids(self):
        claim, result = self.compact_claim()
        self.flow.submit(claim['task_id'], claim['lease_token'], result)
        report = workflow.read_json(self.flow.quality_report('pilot')['report_file'])
        self.assertEqual(len(report['pilot_batch_summaries']), 1)
        self.assertEqual(len(report['records_to_check']), 4)
        self.assertTrue(all('batch_summary' not in r['review'] for r in report['records_to_check']))
        self.f.approve('pilot')
        self.assertEqual(self.flow.status()['records'], {'source': 4})

    def test_compact_summary_cannot_hide_missing_id(self):
        claim, result = self.compact_claim(); result['records'].pop()
        with self.assertRaisesRegex(ValueError, 'assigned ID'):
            self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_project_diagnostic_helper_accepts_compact_pass(self):
        claim, result = self.compact_claim()
        scripts = self.f.project / 'TranslateFiles/scripts'; scripts.mkdir(exist_ok=True)
        helper = scripts / 'single_task.py'
        source = workflow.bridge.ROOT / 'Projects/expeditions-conquistador/TranslateFiles/scripts/single_task.py'
        shutil.copy2(source, helper)
        result_file = self.f.project / 'TranslateFiles/results/compact.json'
        workflow.write_json(result_file, result)
        assignment = scripts / 'assignment.json'
        workflow.write_json(assignment, {'task_file': claim['task_file'], 'result_file': str(result_file)})
        process = subprocess.run([sys.executable, '-X', 'utf8', str(helper), 'check', '--assignment', str(assignment)],
                                 capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['failures'], 0)

    def test_compact_pass_cannot_hide_translation_change(self):
        claim, result = self.compact_claim()
        result['records'][0]['translation'] = claim['payload']['records'][0]['translation'] + '！'
        with self.assertRaisesRegex(ValueError, 'hide corrections'):
            self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_compact_correction_still_requires_original_evidence(self):
        claim, result = self.compact_claim()
        row = claim['payload']['records'][0]; item = result['records'][0]
        item.update(verdict='corrected', translation=row['translation'] + '！', reason='Synthetic punctuation correction',
                    **self.f.review_evidence(row))
        item['checks']['tone']['status'] = 'corrected'
        item['changes'] = [{'category': 'tone', 'severity': 'minor', 'source_excerpt': item['source_excerpt'],
                            'before': row['translation'], 'after': item['translation'], 'reason': item['reason']}]
        self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_reduce_sample_reuses_completed_translation(self):
        project = self.f.project / 'Legacy'; project.mkdir()
        source = project / 'legacy_text.csv'; source.write_text('text\n' * 100, encoding='utf-8')
        self.f.flow = flow = workflow.Workflow(project)
        flow.init(source, {'pilot_records': 100, 'pilot_batch_count': 2})
        self.f.freeze(pilot=False); flow.prepare('pilot_translate')
        claim = flow.claim('pilot_translate', 'first-legacy-batch')
        flow.submit(claim['task_id'], claim['lease_token'], {**self.f.envelope(claim),
            'records': [{'id': r['id'], 'translation': '文本'} for r in claim['payload']['records']]})
        flow.pause(True); result = flow.reduce_pilot(30, 'Adopt cheaper sample while retaining work'); flow.pause(False)
        self.assertEqual(result['reused_translations'], 30)
        plan = flow.pilot_plan()
        self.assertEqual((plan['translation_batches'], plan['review_batches']), (0, 1))
        self.assertEqual(plan['executions_used'], 1)
        self.assertEqual(flow.status()['records'], {'pilot_translate': 30, 'source': 70})
        with flow.db() as db:
            ordinals = flow.config(db)['pilot_ordinals']
        self.assertTrue(all(n <= 50 for n in ordinals))


if __name__ == '__main__':
    unittest.main()
