"""Bounded, isolated review contrast: original bad dialogue and matched controls.

Run each arm once in a fresh Development folder. Two wire calls maximum per
arm, including the normal format retry; no production state or API overrides.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import threading
import uuid
from urllib.parse import urlsplit

import ainiee_bridge as bridge
import translation_workflow as w


BASELINE_INSTRUCTION = (' 独立审校不能以术语命中、直译可对应或沿用候选判断为语义正确依据。accuracy核对词语实际修饰对象、'
    '身体部位、施事/受事及物理/比喻词义，例如stubbled scalp是短发茬头皮，不能搬用脸部胡茬；'
    'tone核对整句中文搭配是否自然。短UI标签结合已有描述/资源信息判断人物特质还是物理状态；'
    '语境不足时明确报告疑点，不把猜测写成已证实。只对具体问题举证，不新增一轮审校或冗余输出字段。')
# Archived unsuccessful experiment, enabled only in this development harness.
CANDIDATE_INSTRUCTION = (' 独立审校不能以术语命中、直译可对应或沿用候选判断为语义正确依据。accuracy核对词语实际修饰对象、'
    '身体部位、施事/受事及物理/比喻词义。先按中文自身词义读译文：谁对什么做了什么，'
    '修饰语是否能描述该对象，是否出现身体部位、材质或动作状态的矛盾；不要先替现译寻找合理解释。'
    '再从原文确定对应短语的实际含义，与中文逐项对照，检查译词是否擅自增加了部位、对象种类或状态。'
    '决定keep前优先排查最可能的具体误译；accuracy依据应对照实际原文短语与对应中文并说明词义/修饰关系，'
    '不能只复述原句或说“意思一致”。tone同时核对中文搭配及人物口吻。'
    '原文明确支持的奇异生理结构、世界设定或比喻应保留，不能仅因现实少见就改写；'
    '原文未支持的异常不能用“也许是奇幻设定”替译文开脱。'
    '短UI标签结合已有描述/资源信息判断实际含义；语境不足时明确报告疑点，不把猜测写成已证实。'
    '只在现有六维依据和changes中记录实际结论，不另加自检报告或重译整批。')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',required=True)
    ap.add_argument('--arm',choices=('baseline','legacy','candidate','source_only','source_reference'),required=True)
    ap.add_argument('--held-out',action='store_true',help='Add unseen sense errors and literal controls; case kinds stay outside API input')
    ap.add_argument('--api',help='Named local API for this isolated run; does not change default_api or concurrency')
    ap.add_argument('--reading-report',help='Actual source-only result; required for the source_reference development arm')
    args=ap.parse_args()
    if args.arm in ('candidate','legacy'):
        original_prompt=w.prompt
        def experimental_prompt(payload,*,api=False):
            instructions,data=original_prompt(payload,api=api)
            if payload['stage'] in ('review','final_review'):
                if instructions.count(w.REVIEW_MEANING_INSTRUCTION)!=1:
                    raise ValueError('Review policy changed; reassess this archived experiment before dispatch')
                instructions=instructions.replace(w.REVIEW_MEANING_INSTRUCTION,
                    BASELINE_INSTRUCTION if args.arm=='legacy' else CANDIDATE_INSTRUCTION)
            return instructions,data
        w.prompt=experimental_prompt
    repo=Path(__file__).resolve().parents[2];base=Path(args.output).resolve()
    if base.exists() or not base.is_relative_to(repo/'Development'):
        raise ValueError('Fresh isolated Development directory required')
    game=repo/'Projects/torment-tides-of-numenera'
    source_path=game/'torment-tides-of-numenera_text.csv'
    translated_path=game/'torment-tides-of-numenera_text_translated.csv'
    def col(p):
        with p.open(encoding='utf-8-sig',newline='') as f:return [r[0] for r in csv.reader(f)]
    source=col(source_path);old=col(translated_path)
    cases=[{'kind':'actual_bad_scalp_4147','source':source[4146],'before':old[4146]},
           {'kind':'actual_bad_scalp_4257','source':source[4256],'before':old[4256]},
           {'kind':'correct_scalp_control','source':'He ran a hand over his stubbled scalp.',
            'before':'他用手抚过留着短发茬的头皮。'},
           {'kind':'correct_chin_control','source':'His chin was covered in stubble.',
            'before':'他的下巴上长满胡茬。'},
           {'kind':'source_explicit_fantasy_control',
            'source':"The mutant's scalp was covered in a thick beard, which he combed carefully.",
            'before':'变种人的头皮上长满浓密的胡须，他仔细地梳理着。'},
           {'kind':'new_bad_hair_sense','source':'She ran a hand over her close-cropped hair.',
            'before':'她用手抚过剪下来的头发。'}]
    if args.held_out:
        cases.extend([
            {'kind':'held_out_bad_brows','source':'The scholar knitted her brows as she studied the map.',
             'before':'学者一边研究地图，一边编织她的眉毛。'},
            {'kind':'held_out_bad_lined_face','source':"Years at sea had left the captain's face deeply lined.",
             'before':'多年的海上生活让船长的脸上画满了深深的线条。'},
            {'kind':'held_out_correct_literal_lines','source':"The surgeon drew two lines on the patient's face.",
             'before':'外科医生在病人的脸上画了两条线。'},
            {'kind':'held_out_correct_literal_eyebrows',
             'source':"The witch wove a pair of eyebrows from silver thread and fixed them above the doll's eyes.",
             'before':'女巫用银线织出一对眉毛，把它们固定在玩偶眼睛上方。'}])
    base.mkdir(parents=True);project=base/'Review';project.mkdir()
    src=project/'cases_text.csv'
    with src.open('w',encoding='utf-8',newline='') as f:csv.writer(f).writerows([[c['source']] for c in cases])
    flow=w.Workflow(project)
    flow.init(src,dict(backend='api',pilot_records=len(cases),batch_tokens=4096,review_batch_tokens=4096,
        stage_backends={'extract':'codex','glossary_review':'codex',
                        'pilot_translate':'api' if args.arm=='source_only' else 'codex','translate':'codex'},
        style='忠实原意，自然简体中文。保留原文明确的科幻奇幻设定；只修复明确错误，不为偏好改写。',
        game_background='科幻奇幻角色扮演游戏的叙事和对白。'))
    if args.arm=='source_reference':
        if not args.reading_report:raise ValueError('Source-reference arm needs a real source-only report')
        reading_path=Path(args.reading_report).resolve()
        if not reading_path.is_relative_to(repo/'Development'):
            raise ValueError('Source reading must come from an isolated Development probe')
        readings=w.read_json(reading_path)['after']
        if (len(readings)!=len(cases) or any(r['ordinal']!=i+1 or r['source']!=c['source']
                or not isinstance(r['translation'],str) or not r['translation'].strip()
                for i,(r,c) in enumerate(zip(readings,cases)))):
            raise ValueError('Independent reading source/ordinal mismatch or missing translation')
        contexts={str(r['ordinal']):dict(independent_source_reading=r['translation'],
            reading_provenance='Independent API draft generated from the source only. Unreviewed candidate, not an approved answer; it can contain errors. Compare with the source, preserve correct existing meanings, and do not copy stylistic differences.') for r in readings}
        flow.pause(True);flow.replace_context(contexts,
            'Development contrast: actual unreviewed blind readings supplied as fallible context; no human corrections');flow.pause(False)
    w.sync_api_batching(flow)
    wire_limit=1 if args.arm in ('source_only','source_reference') else 2
    report={'arm':args.arm,'fixture_cases':cases,'wire_limit':wire_limit,
        'production_sha256_before':hashlib.sha256(translated_path.read_bytes()).hexdigest(),
        'workflow_module_sha256':hashlib.sha256(Path(w.__file__).read_bytes()).hexdigest(),
        'experimental_instruction':(CANDIDATE_INSTRUCTION if args.arm=='candidate' else
                                    BASELINE_INSTRUCTION if args.arm=='legacy' else None),
        'held_out':args.held_out,
        'reading_report':args.reading_report if args.arm=='source_reference' else None,
        'fixture_note':'Developer copied translations, deliberately including historical errors. Empty glossary isolates review; no screenshot, diagnoses, expected corrections, or case kinds sent to API.'}
    calls=[0];lock=threading.Lock();wire_inputs=[]
    class BoundedAPI(bridge.AINieeAPI):
        def __init__(self,settings,key):
            super().__init__(settings,key)
            params=self.parser.apply_platform_thinking_params({'model':settings['model_name'],
                'extra_body':settings.get('extra_body',{})},settings)
            report['selected_api']=w.local_api_name(name=args.api)
            report['transport_settings']={**{k:settings.get(k) for k in
                ('model_name','think_switch','concurrency','max_completion_tokens','temperature','stream','request_timeout')},
                'host':urlsplit(settings['api_url']).hostname,'sent_thinking':params.get('extra_body',{}).get('thinking'),
                'sent_reasoning_effort':params.get('reasoning_effort')}
        def request(self,system,user):
            with lock:
                if calls[0]>=wire_limit:raise bridge.ApiFailure('probe_wire_limit_before_transport',False,usage={'usage_reported':False})
                calls[0]+=1
                wire_inputs.append({'system_sha256':hashlib.sha256(system.encode('utf-8')).hexdigest(),
                    'user_sha256':hashlib.sha256(user.encode('utf-8')).hexdigest()})
            return super().request(system,user)
    def claim(stage):return flow.claim(stage,'developer-fixture-'+uuid.uuid4().hex,backend='codex')
    def submit(c,**data):
        flow.submit(c['task_id'],c['lease_token'],dict(task_id=c['task_id'],input_hash=c['payload']['input_hash'],**data))
    def seed(stage):
        flow.prepare(stage)
        while c:=claim(stage):
            submit(c,records=[dict(id=r['id'],translation=cases[r['ordinal']-1]['before']) for r in c['payload']['records']])
    try:
        flow.prepare('extract')
        while c:=claim('extract'):submit(c,analysis=dict(characters=[],terms=[],non_translate=[]))
        flow.prepare('glossary_review')
        while c:=claim('glossary_review'):
            p=c['payload']
            if p['entries']:raise ValueError('Empty glossary fixture unexpectedly contains terms')
            submit(c,decisions=dict(snapshot_sha256=p['snapshot_sha256'],source_sha256=p['source_hash'],
                review_complete=True,review_summary='Development fixture intentionally uses no frozen terms to isolate semantic review.',
                reviewed_refs=[],entry_reviews=[],acknowledged_findings=[x['id'] for x in p['audit']['findings']],
                edits=[],quarantine=[],additions=[],allow_no_match=[]))
        flow.freeze_glossary()
        if args.arm=='source_only':
            result=w.run_api(flow,'pilot_translate',requester_factory=BoundedAPI,continuous=True,api_name=args.api)
        else:
            seed('pilot_translate')
            pr=flow.pilot_report();flow.pilot_decide(dict(report_hash=pr['report_hash'],reviewer='main',decision='accept',
                summary='Development regression fixtures only: copied historical errors are not approved translations; test the real reviewer.'))
            seed('translate')
            flow.pause(True);flow.configure({'stage_backends':{'extract':'codex','glossary_review':'codex','pilot_translate':'codex','translate':'api'}},
                'Fixture copying complete; all actual review and repairs use configured API',impact='none');flow.pause(False)
            result=w.run_api(flow,'review',requester_factory=BoundedAPI,continuous=True,api_name=args.api)
        report['runner']={k:result.get(k) for k in ('accepted','failed','stage_clean','needs_attention','pending_handoffs')}
    except Exception as exc:report['setup_error']=str(exc)
    with flow.db() as db:
        report['after']=[dict(r) for r in db.execute('SELECT ordinal,source,translation,stage FROM records ORDER BY ordinal')]
        report['api_calls']=[dict(r) for r in db.execute('SELECT status,usage FROM api_calls WHERE status!="cancelled"')]
    flow.pause(True);report['wire_requests']=calls[0];report['wire_inputs']=wire_inputs
    report['production_sha256_after']=hashlib.sha256(translated_path.read_bytes()).hexdigest()
    assert report['production_sha256_after']==report['production_sha256_before']
    w.write_json(base/'report.json',report)
    print(json.dumps({'arm':args.arm,'wire_requests':calls[0],'clean':report.get('runner',{}).get('stage_clean'),
                      'setup_error':report.get('setup_error'),'report':str(base/'report.json')},ensure_ascii=False))


if __name__=='__main__':main()
