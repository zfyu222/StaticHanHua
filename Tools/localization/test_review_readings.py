"""Scope, provenance and unchanged-record protection for optional readings."""
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from contextlib import closing

import test_translation_workflow as fixtures
from workflow_review_readings import load_source_readings


class ReviewReadingsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.WorkflowTests();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.flow=self.fixture.flow;self.fixture.freeze(pilot=True);self.fixture.translate()
        with self.flow.db() as db:self.rows=[dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
        self.reader=self.make_reader('Reader')

    def make_reader(self,name,backend='api',received=True,old_translation=False):
        project=self.flow.project/name;project.mkdir()
        source=project/'source.csv'
        rows=[r for r in self.rows if r['source'].strip()]
        with source.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([[r['source']] for r in rows])
        root=project/'TranslateFiles/state';root.mkdir(parents=True)
        with closing(sqlite3.connect(root/'workflow.sqlite3')) as db, db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE records(id TEXT,source TEXT,translation TEXT); '
                'CREATE TABLE tasks(id TEXT,stage TEXT,backend TEXT,status TEXT,result TEXT,payload TEXT,input_hash TEXT); '
                'CREATE TABLE api_calls(task_id TEXT,status TEXT);')
            sha=hashlib.sha256(source.read_bytes()).hexdigest()
            db.executemany('INSERT INTO meta VALUES (?,?)',[(k,json.dumps(v)) for k,v in {'source_path':str(source),'source_hash':sha}.items()])
            inputs=[];outputs=[]
            for i,row in enumerate(rows):
                sid='side'+str(i);reading='独立候选：'+row['translation']
                db.execute('INSERT INTO records VALUES (?,?,?)',(sid,row['source'],reading))
                inputs.append(dict(id=sid,source=row['source'],translation='旧译' if old_translation else None,protection=[]))
                outputs.append(dict(id=sid,translation=reading))
            payload=dict(source_hash=sha,records=inputs,context=[])
            result=dict(input_hash='h',records=outputs)
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',('pilot','pilot_translate',backend,'done',json.dumps(result),json.dumps(payload),'h'))
            if received:db.execute('INSERT INTO api_calls VALUES (?,?)',('pilot','received'))
        return project

    def test_explicit_import_sends_no_requests_and_changes_no_translation(self):
        self.flow.pause(True);rid=self.rows[0]['id']
        with patch('ainiee_bridge.AINieeAPI',side_effect=AssertionError('Import must not call API')):
            result=self.flow.import_review_readings(self.reader,[rid],'Check doubtful word sense')
        self.assertEqual(result['affected_records'],[rid]);self.assertEqual(result['api_requests'],0)
        with self.flow.db() as db:
            self.assertEqual([dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')],self.rows)
            self.assertEqual(set(self.flow.meta(db,'review_readings')),{rid})
        self.assertEqual(self.flow.import_review_readings(self.reader,[rid],'Repeat')['status'],'unchanged')

    def test_api_and_subagent_inputs_include_only_selected_fallible_reading(self):
        rid=self.rows[0]['id'];self.flow.pause(True)
        self.flow.import_review_readings(self.reader,[rid],'Compare meaning');self.flow.pause(False)
        self.flow.prepare('review');c=self.flow.claim('review','fresh-review-context')
        self.assertIn('independent_source_reading',c['payload']['records'][0])
        for api in (False,True):
            with self.flow.db() as db:
                text=self.flow.render_batch_input(db,c['payload'],[r['id'] for r in c['payload']['records']],self.flow.root/'out.json',api=api)
            rows=fixtures.model_input(text)['records']
            self.assertIn('independent_source_reading',rows[0]);self.assertNotIn('proof',rows[0])
            self.assertNotIn('independent_source_reading',rows[1])
        self.flow.pause(True)
        changed=self.make_reader('NewReader')
        with closing(sqlite3.connect(changed/'TranslateFiles/state/workflow.sqlite3')) as db, db:
            db.execute('UPDATE records SET translation=translation||"新"')
            result=json.loads(db.execute('SELECT result FROM tasks').fetchone()[0])
            for r in result['records']:r['translation']+='新'
            db.execute('UPDATE tasks SET result=?',(json.dumps(result),))
        with self.assertRaisesRegex(ValueError,'outstanding'):
            self.flow.import_review_readings(changed,[rid],'Cannot change in-flight input')

    def test_completed_review_reopens_only_requested_ids_and_preserves_attempts(self):
        self.fixture.review();rid=self.rows[0]['id'];self.flow.pause(True)
        with self.flow.db() as db:
            prior=[tuple(r) for r in db.execute('SELECT id,attempt FROM tasks ORDER BY id')]
        result=self.flow.import_review_readings(self.reader,[rid],'Independent interpretation differs')
        self.assertTrue(result['repair_tasks']);self.assertEqual(result['preserved_records'],3)
        with self.flow.db() as db:
            after=[dict(r) for r in db.execute('SELECT * FROM records ORDER BY ordinal')]
            self.assertEqual(after[0]['stage'],'translate')
            self.assertTrue(all(r['stage']=='review' for r in after[1:]))
            self.assertEqual([r['translation'] for r in after],[r['translation'] for r in self.rows])
            self.assertEqual(prior,[tuple(db.execute('SELECT id,attempt FROM tasks WHERE id=?',(t[0],)).fetchone()) for t in prior])
            self.assertNotIn('final',self.flow.meta(db,'quality_approvals',{}))

    def test_requires_pause_explicit_ids_matching_sources_and_unlocked_records(self):
        rid=self.rows[0]['id']
        with self.assertRaisesRegex(ValueError,'Pause'):self.flow.import_review_readings(self.reader,[rid],'reason')
        self.flow.pause(True)
        for ids in ([],[rid,rid],['missing'],[{}]):
            with self.assertRaises(ValueError):self.flow.import_review_readings(self.reader,ids,'reason')
        with self.assertRaisesRegex(ValueError,'separate'):self.flow.import_review_readings(self.flow.project,[rid],'reason')
        self.flow.lock(rid,True,'confirmed')
        with self.assertRaisesRegex(ValueError,'Unlock'):self.flow.import_review_readings(self.reader,[rid],'reason')

    def test_rejects_old_translation_and_non_api_or_unissued_source_proof(self):
        for name,kwargs in [('Old',dict(old_translation=True)),('SubAgent',dict(backend='subagent')),('NoCall',dict(received=False))]:
            with self.subTest(name=name),self.assertRaises(ValueError):load_source_readings(self.make_reader(name,**kwargs))

    def test_rejects_source_file_and_saved_reading_tampering(self):
        with (self.reader/'source.csv').open('a',encoding='utf-8') as f:f.write('changed\n')
        with self.assertRaisesRegex(ValueError,'source file changed'):load_source_readings(self.reader)
        other=self.make_reader('Other')
        with closing(sqlite3.connect(other/'TranslateFiles/state/workflow.sqlite3')) as db, db:db.execute('UPDATE records SET translation="changed"')
        with self.assertRaisesRegex(ValueError,'no longer matches'):load_source_readings(other)

    def test_repair_budget_stop_rolls_back_import(self):
        self.fixture.review();self.flow.pause(True);rid=self.rows[0]['id']
        with self.flow.db() as db:
            before=[tuple(r) for r in db.execute('SELECT id,attempt,status FROM tasks ORDER BY id')]
        with patch.object(self.flow,'repair_routes',return_value={rid:(9,'stopped')}):
            with self.assertRaisesRegex(ValueError,'repair limits'):
                self.flow.import_review_readings(self.reader,[rid],'No budget reset')
        with self.flow.db() as db:
            self.assertEqual(self.flow.meta(db,'review_readings',{}),{})
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT id,attempt,status FROM tasks ORDER BY id')])
            self.assertEqual(db.execute('SELECT stage FROM records WHERE id=?',(rid,)).fetchone()[0],'review')

    def test_stale_reading_binding_and_hidden_metadata_reference_rejected(self):
        rid=self.rows[0]['id'];self.flow.pause(True)
        self.flow.import_review_readings(self.reader,[rid],'Bound to exact source')
        with self.flow.db() as db:
            readings=self.flow.meta(db,'review_readings');readings[rid]['source_sha256']='stale'
            self.flow.put(db,'review_readings',readings)
        self.flow.pause(False)
        with self.assertRaisesRegex(ValueError,'Stale independent'):self.flow.prepare('review')
        other=self.make_reader('LeakedMetadata')
        with closing(sqlite3.connect(other/'TranslateFiles/state/workflow.sqlite3')) as db, db:
            payload=json.loads(db.execute('SELECT payload FROM tasks').fetchone()[0])
            payload['records'][0]['metadata']={'independent_source_reading':'Old answer'}
            db.execute('UPDATE tasks SET payload=?',(json.dumps(payload),))
        with self.assertRaisesRegex(ValueError,'existing translation'):load_source_readings(other)

    def test_protected_source_and_api_draft_are_restored_before_binding(self):
        with closing(sqlite3.connect(self.reader/'TranslateFiles/state/workflow.sqlite3')) as db, db:
            payload=json.loads(db.execute('SELECT payload FROM tasks').fetchone()[0])
            result=json.loads(db.execute('SELECT result FROM tasks').fetchone()[0])
            row=next(r for r in payload['records'] if '{0}' in r['source'])
            output=next(r for r in result['records'] if r['id']==row['id'])
            source=row['source'];expected=output['translation']
            self.assertIn('{0}',expected)
            row['source']=source.replace('{0}','[P0]');row['protection']=[{'placeholder':'[P0]','original':'{0}'}]
            output['translation']=expected.replace('{0}','[P0]')
            db.execute('UPDATE tasks SET payload=?,result=?',(json.dumps(payload),json.dumps(result)))
        self.assertEqual(load_source_readings(self.reader)[source]['reading'],expected)


if __name__=='__main__':unittest.main()
