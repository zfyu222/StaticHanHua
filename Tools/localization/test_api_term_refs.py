"""Session-bound short term references restore the shared exception contract."""
import copy
import json
from pathlib import Path
import unittest

from workflow_task_contract import api_id_transport, canonical_api_result
import test_translation_workflow as fixtures


class TermRefContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'task_id': 'task', 'input_hash': 'hash',
                        'records': [{'id': 'r1'}, {'id': 'r2'}]}
        self.scope = ['r1', 'r2']
        self.transport = api_id_transport(self.payload, self.scope, 'session')
        self.transport['record_terms'] = {'r1': {'t1': 'rest'}, 'r2': {'t2': 'Don'}}

    def decode(self, records, transport=None):
        return canonical_api_result({'records': records}, transport or self.transport,
                                    'session', 'task', 'hash', self.scope)

    def test_restore_sources_reasons_and_explicit_removal(self):
        result = self.decode([{'id': '1', 'translation': '保留现译',
            'exceptions': [{'term_ref': 't1', 'reason': ' rest 是放置动作。 '}]}])
        self.assertEqual(result['records'][0]['exceptions'], [{'source': 'rest', 'reason': 'rest 是放置动作。'}])
        result = self.decode([{'id': '1', 'exception_changes': [
            {'term_ref': 't1', 'action': 'remove', 'reason': '原句实际是技能名。'}]}])
        self.assertEqual(result['records'][0]['exception_changes'], [
            {'source': 'rest', 'action': 'remove', 'reason': '原句实际是技能名。'}])
        self.assertNotIn('term_ref', json.dumps(result))

    def test_unknown_cross_record_and_duplicate_references_are_rejected(self):
        for entries in ([{'term_ref': 't2', 'reason': '同批另一条的术语不能引用。'}],
                        [{'term_ref': 't99', 'reason': '未知编号。'}],
                        [{'term_ref': 't1', 'reason': '理由。'}] * 2):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.decode([{'id': '1', 'exceptions': entries}])

    def test_empty_nontext_reasons_and_conflicting_source_are_rejected(self):
        for reason in ('', ' ', None, 3, {}):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, 'reason'):
                self.decode([{'id': '1', 'exceptions': [{'term_ref': 't1', 'reason': reason}]}])
        with self.assertRaisesRegex(ValueError, 'also supply source'):
            self.decode([{'id': '1', 'exceptions': [{'term_ref': 't1', 'source': 'Don', 'reason': '不许混用。'}]}])

    def test_map_scope_and_legacy_result_contract(self):
        broken = copy.deepcopy(self.transport)
        broken['record_terms'].pop('r2')
        with self.assertRaisesRegex(ValueError, 'reference map'):
            self.decode([{'id': '1'}], broken)
        old = api_id_transport(self.payload, self.scope, 'session')
        result = self.decode([{'id': '1', 'exceptions': [{'source': 'rest', 'reason': '既有完整协议。'}]}], old)
        self.assertEqual(result['records'][0]['exceptions'][0]['source'], 'rest')
        with self.assertRaisesRegex(ValueError, 'term reference'):
            self.decode([{'id': '1', 'exceptions': [{'term_ref': 't1', 'reason': '没有绑定映射。'}]}], old)


class RenderedTermRefTests(unittest.TestCase):
    def test_persisted_map_matches_rendered_terms_and_record_membership(self):
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.tearDown)
        f.freeze(True)
        f.flow.pause(True)
        f.flow.configure({'backend': 'api', 'api': {'api_url': 'https://example.invalid/v1', 'model_name': 'mock'}}, 'Term map fixture')
        f.flow.pause(False)
        session = f.flow.batch_open('translate', 'term-packet', backend='api')
        text = Path(session['input_file']).read_text(encoding='utf-8')
        transport = json.loads(Path(session['input_file']).with_name('api_id_transport.v2.json').read_text(encoding='utf-8'))
        data = fixtures.model_input(text, include_term_map=True)
        wire = [json.loads(line) for line in text.split('```jsonl\n', 1)[1].split('\n```', 1)[0].splitlines()]
        for row in wire:
            canonical = transport['short_to_canonical'][row['id']]
            expected = {ref: data['shared_terms'][ref]['src'] for ref in row.get('term_refs', [])}
            self.assertEqual(transport['record_terms'][canonical], expected)
        self.assertIn('exceptions=[{term_ref:', text)
        self.assertNotIn('exceptions=[{source:', text)
        self.assertEqual(set(transport['record_terms']), set(transport['short_to_canonical'].values()))


if __name__ == '__main__':
    unittest.main()
