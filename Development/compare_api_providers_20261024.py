from pathlib import Path
import subprocess,sys,json,hashlib,time
sys.path.insert(0,'Tools/localization')
import translation_workflow as w
repo=Path.cwd();root=repo/'Development/api_provider_comparison_20261024';root.mkdir()
config=w.API_CONFIG_FILE;config_hash=hashlib.sha256(config.read_bytes()).hexdigest()
report={'request_limit':8,'order':['yjapi','deepseek','deepseek','yjapi'],'arms':[],'started':time.time(),'stopped_apis':[]}
try:
 for i,name in enumerate(report['order'],1):
  if name in report['stopped_apis']:continue
  if hashlib.sha256(config.read_bytes()).hexdigest()!=config_hash:
   report['stop_reason']='Local API configuration changed during comparison';break
  path=root/(str(i)+'_'+name)
  print(json.dumps({'event':'arm_started','api':name,'index':i}),flush=True)
  with (root/(str(i)+'_'+name+'.log')).open('w',encoding='utf-8') as log:
   r=subprocess.run([sys.executable,'Tools/localization/live_review_plausibility_probe.py','--arm','legacy','--held-out','--api',name,'--output',str(path)],stdout=log,stderr=subprocess.STDOUT)
  if not (path/'report.json').is_file():
   report['stop_reason']='Harness did not save a report';report['returncode']=r.returncode;break
  d=w.read_json(path/'report.json');entry={'index':i,'api':name,'report':str(path.relative_to(repo)/'report.json'),
   'wire_requests':d['wire_requests'],'runner':d.get('runner'),'setup_error':d.get('setup_error')}
  report['arms'].append(entry)
  if any(w.service_failure(c['status']) for c in d.get('api_calls',[])):report['stopped_apis'].append(name)
  report['actual_wire_requests']=sum(a['wire_requests'] for a in report['arms']);assert report['actual_wire_requests']<=8
  w.write_json(root/'campaign.json',report);print(json.dumps({'event':'arm_finished',**entry},ensure_ascii=False),flush=True)
finally:
 report['elapsed_seconds']=round(time.time()-report['started'],3)
 report['config_unchanged']=hashlib.sha256(config.read_bytes()).hexdigest()==config_hash
 w.write_json(root/'campaign.json',report)
