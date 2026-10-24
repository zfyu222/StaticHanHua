"""Read-only production inputs; bounded live regression in a fresh Development project."""
import csv
import json
from pathlib import Path
import uuid
import hashlib
import argparse
import threading
import translation_workflow as w
import ainiee_bridge as bridge


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);args=ap.parse_args()
    repo=Path(__file__).resolve().parents[2];base=Path(args.output).resolve()
    if base.exists() or not base.is_relative_to(repo/'Development'):raise ValueError('Fresh isolated Development directory required')
    game=repo/'Projects/torment-tides-of-numenera';ordinals=[57207,63811,62257,4147,4257]
    def col(name):
        with (game/name).open(encoding='utf-8-sig',newline='') as f:return [r[0] for r in csv.reader(f)]
    sources=col('torment-tides-of-numenera_text.csv');old=col('torment-tides-of-numenera_text_translated.csv')
    texts=[sources[n-1] for n in ordinals];translations=[old[n-1] for n in ordinals]
    production_sha=hashlib.sha256((game/'torment-tides-of-numenera_text_translated.csv').read_bytes()).hexdigest()
    base.mkdir(parents=True);project=base/'Regression';project.mkdir();src=project/'cases_text.csv'
    with src.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([[t] for t in texts])
    contexts={str(i+1):{'original_ordinal':str(n),'text_type':('人物特质名称' if i==0 else '物品说明' if i==1 else 'NPC通用称谓' if i==2 else '对白叙述'),
        'evidence_origin':'user screenshot and original stringtable; provided context is factual, no proposed correction',
        'note':('截图显示阿利格恩人物标题下为“Hardened, a Nano who Fights With His Demons”；原文另有“A set of bonuses granted by the Hardened descriptor.”。' if i==0 else '原文{Female}为不显示的性别控制标记。' if i==2 else '')} for i,n in enumerate(ordinals)}
    cp=base/'contexts.json';w.write_json(cp,contexts)
    f=w.Workflow(project);f.init(src,{'backend':'api','pilot_records':5,'batch_tokens':4096,'review_batch_tokens':4096,
        'stage_backends':{'extract':'codex','pilot_translate':'codex','translate':'codex'},
        'style':'忠实原意、自然简体中文，依据词语所修饰的实际对象和叙事语境选择词义；禁止添加翻译说明。',
        'game_background':'Torment: Tides of Numenera（折磨：扭蒙拉之潮），第九世界背景的科幻奇幻角色扮演游戏；测试副本，原文与历史译文用于问题回归。'})
    # Initialization takes contexts as an explicit fourth/optional argument in normal entry; use public revision here.
    f.pause(True);f.replace_context(contexts,'Actual screenshot/resource context for isolated feedback regression');f.pause(False)
    w.sync_api_batching(f)
    original=json.loads((game/'TranslateFiles/glossary/revision_8_9ceef451/built/glossary_reviewed.json').read_text(encoding='utf-8'))
    legacy={'characters':[],'terms':[],'non_translate':[]}
    for table in ('characters','terms'):
        for row in original[table]:
            if row['source'] in ('Hardened','user','Child {Female}'):
                minimal={k:row[k] for k in ('source','recommended_translation','gender','category_path') if k in row}
                minimal['evidence_rows']=[1 if row['source']=='Hardened' else 2 if row['source']=='user' else 3];legacy[table].append(minimal)
    report={'original_game_ordinals':ordinals,'production_translation_sha256_before':production_sha,'legacy_terms_copied_unchanged':legacy,
            'context_source':contexts,'sources':texts,'before':translations,'wire_request_limit':8,'stages':[],
            'fixture_seeding':'Copied original bad glossary and existing translations, not model outputs; production state untouched.'}
    calls=[0];budget_lock=threading.Lock()
    class BoundedAPI(bridge.AINieeAPI):
        def request(self,system,user):
            with budget_lock:
                if calls[0]>=8:raise bridge.ApiFailure('regression_wire_budget',False,usage={'usage_reported':False})
                calls[0]+=1
            return super().request(system,user)
    def run(stage):
        r=w.run_api(f,stage,requester_factory=BoundedAPI,continuous=True)
        report['stages'].append({k:r.get(k) for k in ('stage','accepted','failed','paused','stage_clean','needs_attention','pending_handoffs')})
        w.write_json(base/'report.json',report)
        if r.get('paused') or r.get('needs_attention') or r.get('pending_handoffs'):raise ValueError('Stop after unresolved API/contract/quality failure')
    def seed(stage):
        f.prepare(stage)
        while claim:=f.claim(stage,'fixture-copy-'+uuid.uuid4().hex,backend='codex'):
            rows=[{'id':r['id'],'translation':translations[r['ordinal']-1]} for r in claim['payload']['records']]
            f.submit(claim['task_id'],claim['lease_token'],{'task_id':claim['task_id'],'input_hash':claim['payload']['input_hash'],'records':rows})
    try:
        f.prepare('extract')
        while claim:=f.claim('extract','fixture-legacy-'+uuid.uuid4().hex,backend='codex'):
            f.submit(claim['task_id'],claim['lease_token'],{'task_id':claim['task_id'],'input_hash':claim['payload']['input_hash'],'analysis':legacy})
        run('glossary_review');f.freeze_glossary()
        with f.db() as db:report['new_glossary']=f.glossary(db)
        seed('pilot_translate');pr=f.pilot_report();f.pilot_decide({'report_hash':pr['report_hash'],'reviewer':'main','decision':'accept',
             'summary':'Development regression only: deliberate unchanged historical errors, not approval of translation quality; formal checks must find them.'})
        seed('translate')
        f.pause(True);f.configure({'stage_backends':{'extract':'codex','pilot_translate':'codex','translate':'api'}},
            'Historical fixtures copied; actual draft repairs use configured API',impact='none');f.pause(False)
        run('draft_check');run('review')
        report['status']='completed'
    except Exception as e:report.update(status='needs_attention',error_type=type(e).__name__,error=str(e))
    with f.db() as db:
        report['after']=[dict(r) for r in db.execute('select ordinal,source,translation,stage from records order by ordinal')]
        report['api_calls']=[dict(r) for r in db.execute('select status,usage from api_calls where status!="cancelled"')]
    report['wire_requests']=calls[0];report['production_translation_sha256_after']=hashlib.sha256((game/'torment-tides-of-numenera_text_translated.csv').read_bytes()).hexdigest()
    assert report['production_translation_sha256_after']==production_sha
    w.write_json(base/'report.json',report);print(json.dumps({'status':report['status'],'requests':calls[0],'report':str(base/'report.json')},ensure_ascii=False))

if __name__=='__main__':main()
