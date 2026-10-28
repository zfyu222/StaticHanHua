"""Real stage/scan/repair integration with mock transports, never paid APIs."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

import ainiee_bridge as bridge
import translation_workflow as workflow
import test_translation_workflow as fixtures


class APICycleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.f.freeze(True, pilot=self._testMethodName != 'test_pilot_stops_for_main_calibration')

    def api(self, **patches):
        self.flow.pause(True)
        self.flow.configure({'backend': 'api', 'api': {'api_url': 'https://example.invalid/v1',
            'model_name': 'mock', 'max_completion_tokens': 16384, 'max_requests': 100,
            'concurrency': 1, 'requests_per_minute': 0}, **patches}, 'API cycle fixture')
        self.flow.pause(False)

    @staticmethod
    def translations(payload):
        return {'records': [{'id': r['id'], 'translation': r['source']
            .replace('Alice sees a Wolf.', '爱丽丝看见一只狼。').replace('The Wolf has', '这只狼拥有')
            .replace('gold.', '金币。').replace('text', '文本')} for r in payload['records']]}

    def run_model(self, stage, reply, **options):
        state = dict(calls=[], factories=0, closes=0)
        glossary_ids = set()
        original_match = bridge.matched_terms
        def matched(analysis, *args, **kwargs):
            glossary_ids.add(id(analysis))
            return original_match(analysis, *args, **kwargs)
        class Client:
            def request(inner, system, user):
                payload = fixtures.model_input(user, include_term_map=True)
                state['calls'].append(payload)
                return json.dumps(reply(payload, len(state['calls'])), ensure_ascii=False), {'prompt_tokens': 10, 'completion_tokens': 5}
            def close(inner):
                state['closes'] += 1
        def factory(*_):
            state['factories'] += 1
            return Client()
        stdout = io.StringIO()
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock-only'}), \
                patch.object(bridge, 'matched_terms', side_effect=matched), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            summary = workflow.run_api(self.flow, stage, factory, **options)
        state['glossary_objects'] = len(glossary_ids)
        state['stdout'] = stdout.getvalue()
        return summary, state

    def test_translate_returns_before_check_then_separate_check_repairs(self):
        self.api()
        def reply(payload, number):
            result = self.translations(payload)
            if number == 1:
                result['records'][0]['translation'] = ''
            return result
        summary, state = self.run_model('translate', reply)
        self.assertFalse(summary['stage_clean'])
        self.assertTrue(summary['drafts_complete'])
        self.assertEqual(summary['next_action'], 'draft_check')
        self.assertEqual(summary['needs_attention'], [])
        self.assertEqual(len(summary['api_rounds']), 1)
        self.assertEqual([len(p['records']) for p in state['calls']], [2, 2])
        self.assertNotIn('draft_scan', summary['performance']['timings'])
        self.assertFalse((self.flow.root / 'reports/full_draft_scan.json').exists())
        self.assertEqual(summary['performance']['run_tokens']['total_tokens'], 30)
        checked, state = self.run_model('draft_check', lambda p, n: self.translations(p))
        self.assertTrue(checked['stage_clean'])
        self.assertEqual([len(p['records']) for p in state['calls']], [1])
        self.assertEqual(state['factories'], 1)
        self.assertEqual(state['closes'], 1)
        self.assertEqual(state['glossary_objects'], 1)
        self.assertEqual(state['stdout'], '')
        self.assertEqual(checked['accepted'], 1)
        self.assertEqual(checked['performance']['run_tokens']['total_tokens'], 15)
        self.assertEqual(checked['performance']['project_tokens']['total_tokens'], 45)
        self.assertNotEqual(summary['performance']['profile_log'], checked['performance']['profile_log'])
        self.assertEqual(checked['performance']['stage'], 'draft_check')
        self.assertEqual(checked['performance']['timings']['draft_scan']['count'], 2)
        self.assertGreater(checked['performance']['counters'].get('term_cache_hits', 0), 0)

    def check_review(self, stage):
        if stage == 'final_review':
            self.flow.pause(True); self.flow.configure({'polish': True}, 'Enable final-review fixture'); self.flow.pause(False)
        self.f.translate()
        self.flow.draft_scan()
        if stage == 'final_review':
            self.f.review()
            self.f.translate('polish')
        self.api()
        def reply(payload, number):
            result = {'records': [{'id': r['id'], 'translation': r['translation'], 'reviewed': True,
                'reason': '逐条对照原文。', **self.f.review_evidence(r)} for r in payload['records']]}
            if number == 1:
                result['records'][0]['issues'] = [{'severity':'blocking','reason':'本轮尚未解决的具体语义问题。'}]
            return result
        summary, state = self.run_model(stage, reply)
        self.assertTrue(summary['stage_clean'])
        self.assertEqual(summary['next_action'], 'stage_complete')
        self.assertEqual(len(summary['api_rounds']), 2)
        self.assertEqual([len(p['records']) for p in state['calls']], [2, 2, 1])
        self.assertEqual(state['glossary_objects'], 1)
        self.assertEqual(state['factories'], 1)
        self.assertEqual(state['closes'], 1)
        self.assertEqual(summary['performance']['timings']['review_scan']['count'], 2)
        self.assertEqual(state['stdout'], '')

    def test_independent_review_repairs_only_failed_candidate(self):
        self.check_review('review')

    def test_bad_term_reference_repairs_only_the_bad_record(self):
        self.api()
        def reply(payload, number):
            result = self.translations(payload)
            if number == 1:
                result['records'][0]['exceptions'] = [{'term_ref': 't999', 'reason': '无效编号测试。'}]
            return result
        summary, state = self.run_model('translate', reply)
        self.assertTrue(summary['drafts_complete'])
        self.assertEqual(summary['failed'], 0)  # Row diagnostic, not a failed network attempt.
        self.assertEqual(len(summary['api_rounds']), 1)
        self.assertEqual(len(state['calls']), 3)
        self.assertEqual([r['source'] for r in state['calls'][1]['records']],
                         [state['calls'][0]['records'][0]['source']])
        self.assertNotEqual([r['source'] for r in state['calls'][1]['records']],
                            [r['source'] for r in state['calls'][2]['records']])

    def test_reasoned_terminology_acceptance_survives_restart_review_and_export(self):
        self.api()
        def reply(payload, number):
            result = self.translations(payload)
            for original, item in zip(payload['records'], result['records']):
                # Synthetic model decision: keep the existing rendering, explain
                # the disputed name instead of rewriting the sentence repeatedly.
                item['translation'] = item['translation'].replace('爱丽丝', '她')
                findings = original.get('terminology_findings', [])
                if findings:
                    self.assertEqual(len(findings), 1)
                    self.assertEqual(payload['shared_terms'][findings[0]['term_ref']]['src'], 'Alice')
                    item['exceptions'] = [{'term_ref': finding['term_ref'],
                        'reason': '本句 Alice sees 指同一主体，测试语境中以“她”指代 Alice，保留现译。'}
                        for finding in findings]
            return result
        self.run_model('translate', reply)
        summary, state = self.run_model('draft_check', reply)
        self.assertTrue(summary['stage_clean'])
        self.assertEqual(len(summary['api_rounds']), 1)
        self.assertEqual([len(p['records']) for p in state['calls']], [1])
        self.flow = self.f.flow = workflow.Workflow(self.f.project)
        self.assertTrue(self.flow.draft_scan(prepare_repairs=True)['clean'])
        with self.flow.db() as db:
            first = dict(db.execute('SELECT * FROM records ORDER BY ordinal LIMIT 1').fetchone())
            self.assertIn('她', first['translation'])
            decision = json.loads(first['exceptions'])
            self.assertEqual(decision[0]['source'], 'Alice')
            # A real full recheck, rather than just reading cached clean receipts.
            self.assertEqual(self.flow.collect_draft_issues(db, self.flow.config(db))[1], [])
        def review(payload, number):
            self.assertTrue(all(not r.get('terminology_findings') for r in payload['records']))
            return {'records': [{'id': r['id'], 'translation': r['translation'], 'reviewed': True,
                'reason': '独立对照原文及已有术语结论。', 'exceptions': [],
                **self.f.review_evidence(r)} for r in payload['records']]}
        reviewed, review_state = self.run_model('review', review)
        self.assertTrue(reviewed['stage_clean'])
        self.assertEqual(len(reviewed['api_rounds']), 1)
        self.f.approve('final')
        self.assertEqual(self.flow.export()['status'], 'passed')
        with self.flow.db() as db:
            saved = db.execute('SELECT exceptions FROM records WHERE id=?', (first['id'],)).fetchone()[0]
            self.assertEqual(json.loads(saved), decision)

    def test_final_review_runs_same_cycle_with_final_stage_gate(self):
        self.check_review('final_review')

    def test_stops_at_standard_escalation_without_sending_standard_to_api(self):
        self.api(body_repair_round_limit=1)
        def reply(payload, number):
            result = self.translations(payload)
            for row, item in zip(payload['records'], result['records']):
                if 'Alice sees' in row['source']:
                    item['translation'] = ''
            return result
        self.run_model('translate', reply)
        summary, state = self.run_model('draft_check', reply)
        self.assertFalse(summary['stage_clean'])
        self.assertEqual(summary['next_action'], 'external_executor_required')
        self.assertEqual(len(state['calls']), 1)
        self.assertTrue(all(task['backend'] == 'subagent' for task in summary['needs_attention'][0]['repair_dispatch']))
        self.assertEqual(state['closes'], 1)

    def test_pilot_stops_for_main_calibration(self):
        self.api()
        summary, state = self.run_model('pilot_translate', lambda p, n: self.translations(p))
        self.assertEqual(summary['next_action'], 'pilot-decide')
        self.assertEqual(len(summary['api_rounds']), 1)
        self.assertEqual(len(state['calls']), 2)
        with self.flow.db() as db:
            self.assertNotIn('pilot', self.flow.meta(db, 'quality_approvals', {}))

    def test_request_budget_pauses_before_any_quality_scan(self):
        self.api()
        self.flow.pause(True); self.flow.configure({'api': {'max_requests': 1}}, 'Bound requests'); self.flow.pause(False)
        summary, state = self.run_model('translate', lambda p, n: self.translations(p))
        self.assertTrue(summary['paused'])
        self.assertEqual(summary['next_action'], 'resolve_execution')
        self.assertEqual(len(state['calls']), 1)
        self.assertNotIn('draft_scan', summary['performance']['timings'])
        self.assertFalse(summary['stage_clean'])

    def test_clean_draft_check_makes_no_api_request(self):
        self.api()
        self.run_model('translate', lambda p, n: self.translations(p))
        summary, state = self.run_model('draft_check', lambda *_: self.fail('Clean drafts need no API'))
        self.assertTrue(summary['stage_clean'])
        self.assertEqual(state['factories'], 0)
        self.assertEqual(state['calls'], [])
        self.assertEqual(summary['performance']['run_tokens']['total_tokens'], 0)
        self.assertEqual(summary['performance']['project_tokens']['total_tokens'], 30)

    def test_draft_check_rejects_incomplete_translation_before_api(self):
        self.api()
        with self.assertRaisesRegex(ValueError, 'Complete all production drafts'):
            self.run_model('draft_check', lambda *_: self.fail('Must finish translation first'))

    def test_draft_check_resumes_saved_repair_without_retranslation(self):
        self.api()
        def initial(p, n):
            result = self.translations(p)
            if n == 1: result['records'][0]['translation'] = ''
            return result
        self.run_model('translate', initial)
        self.flow.draft_scan(prepare_repairs=True)
        session = self.flow.batch_open('translate', 'saved-repair', backend='api')
        payload = fixtures.model_input(Path(session['input_file']).read_text(encoding='utf-8'))
        Path(session['result_file']).with_name('response.raw.txt').write_text(
            json.dumps(self.translations(payload)), encoding='utf-8')
        self.flow = self.f.flow = workflow.Workflow(self.f.project)
        summary, state = self.run_model('draft_check', lambda *_: self.fail('Use saved reply'), resume=True)
        self.assertTrue(summary['stage_clean'])
        self.assertEqual(summary['recovered_batches'], 1)
        self.assertEqual(state['factories'], 0)

    def test_draft_check_finishes_prepared_round_and_preserves_pause(self):
        self.api()
        def initial(p, n):
            result = self.translations(p)
            if n == 1: result['records'][0]['translation'] = ''
            return result
        self.run_model('translate', initial)
        self.flow.draft_scan(prepare_repairs=True)
        self.flow.pause(True)
        paused, state = self.run_model('draft_check', lambda *_: self.fail('Paused'))
        self.assertTrue(paused['paused'])
        self.assertEqual(state['factories'], 0)
        self.flow.pause(False)
        summary, state = self.run_model('draft_check', lambda p, n: self.translations(p))
        self.assertTrue(summary['stage_clean'])
        self.assertEqual(len(state['calls']), 1)

    def test_draft_check_keeps_multiple_repair_rounds_in_one_client(self):
        self.api()
        def incomplete(p, n):
            result = self.translations(p)
            result['records'][0]['translation'] = ''
            return result
        self.run_model('translate', incomplete)
        def repair(p, n):
            result = self.translations(p)
            if n == 1: result['records'][0]['translation'] = ''
            return result
        summary, state = self.run_model('draft_check', repair)
        self.assertTrue(summary['stage_clean'])
        self.assertEqual(len(summary['api_rounds']), 2)
        self.assertEqual(state['factories'], 1)
        self.assertEqual(state['closes'], 1)
        self.assertEqual(state['glossary_objects'], 1)
        self.assertEqual(summary['performance']['timings']['draft_scan']['count'], 3)

    def test_draft_check_does_not_offer_an_unchecked_single_round(self):
        with self.assertRaisesRegex(ValueError, 'draft_check requires continuous'):
            self.run_model('draft_check', lambda *_: self.fail('No API'), continuous=False)


if __name__ == '__main__':
    unittest.main()
