"""Glossary batches must only ask for citations they can actually verify."""
import unittest

import translation_workflow as workflow
from workflow_quality import validate_glossary_reviews
from workflow_task_contract import api_protocol_data


SOURCE_WITH_BREAK = ("You find an oddly shaped item amongst what's left.\n"
                     "{LOOT: Drop Silver Merecaster.}")


class GlossaryInputFixTests(unittest.TestCase):
    def payload(self, evidence=(10, 99, 100)):
        return {'stage': 'glossary_review', 'task_id': 'task', 'input_hash': 'a' * 64,
                'source_hash': 'b' * 64, 'snapshot_sha256': 'c' * 64,
                'records': [{'id': 'r1', 'ordinal': 10, 'source': SOURCE_WITH_BREAK},
                            {'id': 'r2', 'ordinal': 11, 'source': 'Another line.'}],
                'entries': [
                    {'ref': 'terms:1', 'table': 'terms', 'index': 1, 'row': {
                        'source': 'Merecaster', 'recommended_translation': '银忆器',
                        'evidence_rows': list(evidence),
                        'extraction_candidates': [{'row': {'recommended_translation': '银忆器'},
                                                   'evidence_rows': [99]}]}},
                    {'ref': 'terms:2', 'table': 'terms', 'index': 2, 'row': {
                        'source': 'Ghost', 'recommended_translation': '幽影',
                        'evidence_rows': [98]}}],
                'related_entries': [{'ref': 'terms:3', 'row': {'source': 'Ghosts',
                                                              'evidence_rows': [10, 97]}}],
                'term_families': {'candidates': [{'refs': ['terms:1', 'terms:3'], 'kind': 'root'}]},
                'audit': {'findings': [{'id': 'f1', 'refs': ['terms:1']}]}}

    def validation_payload(self):
        payload = self.payload()
        payload['entries'] = payload['entries'][:1]
        return payload

    def decisions(self, rows=(10,), excerpt=SOURCE_WITH_BREAK, ref='terms:1'):
        return {'entry_reviews': [{'ref': ref, 'evidence_rows': list(rows),
                                   'source_excerpt': excerpt, 'reason': '引用本行原文核对译名。',
                                   'checks': {name: {'status': 'pass', 'evidence': '本行原文支持该译名。'}
                                              for name in ('source', 'translation', 'category', 'consistency')}}]}

    def test_wire_hides_rows_without_attached_text(self):
        wire = api_protocol_data(self.payload())
        self.assertEqual(wire['entries'][0]['row']['evidence_rows'], [10])
        self.assertEqual(wire['entries'][0]['row']['extraction_candidates'][0]['evidence_rows'], [])
        self.assertEqual(wire['related_entries'][0]['row']['evidence_rows'], [10])

    def test_read_only_entry_keeps_nothing_it_cannot_quote(self):
        payload = self.payload()
        payload['related_entries'][0]['row']['evidence_rows'] = [97]
        wire = api_protocol_data(payload)
        self.assertEqual(wire['related_entries'][0]['row']['evidence_rows'], [])

    def test_entry_keeps_its_list_when_no_row_is_readable(self):
        wire = api_protocol_data(self.payload(evidence=(98, 99)))
        self.assertEqual(wire['entries'][0]['row']['evidence_rows'], [98, 99])

    def test_prompt_requires_visible_rows_and_verbatim_quotes(self):
        text, _ = workflow.prompt(self.payload(), api=True)
        self.assertIn('只能从本条目的 evidence_rows 中选', text)
        self.assertIn('连续逐字片段', text)
        self.assertIn('不得包含 related_entries', text)
        self.assertIn('checks 每维固定为两元素数组', text)

    def test_extract_api_prompt_forbids_reasoning_in_content(self):
        text, _ = workflow.prompt({'stage': 'extract'}, api=True)
        self.assertIn('第一个字符就是 {', text)
        self.assertIn('不输出分析过程', text)

    def test_line_break_variants_still_verify(self):
        collapsed = ' '.join(SOURCE_WITH_BREAK.split('\n'))
        validate_glossary_reviews(self.decisions(excerpt=collapsed), self.validation_payload())

    def test_paraphrase_and_ellipsis_are_still_rejected(self):
        for excerpt in ("You find an item amongst what's left.", 'You find an oddly shaped item ... {LOOT:'):
            with self.subTest(excerpt=excerpt), self.assertRaises(ValueError):
                validate_glossary_reviews(self.decisions(excerpt=excerpt), self.validation_payload())

    def test_citation_outside_the_entry_evidence_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid_or_unprovided_rows=\\[11\\]'):
            validate_glossary_reviews(self.decisions(rows=(11,)), self.validation_payload())


if __name__ == '__main__':
    unittest.main()
