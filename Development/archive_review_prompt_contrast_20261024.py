from pathlib import Path
from contextlib import closing
import json,sqlite3,hashlib
import sys
sys.path.insert(0,'Tools/localization')
import translation_workflow as w
from live_review_plausibility_probe import BASELINE_INSTRUCTION
repo=Path.cwd();arms=[]
for name,label,instruction in [('review_prompt_legacy_20261024','previous',BASELINE_INSTRUCTION),('review_prompt_contrast_20261024','contrast',w.REVIEW_MEANING_INSTRUCTION)]:
 base=repo/'Development'/name;d=w.read_json(base/'report.json')
 with closing(sqlite3.connect((base/'Review/TranslateFiles/state/workflow.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row
  assert not db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone()
  assert json.loads(db.execute('SELECT value FROM meta WHERE key="paused"').fetchone()[0])
  run=db.execute('SELECT * FROM batch_runs ORDER BY rowid DESC LIMIT 1').fetchone()
  folder=Path(run['result_file']).parent
  result=w.read_json(Path(run['result_file']))
  records=[]
  for case,after,accepted in zip(d['fixture_cases'],d['after'],result['records']):
   records.append({**case,'after':after['translation'],'changed':case['before']!=after['translation'],
     'accuracy':accepted['checks']['accuracy'],'changes':accepted.get('changes',[])})
  calls=[{**c,'usage':json.loads(c['usage'])} for c in d['api_calls']]
  assert d['wire_requests']==1 and d['runner']['stage_clean']
  assert sum(r['changed'] for r in records)==5
  assert all(not r['changed'] for r in records if 'correct' in r['kind'] or r['kind']=='source_explicit_fantasy_control')
  arms.append(dict(arm=label,meaning_instruction=instruction,wire_requests=d['wire_requests'],runner=d['runner'],cases=records,calls=calls,
    session=run['id'],input_hash=w.read_json(folder/'api_id_transport.v2.json')['input_hash'],
    input_sha256=hashlib.sha256(Path(run['input_file']).read_bytes()).hexdigest(),
    raw_sha256=hashlib.sha256((folder/'response.raw.txt').read_bytes()).hexdigest(),
    raw_reply=(folder/'response.raw.txt').read_text(encoding='utf-8')))
usage=[c['usage'] for a in arms for c in a['calls']]
sha=hashlib.sha256((repo/'Projects/torment-tides-of-numenera/torment-tides-of-numenera_text_translated.csv').read_bytes()).hexdigest()
assert sha=='36d5b3a85f6353f67fbe23b7726b57861dcd0b64195461b984dfadd969db0f9f'
report=dict(date='2026-10-24',scope='Workflow prompt only. No game repair. No automatic source-reading stage.',
 wire_limit=4,actual_wire_requests=2,usage_totals={k:sum(u[k] for u in usage) for k in ('prompt_tokens','completion_tokens','total_tokens')},
 reported_reasoning_tokens=sum(u.get('completion_tokens_details',{}).get('reasoning_tokens',0) or 0 for u in usage),
 model_aliases=sorted(set(u['response_model'] for u in usage)),concurrency='unchanged/config-driven',thinking='unchanged/enabled',
 arms=arms,regressions={'tests':49,'passed':True,'log':'Development/review_prompt_regression_20261024.log'},
 input_token_difference=usage[1]['prompt_tokens']-usage[0]['prompt_tokens'],
 production_translation_sha256=sha,production_changed=False,all_probe_projects_paused=True,reserved_requests=0,
 limitations=['Both old and new instructions passed every test case. No demonstrated accuracy improvement.',
 'One run per arm, one service, few cases; no statistical accuracy or cost reduction claim.',
 'Two real bad scalp translations and matching scalp/chin/beard controls are examples already present or expanded in the prompt; not held-out generalization evidence.',
 'New brows/lined-face cases and literal counterparts are not in the prompt; expected case labels and corrections were not sent as task diagnoses.',
 'No extra output fields, full-source explanation pass, source-reading request or manual selection dependency added to ordinary review.',
 'Existing in-flight files and accepted reviews were not rewritten or reopened. No current game changes.',
 'No lexical substitution or programmatic semantic pass was used; model supplied actual corrected evidence and full translations.'])
w.write_json(repo/'Tools/localization/reports/review_prompt_contrast_20261024.json',report)
print(json.dumps({k:report[k] for k in ('actual_wire_requests','usage_totals','input_token_difference')},ensure_ascii=False))
