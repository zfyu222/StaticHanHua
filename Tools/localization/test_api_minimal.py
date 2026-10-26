"""Minimal wire replies reconstruct canonical data without fabricating evidence."""
import copy
import unittest

from workflow_task_contract import api_protocol_data, api_id_transport, canonical_api_result
from workflow_quality import validate_review, validate_glossary_reviews


class MinimalAPITests(unittest.TestCase):
    def glossary(self):
        return {'stage': 'glossary_review', 'task_id': 'long-task-id', 'input_hash': 'a'*64,
            'source_hash': 'b'*64, 'snapshot_sha256': 'c'*64,
            'records': [{'id': 'long-record-id', 'ordinal': 73, 'source': 'Rest here.'}],
            'entries': [{'ref': 'terms:12', 'table': 'terms', 'index': 12, 'row': {
                'source': 'Rest', 'recommended_translation': '休息', 'evidence_rows': [73],
                'extraction_candidates': [{'row': {'recommended_translation': '休息'},
                    'evidence_rows': [73], 'extraction_tasks': ['long-extract-id']}],
                'review_provenance': {'reviewer_id': 'long-agent-id', 'input_hash': 'x'*64,
                                      'reason': '先前的语义依据仍应保留。'}}}],
            'related_entries': [{'ref': 'terms:99', 'row': {'source': 'Resting'}}],
            'term_families': {'candidates': [{'refs': ['terms:12', 'terms:99'], 'kind': 'root'}]},
            'audit': {'snapshot_sha256': 'c'*64, 'findings': [{'id': 'long-finding-id', 'refs': ['terms:12', 'terms:99']}]}}

    def decode(self, payload, result):
        scope = [r['id'] for r in payload['records']]
        transport = api_id_transport(payload, scope, 'session')
        return canonical_api_result(result, transport, 'session', payload['task_id'], payload['input_hash'], scope)

    def decisions(self):
        return {'review_complete': True, 'review_summary': '已核对原文和术语。',
            'entry_reviews': [{'ref': 'e1', 'evidence_rows': [73], 'source_excerpt': 'Rest here.',
                'reason': 'Rest here 表示在此休息。', 'checks': {name: {'status': 'pass', 'evidence': 'Rest here.'}
                for name in ('source', 'translation', 'category', 'consistency')}}],
            'acknowledged_findings': ['f1'],
            'edits': [{'ref': 'e1', 'patch': {'recommended_translation': '休憩'}, 'reason': '具体出处。', 'evidence_rows': [73]}]}

    def test_glossary_packet_drops_tracking_keeps_semantics_and_shortens_refs(self):
        payload = self.glossary(); original = copy.deepcopy(payload)
        wire = api_protocol_data(payload)
        self.assertEqual(payload, original)
        self.assertNotIn('task_id', wire); self.assertNotIn('input_hash', wire)
        self.assertNotIn('source_hash', wire); self.assertNotIn('snapshot_sha256', wire)
        self.assertNotIn('id', wire['records'][0])
        self.assertEqual(wire['records'][0]['ordinal'], 73)
        self.assertEqual(wire['entries'][0]['ref'], 'e1')
        self.assertNotIn('index', wire['entries'][0])
        self.assertEqual(wire['audit']['findings'][0]['refs'], ['e1', 'e2'])
        self.assertEqual(wire['audit']['findings'][0]['id'], 'f1')
        row = wire['entries'][0]['row']
        self.assertNotIn('extraction_tasks', row['extraction_candidates'][0])
        self.assertEqual(row['review_provenance'], {'reason': '先前的语义依据仍应保留。'})
        self.assertNotIn('reviewed_refs', wire['output_template']['decisions'])

    def test_glossary_reply_recovers_bound_fields_and_still_validates_evidence(self):
        payload = self.glossary()
        value = self.decode(payload, {'decisions': self.decisions()})
        d = value['decisions']
        self.assertEqual(d['snapshot_sha256'], 'c'*64)
        self.assertEqual(d['source_sha256'], 'b'*64)
        self.assertEqual(d['reviewed_refs'], ['terms:12'])
        self.assertEqual(d['acknowledged_findings'], ['long-finding-id'])
        self.assertEqual(d['edits'][0]['table'], 'terms')
        self.assertEqual(d['edits'][0]['index'], 12)
        validate_glossary_reviews(d, payload)
        broken = self.decisions(); broken['entry_reviews'][0]['checks'] = {}
        with self.assertRaises(ValueError):
            validate_glossary_reviews(self.decode(payload, {'decisions': broken})['decisions'], payload)

    def test_bare_decisions_preserve_all_semantic_fields_and_bound_checks(self):
        payload = self.glossary()
        wrapped = self.decode(payload, {'decisions': self.decisions()})
        bare = self.decode(payload, self.decisions())
        self.assertEqual(bare, wrapped)
        validate_glossary_reviews(bare['decisions'], payload)
        broken = self.decisions(); broken['entry_reviews'][0]['ref'] = 'e99'
        with self.assertRaises(ValueError):
            self.decode(payload, broken)
        broken = self.decisions(); broken['entry_reviews'][0]['checks'] = {}
        with self.assertRaises(ValueError):
            validate_glossary_reviews(self.decode(payload, broken)['decisions'], payload)
        with self.assertRaises(ValueError):
            self.decode(payload, {'review_complete': True})

    def test_read_only_unknown_duplicate_and_missing_glossary_refs_fail(self):
        for ref in ('e2', 'e99', None, []):
            d = self.decisions(); d['entry_reviews'][0]['ref'] = ref
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.decode(self.glossary(), {'decisions': d})
        d = self.decisions(); d['entry_reviews'] *= 2
        with self.assertRaises(ValueError): self.decode(self.glossary(), {'decisions': d})
        d = self.decisions(); d.pop('acknowledged_findings')
        self.assertEqual(self.decode(self.glossary(), {'decisions': d})['decisions']['acknowledged_findings'], [])

    def test_extraction_returns_only_analysis_and_keeps_original_evidence_numbers(self):
        payload = {**self.glossary(), 'stage': 'extract'}
        analysis = {'characters': [], 'terms': [{'source': 'Rest', 'evidence_rows': [73]}], 'non_translate': []}
        value = self.decode(payload, {'analysis': analysis})
        self.assertEqual(value['task_id'], payload['task_id'])
        self.assertEqual(value['analysis'], analysis)

    def test_glossary_actions_resolve_changed_sources_and_explicit_additions(self):
        d = self.decisions()
        d['edits'][0]['patch']['source'] = 'Resting'
        d['quarantine'] = [{'ref': 'e1', 'reason': '隔离依据。'}]
        d['additions'] = [{'table': 'terms', 'row': {'source': 'Rest stop'}}]
        d['allow_no_match'] = [{'ref': 'e1', 'reason': '变形依据。'},
                               {'source': 'Rest stop', 'reason': '新增依据。'}]
        value = self.decode(self.glossary(), {'decisions': d})['decisions']
        self.assertEqual([e['source'] for e in value['allow_no_match']], ['Resting', 'Rest stop'])
        self.assertEqual(value['quarantine'][0]['index'], 12)
        d['allow_no_match'][1]['source'] = 'Unknown'
        with self.assertRaises(ValueError): self.decode(self.glossary(), {'decisions': d})

    def test_empty_glossary_can_complete_without_record_ids(self):
        payload = self.glossary()
        payload.update(records=[], entries=[], related_entries=[], term_families={}, audit={'findings': []})
        d = {'review_complete': True, 'review_summary': '本批没有术语。', 'entry_reviews': []}
        restored = self.decode(payload, {'decisions': d})['decisions']
        self.assertEqual(restored['reviewed_refs'], [])
        self.assertEqual(restored['acknowledged_findings'], [])
        validate_glossary_reviews(restored, payload)

    def body(self, stage='review'):
        return {'stage': stage, 'task_id': 'task', 'input_hash': 'hash',
                'records': [{'id': 'record', 'source': 'Rest here.', 'translation': '在这里休息。'}]}

    def review_item(self):
        return {'id': '1', 'keep': True, 'reason': '原文 Rest here 对应在这里休息。',
                'source_excerpt': 'Rest here.', 'changes': [], 'issues': [],
                'checks': {name: {'status': 'pass', 'evidence': 'Rest here 对应在这里休息。'}
                           for name in ('accuracy', 'completeness', 'terminology', 'tone', 'logic', 'placeholders')}}

    def test_keep_is_explicit_and_missing_checks_are_not_fabricated(self):
        payload = self.body()
        item = self.decode(payload, {'records': [self.review_item()]})['records'][0]
        self.assertEqual(item['translation'], '在这里休息。')
        self.assertTrue(item['reviewed']); self.assertNotIn('keep', item)
        validate_review(item, 'Rest here.', '在这里休息。', item['translation'])
        item.pop('checks')
        with self.assertRaises(ValueError): validate_review(item, 'Rest here.', '在这里休息。', item['translation'])
        for row in ({'id': '1'}, {'id': '1', 'keep': None}, {'id': '1', 'keep': False},
                    {'id': '1', 'keep': True, 'translation': '另一个译文'}):
            with self.subTest(row=row), self.assertRaises(ValueError): self.decode(payload, {'records': [row]})

    def test_corrected_translation_is_written_once_and_before_after_are_bound(self):
        payload = self.body(); row = self.review_item(); row.pop('keep')
        row['translation'] = '在此休息。'
        row['checks']['accuracy']['status'] = 'corrected'
        row['changes'] = [{'category': 'accuracy', 'severity': 'minor', 'source_excerpt': 'Rest here.', 'reason': '具体改动依据。'}]
        item = self.decode(payload, {'records': [row]})['records'][0]
        self.assertEqual(item['changes'][0]['before'], '在这里休息。')
        self.assertEqual(item['changes'][0]['after'], '在此休息。')
        validate_review(item, 'Rest here.', '在这里休息。', '在此休息。')
        row['changes'][0]['before'] = '错误的旧译文'
        item = self.decode(payload, {'records': [row]})['records'][0]
        with self.assertRaises(ValueError): validate_review(item, 'Rest here.', '在这里休息。', '在此休息。')
        kept = self.decode(self.body('polish'), {'records': [{'id': '1', 'keep': True}]})['records'][0]
        self.assertEqual(kept['translation'], '在这里休息。')
        self.assertNotIn('reviewed', kept)


if __name__ == '__main__': unittest.main()
