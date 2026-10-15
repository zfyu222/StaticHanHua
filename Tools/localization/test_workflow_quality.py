"""Behavioral regression tests for the quality gates, using synthetic local text."""
import csv
import importlib.util
from pathlib import Path
import unittest
import uuid

import test_translation_workflow as fixtures
import translation_workflow as workflow


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.flow = self.fixture.flow
        self.fixture.freeze(True, pilot=False)

    def decisions(self, kind):
        result = self.flow.quality_report(kind)
        report = workflow.read_json(result['report_file'])
        return {'report_hash': report['report_hash'], 'reviewer': 'sol', 'summary': '已核对证据。',
                'decisions': [{'id': key, 'disposition': 'pass', 'reason': '逐项核对。'} for key in report['required_decisions']]}

    def complete(self):
        self.fixture.pilot()
        self.fixture.translate()
        self.fixture.review()

    def review_result(self, claim):
        return {**self.fixture.envelope(claim), 'records': [
            {'id': row['id'], 'translation': row['translation'], 'reviewed': True,
             'reason': '已核对动作及条件。', **self.fixture.review_evidence(row)} for row in claim['payload']['records']]}

    def runtime_report(self):
        payload = self.flow.project / 'patch.dll'; payload.write_bytes(b'synthetic-patch')
        evidence = self.flow.project / 'screen.txt'; evidence.write_text('Synthetic unit-test evidence.', encoding='utf-8')
        report = workflow.read_json(self.flow.root / 'reports/in_game_check.template.json')
        report.update(game_version='synthetic-test-v1', platform='local-unit-test',
                      payload_files=[{'path': payload.name, 'sha256': workflow.digest(payload), 'game_path': 'plugins/patch.dll'}],
                      evidence_files=[{'path': evidence.name, 'sha256': workflow.digest(evidence)}])
        for check in report['checks']:
            check.update(status='pass', scene='Synthetic scene', notes='Synthetic verification')
        return report

    @staticmethod
    def packager():
        script = Path(workflow.__file__).resolve().parents[2] / '.agents/skills/game-release-patch/scripts/create_release_patch.py'
        spec = importlib.util.spec_from_file_location('quality_release_test', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def raw_review(self, stage):
        self.flow.prepare(stage)
        while claim := self.flow.claim(stage, stage + '-' + uuid.uuid4().hex):
            self.flow.submit(claim['task_id'], claim['lease_token'], self.review_result(claim))

    def test_full_translation_waits_for_pilot_acceptance(self):
        with self.assertRaisesRegex(ValueError, 'pilot approval'):
            self.flow.prepare('translate')
        self.fixture.translate('pilot_translate')
        self.raw_review('pilot_review')
        with self.assertRaisesRegex(ValueError, 'pilot approval'):
            self.flow.prepare('translate')
        self.flow.approve_quality('pilot', self.decisions('pilot'))
        self.flow.prepare('translate')
        payload = self.flow.claim('translate', 'full-translation-instance')['payload']
        self.assertTrue(payload['approved_pilot_examples'])
        self.assertEqual(self.flow.status()['records'], {'source': 4})

    def test_producer_cannot_review_own_pilot(self):
        self.fixture.translate('pilot_translate')
        self.flow.prepare('pilot_review')
        with self.assertRaisesRegex(ValueError, 'Independent reviewer|Fresh agent context'):
            self.flow.claim('pilot_review', self.fixture.worker_ids['pilot_translate'][0])
        self.assertIsNotNone(self.flow.claim('pilot_review', 'another-instance'))

    def test_scheduling_revision_retains_partial_pilot_and_only_prepares_remaining(self):
        self.flow.pause(True); self.flow.configure({'pilot_batch_count': 2}, 'Two toy batches'); self.flow.pause(False)
        self.flow.prepare('pilot_translate')
        claim = self.flow.claim('pilot_translate', 'pilot-before-scheduling')
        result = {**self.fixture.envelope(claim), 'records': [
            {'id': row['id'], 'translation': row['source'].replace('Alice sees a Wolf.', '爱丽丝看见一只狼。').replace('The Wolf has', '这只狼拥有').replace('gold.', '金币。')}
            for row in claim['payload']['records']]}
        self.flow.submit(claim['task_id'], claim['lease_token'], result)
        before = self.flow.status()['records']
        self.flow.pause(True)
        revised = self.flow.configure({'batch_records': 1, 'context_records': 0}, 'No adjacent CSV context')
        self.assertFalse(revised['semantic_reset'])
        self.assertEqual(self.flow.status()['records'], before)
        self.flow.pause(False)
        self.flow.prepare('pilot_translate')
        claimed = []
        while next_claim := self.flow.claim('pilot_translate', 'new-' + uuid.uuid4().hex):
            claimed.extend(r['id'] for r in next_claim['payload']['records'])
        self.assertEqual(len(claimed), 2)
        self.assertTrue(set(claimed).isdisjoint(r['id'] for r in claim['payload']['records']))

    def test_semantic_revision_restores_pilot_baseline(self):
        self.fixture.translate('pilot_translate')
        self.flow.pause(True)
        self.flow.configure({'style': 'Changed translation style'}, 'Semantic style revision', impact='all')
        self.assertEqual(self.flow.status()['records'], {'source': 4})

    def test_pilot_scope_revision_restores_pilot_baseline(self):
        self.fixture.translate('pilot_translate')
        self.flow.pause(True)
        self.flow.configure({'pilot_ordinals': [1]}, 'Change representative sample')
        self.assertEqual(self.flow.status()['records'], {'source': 4})

    def test_producer_cannot_review_full_translation(self):
        self.fixture.pilot(); self.fixture.translate()
        self.flow.prepare('review')
        with self.assertRaisesRegex(ValueError, 'Independent reviewer|Fresh agent context'):
            self.flow.claim('review', self.fixture.worker_ids['translate'][0])

    def test_glossary_extractor_cannot_review_its_terms(self):
        # New run so extraction and glossary review have not already completed.
        other = self.flow.project / 'Other'; other.mkdir()
        source = other / 'other_text.csv'; source.write_text('Alice\n', encoding='utf-8')
        flow = workflow.Workflow(other); flow.init(source); flow.prepare('extract')
        claim = flow.claim('extract', 'same-instance')
        result = {**self.fixture.envelope(claim), 'analysis': {'characters': [
            {'source': 'Alice', 'recommended_translation': '爱丽丝', 'gender': '女性', 'evidence_rows': [1]}],
            'terms': [], 'non_translate': []}}
        flow.submit(claim['task_id'], claim['lease_token'], result); flow.prepare('glossary_review')
        with self.assertRaisesRegex(ValueError, 'Independent reviewer'):
            flow.claim('glossary_review', 'same-instance')

    def test_review_requires_dimensions_and_real_source_excerpt(self):
        self.fixture.translate('pilot_translate'); self.flow.prepare('pilot_review')
        claim = self.flow.claim('pilot_review', 'independent')
        result = self.review_result(claim)
        result['records'][0].pop('checks')
        with self.assertRaisesRegex(ValueError, 'Structured review'):
            self.flow.submit(claim['task_id'], claim['lease_token'], result)
        result = self.review_result(claim)
        result['records'][0]['source_excerpt'] = 'invented source'
        with self.assertRaisesRegex(ValueError, 'source_excerpt'):
            self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_correction_requires_before_after_evidence(self):
        self.fixture.translate('pilot_translate'); self.flow.prepare('pilot_review')
        claim = self.flow.claim('pilot_review', 'independent')
        result = self.review_result(claim)
        item = result['records'][0]; before = item['translation']; item['translation'] = before + '！'
        with self.assertRaisesRegex(ValueError, 'correction requires'):
            self.flow.submit(claim['task_id'], claim['lease_token'], result)
        item['checks']['tone']['status'] = 'corrected'
        item['changes'] = [{'category': 'tone', 'severity': 'minor', 'source_excerpt': item['source_excerpt'],
                           'before': before, 'after': item['translation'], 'reason': '修正语气标点。'}]
        self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_pilot_acceptance_rejects_pending_and_incomplete_decisions(self):
        self.fixture.translate('pilot_translate'); self.raw_review('pilot_review')
        decisions = self.decisions('pilot'); decisions['decisions'][0]['disposition'] = 'pending'
        with self.assertRaisesRegex(ValueError, 'Unresolved Sol'):
            self.flow.approve_quality('pilot', decisions)
        decisions = self.decisions('pilot'); decisions['decisions'].pop()
        with self.assertRaisesRegex(ValueError, 'every requested check'):
            self.flow.approve_quality('pilot', decisions)

    def test_export_waits_for_sol_risk_acceptance(self):
        self.fixture.pilot(); self.fixture.translate(); self.raw_review('review')
        with self.assertRaisesRegex(ValueError, 'Sol risk/consistency'):
            self.flow.export()
        report = workflow.read_json(self.flow.quality_report('final')['report_file'])
        flags = {flag for row in report['records_to_check'] for flag in row['risk_reasons']}
        self.assertIn('numbers', flags); self.assertIn('variables', flags)
        self.flow.approve_quality('final', self.decisions('final'))
        self.assertEqual(self.flow.export()['in_game_check'], 'pending_repack')

    def test_reopening_review_invalidates_receipt_and_rejects_stale_report(self):
        self.complete()
        old = self.decisions('final')
        with self.flow.db() as db:
            task = db.execute('SELECT id FROM tasks WHERE stage="review" AND status="done" ORDER BY id').fetchone()['id']
        self.flow.reopen_body(task, '复核口吻。')
        with self.assertRaises(ValueError): self.flow.export()
        claim = self.flow.claim('review', 'new-independent-instance')
        self.flow.submit(claim['task_id'], claim['lease_token'], self.review_result(claim))
        with self.assertRaisesRegex(ValueError, 'current report'):
            self.flow.approve_quality('final', old)
        self.flow.approve_quality('final', self.decisions('final')); self.flow.export()

    def test_runtime_check_needs_current_export_payload_and_evidence(self):
        self.complete(); export = self.flow.export()
        with self.assertRaisesRegex(ValueError, 'in-game'):
            self.flow.release_check()
        payload = self.flow.project / 'patch.dll'; payload.write_bytes(b'synthetic-patch')
        evidence = self.flow.project / 'screen.txt'; evidence.write_text('Synthetic test evidence; not a real game validation.', encoding='utf-8')
        report = workflow.read_json(self.flow.root / 'reports/in_game_check.template.json')
        report.update(game_version='synthetic-test-v1', platform='local-unit-test',
                      payload_files=[{'path': payload.name, 'sha256': workflow.digest(payload), 'game_path': 'plugins/patch.dll'}],
                      evidence_files=[{'path': evidence.name, 'sha256': workflow.digest(evidence)}])
        with self.assertRaisesRegex(ValueError, 'Unverified'):
            self.flow.record_game_check(report)
        for check in report['checks']:
            check.update(status='pass', scene='Synthetic scene', notes='Synthetic verified result')
        self.flow.record_game_check(report); self.assertEqual(self.flow.release_check()['status'], 'passed')
        payload.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.flow.release_check()

    def test_stale_game_check_translation_is_rejected(self):
        self.complete(); self.flow.export()
        report = workflow.read_json(self.flow.root / 'reports/in_game_check.template.json')
        report['translation_sha256'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'current exported'):
            self.flow.record_game_check(report)

    def test_release_rejects_frozen_glossary_file_changed_after_acceptance(self):
        self.complete(); self.flow.export()
        with self.flow.db() as db:
            glossary = self.flow.meta(db, 'glossary')
        (self.flow.root / glossary['path']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Frozen glossary modified'):
            self.flow.release_check()

    def test_pilot_sample_spans_types_and_corpus(self):
        other = self.flow.project / 'Sampling'; other.mkdir()
        source = other / 'sampling_text.csv'
        with source.open('w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows([[f'Line {i}'] for i in range(100)])
        contexts = {str(i): {'text_type': 'dialogue' if i <= 95 else 'ui', 'scene': str(i // 10)} for i in range(1, 101)}
        context = other / 'context.json'; workflow.write_json(context, contexts)
        flow = workflow.Workflow(other); flow.init(source, {'pilot_records': 20}, context)
        with flow.db() as db:
            rows = flow.quality_rows(db); selected = flow.pilot_ids(db, rows, flow.config(db))
            picked = [r for r in rows if r['id'] in selected]
        self.assertEqual(len(picked), 20)
        self.assertEqual({r['metadata']['text_type'] for r in picked}, {'ui', 'dialogue'})
        self.assertGreater(max(r['ordinal'] for r in picked), 95)

    def test_duplicate_source_translation_conflicts_are_reported(self):
        self.complete()
        # Model submits one of two translations for repeated text in different batches.
        # Create a separate synthetic run through the public protocol.
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        fixture.source.write_text('text\ntext\n', encoding='utf-8')
        other = fixture.project / 'Duplicate'; other.mkdir()
        source = other / 'dup_text.csv'; source.write_text('text\ntext\n', encoding='utf-8')
        fixture.flow = workflow.Workflow(other); fixture.flow.init(source, {'batch_records': 1})
        fixture.freeze(); fixture.flow.prepare('translate')
        for translation in ('文本', '文字'):
            claim = fixture.flow.claim('translate', 'translation-instance-' + uuid.uuid4().hex)
            fixture.flow.submit(claim['task_id'], claim['lease_token'], {
                **fixture.envelope(claim), 'records': [{'id': claim['payload']['records'][0]['id'], 'translation': translation}]})
        fixture.review()
        report = workflow.read_json(fixture.flow.quality_report('final')['report_file'])
        self.assertTrue(any(g['kind'] == 'same_source_multiple_translations' for g in report['consistency']))

    def test_packager_rejects_untested_custom_run(self):
        self.complete(); self.flow.export()
        package = self.packager()
        with self.assertRaisesRegex(ValueError, 'in-game'):
            package.check_custom_quality(self.flow.project, [], 'v1', 'Windows')

    def test_packager_binds_destinations_hashes_version_and_platform(self):
        self.complete(); self.flow.export(); self.flow.record_game_check(self.runtime_report())
        package = self.packager()
        entries = [package.parse_include('patch.dll=plugins/patch.dll', self.flow.project)]
        package.check_custom_quality(self.flow.project, entries, 'synthetic-test-v1', 'local-unit-test')
        with self.assertRaisesRegex(ValueError, '版本/平台'):
            package.check_custom_quality(self.flow.project, entries, 'another-version', 'local-unit-test')
        wrong = [package.parse_include('patch.dll=wrong/patch.dll', self.flow.project)]
        with self.assertRaisesRegex(ValueError, '载荷或安装路径'):
            package.check_custom_quality(self.flow.project, wrong, 'synthetic-test-v1', 'local-unit-test')

    def test_packager_configuration_tampering_cannot_disable_gate(self):
        self.complete(); self.flow.export()
        config = workflow.read_json(self.flow.root / 'config/workflow.json'); config['workflow'] = 'ainiee'
        workflow.write_json(self.flow.root / 'config/workflow.json', config)
        with self.assertRaisesRegex(ValueError, 'Configuration changed'):
            self.packager().check_custom_quality(self.flow.project, [], 'v1', 'Windows')

    def test_final_review_repair_after_polish_configuration(self):
        self.complete()
        self.flow.pause(True); self.flow.configure({'polish': True, 'batch_records': 4}, '测试后续更大批次'); self.flow.pause(False)
        self.flow.prepare('polish')
        claim = self.flow.claim('polish', 'polish-instance')
        self.flow.submit(claim['task_id'], claim['lease_token'], {**self.fixture.envelope(claim),
            'records': [{'id': r['id'], 'translation': r['translation']} for r in claim['payload']['records']]})
        self.fixture.review('final_review')
        # Reconfigure produced a new revision; finish one explicit review task for all records
        # through the public repair path of the current final review.
        with self.flow.db() as db:
            task = db.execute('SELECT id FROM tasks WHERE stage="final_review" AND status="done" ORDER BY rowid DESC').fetchone()['id']
        self.flow.reopen_body(task, '重新核对润色后的条件。')
        claim = self.flow.claim('final_review', 'fresh-final-review')
        self.flow.submit(claim['task_id'], claim['lease_token'], self.review_result(claim))
        self.flow.approve_quality('final', self.decisions('final')); self.flow.export()

    def test_repair_keeps_unaffected_evidence_from_obsolete_larger_batch(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        project = fixture.project / 'Regroup'; project.mkdir()
        source = project / 'regroup_text.csv'; source.write_text('text\n' * 4, encoding='utf-8')
        fixture.flow = flow = workflow.Workflow(project)
        flow.init(source, {'polish': True, 'batch_records': 4, 'batch_chars': 35})
        fixture.freeze(); flow.prepare('translate')
        while claim := flow.claim('translate', 'long-translation-instance-' + uuid.uuid4().hex):
            flow.submit(claim['task_id'], claim['lease_token'], {**fixture.envelope(claim),
                'records': [{'id': r['id'], 'translation': '测' * 20} for r in claim['payload']['records']]})
        fixture.review()

        def polish():
            flow.prepare('polish')
            while claim := flow.claim('polish', 'short-polish-instance-' + uuid.uuid4().hex):
                flow.submit(claim['task_id'], claim['lease_token'], {**fixture.envelope(claim),
                    'records': [{'id': r['id'], 'translation': '文本', 'reason': 'Synthetic shortening'} for r in claim['payload']['records']]})

        polish(); fixture.review('final_review')
        with flow.db() as db:
            review = db.execute('SELECT id,ids FROM tasks WHERE stage="review" AND status="done" ORDER BY id').fetchone()
            final = db.execute('SELECT ids FROM tasks WHERE stage="final_review" AND status="done" ORDER BY id').fetchone()
            self.assertEqual(len(workflow.json.loads(review['ids'])), 1)
            self.assertEqual(len(workflow.json.loads(final['ids'])), 4)
        flow.reopen_body(review['id'], '复核一条文本，其他记录保持原译文。')
        fixture.review(); polish(); fixture.review('final_review')
        self.assertEqual(flow.export()['records'], 4)

    def test_legacy_upgrade_restarts_evidence_pipeline(self):
        self.complete()
        with self.flow.db() as db:
            config = self.flow.meta(db, 'config'); config.pop('quality_version')
            self.flow.put(db, 'config', config)
        workflow.write_json(self.flow.root / 'config/workflow.json', config)
        with self.assertRaisesRegex(ValueError, 'Legacy run'):
            self.flow.prepare('translate')
        self.flow.pause(True); result = self.flow.configure({}, '升级质量门槛')
        self.assertTrue(result['semantic_reset']); self.assertFalse(self.flow.status()['glossary_frozen'])
        self.assertEqual(self.flow.status()['records'], {'source': 4})
        self.flow.pause(False); self.assertTrue(self.flow.prepare('extract')['tasks'])

    def test_blank_source_cannot_generate_new_content(self):
        self.fixture.pilot(); self.flow.prepare('translate')
        while claim := self.flow.claim('translate', 'new-translator-' + uuid.uuid4().hex):
            rows = [{'id': r['id'], 'translation': r['source'], 'exceptions': [
                {'source': term['src'], 'reason': 'Synthetic test context exception'} for term in claim['payload']['terms']
                if term['src'] in r['source']]} for r in claim['payload']['records']]
            blank = next((r for r in rows if r['translation'] == ''), None)
            if blank:
                blank['translation'] = 'invented'
                self.flow.submit(claim['task_id'], claim['lease_token'], {**self.fixture.envelope(claim), 'records': rows})
                report = workflow.read_json(self.flow.draft_scan()['report_file'])
                self.assertTrue(any('Blank source' in str(r['diagnostics']) for r in report['failures']))
                return
            self.flow.submit(claim['task_id'], claim['lease_token'], {**self.fixture.envelope(claim), 'records': rows})
        self.fail('No blank source record in fixture')


if __name__ == '__main__':
    unittest.main()
