"""Token-only stages, dual-limited review, and AINiee counting parity."""
import unittest
from translation_workflow import DEFAULT, Workflow, merge_config
from workflow_tokens import count_tokens, record_tokens, batch_limit, record_limit


class TokenBatchTests(unittest.TestCase):
    def test_count_matches_ainiee_for_multilingual_and_escaped_text(self):
        from ModuleFolders.Service.Cache.CacheItem import CacheItem
        for text in ('Hello world', '中文对白与角色名', '変数{0}\\n[P1]', '', 'A' * 1000):
            self.assertEqual(count_tokens(text), CacheItem.get_token_count(text))

    def test_default_limits_align_with_saved_ainiee_settings(self):
        self.assertEqual(batch_limit(DEFAULT, 'translate'), 1024)
        self.assertEqual(batch_limit(DEFAULT, 'polish'), 1024)
        self.assertEqual(batch_limit(DEFAULT, 'extract'), 10000)
        self.assertEqual(batch_limit(DEFAULT, 'glossary_review'), 10000)
        self.assertEqual(batch_limit(DEFAULT, 'review'), 1024)
        for stage in ('translate', 'polish', 'extract'):
            self.assertIsNone(record_limit(DEFAULT, stage))
        self.assertEqual(record_limit(DEFAULT, 'review'), 80)
        self.assertEqual(record_limit(DEFAULT, 'glossary_review'), 60)

    def test_source_only_weights_and_legacy_character_fields_do_not_split(self):
        rows = [{'id': str(i), 'source': 'text', 'translation': '很长的已有译文' * 100}
                for i in range(200)]
        config = {**DEFAULT, 'batch_chars': 1, 'task_input_chars': 1}
        flow = Workflow('.')
        self.assertEqual([len(g) for g in flow.batches(rows, config, False)], [200])
        self.assertEqual([len(g) for g in flow.batches(rows, config, False, stage='polish')], [200])

    def test_token_boundaries_keep_order_and_oversized_source_intact(self):
        rows = [{'id': str(i), 'source': 'word ' * n} for i, n in enumerate((3, 3, 30, 3))]
        config = merge_config(DEFAULT, {'batch_tokens': 10})
        groups = list(Workflow('.').batches(rows, config, False))
        self.assertEqual([r for g in groups for r in g], rows)
        self.assertTrue(all(sum(record_tokens(r, 'translate') for r in g) <= 10 or len(g) == 1 for g in groups))
        self.assertEqual(next(g for g in groups if rows[2] in g), [rows[2]])

    def test_review_uses_source_translation_and_problem_plus_record_cap(self):
        row = {'source': 'House', 'translation': '房屋', 'diagnostic': 'missing term'}
        self.assertGreater(record_tokens(row, 'review'), count_tokens(row['source']))
        rows = [{'id': str(i), **row} for i in range(10)]
        config = merge_config(DEFAULT, {'review_batch_records': 3, 'review_batch_tokens': 100000,
                                       'review_output_tokens': 100000})
        self.assertEqual([len(g) for g in Workflow('.').review_batches(rows, config, 'review')], [3, 3, 3, 1])
        config['review_batch_tokens'] = record_tokens(row, 'review')
        self.assertEqual([len(g) for g in Workflow('.').review_batches(rows, config, 'review')], [1] * 10)


if __name__ == '__main__':
    unittest.main()
