"""Local credentials must never enter persisted workflow configuration."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from translation_workflow import DEFAULT, load_api_settings, local_api_name, merge_config
from workflow_quality import QualityMixin


class APISettingsTests(unittest.TestCase):
    def test_optional_streaming_loads_for_older_snapshot_without_mutation(self):
        original = copy.deepcopy(DEFAULT); original['api'].pop('stream')
        self.save({'api_url': 'https://example.invalid/v1', 'api_key': 'fixture-only',
                   'model_name': 'fixture', 'stream': True})
        settings, _ = load_api_settings(original, self.path)
        self.assertTrue(settings['stream'])
        self.assertNotIn('stream', original['api'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'api.local.jsonc'

    def save(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8-sig')

    def test_local_file_overrides_connection_without_mutating_snapshot(self):
        original = copy.deepcopy(DEFAULT)
        self.save({'api_url': 'https://example.invalid/v1', 'api_key': 'fixture-only',
                   'model_name': 'selected-model', 'concurrency': 7})
        settings, key = load_api_settings(original, self.path)
        self.assertEqual(key, 'fixture-only')
        self.assertEqual(settings['model_name'], 'selected-model')
        self.assertEqual(settings['concurrency'], 7)
        self.assertNotIn('api_key', settings)
        self.assertEqual(original, DEFAULT)

    def test_missing_or_blank_file_supports_legacy_environment(self):
        config = copy.deepcopy(DEFAULT)
        config['api'].update(api_url='https://example.invalid', model_name='legacy')
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'fixture-legacy'}):
            self.assertEqual(load_api_settings(config, self.path)[1], 'fixture-legacy')
            self.save({'api_url': '', 'api_key': '', 'model_name': ''})
            self.assertEqual(load_api_settings(config, self.path)[1], 'fixture-legacy')

    def test_partial_or_invalid_config_fails_without_echoing_secret(self):
        for value in ({'api_key': 'fixture-secret'}, {'api_key': 'fixture-secret', 'bad': 1},
                      {'api_key': 'fixture-secret', 'api_url': 'https://example.invalid',
                       'model_name': 'test', 'concurrency': 0}):
            self.save(value)
            with self.assertRaises(ValueError) as error:
                load_api_settings(DEFAULT, self.path)
            self.assertNotIn('fixture-secret', str(error.exception))
        self.path.write_text('{broken fixture-secret', encoding='utf-8')
        with self.assertRaises(ValueError) as error:
            load_api_settings(DEFAULT, self.path)
        self.assertNotIn('fixture-secret', str(error.exception))

    def test_batching_is_not_sent_as_transport_or_credentials(self):
        self.save({'api_url': 'https://example.invalid', 'api_key': 'fixture-secret',
                   'model_name': 'fixture', 'batching': {'batch_records': 12}})
        api, _ = load_api_settings(DEFAULT, self.path)
        self.assertNotIn('batching', api)
        self.assertNotIn('api_key', api)

    def test_jsonc_comments_and_trailing_commas_preserve_values(self):
        self.path.write_text('''{
            // 服务地址中包含斜杠
            "api_url": "https://example.invalid/v1",
            "api_key": "fixture//key/*literal*/,}", /* 行内注释 */
            "model_name": "fixture",
            "batching": {"batch_records": 12,},
        }''', encoding='utf-8-sig')
        settings, key = load_api_settings(DEFAULT, self.path)
        self.assertEqual(settings['api_url'], 'https://example.invalid/v1')
        self.assertEqual(key, 'fixture//key/*literal*/,}')
        from jsonc_config import loads
        for value in ('quote" // /* x */', 'backslash\\"', 'comma,}', 'line\nnext'):
            self.assertEqual(loads(json.dumps({'value': value})), {'value': value})
        with self.assertRaises(ValueError):
            loads('{/* unfinished')

    def test_named_apis_use_default_and_allow_per_run_override(self):
        original = copy.deepcopy(DEFAULT)
        self.save({'default_api': 'relay', 'apis': {
            'relay': {'api_url': 'https://relay.invalid/v1', 'api_key': 'fixture-relay',
                      'model_name': 'relay-model', 'stream': True},
            'official': {'api_url': 'https://official.invalid', 'api_key': 'fixture-official',
                         'model_name': 'official-model'}}})
        settings, key = load_api_settings(original, self.path)
        self.assertEqual(key, 'fixture-relay')
        self.assertEqual(settings['api_url'], 'https://relay.invalid/v1')
        self.assertEqual(settings['model_name'], 'relay-model')
        self.assertTrue(settings['stream'])
        self.assertNotIn('api_key', settings)
        other, other_key = load_api_settings(original, self.path, 'official')
        self.assertEqual(other_key, 'fixture-official')
        self.assertEqual(other['model_name'], 'official-model')
        self.assertFalse(other['stream'])
        self.assertEqual(original, DEFAULT)
        self.assertEqual(local_api_name(self.path), 'relay')
        self.assertEqual(local_api_name(self.path, 'official'), 'official')

    def test_shared_settings_apply_and_entries_win(self):
        self.save({'default_api': 'a', 'concurrency': 7, 'request_timeout': 42,
                   'batching': {'batch_records': 5}, 'apis': {
                       'a': {'api_url': 'https://a.invalid', 'api_key': 'fixture-a',
                             'model_name': 'a-model', 'concurrency': 3},
                       'b': {'api_url': 'https://b.invalid', 'api_key': 'fixture-b',
                             'model_name': 'b-model'}}})
        settings, key = load_api_settings(DEFAULT, self.path)
        self.assertEqual(key, 'fixture-a')
        self.assertEqual(settings['concurrency'], 3)
        self.assertEqual(settings['request_timeout'], 42)
        self.assertNotIn('batching', settings)
        self.assertNotIn('apis', settings)
        self.assertNotIn('default_api', settings)
        self.assertEqual(load_api_settings(DEFAULT, self.path, 'b')[0]['concurrency'], 7)

    def test_single_named_api_needs_no_default_field(self):
        self.save({'apis': {'only': {'api_url': 'https://only.invalid', 'api_key': 'fixture-only',
                                     'model_name': 'only-model'}}})
        self.assertEqual(local_api_name(self.path), 'only')
        self.assertEqual(load_api_settings(DEFAULT, self.path)[1], 'fixture-only')

    def test_ambiguous_or_unknown_selection_fails_without_secret(self):
        entry = {'api_url': 'https://one.invalid', 'api_key': 'fixture-secret', 'model_name': 'm'}
        self.save({'apis': {'one': entry, 'two': dict(entry)}})
        with self.assertRaisesRegex(ValueError, 'default_api'):
            load_api_settings(DEFAULT, self.path)
        for name in ('missing', 'one '):
            with self.assertRaises(ValueError) as error:
                load_api_settings(DEFAULT, self.path, name)
            self.assertNotIn('fixture-secret', str(error.exception))
        self.save({'default_api': 'ghost', 'apis': {'one': entry}})
        with self.assertRaisesRegex(ValueError, 'ghost'):
            load_api_settings(DEFAULT, self.path)
        self.save({'api_url': 'https://flat.invalid', 'api_key': 'fixture-secret', 'model_name': 'm'})
        with self.assertRaisesRegex(ValueError, 'no apis are defined'):
            load_api_settings(DEFAULT, self.path, 'one')

    def test_invalid_api_shapes_and_missing_entry_fields_fail(self):
        entry = {'api_url': 'https://one.invalid', 'api_key': 'fixture-secret', 'model_name': 'm'}
        for value in ({'default_api': 'one', 'apis': {}},
                      {'apis': {'': entry}},
                      {'apis': {'one': 'not-an-object'}},
                      {'apis': {'one': {**entry, 'batching': {}}}},
                      {'apis': {'one': {**entry, 'unknown': 1}}},
                      {'apis': {'one': entry}, 'default_api': ''},
                      {'apis': {'one': {k: v for k, v in entry.items() if k != 'model_name'}}},
                      {'apis': {'one': {**entry, 'api_key': ' '}}}):
            self.save(value)
            with self.assertRaises(ValueError) as error:
                load_api_settings(DEFAULT, self.path)
            self.assertNotIn('fixture-secret', str(error.exception))

    def test_estimate_coefficients_and_reserve_change_real_budget(self):
        config = merge_config(DEFAULT, {'review_output_tokens': 1000,
            'review_output_reserve_percent': 10, 'review_estimate': {
                'full_base_tokens': 100, 'ascii_chars_per_token': 2,
                'non_ascii_tokens_per_char': 1, 'translation_multiplier': 2}})
        self.assertEqual(QualityMixin.review_output_budget(config, 'review'), 900)
        self.assertEqual(QualityMixin.estimate_review_output(
            {'source': 'abcd', 'translation': '中文'}, 'review', config), 106)
        for patch in ({'review_output_reserve_percent': 100},
                      {'review_estimate': {'translation_multiplier': 0}}, {'batch_tokens': 0}):
            with self.assertRaises(ValueError):
                merge_config(DEFAULT, patch)


if __name__ == '__main__':
    unittest.main()
