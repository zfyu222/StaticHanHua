"""Bounded real validation of format recovery and explicit reading import."""
from pathlib import Path
import hashlib
import json
import threading
import time
import uuid
import ainiee_bridge as bridge
import translation_workflow as w

repo=Path('D:/StaticHanHua');path=repo/'Development'/('review_optimization_'+uuid.uuid4().hex[:8]+'.json')
production=repo/'Projects/torment-tides-of-numenera/torment-tides-of-numenera_text_translated.csv'
report={'started':time.time(),'wire_limit':6,'arms':[],'production_sha256_before':hashlib.sha256(production.read_bytes()).hexdigest()}
total=[0];current=[0];lock=threading.Lock()
class BoundedAPI(bridge.AINieeAPI):
    def request(self,system,user):
        with lock:
            if total[0]>=6 or current[0]>=3:
                raise bridge.ApiFailure('optimization_probe_wire_limit_before_transport',False,usage={'usage_reported':False})
            total[0]+=1;current[0]+=1
        return super().request(system,user)

def run(flow,label,resume=False):
    current[0]=0
    with flow.db() as db:
        assert not db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone()
        before=db.execute('SELECT coalesce(max(id),0) FROM api_calls').fetchone()[0]
        old_rows=[dict(r) for r in db.execute('SELECT id,source,translation,stage FROM records ORDER BY ordinal')]
    try:r=w.run_api(flow,'review',requester_factory=BoundedAPI,resume=resume,continuous=True)
    finally:flow.pause(True)
    with flow.db() as db:
        item={'label':label,'new_wire_requests':current[0],
            'runner':{k:r.get(k) for k in ('accepted','failed','stage_clean','needs_attention','pending_handoffs')},
            'before':old_rows,'after':[dict(x) for x in db.execute('SELECT id,source,translation,stage FROM records ORDER BY ordinal')],
            'new_calls':[dict(x) for x in db.execute('SELECT status,usage FROM api_calls WHERE id>? AND status!="cancelled"',(before,))]}
    report['arms'].append(item);w.write_json(path,report)
    if any(w.service_failure(c['status']) for c in item['new_calls'] if 'wire_limit' not in c['status']):
        raise ValueError('Stop after actual API service failure')
    return item

try:
    partial=w.Workflow(repo/'Development/review_plausibility_source_reference_20261024/Review')
    with partial.db() as db:
        old_limit=partial.config(db)['max_attempts']
    if old_limit<6:
        partial.pause(True);partial.configure({'max_attempts':6},
            'Development validation of revised output examples: preserve previous attempts, extend only local fixture ceiling within six-wire-call experiment',impact='none')
    run(partial,'finish_saved_partial_review',resume=True)
    target=w.Workflow(repo/'Development/review_plausibility_baseline_20261024/Review')
    with target.db() as db:
        ids=[r['id'] for r in db.execute('SELECT id FROM records WHERE ordinal IN (1,2) ORDER BY ordinal')]
    target.pause(True)
    report['import_result']=target.import_review_readings(repo/'Development/review_plausibility_source_only_20261024/Review',ids,
        'Development regression: independently generated source-only readings for these two explicit doubtful records; preserve other reviewed records')
    target.pause(False);run(target,'import_only_two_doubtful_records')
except Exception as exc:report['error']={'type':type(exc).__name__,'message':str(exc)}
finally:
    report['new_actual_wire_requests']=total[0];report['elapsed_seconds']=round(time.time()-report['started'],3)
    report['production_sha256_after']=hashlib.sha256(production.read_bytes()).hexdigest()
    assert report['production_sha256_after']==report['production_sha256_before']
    w.write_json(path,report)
    print(json.dumps({'wire_requests':total[0],'error':report.get('error'),'report':str(path)},ensure_ascii=False))
