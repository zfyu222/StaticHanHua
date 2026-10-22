import copy
import unittest

import ainiee_bridge as bridge
import glossary_tool
from workflow_term_scope import validate_applicability
from workflow_review_evidence import expand_evidence
from workflow_format_normalize import normalize_body_item
from workflow_quality import validate_review
from workflow_task_contract import api_id_transport, canonical_api_partial_result


class ScopeTests(unittest.TestCase):
    options = {'case_sensitive': False, 'whole_word': True}

    def analysis(self, source, mode, **extra):
        return {'characters': [], 'non_translate': [], 'terms': [{
            'source': source, 'recommended_translation': '力量',
            'applicability': {'mode': mode, 'case_sensitive': True,
                              'reason': 'Explicit game attribute label', **extra}}]}

    def test_whole_record_does_not_match_ordinary_usage(self):
        a = self.analysis('Might', 'whole_record')
        self.assertTrue(bridge.matched_terms(a, ['  Might  '], self.options))
        self.assertFalse(bridge.matched_terms(a, ['It might rain.'], self.options))
        self.assertFalse(bridge.matched_terms(a, ['Might be useful.'], self.options))
        self.assertTrue(bridge.matched_terms(a, ['Might', 'It might rain.'], self.options))

    def test_case_sensitive_name_and_literal_context(self):
        a = self.analysis('May', 'text')
        self.assertTrue(bridge.matched_terms(a, ['Ask May.'], self.options))
        self.assertFalse(bridge.matched_terms(a, ['You may enter.'], self.options))
        a = self.analysis('Might', 'literal_context', prefix='Attribute: ', suffix=' =')
        self.assertTrue(bridge.matched_terms(a, ['Attribute: Might = 4'], self.options))
        self.assertFalse(bridge.matched_terms(a, ['Might = 4'], self.options))

    def test_new_ambiguous_entries_need_reviewed_scope(self):
        with self.assertRaisesRegex(ValueError, 'quarantine'):
            validate_applicability({'source': 'i'}, required=True)
        validate_applicability({'source': 'Alice'}, required=True)
        validate_applicability({'source': 'i'})  # Old snapshots unchanged.
        a = {'characters': [], 'terms': [], 'non_translate': [{'source': 'i'}]}
        self.assertTrue(any(x['code'] == 'ambiguous_applicability' for x in glossary_tool.findings(a, ['I agree.'])))

    def test_malformed_scope_is_not_silently_treated_as_global(self):
        for scope in ({'mode': 'unknown'}, {'mode': 'literal_context', 'case_sensitive': True, 'reason': 'label'}):
            with self.assertRaises(ValueError):
                validate_applicability({'source': 'Might', 'applicability': scope})

    def test_new_review_must_resolve_ordinary_word_risk_before_freeze(self):
        from workflow_quality import validate_glossary_applicability
        payload = {'applicability_policy': 1, 'entries': [{'ref': 'terms:0', 'row': {'source': 'might'}}]}
        with self.assertRaises(ValueError):
            validate_glossary_applicability({}, payload)
        validate_glossary_applicability({'quarantine': [{'table': 'terms', 'index': 0}]}, payload)
        scope = self.analysis('might', 'whole_record')['terms'][0]['applicability']
        action = {'table': 'terms', 'index': 0, 'patch': {'applicability': scope}}
        with self.assertRaisesRegex(ValueError, 'evidence rows'):
            validate_glossary_applicability({'edits': [action]}, payload)
        action['evidence_rows'] = [1]
        validate_glossary_applicability({'edits': [action]}, payload)


