from pathlib import Path
import csv,json,hashlib,threading,uuid
import translation_workflow as w
import ainiee_bridge as bridge
from workflow_review_readings import load_source_readings
root=Path('D:/StaticHanHua/Development/review_reading_marker_live_20261024');root.mkdir()
source=root/'cases_text.csv'
texts=['The device grants {0} points.','Continue.\\nKeep <b>{0}</b> items.']
with source.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([[s] for s in texts])
flow=w.Workflow(root);flow.init(source,dict(backend='api',pilot_records=2,stage_backends={'extract':'codex','glossary_review':'codex','pilot_translate':'api'}))
count=[0];lock=threading.Lock();report={'wire_limit':1,'sources':texts}
class BoundedAPI(bridge.AINieeAPI):
 def request(self,system,user):
  with lock:
   if count[0]>=1:raise bridge.ApiFailure('probe_wire_limit_before_transport',False,usage={'usage_reported':False})
   count[0]+=1
  return super().request(system,user)
try:
 for stage in ('extract','glossary_review'):
  flow.prepare(stage)
  while c:=flow.claim(stage,'marker-fixture-'+uuid.uuid4().hex,backend='codex'):
   p=c['payload']
   data=dict(analysis=dict(characters=[],terms=[],non_translate=[])) if stage=='extract' else dict(decisions=dict(snapshot_sha256=p['snapshot_sha256'],source_sha256=p['source_hash'],review_complete=True,review_summary='Two development sentences have no specific named/game terms.',reviewed_refs=[],entry_reviews=[],acknowledged_findings=[f['id'] for f in p['audit']['findings']],edits=[],quarantine=[],additions=[],allow_no_match=[]))
   flow.submit(c['task_id'],c['lease_token'],dict(task_id=c['task_id'],input_hash=p['input_hash'],**data))
 flow.freeze_glossary();w.sync_api_batching(flow)
 result=w.run_api(flow,'pilot_translate',requester_factory=BoundedAPI,continuous=True)
 report['runner']={k:result.get(k) for k in ('accepted','failed','drafts_complete','needs_attention')}
 report['readings']=load_source_readings(root)
 with flow.db() as db:
  report['calls']=[dict(r) for r in db.execute('SELECT status,usage FROM api_calls WHERE status!="cancelled"')]
  run=db.execute('SELECT * FROM batch_runs ORDER BY rowid DESC LIMIT 1').fetchone()
  folder=Path(run['result_file']).parent;report['session_id']=run['id']
  report['input_sha256']=hashlib.sha256(Path(run['input_file']).read_bytes()).hexdigest()
  report['raw_reply']=(folder/'response.raw.txt').read_text(encoding='utf-8')
  payload=json.loads(db.execute('SELECT payload FROM tasks WHERE id=?',(run['task_id'],)).fetchone()[0])
  report['protected_input_tokens']=[r['protection'] for r in payload['records']]
 assert len(report['readings'])==2
 for s,v in report['readings'].items():
  assert '{0}' in v['reading']
  if '<b>' in s:assert all(t in v['reading'] for t in ('<b>','</b>','\\n'))
 report['binding_verified']=True
except Exception as exc:report['error']={'type':type(exc).__name__,'message':str(exc)}
finally:
 flow.pause(True);report['actual_wire_requests']=count[0]
 w.write_json(root/'report.json',report)
 print(json.dumps({k:v for k,v in report.items() if k not in ('readings','raw_reply','protected_input_tokens')},ensure_ascii=False))
