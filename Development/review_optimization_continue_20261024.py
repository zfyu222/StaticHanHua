"""Continue later validation arms without replaying the completed first arm."""
from pathlib import Path
import hashlib
import json
import threading
import time
import uuid
import ainiee_bridge as bridge
import translation_workflow as w

repo=Path('D:/StaticHanHua')
prior=w.read_json(repo/'Development/review_optimization_dc498b2a.json')
assert prior['arms'][0]['runner']['stage_clean']
total=[prior['new_actual_wire_requests']];current=[0];cap=[2];lock=threading.Lock()
path=repo/'Development'/('review_optimization_continued_'+uuid.uuid4().hex[:8]+'.json')
production=repo/'Projects/torment-tides-of-numenera/torment-tides-of-numenera_text_translated.csv'
report={'started':time.time(),'previous_report':'Development/review_optimization_dc498b2a.json',
        'wire_limit_combined':6,'previous_actual_wire_requests':total[0],'arms':[],
        'production_sha256_before':hashlib.sha256(production.read_bytes()).hexdigest()}
class BoundedAPI(bridge.AINieeAPI):
    def request(self,system,user):
        with lock:
            if total[0]>=6 or current[0]>=cap[0]:raise bridge.ApiFailure('optimization_probe_wire_limit_before_transport',False,usage={'usage_reported':False})
            total[0]+=1;current[0]+=1
        return super().request(system,user)
def run(flow,label,limit):
    current[0]=0;cap[0]=limit
    with flow.db() as db:
        assert not db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone()
        before=db.execute('SELECT coalesce(max(id),0) FROM api_calls').fetchone()[0]
        old=[dict(r) for r in db.execute('SELECT id,source,translation,stage FROM records ORDER BY ordinal')]
    try:r=w.run_api(flow,'review',requester_factory=BoundedAPI,continuous=True)
    finally:flow.pause(True)
    with flow.db() as db:
        item={'label':label,'new_wire_requests':current[0],
              'runner':{k:r.get(k) for k in ('accepted','failed','stage_clean','needs_attention','pending_handoffs')},
              'before':old,'after':[dict(x) for x in db.execute('SELECT id,source,translation,stage FROM records ORDER BY ordinal')],
              'new_calls':[dict(x) for x in db.execute('SELECT status,usage FROM api_calls WHERE id>? AND status!="cancelled"',(before,))]}
    report['arms'].append(item);w.write_json(path,report)
    if any(w.service_failure(c['status']) for c in item['new_calls'] if 'wire_limit' not in c['status']):raise ValueError('Stop after API service failure')
    return item
try:
    target=w.Workflow(repo/'Development/review_plausibility_baseline_20261024/Review')
    with target.db() as db:ids=[r['id'] for r in db.execute('SELECT id FROM records WHERE ordinal IN (1,2) ORDER BY ordinal')]
    target.pause(True)
    report['import_result']=target.import_review_readings(repo/'Development/review_plausibility_source_only_20261024/Review',ids,
        'Development regression: two explicit doubtful records; preserve other reviewed records')
    target.pause(False)
    item=run(target,'import_only_two_doubtful_records',2)
    if not item['runner']['stage_clean']:raise ValueError('Selected review did not close; do not dispatch unrelated controls')
    with target.db() as db:controls=[r['id'] for r in db.execute('SELECT id FROM records WHERE ordinal IN (3,4,5) ORDER BY ordinal')]
    report['control_reopen']=target.reopen_body_ids('Development validation of new output example on correct controls without independent readings',controls)
    target.pause(False);run(target,'default_path_correct_controls',1)
except Exception as exc:report['error']={'type':type(exc).__name__,'message':str(exc)}
finally:
    report['combined_actual_wire_requests']=total[0];report['elapsed_seconds']=round(time.time()-report['started'],3)
    report['production_sha256_after']=hashlib.sha256(production.read_bytes()).hexdigest()
    assert report['production_sha256_after']==report['production_sha256_before']
    w.write_json(path,report);print(json.dumps({'combined_wire_requests':total[0],'error':report.get('error'),'report':str(path)},ensure_ascii=False))
