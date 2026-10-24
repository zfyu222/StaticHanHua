"""API presentation stays lossless while shared result validation stays strict."""
import copy
import json
import unittest

from translation_workflow import prompt
from workflow_task_contract import (api_output_template, api_protocol_data,
                                    api_template_instruction, output_template)
from workflow_tokens import count_tokens


class APICompactionTests(unittest.TestCase):
    def payload(self, stage):
        return {'stage': stage, 'task_id': 'qa', 'input_hash': 'a' * 64,
                'source_hash': 'b' * 64, 'config_hash': 'c' * 64,
                'revision': 7, 'protocol': 1, 'worker': 'local-worker',
                'lease_token': 'private-local-lease', 'attempt': 2,
                'language': {'source': 'English', 'target': '简体中文'},
                'context': [], 'terms': [], 'glossary_version': None,
                'records': [{'id': f'r{i}', 'ordinal': i, 'source': 'Gate {0}',
                             'translation': None, 'metadata': {}, 'locked': False,
                             'protection': [], 'exceptions': []} for i in range(1, 41)]}

    def test_extraction_keeps_complete_sources_ordinals_and_context(self):
        payload = self.payload('extract')
        payload['records'][0].update(metadata={'speaker': 'Guard'}, source='Gate\n"{0}"')
        payload['records'][1]['source'] = ''
        payload['new_semantic_field'] = {'meaning': 'keep this future field'}
        before = copy.deepcopy(payload)
        data = api_protocol_data(payload)
        self.assertEqual(payload, before)
        self.assertEqual(data['records'][0]['metadata'], {'speaker': 'Guard'})
        self.assertEqual([r['source'] for r in data['records']], [r['source'] for r in payload['records']])
        self.assertEqual([r['ordinal'] for r in data['records']], list(range(1, 41)))
        self.assertEqual(data['new_semantic_field'], payload['new_semantic_field'])
        for key in ('config_hash', 'revision', 'protocol', 'worker', 'lease_token', 'attempt', 'context'):
            self.assertNotIn(key, data)
        instructions, _ = prompt(payload, api=True)
        for text in ('exception_changes', 'repair_constraints', 'issues 条目', '锁定译文'):
            self.assertNotIn(text, instructions)

    def test_glossary_examples_do_not_drop_candidates_conflicts_or_sources(self):
        payload = self.payload('glossary_review')
        payload.update(snapshot_sha256='d' * 64,
                       entries=[{'ref': f'terms:{i}', 'row': {
                           'source': f'Gate{i}', 'evidence_rows': [i + 1],
                           'extraction_candidates': [{'translation': '城门'}, {'translation': '大门'}]}}
                           for i in range(40)],
                       related_entries=[{'ref': 'terms:88', 'row': {'source': 'Gates', 'evidence_rows': [1]}}],
                       audit={'findings': [{'id': 'conflict-1', 'refs': ['terms:0', 'terms:88']}]},
                       term_families={'related_terms': [{'ref': 'terms:88', 'source': 'Gates'}]})
        data = api_protocol_data(payload)
        self.assertEqual([e['row'] for e in data['entries']], [e['row'] for e in payload['entries']])
        self.assertEqual(data['related_entries'][0]['row'], payload['related_entries'][0]['row'])
        self.assertEqual(data['term_families']['related_terms'], [{'ref': 'e41', 'source': 'Gates'}])
        self.assertEqual(data['audit']['findings'], [{'id': 'f1', 'refs': ['e1', 'e41']}])
        self.assertNotIn('source_hash', data)
        self.assertNotIn('snapshot_sha256', data)
        template = data['output_template']['decisions']
        self.assertEqual(len(template['entry_reviews']), 1)
        self.assertEqual(len(output_template(payload)['decisions']['entry_reviews']), 40)
        self.assertIn('全部分配 entries.ref', api_template_instruction('glossary_review'))
        self.assertIn('全部 ID', api_template_instruction('review'))

    def test_review_examples_keep_six_dimensions_without_copying_translations(self):
        for stage in ('review', 'final_review', 'polish'):
            with self.subTest(stage=stage):
                payload = self.payload(stage)
                for row in payload['records']:
                    row['translation'] = '原译文必须只在输入记录中出现。'
                full = output_template(payload)
                template = api_output_template(payload)
                self.assertEqual(len(full['records']), 40)
                self.assertEqual(len(template['records']), 2)
                self.assertIn('translation', template['records'][0])
                self.assertNotIn('keep', template['records'][0])
                self.assertNotIn('translation', template['records'][1])
                self.assertIs(template['records'][1]['keep'], True)
                self.assertNotEqual(template['records'][0]['id'], template['records'][1]['id'])
                self.assertNotIn('原译文必须只在输入记录中出现。',json.dumps(template,ensure_ascii=False))
                if stage != 'polish':
                    self.assertEqual(set(template['records'][0]['checks']), {
                        'accuracy', 'completeness', 'terminology', 'tone', 'logic', 'placeholders'})
                self.assertLess(count_tokens(json.dumps(template)), count_tokens(json.dumps(full)) / 4)
                instructions, _ = prompt(payload, api=True)
                self.assertEqual(instructions.count('锁定译文不得改动'), 1)
                if stage != 'polish':
                    self.assertIn('accuracy/completeness/logic 每项为 [pass或corrected', instructions)
                    self.assertIn('changes[].source_excerpt', instructions)
                    self.assertIn('不在三处复制整段相同说明', instructions)

    def test_legacy_pilot_example_uses_full_review_structure_and_preserves_exceptions(self):
        payload = self.payload('pilot_review')
        payload['records'][1]['exceptions'] = [{'source': 'Charge', 'reason': '费用'}]
        before = copy.deepcopy(payload)
        template = api_output_template(payload)
        self.assertEqual(payload, before)
        self.assertEqual([r['id'] for r in template['records']], ['r1'])
        self.assertEqual(len(template['records'][0]['checks']), 6)
        self.assertNotIn('review_summary', template)
        self.assertEqual(api_protocol_data(payload)['records'][1]['exceptions'], payload['records'][1]['exceptions'])

    def test_explicit_empty_translation_is_not_discarded(self):
        payload = self.payload('review')
        payload['records'][0]['translation'] = ''
        data = api_protocol_data(payload)
        self.assertIn('translation', data['records'][0])
        self.assertEqual(data['records'][0]['translation'], '')

    def test_reasoned_keep_route_is_visible_in_both_templates(self):
        for stage in ('translate', 'review', 'final_review', 'polish'):
            with self.subTest(stage=stage):
                payload = self.payload(stage)
                self.assertEqual(output_template(payload)['records'][0]['exceptions'], [])
                self.assertEqual(api_output_template(payload)['records'][0]['exceptions'], [])
                for api in (False, True):
                    instructions, _ = prompt(payload, api=api)
                    self.assertIn('坚持现译正确时可保留译文', instructions)
                    self.assertIn('仅重复原译文', instructions)
                    self.assertIn('后续程序检查沿用', instructions)
                    self.assertIn('term_ref' if api else 'source:该疑问', instructions)
                instructions, _ = prompt(payload, api=True)
                self.assertNotIn('exceptions=[{source:', instructions)
                self.assertNotIn('exception_changes=[{source,', instructions)


if __name__ == '__main__':
    unittest.main()
