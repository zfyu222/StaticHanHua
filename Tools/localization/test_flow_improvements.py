"""Protocol, partial collection and executor isolation; no external API calls."""
import copy
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import translation_workflow as w
import test_translation_workflow as fixtures
from workflow_api_reliability import parse_json_reply
from workflow_task_contract import api_id_transport, canonical_api_result, canonical_api_partial_result
from workflow_quality import REVIEW_CHECKS, validate_review


class WireTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'stage':'review','task_id':'task','input_hash':'hash','records':[
            {'id':'r1','source':'[i]Rest here.[/i]','translation':'[i]在此休息。[/i]'},
            {'id':'r2','source':'Open the gate.','translation':'打开门。'}]}
        self.scope=['r1','r2']; self.transport=api_id_transport(self.payload,self.scope,'session')
        self.transport['record_terms']={'r1':{},'r2':{}}
        self.transport['record_exceptions']={'r1':{'x1':'are'},'r2':{'x1':'His'}}

    def decode(self, rows, partial=False, transport=None):
        fn=canonical_api_partial_result if partial else canonical_api_result
        return fn({'records':rows},transport or self.transport,'session','task','hash',self.scope)

    def row(self, local='1'):
        return {'id':local,'keep':True,'source_ref':'whole','reason':'逐项对照本条原文。',
                'checks':{name:['pass','本维度已对照原句及译文。'] for name in REVIEW_CHECKS},
                'changes':[],'issues':[]}

    def test_check_pairs_and_whole_source_restore_without_semantic_fill(self):
        row=self.decode([self.row()])['records'][0]
        self.assertEqual(row['source_excerpt'],'[i]Rest here.[/i]')
        self.assertEqual(row['translation'],'[i]在此休息。[/i]')
        self.assertEqual(row['checks']['accuracy'],{'status':'pass','evidence':'本维度已对照原句及译文。'})
        validate_review(row,self.payload['records'][0]['source'],row['translation'],row['translation'])
        bad=self.row();bad['checks']['logic']=['not_applicable','没有数字。']
        row=self.decode([bad])['records'][0]
        with self.assertRaisesRegex(ValueError,'cannot be skipped'):
            validate_review(row,self.payload['records'][0]['source'],row['translation'],row['translation'])

    def test_empty_or_malformed_pair_never_becomes_evidence(self):
        for pair in (['pass'],['pass','', 'extra'],['pass',None]):
            row=self.row();row['checks']['accuracy']=pair
            with self.subTest(pair=pair),self.assertRaises(ValueError):self.decode([row])
        row=self.row();row['checks']['accuracy']=['pass','']
        result=self.decode([row])['records'][0]
        with self.assertRaises(ValueError):validate_review(result,self.payload['records'][0]['source'],result['translation'],result['translation'])

    def test_unmatched_existing_exception_can_be_removed_with_bound_reason(self):
        row=self.row();row['exception_changes']=[{'exception_ref':'x1','action':'remove','reason':'本句 Rest here 没有 are。'}]
        decoded=self.decode([row])['records'][0]
        self.assertEqual(decoded['exception_changes'],[{'source':'are','action':'remove','reason':'本句 Rest here 没有 are。'}])
        for changes in ([{'exception_ref':'x2','action':'remove','reason':'未知'}],
                        [{'exception_ref':'x1','action':'remove','reason':''}],
                        [{'exception_ref':'x1','action':'remove','reason':'重复'}]*2,
                        [{'exception_ref':True,'action':'remove','reason':'错误类型'}]):
            row=self.row();row['exception_changes']=changes
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.decode([row])
        row=self.row();row['exceptions']=[{'exception_ref':'x1','reason':'不能拿撤销编号新增。'}]
        with self.assertRaises(ValueError):self.decode([row])

    def test_citation_conflict_and_unknown_binding_are_rejected(self):
        row=self.row();row['source_excerpt']=''
        self.assertEqual(self.decode([row])['records'][0]['source_excerpt'],'[i]Rest here.[/i]')
        for update in ({'source_excerpt':'Rest here.'},{'source_ref':'other'}):
            row=self.row();row.update(update)
            with self.subTest(update=update),self.assertRaises(ValueError):self.decode([row])
        broken=copy.deepcopy(self.transport);broken['original_sources'].pop('r2')
        with self.assertRaises(ValueError):self.decode([self.row()],transport=broken)

    def test_actual_api_whole_source_with_empty_template_excerpt(self):
        from workflow_task_contract import restore_source_references
        fixture=json.loads((Path(__file__).parent/'test_fixtures/review_whole_source_empty_excerpt.json').read_text('utf-8'))
        row=fixture['record'];self.assertEqual(row['source_ref'],'whole');self.assertEqual(row['source_excerpt'],'')
        restored=restore_source_references(row,fixture['bound_source'])
        self.assertEqual(restored['source_excerpt'],fixture['bound_source'])
        self.assertEqual(restored['checks'],row['checks'])
        self.assertEqual(restored['changes'],row['changes'])
        self.assertNotIn('source_ref',restored)

    def test_actual_glossary_reply_with_missing_brace_stays_rejected(self):
        raw=(Path(__file__).parent/'test_fixtures/glossary_missing_check_brace.raw.txt').read_text('utf-8')
        with self.assertRaises(ValueError):parse_json_reply(raw)

    def test_partial_reply_preserves_good_row_but_never_guesses_translation(self):
        bad={'id':'2','reason':'Forgot explicit decision.'}
        result=self.decode([self.row(),bad],partial=True)
        self.assertEqual(result['records'][0]['translation'],'[i]在此休息。[/i]')
        failed=result['records'][1]
        self.assertEqual(failed['id'],'r2');self.assertIn('_format_error',failed)
        self.assertNotIn('translation',failed);self.assertEqual(failed['_raw_api_item'],bad)
        for rows in ([self.row(),self.row()],[self.row(),{'id':'99'}]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):self.decode(rows,partial=True)

    def test_json_duplicates_missing_values_and_content_changes_are_rejected(self):
        for raw in ('{"a":1,"a":2}','{"checks":{"logic":{},"logic":null}}','[,]','{"a":,}', '{"a":1', '{"a":"unterminated}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_json_reply(raw)
        with tempfile.TemporaryDirectory() as folder:
            audit=Path(folder)/'audit.json'; raw='```json\n{"text":"keep ,} exactly", "items":[1,],}\n```'
            self.assertEqual(parse_json_reply(raw,audit),{'text':'keep ,} exactly','items':[1]})
            data=json.loads(audit.read_text(encoding='utf-8'))
            self.assertEqual(len(data['edits']),2);self.assertFalse(data['semantic_fields_added'])


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.WorkflowTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.freeze(True);self.f.translate();self.flow=self.f.flow

    def result(self, session):
        with self.flow.db() as db:
            run=self.flow._batch_run(db,session['session_id']);scope=set(json.loads(run['scope']))
            task=db.execute('SELECT * FROM tasks WHERE id=?',(run['task_id'],)).fetchone()
        rows=json.loads(task['payload'])['records']
        return {'task_id':task['id'],'input_hash':task['input_hash'],'records':[
            {'id':r['id'],'translation':r['translation'],'reviewed':True,'reason':'逐条对照。',**self.f.review_evidence(r)}
            for r in rows if r['id'] in scope]}

    def save(self,session,result):
        w.write_json(session['result_file'],result);return self.flow.batch_finish(session['session_id'])

    def test_new_envelope_repairs_only_bad_id_and_preserves_authors(self):
        session=self.flow.batch_open('review','initial-reviewer');value=self.result(session)
        bad,good=[r['id'] for r in value['records']]
        value['records'][0]['checks']['logic']['status']='not_applicable'
        receipt=self.save(session,value)
        self.assertEqual(receipt['failed_ids'],[bad]);self.assertEqual(receipt['preserved_records'],1)
        self.assertEqual(self.flow.review_scan()['checked_records'],0)
        repair=self.flow.batch_open('review','local-format-reviewer')
        self.assertEqual(repair['records'],1)
        self.assertEqual(self.save(repair,self.result(repair))['status'],'submitted')
        with self.flow.db() as db:
            authors=dict(db.execute('SELECT record_id,instance FROM batch_items WHERE task_id=?',(session['task_id'],)))
            self.assertEqual(authors[bad],'local-format-reviewer');self.assertEqual(authors[good],'initial-reviewer')
            self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE id=?',(session['task_id'],)).fetchone()[0],2)
        second=self.flow.batch_open('review','second-reviewer');self.save(second,self.result(second))
        self.assertTrue(self.flow.review_scan()['clean'])

    def test_blocking_remains_a_round_quality_failure(self):
        first=self.flow.batch_open('review','blocking-reviewer');value=self.result(first)
        value['records'][0]['issues']=[{'severity':'blocking','reason':'未解决的具体含义歧义。'}]
        self.assertEqual(self.save(first,value)['status'],'submitted')
        second=self.flow.batch_open('review','other-reviewer');self.save(second,self.result(second))
        scan=self.flow.review_scan(prepare_repairs=True)
        self.assertEqual(scan['failed_records'],1)
        self.assertEqual(scan['failure_summary']['checked_failures'],{'reported_blocking_semantic_issue':1})


class ExecutorIsolationTests(unittest.TestCase):
    def test_exhausted_handoff_does_not_consume_the_only_worker_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);source=folder/'sample_text.csv'
            with source.open('w',encoding='utf-8',newline='') as stream:
                csv.writer(stream).writerows([[f'Synthetic text {i}.'] for i in range(4)])
            with patch.object(w,'API_CONFIG_FILE',folder/'absent.local.jsonc'):
                flow=w.Workflow(folder);flow.init(source,{'backend':'api','batch_records':1,
                    'api':{'api_url':'https://example.invalid/v1','model_name':'mock','concurrency':1,'max_requests':10}})
                calls=[]
                class Mock:
                    def request(self,system,user):
                        data=fixtures.model_input(user);ordinal=data['records'][0]['ordinal'];calls.append(ordinal)
                        reply='{"analysis":' if ordinal==1 else json.dumps({'analysis':{'characters':[],'terms':[],'non_translate':[]}})
                        return reply,{'prompt_tokens':1,'completion_tokens':1,'usage_reported':True,'finish_reason':'stop'}
                    def close(self):pass
                with patch.dict(os.environ,{'SHH_TRANSLATION_API_KEY':'mock-only'}):
                    result=w.run_api(flow,'extract',lambda *_:Mock())
                self.assertEqual(result['accepted'],3);self.assertEqual(result['failed'],2)
                self.assertEqual(calls.count(1),2);self.assertEqual(set(calls),{1,2,3,4})
                self.assertEqual(len(result['pending_handoffs']),1)
                with flow.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE status="pending"').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE status="running"').fetchone()[0],2)


if __name__=='__main__':unittest.main()
