"""A model's explicit, reasoned context decision closes only its own term finding."""
import copy
import unittest

import ainiee_bridge as bridge
import translation_workflow as workflow


class TerminologyAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.flow = workflow.Workflow.__new__(workflow.Workflow)
        self.config = workflow.DEFAULT
        self.analysis = {'terms': [{'source': 'Rest', 'recommended_translation': '休息'}]}
        self.task = {'stage': 'translate', 'payload': '{"strict_translation":true}'}
        self.row = {'id': 'r1', 'source': 'You rest your hand on her forehead.',
                    'translation': '你把手放在她额头上。', 'exceptions': '[]', 'locked': False}
        self.item = {'id': 'r1', 'translation': self.row['translation']}
        self.reason = '原句 rest your hand on her forehead 是把手放在额头上，不是技能“休息”，现译正确。'

    def validate(self, item, row=None):
        return self.flow.validate_body_row(row or self.row, self.task, {}, item,
            {'protection': []}, self.config, bridge.patterns(), self.analysis)

    def test_unchanged_text_and_top_level_reason_do_not_invent_acceptance(self):
        for item in (self.item, {**self.item, 'reason': self.reason}):
            with self.assertRaisesRegex(ValueError, 'terminology mismatch'):
                self.validate(item)

    def test_explicit_reason_accepts_unchanged_translation_and_normalizes_term_key(self):
        item = {**self.item, 'exceptions': [{'source': 'Rest', 'reason': self.reason}]}
        value, exceptions = self.validate(item)
        self.assertEqual(value, self.item['translation'])
        self.assertEqual(exceptions, [{'source': 'rest', 'reason': self.reason}])
        repeated = {**item, 'exceptions': exceptions}
        self.assertEqual(self.validate(repeated), (value, exceptions))

    def test_empty_reasons_and_unrelated_terms_do_not_pass(self):
        for reason in ('', ' ', None, 7):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.validate({**self.item, 'exceptions': [{'source': 'rest', 'reason': reason}]})
        with self.assertRaisesRegex(ValueError, 'unrelated term'):
            self.validate({**self.item, 'exceptions': [{'source': 'Wolf', 'reason': self.reason}]})

    def test_term_acceptance_does_not_hide_broken_placeholders(self):
        row = {**self.row, 'source': self.row['source'] + ' {0}'}
        with self.assertRaisesRegex(ValueError, 'structural mismatch'):
            self.validate({**self.item, 'exceptions': [{'source': 'rest', 'reason': self.reason}]}, row)
        # The exception is record-local, not a global mutation to the glossary.
        with self.assertRaisesRegex(ValueError, 'terminology mismatch'):
            self.validate(copy.deepcopy(self.item))

    def test_accepting_one_term_does_not_close_other_term_findings(self):
        self.analysis['terms'].append({'source': 'forehead', 'recommended_translation': '前额'})
        with self.assertRaisesRegex(ValueError, 'forehead'):
            self.validate({**self.item, 'exceptions': [{'source': 'rest', 'reason': self.reason}]})


if __name__ == '__main__':
    unittest.main()
