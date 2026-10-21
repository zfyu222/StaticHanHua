"""Random pilot sampling persists its IDs without classification rules."""
import sqlite3
import unittest
from unittest.mock import patch

from translation_workflow import Workflow


class PilotSamplingTests(unittest.TestCase):
    def setUp(self):
        self.flow = Workflow('.')
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        self.addCleanup(self.db.close)
        self.config = {'pilot_records': 150, 'pilot_ordinals': []}

    def rows(self, count):
        return [{'id': str(i), 'ordinal': i + 1, 'source': 'same text'} for i in range(count)]

    def test_random_sample_has_150_distinct_record_ids_and_is_persisted(self):
        rows = self.rows(250)
        selected = self.flow.pilot_ids(self.db, rows, self.config)
        self.assertEqual(len(selected), 150)
        self.assertTrue(selected.issubset({row['id'] for row in rows}))
        with patch('workflow_quality.random.sample', side_effect=AssertionError('Must reuse saved IDs')):
            self.assertEqual(Workflow('.').pilot_ids(self.db, rows, self.config), selected)

    def test_small_corpus_uses_all_records_without_random_sampling(self):
        rows = self.rows(100)
        with patch('workflow_quality.random.sample', side_effect=AssertionError('All records fit')):
            self.assertEqual(self.flow.pilot_ids(self.db, rows, self.config), {r['id'] for r in rows})

    def test_explicit_selection_is_preserved(self):
        config = {**self.config, 'pilot_ordinals': [2, 5]}
        with patch('workflow_quality.random.sample', side_effect=AssertionError('Explicit sample')):
            self.assertEqual(self.flow.pilot_ids(self.db, self.rows(250), config), {'1', '4'})

    def test_preexisting_selection_is_not_replaced(self):
        self.flow.put(self.db, 'pilot_selection', ['existing-id'])
        with patch('workflow_quality.random.sample', side_effect=AssertionError('Existing sample')):
            self.assertEqual(self.flow.pilot_ids(self.db, self.rows(250), self.config), {'existing-id'})


if __name__ == '__main__':
    unittest.main()
