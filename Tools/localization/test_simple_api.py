"""Short transport, cache reuse and restart safety; isolated projects and mock APIs."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import ainiee_bridge as bridge
import translation_workflow as workflow
import test_translation_workflow as fixtures
from workflow_task_contract import api_id_transport, canonical_api_result


class SimpleProtocolTests(unittest.TestCase):
    def test_local_ids_are_bound_to_session_and_reject_duplicates(self):
        payload = {'task_id': 'task', 'input_hash': 'hash', 'records': [
            {'id': 'R0000100_abcdef'}, {'id': 'R0000300_123456'}]}
        scope = [r['id'] for r in payload['records']]
        transport = api_id_transport(payload, scope, 'session')
        self.assertEqual(transport['short_to_canonical'], {'1': scope[0], '2': scope[1]})
        result = {'records': [{'id': '2', 'translation': '乙'}, {'id': '1', 'translation': '甲'}]}
        restored = canonical_api_result(result, transport, 'session', 'task', 'hash', scope)
        self.assertEqual([r['id'] for r in restored['records']], scope[::-1])
        self.assertEqual(restored['input_hash'], 'hash')
        for bad in (['1', '1'], ['1', '3']):
            with self.assertRaises(ValueError):
                canonical_api_result({'records': [{'id': i} for i in bad]}, transport, 'session', 'task', 'hash', scope)
        with self.assertRaisesRegex(ValueError, 'binding'):
            canonical_api_result(result, transport, 'other', 'task', 'hash', scope)

    def test_short_placeholders_roundtrip_avoid_collision_and_reject_inventions(self):
        source = 'Hello [P1] {name} and {0}.'
        protected, mapping = bridge.protect(source, 'record', [('variables', __import__('re').compile(r'\{[^}]+\}'))])
        self.assertEqual([p['placeholder'] for p in mapping], ['[PP1]', '[PP2]'])
        self.assertEqual(bridge.restore(protected, mapping), source)
        with self.assertRaises(ValueError):
            bridge.restore(protected + ' [PP3]', mapping)
        with self.assertRaises(ValueError):
            bridge.restore(protected.replace('[PP1]', ''), mapping)

    def test_matching_cache_preserves_results_and_option_boundaries(self):
        analysis = {'terms': [{'source': 'Wolf', 'recommended_translation': '狼'}]}
        config = {'case_sensitive': False, 'whole_word': True}
        matcher = bridge._frozen_matcher(bridge.helper())
        with patch.object(matcher, 'build_search_pattern', wraps=matcher.build_search_pattern) as collect:
            first = bridge.matched_terms(analysis, ['wolf!'], config)
            first[0]['dst'] = 'changed by caller'
            second = bridge.matched_terms(analysis, ['wolf!'], config)
            self.assertEqual(second[0]['dst'], '狼')
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(bridge.matched_terms(analysis, ['wolf!'], {**config, 'case_sensitive': True}), [])
            self.assertEqual(collect.call_count, 2)
            changed = copy.deepcopy(analysis)
            changed['terms'][0]['recommended_translation'] = '野狼'
            self.assertEqual(bridge.matched_terms(changed, ['wolf!'], config)[0]['dst'], '野狼')


class SimpleResumeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.f.freeze(True)
        self.flow.pause(True)
        self.flow.configure({'backend': 'api', 'api': {'api_url': 'https://example.invalid/v1',
            'model_name': 'mock', 'concurrency': 1, 'requests_per_minute': 0}}, 'mock transport')
        self.flow.pause(False)

    def reply(self, session):
        data = fixtures.model_input(Path(session['input_file']).read_text(encoding='utf-8'))
        return {'records': [{'id': r['id'], 'translation': r['source']} for r in data['records']]}

    def test_restart_collects_saved_reply_retries_only_missing_and_prepares_once(self):
        saved = self.flow.batch_open('translate', 'saved', backend='api')
        pack = Path(saved['input_file']).read_text(encoding='utf-8')
        self.assertEqual([r['id'] for r in fixtures.model_input(pack)['records']], ['1', '2'])
        self.assertNotIn('⟪SHH_', pack)
        with self.flow.db() as db:
            task = db.execute('SELECT input_hash,ids FROM tasks WHERE id=?', (saved['task_id'],)).fetchone()
            self.assertNotIn(task['input_hash'], pack)
            self.assertTrue(all(i not in pack for i in json.loads(task['ids'])))
        Path(saved['result_file']).with_name('response.raw.txt').write_text(json.dumps(self.reply(saved)), encoding='utf-8')
        unfinished = self.flow.batch_open('translate', 'interrupted', backend='api')
        with self.flow.db() as db:
            db.execute('UPDATE tasks SET lease_until=0 WHERE status="running"')
        restarted = workflow.Workflow(self.f.project)
        sent = []

        class Mock:
            def request(inner, system, user):
                data = fixtures.model_input(user)
                sent.extend(r['source'] for r in data['records'])
                return json.dumps({'records': [{'id': r['id'], 'translation': r['source']} for r in data['records']]}), {'prompt_tokens': 11, 'completion_tokens': 7}
            def close(inner): pass

        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock'}), patch.object(restarted, 'prepare', wraps=restarted.prepare) as prepare:
            result = workflow.run_api(restarted, 'translate', lambda *_: Mock(), resume=True, continuous=False)
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(result['accepted'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['performance']['requests_started'], 1)
        self.assertEqual(result['performance']['run_tokens']['total_tokens'], 18)
        self.assertEqual(result['performance']['project_tokens']['total_tokens'], 18)
        self.assertEqual(result['performance']['timings']['api_request']['count'], 1)
        self.assertEqual(len(sent), 2)
        self.assertEqual(restarted.status()['records'], {'translate': 4})
        # Another new instance must issue no requests after both batches were saved.
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock'}):
            result = workflow.run_api(workflow.Workflow(self.f.project), 'translate', lambda *_: Mock(), resume=True, continuous=False)
        self.assertEqual(len(sent), 2)
        self.assertEqual(result['accepted'], 0)
        self.assertEqual(result['performance']['run_tokens']['total_tokens'], 0)
        self.assertEqual(result['performance']['project_tokens']['total_tokens'], 18)

    def test_profile_keeps_usage_of_malformed_replies_and_retries(self):
        self.check_profile_retry(False)

    def test_profile_keeps_usage_of_empty_api_response_and_retry(self):
        self.check_profile_retry(True)

    def check_profile_retry(self, api_failure):
        sent = []
        class Mock:
            def request(inner, system, user):
                sent.append(user)
                data = fixtures.model_input(user)
                if len(sent) == 1 and api_failure:
                    raise bridge.ApiFailure('empty_response', True, usage={'prompt_tokens': 11, 'completion_tokens': 7})
                reply = 'invalid-json' if len(sent) == 1 else json.dumps({'records': [
                    {'id': r['id'], 'translation': r['source']} for r in data['records']]})
                return reply, {'prompt_tokens': 11, 'completion_tokens': 7}
            def close(inner): pass
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock'}):
            result = workflow.run_api(self.flow, 'translate', lambda *_: Mock(), continuous=False)
        profile = result['performance']
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['accepted'], 2)
        self.assertEqual(profile['run_tokens']['total_tokens'], 54)
        self.assertEqual(profile['project_tokens']['total_tokens'], 54)
        self.assertEqual(profile['requests_started'], 3)
        events = [json.loads(line) for line in Path(profile['profile_log']).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(sum(e['event'] == 'attempt_failed' for e in events), 1)
        self.assertEqual(sum(e['event'] == 'request_finished' for e in events), 3)
        self.assertFalse(any('Synthetic source' in json.dumps(e) or 'Alice sees' in json.dumps(e) for e in events))


if __name__ == '__main__':
    unittest.main()
