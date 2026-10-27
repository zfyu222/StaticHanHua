"""Resume only the timed-out assisted review, preserving its history."""
import hashlib
from pathlib import Path
import json
import threading
import time
import uuid
import ainiee_bridge as bridge
import translation_workflow as w

base=Path('D:/StaticHanHua/Development/review_plausibility_source_reference_20261024')
old=w.read_json(base/'report.json');assert old['arm']=='source_reference'
flow=w.Workflow(base/'Review')
production=Path('D:/StaticHanHua/Projects/torment-tides-of-numenera/torment-tides-of-numenera_text_translated.csv')
report={'started':time.time(),'previous_report':str(base/'report.json'),'previous_wire_requests':old['wire_requests'],
        'new_wire_limit':2,'method':'Resume existing assisted-review fixture using the current configured API, preserving blind candidates, attempts and old timeout.'}
report['production_sha256_before']=hashlib.sha256(production.read_bytes()).hexdigest()
with flow.db() as db:
    assert not db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone()
    report['attempts_before']=[dict(r) for r in db.execute('SELECT id,stage,status,attempt FROM tasks')]
    call_before=db.execute('SELECT COALESCE(MAX(id),0) FROM api_calls').fetchone()[0]
calls=[0];lock=threading.Lock()
class BoundedAPI(bridge.AINieeAPI):
    def request(self,system,user):
        with lock:
            if calls[0]>=2:raise bridge.ApiFailure('reference_resume_wire_limit_before_transport',False,usage={'usage_reported':False})
            calls[0]+=1
        return super().request(system,user)
try:
    result=w.run_api(flow,'review',requester_factory=BoundedAPI,resume=True,continuous=True)
    report['runner']={k:result.get(k) for k in ('accepted','failed','paused','stage_clean','needs_attention','pending_handoffs','next_action')}
except Exception as exc:
    report['error']={'type':type(exc).__name__,'message':str(exc)}
finally:
    with flow.db() as db:
        report['after']=[dict(r) for r in db.execute('SELECT ordinal,source,translation,stage FROM records ORDER BY ordinal')]
        report['attempts_after']=[dict(r) for r in db.execute('SELECT id,stage,status,attempt FROM tasks')]
        report['new_api_calls']=[dict(r) for r in db.execute('SELECT status,usage FROM api_calls WHERE id>? AND status!="cancelled"',(call_before,))]
    flow.pause(True)
    report['new_wire_requests']=calls[0];report['elapsed_seconds']=round(time.time()-report['started'],3)
    report['production_sha256_after']=hashlib.sha256(production.read_bytes()).hexdigest()
    assert report['production_sha256_after']==report['production_sha256_before']
    path=base/('resume_report_'+uuid.uuid4().hex[:8]+'.json');w.write_json(path,report)
    print(json.dumps({'new_wire_requests':calls[0],'runner':report.get('runner'),'error':report.get('error'),'report':str(path)},ensure_ascii=False))
