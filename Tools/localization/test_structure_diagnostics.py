import copy
import unittest

import ainiee_bridge as bridge
from workflow_quality import (StructuralMismatchError, review_failure_detail,
                              review_candidate_failure_detail, structure_failure_detail)


class StructureDiagnosticsTests(unittest.TestCase):
    def test_command_punctuation_and_legacy_candidate(self):
        config = {'exclusions': [{'regex': r'\\x01(?:(?:\\x[0-9A-Fa-f]{2})|[^\r\n]){2}',
                                  'info': 'Opal: command plus two operands'}]}
        rules = bridge.patterns(False, config['exclusions'])
        source = r'the enmity of the \x01Á, and lose'
        before = r'招致\x01Á,的敌意'
        candidate = {'translation': r'招致\x01Á的敌意'}
        saved = copy.deepcopy(candidate)
        self.assertEqual(bridge.structural_issues(source, before, rules), [])
        detail = review_candidate_failure_detail(
            "R1: structural mismatch ['exclusion_0']", candidate, source, before,
            rules=rules, config=config)
        field = detail['fields'][0]
        self.assertEqual(field['missing'][0]['text'], r'\x01Á,')
        self.assertEqual(field['unexpected'][0]['text'], r'\x01Á的')
        self.assertEqual(field['missing'][0]['codepoints'][-1], 'U+002C')
        self.assertEqual(field['unexpected'][0]['codepoints'][-1], 'U+7684')
        self.assertEqual(field['info'], config['exclusions'][0]['info'])
        self.assertEqual(candidate, saved)
        error = StructuralMismatchError('R1', detail)
        self.assertEqual(review_failure_detail(error), detail)
        self.assertIn('structural mismatch', str(error))

    def test_counts_controls_and_ascii_identifier_boundary(self):
        import re
        rules = [('exclusion_0', re.compile(r'\bXX\b')),
                 ('exclusion_1', re.compile('\x01..'))]
        source = 'XX \x01@, \x01@,'
        target = '中文XX中文 \x01@,'
        failures = bridge.structural_issues(source, target, rules)
        detail = structure_failure_detail(source, target, rules, failures)
        self.assertEqual([f['rule'] for f in detail['fields']], ['exclusion_1'])
        token = detail['fields'][0]['missing'][0]
        self.assertEqual(token['count'], 1)
        self.assertEqual(token['codepoints'][0], 'U+0001')
        self.assertIn('\\x01', token['escaped'])


if __name__ == '__main__':
    unittest.main()
