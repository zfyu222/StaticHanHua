"""Actual assisted-review replies must not fabricate missing translated text."""
import json
from pathlib import Path
import unittest

from workflow_task_contract import canonical_api_partial_result
from workflow_review_evidence import expand_evidence
from workflow_quality import validate_review


class SourceReferenceReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((Path(__file__).parent/'test_fixtures/source_reference_missing_translation.json').read_text(encoding='utf-8'))

    def restore(self,name):
        item=self.fixture[name];binding=item['binding']
        return canonical_api_partial_result(item['reply'],binding,binding['session_id'],binding['task_id'],
            binding['input_hash'],list(binding['short_to_canonical'].values()))

    def test_actual_corrected_judgments_without_full_translation_remain_rejected(self):
        binding=self.fixture['missing']['binding'];rows=self.restore('missing')['records']
        failed={r['id'] for r in rows if r.get('_format_error')}
        self.assertEqual(failed,{binding['short_to_canonical'][n] for n in ('1','2','6')})
        for row in rows:
            if row['id'] in failed:
                self.assertNotIn('translation',row)
            else:
                self.assertEqual(row['translation'],binding['original_translations'][row['id']])
        self.assertEqual(len(rows),6)  # Valid controls survive partial collection.

    def test_actual_explicit_repair_passes_without_reconstructing_text_from_its_reason(self):
        rows=self.restore('repair')['records'];self.assertEqual(len(rows),1)
        row=expand_evidence(rows[0]);binding=self.fixture['repair']['binding'];rid=row['id']
        self.assertNotIn('_format_error',row)
        self.assertNotEqual(row['translation'],binding['original_translations'][rid])
        validate_review(row,binding['original_sources'][rid],binding['original_translations'][rid],row['translation'])


if __name__=='__main__':unittest.main()