class EvidenceTests(unittest.TestCase):
    def item(self):
        return {'id': 'r1', 'translation': '明智之举。', 'reviewed': True,
                'source_excerpt': 'A wise course.', 'evidence': {
                    'e1': 'wise course means 明智之举; all words and judgment are retained.',
                    'e2': 'No named term or variable occurs; short approving tone is retained.'},
                'reason': {'ref': 'e1'}, 'changes': [],
                'checks': {k: {'status': 'pass', 'evidence': {'ref': 'e2' if k in ('terminology', 'tone', 'placeholders') else 'e1'}}
                           for k in ('accuracy', 'completeness', 'terminology', 'tone', 'logic', 'placeholders')}}

    def test_shared_evidence_is_exact_model_text_and_six_verdicts_survive(self):
        raw = self.item(); out = expand_evidence(raw)
        self.assertIn('evidence', raw)
        self.assertEqual(out['reason'], raw['evidence']['e1'])
        self.assertEqual(len(out['checks']), 6)
        validate_review(out, 'A wise course.', '明智之举。', '明智之举。')

    def test_missing_dimension_is_still_rejected(self):
        raw = self.item(); del raw['checks']['logic']
        with self.assertRaises(ValueError):
            validate_review(expand_evidence(raw), 'A wise course.', '明智之举。', '明智之举。')

    def test_bad_refs_empty_nested_or_unused_evidence_rejected(self):
        for mutate in (
            lambda r: r['reason'].update(ref='e99'),
            lambda r: r['reason'].update(ref=[]),
            lambda r: r['evidence'].update(e1=''),
            lambda r: r['evidence'].update(e1={'ref': 'e2'}),
            lambda r: r['evidence'].update(e3='unused')):
            raw = self.item(); mutate(raw)
            with self.assertRaises(ValueError):
                expand_evidence(raw)

    def test_api_row_failure_does_not_invent_another_rows_evidence(self):
        payload = {'stage': 'review', 'task_id': 'task', 'input_hash': 'hash',
                   'records': [{'id': 'r1', 'source': 'A wise course.', 'translation': '明智之举。'},
                               {'id': 'r2', 'source': 'A wise course.', 'translation': '明智之举。'}]}
        transport = api_id_transport(payload, ['r1', 'r2'], 'session')
        a = self.item(); a.update(id='1', keep=True)
        a.pop('translation')
        b = copy.deepcopy(a); b['id'] = '2'; b.pop('evidence')
        # A reference without a table must fail at the normal semantic gate,
        # never borrow e1 from the preceding row.
        value = canonical_api_partial_result({'records': [a, b]}, transport, 'session', 'task', 'hash', ['r1', 'r2'])
        self.assertEqual(value['records'][0]['reason'], a['evidence']['e1'])
        with self.assertRaises(ValueError):
            validate_review(value['records'][1], 'A wise course.', '明智之举。', '明智之举。')


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'r1', 'source': ' \tAlpha\r\nBeta  ', 'locked': 0}
        self.item = {'id': 'r1', 'translation': '甲\n乙', 'changes': [],
                     'checks': {'placeholders': {'status': 'pass', 'evidence': 'No markup changed'}}}

    def normalize(self, **kwargs):
        return normalize_body_item(kwargs.get('item', self.item), kwargs.get('original', {}),
                                   kwargs.get('row', self.row), kwargs.get('config', {}), kwargs.get('policy', 'exact_v1'))

    def test_exact_restore_idempotent_and_does_not_create_checks(self):
        value, audit = self.normalize()
        self.assertEqual(value['translation'], ' \t甲\r\n乙  ')
        self.assertEqual(value['checks'], self.item['checks'])
        self.assertFalse(audit['semantic_checks_created'])
        second, second_audit = self.normalize(item=value)
        self.assertEqual(second, value); self.assertIsNone(second_audit)

    def test_missing_paragraph_or_locked_or_legacy_not_changed(self):
        for args in ({'item': dict(self.item, translation='甲乙')},
                     {'row': dict(self.row, locked=1)}, {'policy': 'off'}):
            value, audit = self.normalize(**args)
            self.assertEqual(value, args.get('item', self.item)); self.assertIsNone(audit)

    def test_explicit_format_correction_not_overridden(self):
        raw = copy.deepcopy(self.item); raw['checks']['placeholders']['status'] = 'corrected'
        self.assertIsNone(self.normalize(item=raw)[1])
        raw = dict(self.item, changes=[{'category': 'placeholders', 'after': '甲\n乙'}])
        self.assertIsNone(self.normalize(item=raw)[1])

    def test_semantic_change_after_binding_retained_and_bad_binding_not_fixed(self):
        raw = dict(self.item, changes=[{'category': 'accuracy', 'after': self.item['translation'], 'reason': 'actual semantic correction'}])
        value, audit = self.normalize(item=raw)
        self.assertEqual(value['changes'][0]['reason'], raw['changes'][0]['reason'])
        self.assertEqual(value['changes'][0]['after'], value['translation'])
        raw['changes'][0]['after'] = 'some other text'
        self.assertIsNone(self.normalize(item=raw)[1])

    def test_source_bound_tutorial_constraint_and_wrong_hash(self):
        from workflow_format_normalize import digest
        cfg = {'record_constraints': {'r1': {'source_sha256': digest(self.row['source']), 'physical_newlines': ['\n']}}}
        self.assertEqual(self.normalize(config=cfg)[0]['translation'], ' \t甲\n乙  ')
        cfg['record_constraints']['r1']['source_sha256'] = 'stale'
        with self.assertRaises(ValueError):
            self.normalize(config=cfg)

    def test_non_whitespace_content_is_not_repaired(self):
        rules = [('tag', __import__('re').compile(r'\{0\}'))]
        row = dict(self.row, source=' {0}\r\nBeta  ')
        value, audit = self.normalize(row=row)
        self.assertIn('tag', bridge.structural_issues(row['source'], value['translation'], rules))


class WorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        import test_translation_workflow as fixtures
        self.f = fixtures.WorkflowTests(); self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.flow = self.f.flow
        self.f.freeze(True); self.f.translate()

    def test_batch_and_direct_review_share_normalization_and_reject_bad_tags(self):
        import json
        import translation_workflow as w
        for number in range(2):
            session = self.flow.batch_open('review', 'reviewer-' + str(number))
            with self.flow.db() as db:
                task = db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone()
            originals = json.loads(task['payload'])['records']
            records = []
            for row in originals:
                item = {'id': row['id'], 'translation': row['translation'], 'reviewed': True,
                        'reason': 'All lexical content and relations checked', **self.f.review_evidence(row)}
                if item['translation'].strip():
                    item['translation'] += '  '
                records.append(item)
            result = {'task_id': task['id'], 'input_hash': task['input_hash'], 'records': records}
            if number == 0:
                w.write_json(session['result_file'], result)
                self.assertEqual(self.flow.batch_finish(session['session_id'])['status'], 'submitted')
            else:
                self.flow.submit(task['id'], task['lease_token'], result)
        self.assertTrue(self.flow.review_scan()['clean'])
        with self.flow.db() as db:
            self.assertGreater(db.execute('SELECT count(*) FROM events WHERE kind="normalize_layout"').fetchone()[0], 0)

    def test_legacy_payload_remains_strict_and_unchanged(self):
        import json
        session = self.flow.batch_open('review', 'legacy-reviewer')
        with self.flow.db() as db:
            task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (session['task_id'],)).fetchone())
            payload = json.loads(task['payload']); payload.pop('layout_policy', None)
            task['payload'] = json.dumps(payload)
            row = payload['records'][0]
            item = {'id': row['id'], 'translation': row['translation'] + '  '}
            prepared, audit = self.flow.prepare_body_item(db, task, item, row)
            self.assertEqual(item, prepared); self.assertIsNone(audit)


if __name__ == '__main__':
    unittest.main()
