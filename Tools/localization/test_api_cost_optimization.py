"""Lossless metadata transport and bounded handling of exhausted output budgets."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import ainiee_bridge as bridge
import translation_workflow as workflow
import test_translation_workflow as fixtures
from workflow_task_contract import api_protocol_data, compact_record_metadata, render_protocol_input
from workflow_tokens import count_tokens


class MetadataTests(unittest.TestCase):
    def payload(self, stage='extract'):
        metadata = {'note': '资源位置只用于定位，不保证说话者身份或剧情连续。' * 3,
                    'group': 'conversations/long_group_name_for_context',
                    'speaker': 'Alice', 'future': {'facts': ['one', 'two'] * 5}}
        return {'stage': stage, 'task_id': 'task', 'input_hash': 'hash',
                'records': [{'id': str(i), 'ordinal': i, 'source': f'White. Death. {i} {{0}}',
                             'metadata': {**metadata, 'resource': f'file/{i}#id={i}', 'unique': i},
                             'protection': [{'placeholder': '[P1]', 'original': '{0}'}]}
                            for i in range(1, 21)]}

    def test_all_fields_are_reconstructable_and_original_is_unchanged(self):
        payload = self.payload(); original = copy.deepcopy(payload)
        wire = api_protocol_data(payload)
        self.assertEqual(payload, original)
        for before, row in zip(payload['records'], wire['records']):
            restored = {}
            for ref in row.get('metadata_refs', []):
                restored.update(wire['shared_metadata'][ref])
            restored.update(row.get('metadata', {}))
            self.assertEqual(restored, before['metadata'])
            self.assertEqual(row['source'], before['source'])
            self.assertEqual(row['ordinal'], before['ordinal'])
            self.assertEqual(row['protection'], before['protection'])
        text = render_protocol_input(payload, Path('unused'), api=True)
        self.assertIn('metadata_refs', text)
        raw = copy.deepcopy(wire)
        raw.pop('shared_metadata')
        for row, before in zip(raw['records'], payload['records']):
            row.pop('metadata_refs', None); row['metadata'] = before['metadata']
        self.assertLess(count_tokens(json.dumps(wire, ensure_ascii=False)),
                        count_tokens(json.dumps(raw, ensure_ascii=False)) * .7)

    def test_absent_metadata_never_inherits_other_record_context(self):
        payload = self.payload(); payload['records'][0].pop('metadata')
        payload['records'][1]['metadata'] = {'speaker': 'Bob'}
        wire = api_protocol_data(payload)
        self.assertNotIn('metadata_refs', wire['records'][0])
        self.assertEqual(wire['records'][1]['metadata'], {'speaker': 'Bob'})
        self.assertNotIn('metadata_refs', wire['records'][1])

    def test_reserved_namespace_and_existing_handoffs_are_not_rewritten(self):
        for extra in ({'shared_metadata': {'unknown': 'keep'}}, {}):
            value = self.payload(); value.update(extra)
            if not extra: value['records'][0]['metadata_refs'] = ['future']
            before = copy.deepcopy(value); compact_record_metadata(value)
            self.assertEqual(value, before)

    def test_non_api_payload_and_single_unique_metadata_stay_inline(self):
        payload = self.payload(); before = copy.deepcopy(payload)
        render_protocol_input(payload, Path('unused'), api=False)
        self.assertEqual(payload, before)
        payload['records'] = [payload['records'][0]]
        self.assertNotIn('shared_metadata', api_protocol_data(payload))

    def test_resource_prefixes_reconstruct_exact_addresses(self):
        payload = self.payload()
        for i, row in enumerate(payload['records']):
            row['metadata']['resource'] = 'en/text/conversations/full_directory/full_filename.stringtable#ID=' + str(i)
        wire = api_protocol_data(payload)
        for before, row in zip(payload['records'], wire['records']):
            resource = row['metadata']['resource']
            self.assertEqual(wire['shared_metadata_text'][resource['prefix_ref']] + resource['suffix'],
                             before['metadata']['resource'])
        self.assertEqual(len(wire['shared_metadata_text']), 1)


class ExhaustionTests(unittest.TestCase):
    def test_html_gateway_page_is_not_retried_as_empty_model_response(self):
        from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester
        client = bridge.AINieeAPI.__new__(bridge.AINieeAPI)
        client.settings = {'model_name': 'fixture', 'extra_body': {}}
        client.parser = OpenaiRequester(); client.client = Mock()
        raw = client.client.chat.completions.with_raw_response.create.return_value
        raw.headers = {'content-type': 'text/html; charset=utf-8'}
        with self.assertRaises(bridge.ApiFailure) as error:
            client.request('', 'fixture')
        self.assertEqual(str(error.exception), 'unexpected_html_response')
        self.assertFalse(error.exception.retryable)
        raw.parse.assert_not_called()

    def test_complete_valid_length_response_is_still_collected(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        flow = fixture.flow; flow.pause(True)
        flow.configure({'backend': 'api', 'api': {'api_url': 'https://example.invalid',
                       'model_name': 'fixture', 'concurrency': 1}}, 'Complete response fixture', impact='none')
        flow.pause(False)
        class Client:
            def request(self, system, user):
                return json.dumps({'analysis': {'characters': [], 'terms': [], 'non_translate': []}}), {
                    'prompt_tokens': 10, 'completion_tokens': 20, 'usage_reported': True, 'finish_reason': 'length'}
            def close(self): pass
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'fixture'}):
            result = workflow.run_api(flow, 'extract', lambda *_: Client())
        self.assertEqual(result['accepted'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertFalse(result['needs_attention'])
        self.assertFalse(result['pending_handoffs'])
        with flow.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE stage="extract" AND status!="done"').fetchone()[0], 0)

    def test_thinking_parameters_are_preserved_and_reasoning_only_exhaustion_stops(self):
        from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester
        client = bridge.AINieeAPI.__new__(bridge.AINieeAPI)
        client.settings = {'model_name': 'deepseek-v4.1-flash', 'target_platform': 'deepseek',
                           'think_switch': True, 'think_depth': 'high', 'extra_body': {}}
        client.parser = OpenaiRequester(); client.client = Mock()
        response = NS(choices=[NS(message=NS(content=''), finish_reason='length')],
                      usage=NS(prompt_tokens=10, completion_tokens=16384, total_tokens=16394,
                               completion_tokens_details={'reasoning_tokens': 16384}), model='fixture')
        client.client.chat.completions.with_raw_response.create.return_value.parse.return_value = response
        with self.assertRaises(bridge.ApiFailure) as error:
            client.request('', 'fixture')
        self.assertFalse(error.exception.retryable)
        self.assertEqual(str(error.exception), 'reasoning_budget_exhausted')
        self.assertEqual(error.exception.usage['total_tokens'], 16394)
        sent = client.client.chat.completions.with_raw_response.create.call_args.kwargs
        self.assertEqual(sent['extra_body']['thinking'], {'type': 'enabled'})

    def test_invalid_length_reply_is_not_retried_and_no_partial_is_accepted(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        flow = fixture.flow; flow.pause(True)
        flow.configure({'backend': 'api', 'api': {'api_url': 'https://example.invalid',
                       'model_name': 'fixture', 'concurrency': 1}}, 'Exhaustion fixture', impact='none')
        flow.pause(False); calls = []
        class Client:
            def request(self, system, user):
                calls.append(user)
                return '{"analysis":', {'prompt_tokens': 10, 'completion_tokens': 20,
                    'total_tokens': 30, 'usage_reported': True, 'finish_reason': 'length'}
            def close(self): pass
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'fixture'}):
            result = workflow.run_api(flow, 'extract', lambda *_: Client())
        self.assertEqual(len(calls), 1)
        self.assertTrue(result['paused'])
        self.assertEqual(result['accepted'], 0)
        with flow.db() as db:
            call = db.execute('SELECT status,usage FROM api_calls WHERE status!="cancelled"').fetchone()
            self.assertEqual(call['status'], 'rejected_length_terminated_response')
            self.assertEqual(json.loads(call['usage'])['total_tokens'], 30)


if __name__ == '__main__': unittest.main()
