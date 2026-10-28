"""Offline structural regressions: no API requester and no production project."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import glossary_tool
from glossary_corpus_evidence import attach_corpus_evidence
from workflow_nontranslate import (validate_nontranslate_scope, usage_witnesses,
    mixed_latin_candidates, retirement_sources, filter_retired_candidate)
from workflow_quality import validate_glossary_applicability
from translation_workflow import Workflow, fingerprint, prompt


class NontranslatePolicyTests(unittest.TestCase):
    def scope(self, mode='whole_record'):
        return dict(mode=mode, case_sensitive=True, reason='Evidence identifies this literal use, not every homonym.')

    def test_arbitrary_words_and_identifiers_need_deliberate_scope(self):
        for text in ('retrain', 'rename', 'opaque_cmd_17', 'Tab', '品牌'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'explicit applicability'):
                validate_nontranslate_scope(dict(source=text))
            validate_nontranslate_scope(dict(source=text, applicability=self.scope()))
        for marker in ('%d', '%%', '[N]', '__', "'NY'", '*.txt'):
            validate_nontranslate_scope(dict(source=marker))

    def test_full_corpus_contrast_does_not_depend_on_definition_cues(self):
        analysis=dict(characters=[], terms=[], non_translate=[dict(source='rename', evidence_rows=[1])])
        records=[dict(ordinal=1,source='rename'),dict(ordinal=2,source='Please rename this companion.'),
                 dict(ordinal=3,source='renamed')]
        before=copy.deepcopy(records)
        stats=attach_corpus_evidence(analysis, iter(records))
        self.assertEqual(analysis['non_translate'][0]['corpus_evidence_rows'],[2])
        self.assertEqual(stats['nontranslate_attached_links'],1)
        self.assertEqual(records,before)
        self.assertEqual(analysis['nontranslate_scope_policy'],1)
        self.assertEqual(usage_witnesses(dict(source='rename'),records),([1],[2]))

    def test_whole_record_protects_command_without_covering_prose(self):
        from workflow_term_scope import applicable
        row=dict(source='retrain',applicability=self.scope())
        self.assertTrue(applicable(row,' retrain ',1,8))
        self.assertFalse(applicable(row,'Please retrain.',7,14))

    def test_global_brand_or_key_requires_both_supplied_uses(self):
        row=dict(source='Tab',evidence_rows=[1],corpus_evidence_rows=[2])
        payload=dict(applicability_policy=1,nontranslate_scope_policy=1,
            entries=[dict(ref='non_translate:0',row=row)],
            records=[dict(ordinal=1,source='Tab'),dict(ordinal=2,source='Press Tab to open the map.')])
        decision=dict(edits=[dict(table='non_translate',index=0,patch=dict(applicability=self.scope('text')),evidence_rows=[1,2])],
                      entry_reviews=[dict(ref='non_translate:0',evidence_rows=[1])])
        with self.assertRaisesRegex(ValueError,'both standalone and embedded'):
            validate_glossary_applicability(decision,payload)
        decision['entry_reviews'][0]['evidence_rows']=[1,2]
        validate_glossary_applicability(decision,payload)
        # Scoped protection need not pretend that both uses are protected.
        decision['edits'][0]['patch']['applicability']=self.scope()
        decision['entry_reviews'][0]['evidence_rows']=[1]
        validate_glossary_applicability(decision,payload)

    def test_old_frozen_tables_keep_original_behavior(self):
        import ainiee_bridge as bridge
        old=dict(characters=[],terms=[],non_translate=[dict(source='retrain')])
        self.assertEqual(bridge.glossary_rows(old)[0]['src'],'retrain')
        with self.assertRaisesRegex(ValueError,'explicit applicability'):
            bridge.glossary_rows({**old,'nontranslate_scope_policy':1})

    def test_freeze_rejects_unscoped_word_even_if_finding_acknowledged(self):
        import csv
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'text.csv'
            with source.open('w',encoding='utf-8-sig',newline='') as stream:
                csv.writer(stream).writerows([['retrain'],['Please retrain.']])
            analysis=dict(characters=[],terms=[],non_translate=[dict(source='retrain',evidence_rows=[1],corpus_evidence_rows=[2])],nontranslate_scope_policy=1)
            snapshot=root/'snapshot.json';glossary_tool.write_json(snapshot,analysis)
            findings=glossary_tool.findings(analysis,['retrain','Please retrain.'])
            self.assertIn('unscoped_nontranslate',[x['code'] for x in findings])
            decision=dict(snapshot_sha256=glossary_tool.digest(snapshot),source_sha256=glossary_tool.digest(source),
                          review_complete=True,review_summary='Fixture scope review',acknowledged_findings=[x['id'] for x in findings],edits=[])
            file=root/'decision.json';glossary_tool.write_json(file,decision)
            with self.assertRaisesRegex(ValueError,'explicit applicability'):
                glossary_tool.build(snapshot,source,file,root/'bad')
            decision['edits']=[dict(table='non_translate',index=0,patch=dict(applicability=self.scope()),reason='Only literal command',evidence_rows=[1])]
            glossary_tool.write_json(file,decision)
            glossary_tool.build(snapshot,source,file,root/'good')
            self.assertEqual(glossary_tool.read_json(root/'good/glossary_reviewed.json')['non_translate'][0]['applicability']['mode'],'whole_record')

    def test_mixed_latin_is_a_candidate_even_when_protected(self):
        glossary=dict(non_translate=[dict(source='retrain'),dict(source='Tab')])
        records=[dict(id='1',ordinal=1,source='Please retrain.',translation='请 retrain。'),
                 dict(id='2',ordinal=2,source='Press Tab.',translation='按 Tab 键。'),
                 dict(id='3',ordinal=3,source='Welcome',translation='欢迎'),
                 dict(id='4',ordinal=4,source='Please go.',translation='请 go。')]
        result=mixed_latin_candidates(records,glossary)
        self.assertEqual([r['ordinal'] for r in result],[1,2,4])
        self.assertTrue(all(r['status']=='needs_semantic_review' for r in result))
        self.assertEqual(result[0]['matched_nontranslate_rules'][0]['source'],'retrain')

    def test_retirement_only_removes_previously_matched_now_absent_sources(self):
        exceptions=[dict(source='retrain',reason='Old command exception'),dict(source='Sword',reason='Valid context'),dict(source='unknown',reason='Invalid')]
        retired=retirement_sources(exceptions,[dict(src='retrain'),dict(src='Sword')],[dict(src='Sword')])
        self.assertEqual(retired,['retrain'])
        item=dict(exceptions=exceptions,translation='已审译文');before=copy.deepcopy(item)
        derived=filter_retired_candidate(item,dict(sources=retired))
        self.assertEqual([x['source'] for x in derived['exceptions']],['Sword','unknown'])
        self.assertEqual(item,before)

    def test_journal_retirement_is_bound_to_candidate_content(self):
        row=dict(id='r',source='Please retrain.',translation='请重训。',stage='translate',exceptions='[]')
        raw=dict(id='r',translation=row['translation'],exceptions=[dict(source='retrain',reason='Historical exception')])
        journal=dict(source_hash=fingerprint(row['source']),translation=row['translation'],raw_item=json.dumps(raw),protection='[]')
        row['_exception_retirement']=dict(content_hash=fingerprint([row['source'],row['translation']]),sources=['retrain'])
        item,_=Workflow.draft_candidate(row,journal)
        self.assertEqual(item['exceptions'],[])
        self.assertEqual(json.loads(journal['raw_item']),raw)
        row['_exception_retirement']['content_hash']='stale'
        self.assertEqual(Workflow.draft_candidate(row,journal)[0]['exceptions'],raw['exceptions'])

    def test_scope_instruction_is_same_for_both_execution_routes(self):
        payload=dict(stage='glossary_review',nontranslate_scope_policy=1,entries=[],records=[],task_id='fixture',input_hash='fixture')
        for api in (False,True):
            instruction,_=prompt(payload,api=api)
            self.assertIn('不能默认全局禁翻',instruction)
            self.assertIn('各引用至少一条完整原文',instruction)

    def test_revision_retires_journal_exception_without_accepting_semantics(self):
        import csv
        import translation_workflow as workflow
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'fixture_text.csv'
            with source.open('w',encoding='utf-8-sig',newline='') as stream:
                csv.writer(stream).writerows([['retrain'],['Please retrain.'],['Sword']])
            flow=Workflow(root);flow.init(source,dict(pilot_ordinals=[3],backend='subagent'))
            legacy=dict(characters=[],terms=[],non_translate=[dict(source='retrain',evidence_rows=[1,2])])
            old_path=flow.root/'glossary/legacy.json';workflow.write_json(old_path,legacy)
            with flow.db() as db:
                flow.put(db,'glossary',dict(path=str(old_path.relative_to(flow.root)),hash=workflow.digest(old_path),revision=1))
                rows=flow.quality_rows(db)
                for row,target in zip(rows,['retrain','请重训。','剑']):
                    exceptions=[dict(source='retrain',reason='Fixture ordinary meaning')] if row['ordinal']==2 else []
                    db.execute('UPDATE records SET stage="review",translation=?,exceptions=? WHERE id=?',(target,json.dumps(exceptions),row['id']))
                flow.draft_tables(db)
                original=flow.quality_rows(db)[1]
                raw=dict(id=original['id'],translation=original['translation'],exceptions=json.loads(original['exceptions']))
                db.execute('INSERT INTO draft_candidates VALUES(?,?,?,?,?,?,?)',
                    (original['id'],json.dumps(raw),'[]',original['translation'],fingerprint(original['source']),'fixture-author','fixture-task'))
            snapshot=flow.root/'glossary/revise.json';workflow.write_json(snapshot,legacy)
            decisions=dict(snapshot_sha256=workflow.digest(snapshot),source_sha256=workflow.digest(source),review_complete=True,
                review_summary='Fixture independently narrows the literal command',reviewed_refs=['non_translate:0'],
                acknowledged_findings=[],edits=[dict(table='non_translate',index=0,patch=dict(applicability=self.scope()),reason='Literal command only',evidence_rows=[1])],
                entry_reviews=[dict(ref='non_translate:0',evidence_rows=[1],source_excerpt='retrain',reason='Fixture contextual check',
                    checks={k:dict(status='pass',evidence='Fixture command source') for k in ('source','translation','category','consistency')})],
                review_instances={'non_translate:0':'fixture-independent-reviewer'})
            decision_file=flow.root/'glossary/decision.json';workflow.write_json(decision_file,decisions)
            flow.pause(True);result=flow.revise_glossary(snapshot,decision_file,'Narrow a command; no model request in this fixture')
            self.assertEqual(result['retired_context_exceptions'],[dict(id=original['id'],sources=['retrain'])])
            with flow.db() as db:
                after,journals=flow.draft_snapshot(db)
                target=next(r for r in after if r['id']==original['id'])
                self.assertEqual(target['stage'],'translate')  # Still needs independent semantic review.
                self.assertEqual(target['exceptions'],'[]')
                self.assertEqual(Workflow.draft_candidate(target,journals[target['id']])[0]['exceptions'],[])
                self.assertEqual(json.loads(journals[target['id']]['raw_item']),raw)
                self.assertEqual(next(r for r in after if r['ordinal']==3)['stage'],'review')
                _,failures=flow.collect_draft_issues(db,flow.config(db))
                self.assertEqual(failures,[])
                current=flow.glossary(db)
            # An unrelated later revision carries the proof only while the
            # term remains absent, rather than resurrecting the old exception.
            workflow.write_json(snapshot,current);decisions['snapshot_sha256']=workflow.digest(snapshot);decisions['edits']=[]
            workflow.write_json(decision_file,decisions)
            flow.revise_glossary(snapshot,decision_file,'Unchanged scope fixture')
            with flow.db() as db:
                after,_=flow.draft_snapshot(db)
                self.assertIn('_exception_retirement',next(r for r in after if r['id']==original['id']))

    def test_local_audit_does_not_modify_translations(self):
        import csv
        import translation_workflow as workflow
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'fixture_text.csv'
            with source.open('w',encoding='utf-8-sig',newline='') as stream:csv.writer(stream).writerows([['Press Tab.']])
            flow=Workflow(root);flow.init(source,dict(backend='subagent'))
            path=flow.root/'glossary/fixture.json';workflow.write_json(path,dict(characters=[],terms=[],non_translate=[dict(source='Tab')]))
            with flow.db() as db:
                flow.put(db,'glossary',dict(path=str(path.relative_to(flow.root)),hash=workflow.digest(path),revision=1))
                db.execute('UPDATE records SET translation=?',('按 Tab 键。',))
                before=[tuple(r) for r in db.execute('SELECT * FROM records')]
            report=flow.audit_mixed_language();self.assertEqual(report['candidate_records'],1)
            with flow.db() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM records')])
            self.assertFalse(workflow.read_json(report['report_file'])['semantic_approval'])
            self.assertEqual(workflow.read_json(report['report_file'])['glossary']['hash'],workflow.digest(path))


if __name__=='__main__':unittest.main()
