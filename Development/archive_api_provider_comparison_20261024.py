from pathlib import Path
import sqlite3,hashlib,json,sys
from contextlib import closing
sys.path.insert(0,'Tools/localization')
import translation_workflow as w
repo=Path.cwd();root=repo/'Development/api_provider_comparison_20261024';campaign=w.read_json(root/'campaign.json');arms=[]
for item in campaign['arms']:
 path=repo/item['report'];d=w.read_json(path);base=path.parent/'Review'
 with closing(sqlite3.connect((base/'TranslateFiles/state/workflow.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row
  assert not db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone()
  assert json.loads(db.execute('SELECT value FROM meta WHERE key="paused"').fetchone()[0])
  run=db.execute('SELECT * FROM batch_runs ORDER BY rowid DESC LIMIT 1').fetchone();folder=Path(run['result_file']).parent
  result=w.read_json(Path(run['result_file']))
 cases=[]
 for c,after,accepted in zip(d['fixture_cases'],d['after'],result['records']):
  cases.append({**c,'after':after['translation'],'changed':c['before']!=after['translation'],
    'accuracy':accepted['checks']['accuracy'],'changes':accepted['changes']})
 assert sum(c['changed'] for c in cases)==5
 assert all(not c['changed'] for c in cases if 'correct' in c['kind'] or c['kind']=='source_explicit_fantasy_control')
 calls=[{**c,'usage':json.loads(c['usage'])} for c in d['api_calls']]
 assert d['wire_requests']==1 and d['runner']['stage_clean'] and all(c['status']=='received' for c in calls)
 profiles=list((base/'TranslateFiles/reports/api_runs').glob('*.profile.json'));assert len(profiles)==1
 arms.append(dict(index=item['index'],api=item['api'],transport_settings=d['transport_settings'],wire_inputs=d['wire_inputs'],
  calls=calls,runner=d['runner'],cases=cases,elapsed_seconds=w.read_json(profiles[0])['elapsed_seconds'],
  session=run['id'],input_hash=w.read_json(folder/'api_id_transport.v2.json')['input_hash'],
  raw_sha256=hashlib.sha256((folder/'response.raw.txt').read_bytes()).hexdigest(),raw_reply=(folder/'response.raw.txt').read_text(encoding='utf-8')))
assert len(arms)==campaign['actual_wire_requests']==4
hashes={(h['system_sha256'],h['user_sha256']) for a in arms for h in a['wire_inputs']};assert len(hashes)==1
summaries={}
for name in ('yjapi','deepseek'):
 group=[a for a in arms if a['api']==name];usage=[c['usage'] for a in group for c in a['calls']]
 summaries[name]=dict(rounds=len(group),actual_requests=len(usage),known_errors_corrected_per_round=[5,5],correct_controls_preserved_per_round=[5,5],
  totals={k:sum(u[k] for u in usage) for k in ('prompt_tokens','completion_tokens','total_tokens')},
  response_models=sorted(set(u.get('response_model') for u in usage)),
  reasoning_tokens=sum(u.get('completion_tokens_details',{}).get('reasoning_tokens',0) or 0 for u in usage),
  stage_seconds=[a['elapsed_seconds'] for a in group])
sha=hashlib.sha256((repo/'Projects/torment-tides-of-numenera/torment-tides-of-numenera_text_translated.csv').read_bytes()).hexdigest()
assert sha=='36d5b3a85f6353f67fbe23b7726b57861dcd0b64195461b984dfadd969db0f9f'
report=dict(date='2026-10-24',question='Does yjapi review perform worse than official DeepSeek on the actual regression examples?',
 conclusion='Not observed in this small current comparison. Each endpoint corrected all five known errors and preserved all five correct controls in both rounds.',
 request_limit=8,actual_requests=4,order=campaign['order'],elapsed_seconds=campaign['elapsed_seconds'],
 prompt='Same previous review instruction (legacy arm), not the expanded new examples. No independent readings.',
 unique_cases=10,known_errors=5,correct_controls=5,repetitions_per_provider=2,all_wire_message_hashes_identical=True,
 evidence=arms,summaries=summaries,total_reported_tokens=sum(s['totals']['total_tokens'] for s in summaries.values()),
 config_unchanged=campaign['config_unchanged'],production_translation_sha256=sha,production_changed=False,
 regression_tests=dict(passed=36,log='Development/api_provider_comparison_regression_20261024.log'),all_probes_paused=True,reserved_requests=0,
 official_source={'url':'https://api-docs.deepseek.com/api/responses','verified_date':'2026-10-24',
  'note':'Official docs map deepseek-flash to DeepSeek-V4.1-Flash-1017. yjapi real backend cannot be established from its requested/echoed model name.'},
 limitations=['Only ten selected cases, repeated twice per provider; not forty independent samples or a general accuracy estimate.',
  'The previous prompt already contains a scalp/stubble example. Four held-out records contain two different errors and two correct literal counterparts.',
  'Both send thinking enabled and reasoning_effort high, temperature 1, max_completion_tokens 65536, concurrency 25. Configured model aliases differ.',
  'Profile streaming and timeout differ: yjapi false/300s, official true/200s. No configuration changes were made; no timeout occurred.',
  'Identical message hashes do not prove identical server model versions or internal processing. Returned model name is not model identity attestation.',
  'Input usage reports differ by 22 tokens per identical-message request; accounting/tokenizer/chat overhead differences are possible. This alone does not prove truncation or prompt rewriting.',
  'Token totals and wall times are observed for these runs only, not billed money or reliable pricing/performance benchmarks.',
  'Historical failed calls cannot be conclusively attributed to a provider/backend solely from current configuration and response model aliases.',
  'No production game, frozen glossary, official translation, state or patch was changed. No translation API default or concurrency was changed.'])
w.write_json(repo/'Tools/localization/reports/api_provider_comparison_20261024.json',report)
print(json.dumps({'tokens':report['total_reported_tokens'],'summaries':summaries},ensure_ascii=False))
