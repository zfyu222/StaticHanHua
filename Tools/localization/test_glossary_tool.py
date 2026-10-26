"""Run: python -m unittest discover -s Tools/localization -p test_glossary_tool.py"""
import copy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import glossary_tool as tool


class GlossaryTests(unittest.TestCase):
    def test_only_provably_disjoint_scoped_terms_can_share_source(self):
        def entry(mode, **context):
            return dict(source='Corruption', recommended_translation='译名', category_path='其他',
                        applicability=dict(mode=mode, case_sensitive=True, reason='Actual source context', **context))
        label = entry('whole_record')
        region = entry('literal_context', prefix='the ', suffix='.')
        def collisions(a, b):
            return [f for f in tool.findings(dict(characters=[], terms=[a, b], non_translate=[]),
                                            ['Corruption', 'the Corruption.']) if f['code'] == 'duplicate_source']
        self.assertFalse(collisions(label, region))
        self.assertTrue(collisions(label, label))
        self.assertTrue(collisions(entry('text'), region))
        self.assertTrue(collisions(label, entry('literal_context', prefix=' ', suffix=' ')))
        self.assertTrue(collisions(region, region))
        self.assertFalse(collisions(entry('literal_context', suffix=')'),
                                    entry('literal_context', suffix=':')))
        self.assertTrue(collisions(entry('literal_context', suffix=':'),
                                   entry('literal_context', suffix=': Works')))
        self.assertFalse(collisions(entry('literal_context', prefix='like '),
                                    entry('literal_context', prefix='named ')))
        self.assertTrue(collisions(entry('literal_context', prefix='the '),
                                   entry('literal_context', prefix='of the ')))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache.json'
        self.source = self.root / 'source.csv'
        with self.source.open('w', encoding='utf-8-sig', newline='') as handle:
            csv.writer(handle).writerows([['Anna carries the Sword.\nA multiline record.'], ['The Sword shines.']])
        self.original = dict(version=1, status='success', last_run_at='preserved', characters=[
            dict(source='Anna', recommended_translation='安娜', gender='女性')], terms=[
            dict(source='Sword', recommended_translation='背剑', category_path='物品'),
            dict(source='ExampleOnly', recommended_translation='', category_path='物品')], non_translate=[])
        self.runtime = dict(extra=dict(analysis_v1=self.original, unrelated='preserve'),
                            files=[dict(translation='existing progress')], private='must-not-export')
        tool.write_json(self.cache, self.runtime)

    def prepare(self):
        output = self.root / 'audit'
        report = tool.audit(self.cache, self.source, output)
        decisions_path = output / 'glossary_decisions.json'
        decisions = tool.read_json(decisions_path)
        decisions.update(review_complete=True, review_summary='Review names and weapon context.',
                         acknowledged_findings=[x['id'] for x in report['findings']],
                         edits=[dict(table='terms', index=0, patch=dict(recommended_translation='剑'),
                                     reason='Weapon description', evidence_rows=[1])],
                         quarantine=[dict(table='terms', index=1, reason='Example not in game source')])
        tool.write_json(decisions_path, decisions)
        return output, decisions_path

    def test_audit_private_data_and_context(self):
        output, _ = self.prepare()
        self.assertNotIn('must-not-export', ''.join(path.read_text(encoding='utf-8') for path in output.iterdir()))
        contexts = tool.read_json(output / 'glossary_contexts.json')
        self.assertEqual(contexts[1]['evidence'][1]['row'], 2)
        self.assertIn('\n', contexts[0]['evidence'][0]['excerpt'])
        with self.assertRaises(ValueError):
            tool.audit(self.cache, self.source, output)

    def test_build_preserves_metadata_and_quarantine(self):
        output, decisions = self.prepare()
        built = self.root / 'built'
        stats = tool.build(output / 'glossary_original.json', self.source, decisions, built)
        revised = tool.read_json(built / 'glossary_reviewed.json')
        self.assertEqual(stats['total_hits'], 2)
        self.assertEqual(revised['last_run_at'], 'preserved')
        self.assertEqual(revised['terms'][0]['recommended_translation'], '剑')
        log = tool.read_json(built / 'glossary_review_changes.json')
        self.assertEqual(log['changes'][1]['before']['source'], 'ExampleOnly')

    def test_incomplete_decisions_and_missing_evidence_block_build(self):
        output, path = self.prepare()
        decisions = tool.read_json(path)
        for field, value in [('review_complete', False), ('acknowledged_findings', [])]:
            changed = copy.deepcopy(decisions)
            changed[field] = value
            tool.write_json(path, changed)
            with self.assertRaises(ValueError):
                tool.build(output / 'glossary_original.json', self.source, path, self.root / 'built')
        decisions['edits'][0]['evidence_rows'] = []
        tool.write_json(path, decisions)
        with self.assertRaises(ValueError):
            tool.build(output / 'glossary_original.json', self.source, path, self.root / 'built')

    def test_source_change_and_unresolved_collision(self):
        output, path = self.prepare()
        decisions = tool.read_json(path)
        decisions['additions'] = [dict(table='terms', row=dict(source='ANNA', recommended_translation='安娜', category_path='身份'),
                                      reason='Duplicate alias', evidence_rows=[1])]
        tool.write_json(path, decisions)
        with self.assertRaises(ValueError):
            tool.build(output / 'glossary_original.json', self.source, path, self.root / 'built')
        self.source.write_text('Changed\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            tool.build(output / 'glossary_original.json', self.source, path, self.root / 'built')

    def revised(self):
        revised = copy.deepcopy(self.original)
        revised['terms'] = [dict(source='Sword', recommended_translation='剑', category_path='物品')]
        tool.refresh_stats(revised)
        return revised

    def test_dry_run_running_guard_and_preserved_runtime(self):
        revised = self.revised()
        raw = self.cache.read_bytes()
        self.assertEqual(tool.apply_analysis(self.cache, self.original, revised)['status'], 'checked')
        self.assertEqual(self.cache.read_bytes(), raw)
        with patch.object(tool, 'ainiee_running', return_value=True):
            with self.assertRaises(ValueError):
                tool.apply_analysis(self.cache, self.original, revised, True)
        self.assertEqual(self.cache.read_bytes(), raw)
        with patch.object(tool, 'ainiee_running', return_value=False):
            result = tool.apply_analysis(self.cache, self.original, revised, True)
        self.assertEqual(Path(result['backup']).read_bytes(), raw)
        expected = copy.deepcopy(self.runtime)
        expected['extra']['analysis_v1'] = revised
        self.assertEqual(tool.read_json(self.cache), expected)
        self.assertEqual(tool.apply_analysis(self.cache, self.original, revised, True)['status'], 'already_installed')

    def test_stale_cache_is_never_overwritten(self):
        changed = copy.deepcopy(self.runtime)
        changed['extra']['analysis_v1']['terms'][0]['recommended_translation'] = '用户新改动'
        tool.write_json(self.cache, changed)
        raw = self.cache.read_bytes()
        with self.assertRaises(ValueError):
            tool.apply_analysis(self.cache, self.original, self.revised(), True)
        self.assertEqual(self.cache.read_bytes(), raw)

    def test_manifest_detects_tampering_via_cli(self):
        output, decisions = self.prepare()
        built = self.root / 'built'
        tool.build(output / 'glossary_original.json', self.source, decisions, built)
        reviewed = built / 'glossary_reviewed.json'
        reviewed.write_text(reviewed.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        raw = self.cache.read_bytes()
        result = subprocess.run([sys.executable, str(Path(tool.__file__)), 'apply', '--cache', str(self.cache),
                                 '--snapshot', str(output / 'glossary_original.json'), '--reviewed', str(reviewed),
                                 '--manifest', str(built / 'glossary_manifest.json'), '--source', str(self.source),
                                 '--decisions', str(decisions), '--apply'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Artifact changed', result.stderr)
        self.assertEqual(self.cache.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
