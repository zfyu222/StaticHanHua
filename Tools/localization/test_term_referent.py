"""Required names stay strict; source-bound exceptions use the existing contract."""
import unittest
import json
from pathlib import Path
from translation_workflow import Workflow
from workflow_task_contract import api_protocol_data
from workflow_quality import validate_review


class ReferentTests(unittest.TestCase):
    def setUp(self):
        self.flow = Workflow.__new__(Workflow)
        self.term = dict(src='shade', dst='谢德', term_kind='proper_name', enforcement='required',
                         term_reason='The name of Qira\'s brother, not every ghost or shadow.')
        self.config = dict(case_sensitive=False, whole_word=True, record_constraints={})

    def validate(self, translation, exceptions=None):
        row = dict(id='r1', source='Leave the shade alone. ', translation='', locked=False, exceptions='[]')
        item = dict(id='r1', translation=translation)
        if exceptions is not None:
            item['exceptions'] = exceptions
        return self.flow.validate_body_row(row, dict(stage='translate', payload='{"strict_translation":true}'),
            {}, item, dict(protection=[]), self.config, [], {}, terms=[self.term])

    def test_no_global_downgrade_or_automatic_exemption(self):
        with self.assertRaisesRegex(ValueError, 'terminology mismatch'):
            self.validate('别去打扰那个幽影。 ')
        self.assertEqual(self.validate('别打扰谢德。 ')[0], '别打扰谢德。 ')

    def test_explicit_different_referent_passes_without_rewriting_glossary(self):
        exception = dict(source='shade', reason='The source context describes a woman-shaped void, not Qira\'s brother.')
        _, saved = self.validate('别去打扰那个幽影。 ', [exception])
        self.assertEqual(saved, [exception])
        self.assertEqual(self.term['enforcement'], 'required')

    def test_empty_or_unrelated_exception_cannot_disable_name(self):
        for exception in (dict(source='shade', reason=''), dict(source='Rose', reason='Different person')):
            with self.assertRaises(ValueError):
                self.validate('别去打扰那个幽影。 ', [exception])

    def test_meaning_reaches_api_without_downgrading_required(self):
        data = api_protocol_data(dict(stage='translate', records=[dict(id='r1', source='Leave the shade alone. ',
            terms=[self.term])], terms=[self.term]))
        # shared_terms keeps the actual reviewed sense and enforcement.
        self.assertIn(self.term['term_reason'], str(data))
        self.assertIn('required', str(data))

    def test_actual_api_results_keep_evidence_and_exception_changes(self):
        cases = json.loads((Path(__file__).parent / 'test_fixtures/term_referent_live_20261027.json').read_text(encoding='utf-8'))
        self.assertEqual(len(cases), 7)
        for case in cases:
            item = case['item']
            validate_review(item, case['source'], case['before'], item['translation'])
            if case['kind'].endswith('correct'):
                self.assertEqual(item['translation'], case['before'])
            else:
                self.assertNotEqual(item['translation'], case['before'])
        by_kind = {c['kind']: c['item'] for c in cases}
        self.assertIn('幽影', by_kind['actual_reported_error']['translation'])
        self.assertIn('谢德', by_kind['named_error']['translation'])
        self.assertIn('玫瑰', by_kind['held_out_flower_error']['translation'])
        self.assertTrue(any(e.get('action') == 'remove' for e in by_kind['named_error']['exception_changes']))


if __name__ == '__main__':
    unittest.main()
