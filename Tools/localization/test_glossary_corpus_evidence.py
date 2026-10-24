"""Source-only regression: source definitions must reach both review routes."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from glossary_corpus_evidence import attach_corpus_evidence
from glossary_merge import merge_extractions, review_evidence_rows
from workflow_api_transport import prune_unreadable_evidence
from workflow_quality import validate_glossary_reviews
from workflow_task_contract import api_protocol_data
import translation_workflow as w


class CorpusEvidenceTests(unittest.TestCase):
    definition = 'A set of bonuses granted by the Hardened descriptor.'

    def analysis(self):
        return {'characters': [], 'terms': [{'source':'Hardened', 'recommended_translation':'硬化',
            'category_path':'其他', 'evidence_rows':[1]}], 'non_translate': []}

    def test_remote_definition_beats_physical_homonyms_without_neighbour_inference(self):
        a=self.analysis()
        records=[dict(ordinal=1,source='Hardened'), dict(ordinal=2,source='A hard-boiled mercenary.'),
            dict(ordinal=50,source='This spell leaves hardened clay.'),
            dict(ordinal=57530,source=self.definition)]
        attach_corpus_evidence(a,records,limit=1)
        self.assertEqual(a['terms'][0]['corpus_evidence_rows'],[57530])
        self.assertEqual(review_evidence_rows(a['terms'][0]),[1,57530])
        self.assertEqual(a['terms'][0]['recommended_translation'],'硬化')  # Retrieval cannot approve/correct meaning.

    def test_word_boundaries_no_definition_and_bounded_complete_records(self):
        a=self.analysis()
        records=[dict(ordinal=10,source='Unhardened descriptor'), dict(ordinal=11,source='Hardened skin.'),
            *[dict(ordinal=i,source=self.definition+('!'*(i-20))) for i in range(20,24)]]
        stats=attach_corpus_evidence(a,records)
        self.assertEqual(a['terms'][0]['corpus_evidence_rows'],[20,21])
        self.assertEqual(stats['attached_record_links'],2)
        attach_corpus_evidence(a,[dict(ordinal=11,source='Hardened skin.')])
        self.assertNotIn('corpus_evidence_rows',a['terms'][0])

    def test_one_role_hop_stays_within_two_complete_records(self):
        a=self.analysis()
        records=[dict(ordinal=1,source='Hardened'),dict(ordinal=3,source=self.definition),
            dict(ordinal=4,source='Your Descriptor provides additional modifiers to your starting Stats and Skills.'),
            dict(ordinal=5,source='Change Character Descriptor'),
            dict(ordinal=6,source='This spell leaves hardened clay.')]
        attach_corpus_evidence(a,records)
        self.assertEqual(a['terms'][0]['corpus_evidence_rows'],[3,4])
        self.assertEqual(len(review_evidence_rows(a['terms'][0])),3)

    def test_model_cannot_supply_corpus_links_to_audit(self):
        a=self.analysis();a['terms'][0]['corpus_evidence_rows']=[999]
        merged=merge_extractions([('fixture',a)])
        self.assertNotIn('corpus_evidence_rows',merged['terms'][0])
        self.assertNotIn('corpus_evidence_rows',merged['terms'][0]['extraction_candidates'][0]['row'])

    def test_already_shown_definition_still_gets_role_context_and_unshown_definition_is_not_lost(self):
        records=[dict(ordinal=1,source='Hardened'),dict(ordinal=2,source='hardened skin'),
                 dict(ordinal=3,source=self.definition),dict(ordinal=4,source='Your Descriptor provides modifiers to your Stats.')]
        for evidence,expected in (([1,3],[4]),([1,2,3],[3,4])):
            a=self.analysis();a['terms'][0]['evidence_rows']=evidence
            attach_corpus_evidence(a,records)
            self.assertEqual(a['terms'][0]['corpus_evidence_rows'],expected)

    def test_only_attached_own_definition_can_be_cited(self):
        row=self.analysis()['terms'][0];row['corpus_evidence_rows']=[57530,999]
        p={'entries':[dict(ref='terms:0',table='terms',index=0,row=row)],
           'records':[dict(ordinal=1,source='Hardened'),dict(ordinal=57530,source=self.definition),
                      dict(ordinal=3,source='A different descriptor.')]}
        item={'ref':'terms:0','evidence_rows':[57530],'source_excerpt':self.definition,
              'reason':'Full source defines this as a descriptor granting bonuses.',
              'checks':{k:dict(status='pass',evidence='Source definition examined.')
                        for k in ('source','translation','category','consistency')}}
        validate_glossary_reviews({'entry_reviews':[item]},p)
        for n in (3,999):
            with self.assertRaisesRegex(ValueError,'invalid_or_unprovided_rows'):
                validate_glossary_reviews({'entry_reviews':[dict(item,evidence_rows=[n])]},p)
        wire=prune_unreadable_evidence(copy.deepcopy(p))
        self.assertEqual(wire['entries'][0]['row']['corpus_evidence_rows'],[57530])

    def test_real_correct_sense_with_invalid_scope_is_rejected_with_actionable_diagnostic(self):
        fixture=json.loads((Path(__file__).parent/'test_fixtures/source_only_glossary_scope.json').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError,r'Hardened.*whole_record.*literal_context.*case_sensitive text is still global'):
            validate_glossary_reviews(fixture['decisions'],fixture['payload'])

    def test_real_extra_completion_note_is_not_silently_accepted(self):
        from workflow_api_reliability import json_response_text
        raw=(Path(__file__).parent/'test_fixtures/source_only_glossary_trailing.txt').read_text(encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(json_response_text(raw))

    def test_real_two_object_review_is_not_joined_into_a_fabricated_complete_reply(self):
        from workflow_api_reliability import json_response_text
        raw=(Path(__file__).parent/'test_fixtures/source_only_review_concatenated.txt').read_text(encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(json_response_text(raw))

    def test_scoped_definition_does_not_hide_reviewed_meaning_from_a_standalone_label(self):
        import ainiee_bridge as bridge
        glossary={'characters':[],'non_translate':[],'terms':[dict(source='Hardened',recommended_translation='坚韧',
            term_kind='game_term',enforcement='reference',term_reason='Character descriptor affecting starting Stats and Skills.',
            applicability=dict(mode='literal_context',case_sensitive=True,prefix='the ',suffix=' descriptor',reason='Exact defining phrase'))]}
        options=dict(case_sensitive=True,whole_word=True)
        self.assertEqual(bridge.matched_terms(glossary,['Hardened'],options,rules=[]),[])
        hints=bridge.label_meaning_hints(glossary,'Hardened',options)
        self.assertEqual(hints,[dict(source='Hardened',meaning=glossary['terms'][0]['term_reason'])])
        self.assertEqual(bridge.label_meaning_hints(glossary,'hardened clay',options),[])
        self.assertEqual(bridge.label_meaning_hints(glossary,'hardened',options),[])
        self.assertNotIn('enforcement',hints[0])

    def test_preparation_binds_definition_before_hash_and_preserves_snapshot_on_resume(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(w,'API_CONFIG_FILE',Path(temp)/'none.jsonc'):
            root=Path(temp);src=root/'cases_text.csv'
            with src.open('w',encoding='utf-8',newline='') as f:
                csv.writer(f).writerows([['Hardened'],['A physically hardened object.'],[self.definition]])
            flow=w.Workflow(root);flow.init(src,{'backend':'codex'})
            flow.prepare('extract');claim=flow.claim('extract','fixture-extractor')
            flow.submit(claim['task_id'],claim['lease_token'],dict(task_id=claim['task_id'],
                input_hash=claim['payload']['input_hash'],analysis=self.analysis()))
            flow.prepare('glossary_review')
            snapshot=flow.root/'glossary/r1/glossary_original.json'
            old_bytes=snapshot.read_bytes()
            with patch('glossary_corpus_evidence.attach_corpus_evidence',side_effect=AssertionError('Existing audit must remain stable')):
                flow.prepare('glossary_review')
            self.assertEqual(snapshot.read_bytes(),old_bytes)
            claim=flow.claim('glossary_review','independent-reviewer')
            p=claim['payload'];row=p['entries'][0]['row']
            self.assertEqual(row['corpus_evidence_rows'],[3])
            self.assertEqual({r['ordinal']:r['source'] for r in p['records']}[3],self.definition)
            self.assertEqual(p['snapshot_sha256'],w.digest(snapshot))
            wire=api_protocol_data(p)
            self.assertEqual(wire['entries'][0]['row']['corpus_evidence_rows'],[3])
            self.assertEqual(next(r for r in wire['records'] if r['ordinal']==3)['source'],self.definition)
            prompt,_=w.prompt(p,api=True)
            self.assertIn('corpus_evidence_rows',prompt)

    def test_reviewed_label_meaning_reaches_each_executor_without_turning_into_a_constraint(self):
        from test_translation_workflow import model_input
        glossary={'characters':[],'non_translate':[],'terms':[dict(source='Hardened',recommended_translation='坚韧',
            term_kind='game_term',enforcement='reference',term_reason='Character descriptor affecting starting Stats and Skills.',
            applicability=dict(mode='literal_context',case_sensitive=True,prefix='the ',suffix=' descriptor',reason='Source defining phrase'))]}
        with tempfile.TemporaryDirectory() as temp, patch.object(w,'API_CONFIG_FILE',Path(temp)/'none.jsonc'):
            root=Path(temp);src=root/'cases_text.csv';src.write_text('Hardened\n',encoding='utf-8')
            flow=w.Workflow(root);flow.init(src)
            with flow.db() as db, patch.object(flow,'glossary',return_value=glossary):
                record=dict(db.execute('SELECT * FROM records').fetchone())
                record.update(translation='硬化',metadata={},locked=False,protection=[],exceptions=[])
                payload=dict(stage='review',task_id='fixture',input_hash='a'*64,records=[record])
                for api in (False,True):
                    text=flow.render_batch_input(db,payload,[record['id']],root/'result.json',api=api)
                    row=model_input(text)['records'][0]
                    self.assertEqual(row['label_meaning_hints'],[dict(source='Hardened',meaning=glossary['terms'][0]['term_reason'])])
                    self.assertEqual(row['terms'],[])
                    self.assertNotIn('terminology_findings',row)


if __name__=='__main__':unittest.main()
