"""Behavior checks: semantic glossary levels and nonprinting-marker wrappers."""
import copy
import unittest
import ainiee_bridge as bridge
from workflow_term_policy import validate_term_policy, term_required
from workflow_quality import validate_glossary_term_policy

class TermPolicyTests(unittest.TestCase):
    def row(self, source='Riftweave', enforcement='reference'):
        return dict(source=source, recommended_translation='裂隙织构', enforcement=enforcement,
                    term_kind='game_term', term_reason='Source describes an invented magical weave')

    def test_ordinary_word_cannot_be_hidden_in_reference(self):
        for level in ('required', 'reference'):
            with self.assertRaises(ValueError):
                validate_term_policy(self.row('user', level), 'terms', required=True)

    def test_common_class_is_rejected_even_with_scope(self):
        row=self.row(); row['term_kind']='common_word'
        with self.assertRaises(ValueError): validate_term_policy(row, 'terms', required=True)

    def test_scoped_game_attribute_is_allowed(self):
        row=self.row('Might', 'required')
        row['applicability']=dict(mode='whole_record',case_sensitive=True,reason='Named strength attribute in UI')
        validate_term_policy(row,'terms',required=True)

    def test_ordinary_word_cannot_bypass_policy_as_non_translate(self):
        with self.assertRaises(ValueError):
            validate_term_policy({'source':'user'},'non_translate',required=True)
        validate_term_policy({'source':'{user}'},'non_translate',required=True)

    def test_names_and_protected_tokens_cannot_be_downgraded(self):
        row=self.row();row['term_kind']='proper_name'
        with self.assertRaises(ValueError):validate_term_policy(row,'characters',required=True)
        with self.assertRaises(ValueError):validate_term_policy(row,'non_translate',required=True)

    def test_old_snapshot_is_not_migrated(self):
        row={'source':'user','recommended_translation':'用户'};before=copy.deepcopy(row)
        validate_term_policy(row,'terms');self.assertTrue(term_required(row));self.assertEqual(row,before)
        with self.assertRaises(ValueError):validate_term_policy(row,'terms',required=True)

    def test_review_must_classify_or_quarantine(self):
        payload={'terminology_policy':1,'entries':[dict(ref='terms:0',table='terms',row={'source':'user'})]}
        with self.assertRaises(ValueError):validate_glossary_term_policy({},payload)
        validate_glossary_term_policy({'quarantine':[{'table':'terms','index':0}]},payload)

    def test_matcher_keeps_reference_level_and_nested_required_name(self):
        glossary={'terms':[self.row('Riftweave device'),dict(self.row('Riftweave','required'))],
                  'characters':[],'non_translate':[],'terminology_policy':1}
        terms=bridge.matched_terms(glossary,['Riftweave device'],{'case_sensitive':False,'whole_word':True})
        self.assertEqual({t['src'] for t in terms},{'Riftweave','Riftweave device'})
        self.assertEqual([t['src'] for t in terms if term_required(t)],['Riftweave'])
        self.assertTrue(any(not term_required(t) for t in terms))

    def test_reference_does_not_trigger_a_body_mismatch_but_required_does(self):
        from translation_workflow import Workflow
        flow=Workflow.__new__(Workflow)
        row=dict(id='r1',source='Riftweave',translation='',locked=False,exceptions='[]')
        task=dict(stage='translate',payload='{"strict_translation":true}')
        config=dict(case_sensitive=True,whole_word=True,record_constraints={})
        term=dict(src='Riftweave',dst='裂隙织构',enforcement='reference')
        args=(row,task,{},dict(id='r1',translation='异象'),dict(protection=[]),config,[],{})
        self.assertEqual(flow.validate_body_row(*args,terms=[term])[0],'异象')
        with self.assertRaisesRegex(ValueError,'terminology mismatch'):
            flow.validate_body_row(*args,terms=[dict(term,enforcement='required')])

    def test_reference_cannot_hide_a_marker_wrapper_failure(self):
        from translation_workflow import Workflow
        flow=Workflow.__new__(Workflow)
        row=dict(id='r1',source='Child {Female}',translation='',locked=False,exceptions='[]')
        task=dict(stage='translate',payload='{"strict_translation":true}')
        config=dict(case_sensitive=True,whole_word=True,record_constraints={})
        with self.assertRaisesRegex(ValueError,'hidden_marker_wrapper_added'):
            flow.validate_body_row(row,task,{},dict(id='r1',translation='孩童（{Female}）'),
                                   dict(protection=[]),config,[],{},terms=[])

    def test_reference_is_not_a_satisfied_or_missing_repair_constraint(self):
        from translation_workflow import Workflow
        flow=Workflow.__new__(Workflow)
        glossary={'terms':[self.row()],'characters':[],'non_translate':[]}
        r=flow.repair_constraints('Riftweave','异象',[],glossary,dict(case_sensitive=True,whole_word=True),rules=[])
        self.assertEqual(r['satisfied_terms'],[])
        self.assertEqual(r['missing_terms'],[])

    def test_real_missing_quote_replies_remain_rejected(self):
        import json
        from pathlib import Path
        from workflow_quality import validate_review
        fixtures=json.loads((Path(__file__).parent/'test_fixtures/term_policy_live_citation.json').read_text(encoding='utf-8'))
        rejected=[x for x in fixtures if not x['accepted']]
        self.assertEqual(len(rejected),2)
        for x in rejected:
            with self.assertRaisesRegex(ValueError,'source_excerpt'):
                validate_review(x['item'],x['source'],x['before'],x['item']['translation'])

    def test_repair_keep_cannot_restore_wrong_original_when_candidate_differs(self):
        from workflow_api_transport import restore_body_result
        transport=dict(stage='review',original_translations={'r1':'门锁上了。'},require_explicit_translation=['r1'])
        with self.assertRaisesRegex(ValueError,'Original and unaccepted candidate differ'):
            restore_body_result({'records':[dict(id='r1',keep=True,changes=[])]},transport)
        valid={'records':[dict(id='r1',translation='门没有上锁。',changes=[])]}
        restore_body_result(valid,transport)
        self.assertEqual(valid['records'][0]['translation'],'门没有上锁。')
        # Existing persisted handoffs keep the old contract, without guessed migration.
        old=dict(transport);old.pop('require_explicit_translation')
        value={'records':[dict(id='r1',keep=True,changes=[])]}
        restore_body_result(value,old)
        self.assertEqual(value['records'][0]['translation'],'门锁上了。')

    def test_marker_wrapper_is_blocked_but_visible_values_are_not(self):
        self.assertIn('hidden_marker_wrapper_added',bridge.structural_issues('Child {Female}','孩童（{Female}）',[]))
        self.assertEqual(bridge.structural_issues('Child {Female}','孩童 {Female}',[]),[])
        self.assertEqual(bridge.structural_issues('Child ({Female})','孩童（{Female}）',[]),[])
        self.assertEqual(bridge.structural_issues('Price {0}','价格（{0}）',[]),[])

if __name__=='__main__':unittest.main()
