"""Calibration observes problems without creating a second production review."""
import json
import io
import sys
from contextlib import redirect_stdout
import unittest
from pathlib import Path
from unittest.mock import patch

import ainiee_bridge as bridge
import test_translation_workflow as fixtures
import translation_workflow as workflow


class PilotCalibrationTests(unittest.TestCase):
    def setUp(self):
        provenance = patch.object(bridge, 'provenance', return_value=bridge.provenance())
        provenance.start(); self.addCleanup(provenance.stop)
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.f.freeze(True, pilot=False)

    def decision(self, decision='accept', **extra):
        report = self.flow.pilot_report()
        return {'report_hash': report['report_hash'], 'reviewer': 'main', 'decision': decision,
                'summary': 'Synthetic assessment of pipeline, style and recurring errors.', **extra}

    def broken_pilot(self):
        original = self.flow.submit
        targets = []
        def submit(task, token, result):
            if not targets:
                targets.append(result['records'][0]['id'])
                result['records'][0]['translation'] = ''
            return original(task, token, result)
        with patch.object(self.flow, 'submit', side_effect=submit):
            self.f.translate('pilot_translate')
        return targets[0]

    def test_report_keeps_raw_errors_and_never_prepares_repairs_or_review(self):
        target = self.broken_pilot()
        before = self.flow.status()['tasks']
        receipt = self.flow.draft_scan(True, stage='pilot_translate')
        self.assertEqual(receipt['status'], 'awaiting_main')
        self.assertEqual(receipt['statistics']['mechanical_failed_records'], 1)
        self.assertEqual(receipt['repair_tasks'], [])
        self.assertEqual(before, self.flow.status()['tasks'])
        report = workflow.read_json(receipt['report_file'])
        row = next(r for r in report['records'] if r['id'] == target)
        self.assertEqual(row['translation'], '')
        self.assertTrue(row['diagnostics'])
        self.assertTrue(row['producer']['instance'])
        self.assertNotIn('required_decisions', report)
        self.assertFalse(list(self.flow.root.glob('reports/pilot_inspections/*')))

    def test_main_can_accept_isolated_errors_and_production_gates_still_apply(self):
        self.broken_pilot()
        with self.assertRaisesRegex(ValueError, 'pilot approval'):
            self.flow.prepare('translate')
        self.assertEqual(self.flow.pilot_decide(self.decision())['status'], 'approved')
        self.assertEqual(self.flow.status()['records'], {'source': 4})
        self.assertEqual(self.flow.status()['quality']['pilot']['reviewer'], 'main')
        self.flow.prepare('translate')
        claim = self.flow.claim('translate', 'production')
        self.assertTrue(claim['payload']['approved_pilot_examples'])
        self.assertTrue(all(r['translation'].strip() for r in claim['payload']['approved_pilot_examples']))
        self.assertNotIn('pilot_style_notes', claim['payload'])  # Overall diagnostics are not translation style.
        with self.assertRaises(ValueError):
            self.flow.export()  # Calibration approval is never a final text approval.

    def test_incomplete_run_cannot_be_accepted(self):
        self.flow.prepare('pilot_translate')
        receipt = self.flow.pilot_report()
        self.assertEqual(receipt['status'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'Complete all pilot'):
            self.flow.pilot_decide(self.decision())

    def test_retired_review_is_rejected_before_credentials_or_requester(self):
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.flow.prepare('pilot_review')
        with patch.object(workflow, 'load_api_settings', side_effect=AssertionError('Must not load keys')):
            with self.assertRaisesRegex(ValueError, 'retired'):
                workflow.run_api(self.flow, 'pilot_review', lambda *_: self.fail('No review request'))

    def test_targeted_retry_preserves_sample_other_outputs_and_old_artifacts(self):
        target = self.broken_pilot()
        decision = self.decision('retry', retry_ids=[target])
        with self.flow.db() as db:
            selected = self.flow.meta(db, 'pilot_selection')
            old_rows = {r['id']: r['translation'] for r in self.flow.quality_rows(db)}
            old_results = [t['result'] for t in db.execute('SELECT result FROM tasks WHERE stage="pilot_translate"')]
        result = self.flow.pilot_decide(decision)
        self.assertEqual(result['retry_records'], 1)
        self.f.translate('pilot_translate')
        with self.flow.db() as db:
            self.assertEqual(self.flow.meta(db, 'pilot_selection'), selected)
            for row in self.flow.quality_rows(db):
                if row['id'] != target:
                    self.assertEqual(row['translation'], old_rows[row['id']])
            results = [t['result'] for t in db.execute('SELECT result FROM tasks WHERE stage="pilot_translate"')]
            self.assertTrue(all(r in results for r in old_results))
            authors = db.execute('SELECT DISTINCT instance FROM quality_authors WHERE record_id=? '
                                 'AND stage="pilot_translate"', (target,)).fetchall()
            self.assertEqual(len(authors), 2)
        self.assertTrue(Path(result['assessment_file']).exists())
        self.assertGreaterEqual(len(list(self.flow.root.glob('reports/pilot_rounds/*.json'))), 1)

    def test_old_report_cannot_approve_after_parameters_change(self):
        self.f.translate('pilot_translate')
        decision = self.decision()
        self.flow.pause(True)
        self.flow.configure({'api': {'temperature': 0.5}}, 'Calibrate sampling temperature')
        self.flow.pause(False)
        with self.assertRaisesRegex(ValueError, 'Stale pilot'):
            self.flow.pilot_decide(decision)
        self.flow.pilot_decide(self.decision())

    def test_no_per_record_proof_or_standard_identity_is_required_for_main_assessment(self):
        self.f.translate('pilot_translate')
        decision = self.decision()
        self.assertNotIn('decisions', decision)
        self.flow.pilot_decide(decision)
        with self.flow.db() as db:
            approval = self.flow.meta(db, 'quality_approvals')['pilot']
        archive = self.flow.root / f'reports/accepted/pilot_{approval["report_hash"][:16]}'
        self.assertTrue((archive / 'main_assessment.json').exists())
        self.assertFalse((archive / 'standard_decisions.json').exists())

    def test_retry_requires_explicit_valid_scope_and_stop_pauses(self):
        self.f.translate('pilot_translate')
        with self.assertRaisesRegex(ValueError, 'subset'):
            self.flow.pilot_retry(['unknown'], 'Bad scope')
        self.assertEqual(self.flow.pilot_decide(self.decision('stop'))['status'], 'stopped')
        self.assertTrue(self.flow.status()['paused'])

    def test_cli_report_decision_and_explicit_retry_share_persistent_state(self):
        self.f.translate('pilot_translate')
        def command(*args):
            output = io.StringIO()
            with patch.object(sys, 'argv', ['translation_workflow.py', '--project', str(self.f.project), *args]), redirect_stdout(output):
                self.assertEqual(workflow.main(), 0)
            return json.loads(output.getvalue())
        report = command('pilot-report')
        target = workflow.read_json(report['report_file'])['records'][0]['id']
        retried = command('pilot-retry', '--ids', target, '--reason', 'Compare a calibrated setting')
        self.assertEqual(retried['retry_records'], 1)
        self.assertTrue(Path(retried['assessment_file']).exists())
        self.f.translate('pilot_translate')
        report = command('pilot-report')
        path = self.flow.root / 'reports/main_decision.json'
        workflow.write_json(path, {'report_hash': report['report_hash'], 'reviewer': 'main',
                                  'decision': 'accept', 'summary': 'Synthetic CLI calibration is suitable.'})
        self.assertEqual(command('pilot-decide', '--file', str(path))['status'], 'approved')

    def test_style_impact_reopens_saved_calibration_without_old_review_provenance(self):
        self.f.pilot()
        with self.flow.db() as db:
            target = self.flow.quality_rows(db)[0]['id']
        self.flow.pause(True)
        self.flow.configure({'style': 'Synthetic scoped clarification'}, 'One sample affected',
                            impact='ids', record_ids=[target])
        self.flow.pause(False)
        self.assertEqual(self.flow.pilot_report()['status'], 'awaiting_main')
        self.flow.pilot_retry([target], 'Compare the scoped change')
        self.f.translate('pilot_translate')
        self.flow.pilot_decide(self.decision())

    def test_old_accepted_pilot_remains_valid_without_restarting_review(self):
        self.f.translate('pilot_translate')
        with self.flow.db() as db:
            config = self.flow.config(db)
            rows = self.flow.quality_rows(db)
            # Minimal historical acceptance fixture; no live approval is fabricated.
            legacy = {'schema': 2, 'kind': 'pilot', 'pilot_key': self.flow.pilot_key(db, config),
                      'records_to_check': rows, 'snapshot_hash': workflow.fingerprint(rows)}
            legacy['report_hash'] = workflow.fingerprint(legacy)
            receipt = {'report_hash': legacy['report_hash'], 'pilot_key': legacy['pilot_key'],
                       'reviewer': 'standard', 'decisions': {'summary': 'Synthetic historical style guidance'}}
            workflow.write_json(self.flow.root / f'reports/accepted/pilot_{legacy["report_hash"][:16]}/report.json', legacy)
            self.flow.put(db, 'quality_approvals', {'pilot': receipt})
            self.flow.restore_pilot_baseline(db)
        self.assertEqual(self.flow.pilot_report()['status'], 'already_approved')
        self.flow.prepare('translate')
        claim = self.flow.claim('translate', 'legacy-production')
        self.assertEqual(claim['payload']['pilot_style_notes'], 'Synthetic historical style guidance')
        self.assertFalse(any(t['stage'] == 'pilot_review' for t in self.flow.status()['tasks']))


if __name__ == '__main__':
    unittest.main()
