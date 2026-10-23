"""Frozen labeled small-sample comparison. No production translation or game mutation."""
import argparse
import html
import json
from pathlib import Path
import subprocess
import sys
import time
import unicodedata
from PIL import Image,ImageDraw,ImageFont
from core import canonical,digest,inventory,load_image,pixel_hash,read_json,write_json

ROOT=Path(__file__).resolve().parents[2]
DEFAULT=Path(__file__).parent/'benchmarks/challenge_v1.json'

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())

def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1):nxt.append(min(row[j]+1,nxt[-1]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]

def ordered(regions):
    # Cluster vertically overlapping lines before left-to-right sorting.
    lines=[]
    for r in sorted(regions,key=lambda x:x['box'][1]):
        box=r['box'];height=max(1,box[3]-box[1])
        row=next((v for v in lines if min(v['bottom'],box[3])-max(v['top'],box[1])>=.5*min(v['bottom']-v['top'],height)),None)
        if row is None:lines.append(dict(top=box[1],bottom=box[3],items=[r]))
        else:row['items'].append(r)
    return ' '.join(r['text'] for row in lines for r in sorted(row['items'],key=lambda x:x['box'][0]))

def prepare(work,cases):
    if (work/'labels.json').exists():
        if read_json(work/'labels.json')!=cases:raise ValueError('Frozen labels differ; use another directory')
        return
    folder=work/'input';folder.mkdir(parents=True,exist_ok=True)
    for case in cases['cases']:
        if case.get('render'):
            im=Image.new('RGB',(720,190),(245,245,240))
            font=ImageFont.truetype(str(ROOT/'Tools/Fonts/NotoSC/NotoSansSC-variable.ttf') if case['render']=='zh' else 'C:/Windows/Fonts/arial.ttf',28)
            ImageDraw.Draw(im).multiline_text((28,25),case['expected'],font=font,fill=(20,20,20),spacing=15)
        else:
            source=Path(case['source_absolute']) if 'source_absolute' in case else ROOT/cases['source_root']/case['source']
            im=load_image(source)
            if case.get('crop'):im=im.crop(case['crop'])
        im.save(folder/f'{case["id"]}.png')
    write_json(work/'labels.json',cases)
    inventory(folder,work/'run')
    print('Frozen',len(cases['cases']),'cases; label hash',digest(canonical(cases)),flush=True)

def ocr(work):
    from engine import Engine,analyze
    if (work/'run/scan.json').exists():raise ValueError('OCR evidence already exists; use a new directory to remeasure')
    started=time.perf_counter();engine=Engine(model_size='medium',device='cuda');init=time.perf_counter()-started
    out=work/'run';manifest=read_json(out/'manifest.json');records=[];timings=[]
    for image in manifest['images']:
        im=load_image(out/'images'/f'{image["image_id"]}.png')
        if pixel_hash(im)!=image['image_id']:raise ValueError('Input pixels changed')
        before=time.perf_counter();r=analyze(engine,im,image['image_id'],'balanced')
        timings.append(dict(image_id=image['image_id'],seconds=time.perf_counter()-before));records.append(r)
        print('OCR',image['sources'][0]['path'],round(timings[-1]['seconds'],3),flush=True)
    config=dict(engine=engine.metadata,profile='balanced',sprite_crops=False)
    write_json(out/'scan.json',dict(schema=1,manifest_hash=manifest['manifest_hash'],fingerprint=digest(canonical(config)),config=config,images=records))
    write_json(work/'ocr_timing.json',dict(initialization_seconds=init,total_seconds=time.perf_counter()-started,images=timings,cache=False))
    if any(x['errors'] for x in records):raise RuntimeError('OCR errors, preserved for diagnosis')

def vlm(work):
    from handoff import prepare as queue
    from local_vlm import run
    from ollama_server import Server
    out=work/'run';n=len(read_json(work/'labels.json')['cases'])
    queue(out,max_jobs=n,negative_samples=0,low_signal_samples=0,policy='full-audit')
    with Server() as server:run(out,server.endpoint)
    from review import build_review
    build_review(out)

def single(work):
    """Diagnostic ablation: same initial whole-image view, no tiles or merging."""
    from engine import Engine,views
    started=time.perf_counter();engine=Engine(model_size='medium',device='cuda');init=time.perf_counter()-started
    out=work/'run';manifest=read_json(out/'manifest.json');records=[]
    for image in manifest['images']:
        im=load_image(out/'images'/f'{image["image_id"]}.png');view,name,offset,scale=next(views(im,'fast'))
        before=time.perf_counter();regions,errors=engine.run(view,name,offset,scale)
        records.append(dict(image_id=image['image_id'],regions=regions,errors=errors,elapsed_seconds=time.perf_counter()-before))
    write_json(work/'paddle_single.json',dict(engine=engine.metadata,images=records,initialization_seconds=init,total_seconds=time.perf_counter()-started))
    if any(x['errors'] for x in records):raise RuntimeError('Single-pass OCR errors')

def report(work):
    from screening import readable
    labels=read_json(work/'labels.json');out=work/'run';manifest=read_json(out/'manifest.json')
    ids={s['path'][:-4]:x['image_id'] for x in manifest['images'] for s in x['sources']}
    scans={x['image_id']:x for x in read_json(out/'scan.json')['images']}
    visions={x['image_id']:x for x in read_json(out/'vision_findings.json')['records']}
    singles={x['image_id']:x for x in read_json(work/'paddle_single.json')['images']} if (work/'paddle_single.json').exists() else {}
    results=[]
    for case in labels['cases']:
        key=ids[case['id']];raw=scans[key];vision=visions[key];expected=normalized(case['expected']);roi=case.get('score_roi')
        def relevant(region):
            if not roi:return True
            b=region['box'];x=(b[0]+b[2])/2;y=(b[1]+b[3])/2
            return roi[0]<=x<=roi[2] and roi[1]<=y<=roi[3]
        choices=dict(paddle_raw=[x for x in raw['regions'] if x['text'].strip()],
                     paddle_06=[x for x in raw['regions'] if readable(x,.6,case.get('script','any' if case.get('render')=='zh' else 'latin'))],qwen=vision['proposals'])
        if singles:choices['paddle_single']=[x for x in singles[key]['regions'] if x['text'].strip()]
        models={}
        for name,regions in choices.items():
            scored=[r for r in regions if relevant(r)];text=ordered(scored);actual=normalized(text)
            models[name]=dict(text=text,edit_distance=distance(expected,actual),reference_characters=len(expected),
                cer=distance(expected,actual)/len(expected) if expected else None,exact=actual==expected,
                presence=vision['presence'] if name=='qwen' else 'text' if regions else 'no_text',
                unscored_text=[r['text'] for r in regions if not relevant(r)])
        results.append(dict(**case,models=models))
    aggregate={}
    for category in ['all','art','pixel','normal','scene','none']:
        rows=[r for r in results if category=='all' or r['category']==category];group={}
        for name in results[0]['models']:
            positive=[r for r in rows if r['expected']];scorable=[r for r in positive if r.get('primary_cer',True)]
            negative=[r for r in rows if not r['expected']];chars=sum(r['models'][name]['reference_characters'] for r in scorable)
            group[name]=dict(positive_images=len(positive),readable_text_predictions=sum(r['models'][name]['presence']=='text' for r in positive),
                uncertain=sum(r['models'][name]['presence']=='uncertain' for r in positive),
                false_no_text=sum(r['models'][name]['presence']=='no_text' for r in positive),
                exact_images=sum(r['models'][name]['exact'] for r in scorable),scorable_images=len(scorable),
                character_errors=sum(r['models'][name]['edit_distance'] for r in scorable),reference_characters=chars,
                cer=sum(r['models'][name]['edit_distance'] for r in scorable)/chars if chars else None,
                negative_images=len(negative),negative_text_predictions=sum(r['models'][name]['presence']=='text' for r in negative),
                negative_uncertain=sum(r['models'][name]['presence']=='uncertain' for r in negative))
        aggregate[category]=group
    timing=read_json(work/'ocr_timing.json');vtime=read_json(out/'local_vlm/report.json')
    result=dict(scope=labels['scope'],label_hash=digest(canonical(labels)),cases=results,aggregate=aggregate,
        timing=dict(paddle=timing,qwen=vtime),method='CER: NFKC, casefold, retain letters/numbers, discard spacing/punctuation. Primary CER excludes fragmented glitch reference. Reading order from box geometry, not reference text. Qwen uncertain has empty transcription; counts as retained for review, not successful OCR. Threshold .6 is the existing screening setting, not tuned here; latin for the known English source and any for the Chinese control. Different existing preprocessing is part of the workflow comparison. No-text examples are not CER-scored.')
    write_json(work/'comparison.json',result)
    names=dict(art='艺术字',pixel='像素难字',normal='正常字',scene='复杂场景',none='无字')
    sizes={x['image_id']:(x['width'],x['height']) for x in manifest['images']}
    cards=[]
    for row in results:
        columns=[]
        for name,label in [('paddle_raw','Paddle 原始多视图'),('paddle_06','Paddle 现有0.6筛选'),('paddle_single','Paddle 单图控制'),('qwen','Qwen 8B')]:
            if name not in row['models']:continue
            m=row['models'][name];cer=f'{m["cer"]:.1%}' if m['cer'] is not None else '不适用'
            columns.append(f'<div><b>{label}</b><p>{m["presence"]} / CER {cer}</p><pre>{html.escape(m["text"] or "（无转写）")}</pre></div>')
        width,height=sizes[ids[row['id']]];scale=min(8,900/width,500/height)
        image_path=f'run/images/{ids[row["id"]]}.png'
        cards.append(f'<article><h2>{names[row["category"]]} · {row["id"]}</h2><p>{html.escape(row["note"])}</p><a href="{image_path}" target="_blank" rel="noopener"><img loading="lazy" style="width:min(100%,{round(width*scale)}px)" src="{image_path}"></a><h3>预先冻结的参考原文</h3><pre>{html.escape(row["expected"] or "（确认无字）")}</pre><section>{"".join(columns)}</section></article>')
    page='''<!doctype html><meta charset="utf-8"><title>本地OCR与Qwen对照</title><style>body{font:16px "Microsoft YaHei",sans-serif;background:#f3f4f5;color:#222;max-width:1500px;margin:30px auto;padding:0 20px}article{background:white;padding:22px;margin:22px 0;border-radius:10px}img{display:block;max-width:100%;max-height:550px;background:#888;image-rendering:pixelated}section{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}section>div{background:#eef1f5;padding:15px}pre{white-space:pre-wrap;overflow-wrap:anywhere}p{line-height:1.6}</style><h1>PP-OCRv6 medium / Qwen3-VL 8B：20张小样本对照</h1><p>每类4张，目的性开发样本。不是全游戏/跨游戏准确率；18张真实素材或截图，2张程序排版对照。故障残字只作诊断，不纳入主CER。</p><p>CER仅保留字母数字，忽略大小写、标点和空白；越低越好。uncertain保留待审，不算转写成功。正常文字均正确不等于其他类别可靠。主流程输出与单图控制分列，避免把分块重复全算成模型能力。无字误报需看presence，不能看CER。</p>'''+''.join(cards)
    (work/'comparison.html').write_text(page,encoding='utf-8')
    print(json.dumps(dict(aggregate=aggregate,timing=dict(paddle=timing['total_seconds'],qwen=vtime['elapsed_seconds'])),ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['prepare','ocr','single','vlm','report','all'])
    p.add_argument('--work',required=True);p.add_argument('--cases',default=str(DEFAULT));a=p.parse_args();work=Path(a.work).resolve()
    if a.phase in ('prepare','all'):prepare(work,read_json(a.cases))
    if a.phase=='all':
        if not (work/'run/scan.json').exists():subprocess.run([sys.executable,__file__,'ocr','--work',str(work)],check=True)
        if not (work/'paddle_single.json').exists():subprocess.run([sys.executable,__file__,'single','--work',str(work)],check=True)
        vlm(work);report(work)
    elif a.phase=='ocr':ocr(work)
    elif a.phase=='vlm':vlm(work)
    elif a.phase=='single':single(work)
    elif a.phase=='report':report(work)

if __name__=='__main__':main()
