"""Resume the existing source-only probe; keep all old attempts and responses."""
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
import ainiee_bridge as bridge
import translation_workflow as w

base=Path('D:/StaticHanHua/Development/review_plausibility_source_only_20261024')
old=w.read_json(base/'report.json')
assert old['arm']=='source_only'
flow=w.Workflow(base/'Review')
report={'old_report':str(base/'report.json'),'previous_wire_requests':old['wire_requests'],
        'new_wire_limit':2,'started':time.time(),
        'method':'Resume existing source-only pilot via public resume; no previous translation, screenshots, proposed correction or error diagnosis supplied to model.'}
production=Path('D:/StaticHanHua/Projects/torment-tides-of-numenera/torment-tides-of-numenera_text_translated.csv')
report['production_sha256_before']=hashlib.sha256(production.read_bytes()).hexdigest()
with flow.db() as db:
    assert not db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone()
    report['attempts_before']=[dict(t) for t in db.execute('SELECT id,stage,status,attempt FROM tasks')]
    call_before=db.execute('SELECT COALESCE(MAX(id),0) FROM api_calls').fetchone()[0]
calls=[0];lock=threading.Lock()
class BoundedAPI(bridge.AINieeAPI):
    def request(self,system,user):
        with lock:
            if calls[0]>=2:
                raise bridge.ApiFailure('resume_probe_wire_limit_before_transport',False,usage={'usage_reported':False})
            calls[0]+=1
        return super().request(system,user)
try:
    result=w.run_api(flow,'pilot_translate',requester_factory=BoundedAPI,resume=True,continuous=True)
    report['runner']={k:result.get(k) for k in ('accepted','failed','paused','needs_attention','pending_handoffs','next_action')}
except Exception as exc:
    report['error']={'type':type(exc).__name__,'message':str(exc)}
finally:
    with flow.db() as db:
        report['after']=[dict(r) for r in db.execute('SELECT ordinal,source,translation,stage FROM records ORDER BY ordinal')]
        report['attempts_after']=[dict(t) for t in db.execute('SELECT id,stage,status,attempt FROM tasks')]
        report['new_api_calls']=[dict(r) for r in db.execute('SELECT status,usage FROM api_calls WHERE id>? AND status!="cancelled"',(call_before,))]
    flow.pause(True)
    report['new_wire_requests']=calls[0]
    report['elapsed_seconds']=round(time.time()-report['started'],3)
    report['production_sha256_after']=hashlib.sha256(production.read_bytes()).hexdigest()
    assert report['production_sha256_after']==report['production_sha256_before']
    path=base/('resume_report_'+uuid.uuid4().hex[:8]+'.json')
    w.write_json(path,report)
    print(json.dumps({'new_wire_requests':calls[0],'accepted':report.get('runner',{}).get('accepted'),
                      'error':report.get('error'),'report':str(path)},ensure_ascii=False))
