"""Actual API omission replies: grouping and recovery preserve valid siblings."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import translation_workflow as w
from workflow_task_contract import canonical_api_partial_result
from workflow_api_reliability import groupable_translation_omissions


class OmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        p=patch.object(w,'API_CONFIG_FILE',self.root/'none.jsonc');p.start();self.addCleanup(p.stop)
        self.fixture=json.loads((Path(__file__).parent/'test_fixtures/source_reference_missing_translation.json').read_text(encoding='utf-8'))
        b=self.fixture['missing']['binding'];ids=[b['short_to_canonical'][str(i)] for i in range(1,7)]
        self.sources=[b['original_sources'][i] for i in ids];self.translations=[b['original_translations'][i] for i in ids]
        source=self.root/'six_text.csv'
        with source.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([[s] for s in self.sources])
        self.flow=w.Workflow(self.root);self.flow.init(source,dict(pilot_records=6,batch_tokens=4096,review_batch_tokens=4096))
        self.flow.prepare('extract')
        while c:=self.flow.claim('extract','fixture-'+uuid.uuid4().hex):
            self.submit(c,analysis=dict(characters=[],terms=[],non_translate=[]))
        self.flow.prepare('glossary_review')
        while c:=self.flow.claim('glossary_review','fixture-'+uuid.uuid4().hex):
            p=c['payload'];self.submit(c,decisions=dict(snapshot_sha256=p['snapshot_sha256'],source_sha256=p['source_hash'],
                review_complete=True,review_summary='Empty terminology fixture, not model evidence.',reviewed_refs=[],entry_reviews=[],
                acknowledged_findings=[],edits=[],quarantine=[],additions=[],allow_no_match=[]))
        self.flow.freeze_glossary()
        self.seed('pilot_translate');pr=self.flow.pilot_report()
        self.flow.pilot_decide(dict(report_hash=pr['report_hash'],reviewer='main',decision='accept',
            summary='Development fixture deliberately copies historical errors; not approval of their meaning.'))
        self.seed('translate');self.flow.pause(True)
        self.flow.configure({'stage_backends':{'review':'api'}},'Synthetic transport test',impact='none');self.flow.pause(False)

    def submit(self,c,**data):
        self.flow.submit(c['task_id'],c['lease_token'],dict(task_id=c['task_id'],input_hash=c['payload']['input_hash'],**data))

    def seed(self,stage):
        self.flow.prepare(stage)
        while c:=self.flow.claim(stage,'fixture-'+uuid.uuid4().hex):
            self.submit(c,records=[dict(id=r['id'],translation=self.translations[r['ordinal']-1]) for r in c['payload']['records']])

    def save_reply(self,run,reply,finish='stop'):
        path=Path(run['result_file']);b=w.read_json(path.with_name('api_id_transport.v2.json'))
        result=canonical_api_partial_result(reply,b,run['session_id'],b['task_id'],b['input_hash'],list(b['short_to_canonical'].values()))
        w.write_json(path,result);w.write_json(path.with_name('response.metadata.json'),{'finish_reason':finish})
        return self.flow.batch_finish(run['session_id'])

    def first_repair(self,finish):
        self.flow.prepare('review');run=self.flow.batch_open('review','first-api-'+uuid.uuid4().hex,backend='api')
        self.assertEqual(self.save_reply(run,self.fixture['missing']['reply'],finish)['preserved_records'],3)
        with patch.object(self.flow,'effective_attempts',return_value=2):
            repair=self.flow.batch_repair(run['session_id'],'repair-'+uuid.uuid4().hex)
        return run,repair

    def test_complete_omissions_remain_grouped_but_truncation_still_splits(self):
        run,repair=self.first_repair('stop')
        self.assertEqual(repair['repair_parts'],1)
        b=w.read_json(Path(repair['result_file']).with_name('api_id_transport.v2.json'))
        self.assertEqual(len(b['short_to_canonical']),3)
        error='Uncollected API record format: Record r1: return keep:true or an explicit translation'
        self.assertFalse(groupable_translation_omissions('review',[error],'length'))
        self.assertFalse(groupable_translation_omissions('review',[error,'structural mismatch'],'stop'))

    def test_resuming_a_missing_repair_part_preserves_saved_controls_and_candidate(self):
        run,repair=self.first_repair('length')
        self.assertEqual(repair['repair_parts'],3)
        self.assertEqual(self.save_reply(repair,self.fixture['repair']['reply'])['status'],'repair_part_saved')
        blocked=self.flow.batch_repair(run['session_id'],'blocked-'+uuid.uuid4().hex)
        with self.flow.db() as db:
            saved=[tuple(r) for r in db.execute('SELECT record_id,item,valid,error FROM batch_items ORDER BY record_id')]
            task=dict(db.execute('SELECT * FROM tasks WHERE id=?',(run['task_id'],)).fetchone())
            db.execute('CREATE TABLE IF NOT EXISTS api_calls(id INTEGER PRIMARY KEY,task_id TEXT,session_id TEXT,status TEXT,usage TEXT)')
        self.flow.pause(True);w.resume_api_tasks(self.flow,'review')
        with self.flow.db() as db:
            self.assertEqual(saved,[tuple(r) for r in db.execute('SELECT record_id,item,valid,error FROM batch_items ORDER BY record_id')])
            new=db.execute('SELECT * FROM tasks WHERE id=?',(run['task_id'],)).fetchone()
            self.assertEqual(new['lease_token'],task['lease_token']);self.assertEqual(new['attempt'],task['attempt'])
            self.assertEqual(db.execute('SELECT status FROM batch_runs WHERE id=?',(blocked['session_id'],)).fetchone()[0],'interrupted')
            self.assertEqual(db.execute('SELECT count(*) FROM batch_repair_plans WHERE status="pending"').fetchone()[0],2)
        self.flow.pause(False)
        next_part=self.flow.batch_repair(run['session_id'],'next-'+uuid.uuid4().hex)
        b=w.read_json(Path(next_part['result_file']).with_name('api_id_transport.v2.json'))
        original=self.fixture['missing']['binding']['short_to_canonical']
        self.assertTrue(set(b['short_to_canonical'].values()).issubset({original['2'],original['6']}))


if __name__=='__main__':unittest.main()
