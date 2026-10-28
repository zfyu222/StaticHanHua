"""Regression coverage for review prompt/validator contract and repair diagnostics."""
import copy
import json
import unittest

from translation_workflow import prompt, Workflow, DEFAULT, merge_config
from workflow_quality import (REVIEW_CHECKS, ReviewContractError, validate_review, review_failure_detail,
                              review_candidate_failure_detail)


class ReviewContractTests(unittest.TestCase):
    def test_review_output_budget_splits_without_losing_or_reordering_ids(self):
        flow = Workflow('.')
        rows = [{'id': str(i), 'source': 'House', 'translation': '房屋'} for i in range(80)]
        config = merge_config(DEFAULT, {'backend': 'api'})
        batches = list(flow.review_batches(rows, config, 'review'))
        self.assertGreater(len(batches), 1)
        self.assertEqual([r for batch in batches for r in batch], rows)
        for batch in batches:
            self.assertLessEqual(sum(flow.estimate_review_output(r, 'review') for r in batch),
                                 flow.review_output_budget(config, 'review'))
        self.assertEqual(flow.review_output_budget(config, 'review'), flow.review_output_budget(config, 'review', 'subagent'))
        self.assertEqual(batches, list(flow.review_batches(rows, config, 'review', 'subagent')))

    def test_single_oversized_review_record_stops_before_creating_a_task(self):
        flow = Workflow('.')
        with self.assertRaisesRegex(ValueError, 'output budget too small'):
            list(flow.review_batches([{'id': 'long', 'source': '长' * 10000, 'translation': '译' * 10000}],
                                     DEFAULT, 'review'))

    def item(self):
        return {'source_excerpt': 'House', 'changes': [],
                'checks': {name: {'status': 'pass', 'evidence': 'Checked the named building and absence of added relations.'}
                           for name in REVIEW_CHECKS}}

    def test_nonempty_label_requires_each_core_dimension_with_specific_repair(self):
        for dimension in ('accuracy', 'completeness', 'logic'):
            with self.subTest(dimension=dimension):
                item = self.item(); item['checks'][dimension]['status'] = 'not_applicable'
                before = copy.deepcopy(item)
                with self.assertRaises(ReviewContractError) as raised:
                    validate_review(item, 'House', '房屋', '房屋')
                detail = review_failure_detail(raised.exception)
                self.assertEqual(detail['code'], 'required_dimension_not_applicable')
                self.assertEqual(detail['fields'][0]['path'], f'checks.{dimension}.status')
                self.assertEqual(detail['fields'][0]['expected'], ['pass', 'corrected'])
                self.assertEqual(item, before)  # Diagnostics must never fabricate corrected evidence.

    def test_checked_label_passes_and_optional_not_applicable_is_preserved(self):
        item = self.item(); item['checks']['placeholders']['status'] = 'not_applicable'
        validate_review(item, 'House', '房屋', '房屋')
        item['source_excerpt'] = ''
        for check in item['checks'].values():
            check.update(status='not_applicable', evidence='Verified both source and translation are empty.')
        validate_review(item, '', '', '')

    def test_changes_may_cite_different_valid_source_quotes(self):
        item = self.item(); item['checks']['accuracy']['status'] = 'corrected'
        item['changes'] = [{'before': '家', 'after': '房屋', 'category': 'accuracy', 'severity': 'minor',
                            'source_excerpt': 'House label', 'reason': 'The source names a building.'}]
        validate_review(item, 'House label', '家', '房屋')
        item['changes'][0]['source_excerpt'] = 'Invented excerpt'
        with self.assertRaises(ReviewContractError) as raised:
            validate_review(item, 'House label', '家', '房屋')
        self.assertEqual(raised.exception.detail['fields'][0]['path'], 'changes[0].source_excerpt')
        item['changes'][0]['source_excerpt'] = 'House'
        validate_review(item, 'House label', '家', '房屋')

    def test_dispatch_prompt_explains_status_and_excerpt_constraints(self):
        for stage in ('review', 'final_review'):
            system, _ = prompt({'stage': stage})
            self.assertIn('只允许 pass/corrected', system)
            self.assertIn('changes[].source_excerpt 必须', system)
            self.assertIn('diagnostic_detail', system)
            self.assertNotIn('无相关内容时写 not_applicable', system)

    def test_gate_categories_do_not_conflate_format_and_quality(self):
        for error, category in [('Invalid review change evidence', 'review_record_contract'),
                                ('R1: terminology mismatch [House]', 'terminology_gate'),
                                ('R1: structural mismatch [tags]', 'structure_gate'),
                                ('Unresolved blocking semantic issue', 'reported_blocking_semantic_issue')]:
            self.assertEqual(review_failure_detail(error)['category'], category)

    def test_legacy_candidate_diagnostic_is_enriched_without_modifying_evidence(self):
        candidate = self.item(); candidate['translation'] = '房屋'
        candidate['checks']['logic']['status'] = 'not_applicable'
        before = copy.deepcopy(candidate)
        detail = review_candidate_failure_detail(
            'Meaning/completeness/logic checks cannot be skipped for non-empty text', candidate, 'House', '房屋')
        self.assertEqual(detail['fields'][0]['path'], 'checks.logic.status')
        self.assertEqual(candidate, before)

    def test_whitespace_only_source_has_the_same_empty_excerpt_rule_as_empty_source(self):
        item = self.item(); item['source_excerpt'] = ''
        validate_review(item, ' \t\n', ' \t\n', ' \t\n')
        with self.assertRaisesRegex(ValueError, 'source_excerpt'):
            validate_review(item, 'House', '房屋', '房屋')

    def test_legacy_pilot_prompt_contains_the_same_evidence_restrictions_as_full_review(self):
        system, _ = prompt({'stage': 'pilot_review'})
        self.assertIn('只允许 pass/corrected', system)
        self.assertIn('changes[].source_excerpt 必须', system)
        self.assertIn('已有 exceptions 默认继承', system)
        system, _ = prompt({'stage': 'translate'})
        self.assertIn('issues=[{severity:warning,reason:', system)

    def test_null_and_nontext_reasons_cannot_pass_strict_semantic_review(self):
        flow = Workflow('.')
        row = {'id': 'x', 'source': 'House', 'translation': '房屋', 'locked': False, 'exceptions': '[]'}
        task = {'stage': 'review', 'payload': json.dumps({'strict_review': True})}
        for value in (None, 0, False, {'note': 'not a string'}, ['not a string']):
            item = {**self.item(), 'id': 'x', 'translation': '房屋', 'reviewed': True, 'reason': value}
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'explicit semantic review'):
                flow.validate_body_row(row, task, {}, item, row, {}, [], {}, terms=[])
            item['reason'] = 'Explicit source comparison.'
            item['issues'] = [{'severity': 'warning', 'reason': value}]
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Issues require'):
                flow.validate_body_row(row, task, {}, item, row, {}, [], {}, terms=[])


if __name__ == '__main__':
    unittest.main()
