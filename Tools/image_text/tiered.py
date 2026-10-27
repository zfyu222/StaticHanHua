"""Three-tier routing, bounded local batches, visual adjudication and confirmed export."""
import csv
import math
from pathlib import Path
import random
import shutil
import re
from PIL import Image,ImageDraw
from core import canonical,digest,load_image,pixel_hash,read_json,validate_box,write_json
from screening import readable
from ocr_policy import require_standard_scan

def bound(out):
    out=Path(out);m=read_json(out/'manifest.json');s=read_json(out/'scan.json')
    return out,m,s,digest(canonical(s))

def plan(out,policy=None):
    out,m,s,scan_hash=bound(out)
    require_standard_scan(s)
    policy=policy or {};script=policy.get('script','any')
    if script not in ('any','latin'):raise ValueError('Unsupported source script')
    rows={x['image_id']:x for x in s['images']};hints=policy.get('images',{});families=policy.get('families',{})
    routes=[]
    for image in m['images']:
        key=image['image_id'];r=rows.get(key);hint=hints.get(key)
        if hint is None:hint=next((hints[x['path']] for x in image['sources'] if x['path'] in hints),{})
        family=families.get(hint.get('family'),{})
        limits=family.get('glyph_height_range',[])
        validated=(family.get('kind')=='normal' and family.get('validated') is True and bool(family.get('evidence','').strip()) and len(limits)==2)
        risks=list(hint.get('risks',[]));regions=r.get('regions',[]) if r else []
        plausible=any(readable(x,.6,script) for x in regions)
        known=hint.get('known_text') is True
        if validated:validated=bool(regions) and all(limits[0]<=x['box'][3]-x['box'][1]<=limits[1] for x in regions)
        tiny=bool(regions) and max(x['box'][3]-x['box'][1] for x in regions)<=8
        stable=bool(regions) and all(x['text'].strip() and x['recognition_score']>=policy.get('ordinary_score',.95) and not x.get('disagreement') for x in regions)
        if not r or r.get('errors'):route,reason='blocked','scan_missing_or_failed'
        elif (known or plausible) and ((tiny and not validated) or 'glitch' in risks):route,reason='agent','extreme_pixel_or_glitch'
        elif known or risks:route,reason='qwen','known_text_or_risky_family'
        elif plausible and validated and stable:route,reason='candidate','validated_normal_family'
        elif plausible:route,reason='qwen','unknown_or_unstable_text'
        else:route,reason='excluded','weak_signal' if regions else 'not_detected'
        routes.append(dict(image_id=key,route=route,reason=reason,family=hint.get('family'),risks=risks))
    rng=random.Random(policy.get('seed',42))
    for reason,amount in [('not_detected',policy.get('negative_samples',20)),('weak_signal',policy.get('weak_samples',10))]:
        if type(amount) is not int or amount<0:raise ValueError('Invalid audit sample count')
        pool=[x for x in routes if x['route']=='excluded' and x['reason']==reason]
        # Size strata prevent an audit composed exclusively of tiny sprites.
        sizes={x['image_id']:x['width']*x['height'] for x in m['images']}
        pool.sort(key=lambda x:(sizes[x['image_id']],x['image_id']))
        groups=[pool[i*len(pool)//3:(i+1)*len(pool)//3] for i in range(3)];selected=[]
        while len(selected)<min(amount,len(pool)):
            for group in groups:
                if group and len(selected)<amount:selected.append(group.pop(rng.randrange(len(group))))
        for row in selected:row.update(route='qwen',reason=reason+'_audit')
    n=policy.get('ordinary_samples',10)
    if type(n) is not int or n<0:raise ValueError('Invalid ordinary audit count')
    ordinary=[x for x in routes if x['route']=='candidate']
    for row in rng.sample(ordinary,min(n,len(ordinary))):row.update(route='qwen',reason='ordinary_audit')
    size=policy.get('qwen_batch',100)
    if type(size) is not int or not 1<=size<=1000:raise ValueError('Invalid Qwen batch size')
    qwen=[x['image_id'] for x in routes if x['route']=='qwen']
    result=dict(schema=1,scan_hash=scan_hash,manifest_hash=m['manifest_hash'],policy=policy,routes=routes,
                qwen_batches=[qwen[i:i+size] for i in range(0,len(qwen),size)])
    result['plan_hash']=digest(canonical(result));target=out/'tiered/plan.json'
    if target.exists() and read_json(target)!=result:raise ValueError('Routing plan changed; use a new work directory to preserve existing evidence')
    write_json(target,result);return result

def load_plan(out):
    out,m,s,h=bound(out);p=read_json(out/'tiered/plan.json')
    if p['scan_hash']!=h or p['manifest_hash']!=m['manifest_hash'] or p['plan_hash']!=digest(canonical({k:v for k,v in p.items() if k!='plan_hash'})):
        raise ValueError('Stale or modified routing plan')
    return out,m,s,p

def qwen_batches(out,batch=None):
    from handoff import prepare
    from local_vlm import run
    from ollama_server import Server
    out,m,s,p=load_plan(out);all_findings={};executed=reused=requests=0
    require_standard_scan(s)
    numbers=list(range(len(p['qwen_batches']))) if batch is None else [batch-1]
    if any(i<0 or i>=len(p['qwen_batches']) for i in numbers):raise ValueError('Unknown batch')
    for number in numbers:
        ids=p['qwen_batches'][number];folder=out/'tiered/qwen'/str(number+1)
        subset=dict(m);subset['images']=[x for x in m['images'] if x['image_id'] in ids]
        subset['files']=[x for x in m['files'] if x['image_id'] in ids];subset['errors']=[];subset.pop('manifest_hash')
        subset['manifest_hash']=digest(canonical(subset))
        scan=dict(s,manifest_hash=subset['manifest_hash'],images=[x for x in s['images'] if x['image_id'] in ids])
        if (folder/'scan.json').exists() and read_json(folder/'scan.json')!=scan:raise ValueError('Saved Qwen batch belongs to different OCR')
        for key in ids:
            source=out/'images'/f'{key}.png'
            if pixel_hash(load_image(source))!=key:raise ValueError('Changed image')
            target=folder/'images'/source.name;target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and pixel_hash(load_image(target))!=key:raise ValueError('Changed batch image')
            if not target.exists():shutil.copy2(source,target)
        write_json(folder/'manifest.json',subset);write_json(folder/'scan.json',scan)
        prepare(folder,max_jobs=len(ids),negative_samples=0,low_signal_samples=0,policy='full-audit')
        findings=read_json(folder/'vision_findings.json') if (folder/'vision_findings.json').exists() else {}
        if len(findings.get('records',[]))!=len(ids):
            with Server() as server:receipt=run(folder,server.endpoint)
            executed+=1;requests+=receipt['requests']
        else:reused+=1
    for i in range(len(p['qwen_batches'])):
        path=out/'tiered/qwen'/str(i+1)/'vision_findings.json'
        if path.exists():
            folder=path.parent;findings=read_json(path)
            if findings['scan_hash']!=digest(canonical(read_json(folder/'scan.json'))):raise ValueError('Stale Qwen findings')
            for row in findings['records']:
                if row['image_id'] not in p['qwen_batches'][i]:raise ValueError('Unexpected Qwen image')
                all_findings[row['image_id']]=row
    result=dict(plan_hash=p['plan_hash'],images=all_findings,invocation=dict(executed_batches=executed,reused_batches=reused,requests=requests))
    write_json(out/'tiered/findings.json',result);return result

def tasks(out):
    from compare_models import ordered
    out,m,s,p=load_plan(out);raw={x['image_id']:x for x in s['images']}
    finding_path=out/'tiered/findings.json';findings=read_json(finding_path) if finding_path.exists() else dict(plan_hash=p['plan_hash'],images={})
    if findings['plan_hash']!=p['plan_hash']:raise ValueError('Stale merged findings')
    result=[]
    normalize=lambda t:re.sub(r'\s+','',t)
    for row in p['routes']:
        key=row['image_id'];route=row['route']
        if route not in ('agent','candidate','qwen'):continue
        r=raw[key];vision=findings['images'].get(key)
        if route=='qwen' and vision is None:continue
        reason=row['reason'];purpose='dispute' if route=='agent' else 'batch_acceptance'
        if vision:
            credible=any(readable(x,.6,p['policy'].get('script','any')) for x in r['regions'])
            if vision['presence']=='no_text' and not credible and not row['risks'] and row['reason'].endswith('_audit'):continue
            equal=normalize(ordered([x for x in r['regions'] if x['text'].strip()]))==normalize(ordered(vision['proposals']))
            if vision['presence']!='text' or not equal:purpose='dispute';reason='model_conflict_or_new_text'
        result.append(dict(image_id=key,purpose=purpose,reason=reason,paddle=r['regions'],qwen=vision))
    return result

def package(out,limit=20,direct_image_ids=None):
    if not 1<=limit<=100:raise ValueError('Agent package limit must be 1..100')
    out,m,s,p=load_plan(out);decisions_path=out/'tiered/decisions.json'
    previous=read_json(decisions_path) if decisions_path.exists() else dict(plan_hash=p['plan_hash'],images={})
    if previous['plan_hash']!=p['plan_hash']:raise ValueError('Stale decisions')
    candidates=tasks(out)
    if direct_image_ids is not None:
        selected=set(direct_image_ids)
        routes={x['image_id']:x for x in p['routes']}
        if not selected or any(k not in routes or routes[k]['route'] in ('excluded','blocked') for k in selected):
            raise ValueError('Direct visual review requires explicit, non-blocked candidate image IDs')
        raw={x['image_id']:x for x in s['images']}
        existing={x['image_id']:x for x in candidates}
        candidates=[existing[k] if k in existing else dict(image_id=k,purpose='dispute',reason='explicit_direct_visual_review_without_qwen_result',paddle=raw[k]['regions'],qwen=None) for k in sorted(selected)]
    pending=[x for x in candidates if x['image_id'] not in previous['images'] or previous['images'][x['image_id']]['unreadable']]
    pending.sort(key=lambda x:(x['purpose']!='dispute',x['image_id']))
    jobs=[]
    for item in pending[:limit]:
        key=item['image_id'];im=load_image(out/'images'/f'{key}.png')
        if pixel_hash(im)!=key:raise ValueError('Changed review image')
        jobs.append(dict(item,id=str(len(jobs)+1),size=list(im.size),image=f'images/{key}.png'))
    pack=dict(schema=2,plan_hash=p['plan_hash'],jobs=jobs,pending=len(pending))
    pack['package_hash']=digest(canonical(pack));folder=out/'tiered/agent'/pack['package_hash']
    for job in jobs:
        im=load_image(out/job['image']);bg=Image.new('RGBA',im.size,(128,128,128,255));bg.alpha_composite(im)
        scale=min(8,max(1,512//max(im.size)));view=bg.resize((im.width*scale,im.height*scale),Image.Resampling.NEAREST)
        view_path=folder/f'{job["id"]}.png';view_path.parent.mkdir(parents=True,exist_ok=True);view.save(view_path)
        job['evidence_view']=str(view_path.relative_to(out));job['evidence_sha256']=digest(view_path.read_bytes())
    pack['contact_sheets']=[]
    for offset in range(0,len(jobs),6):
        page=Image.new('RGB',(1560,1170),(230,230,230));draw=ImageDraw.Draw(page)
        for n,job in enumerate(jobs[offset:offset+6]):
            im=load_image(out/job['evidence_view']).convert('RGB');scale=min(1,750/im.width,345/im.height)
            im=im.resize((max(1,int(im.width*scale)),max(1,int(im.height*scale))),Image.Resampling.NEAREST)
            x=(n%2)*780+10;y=(n//2)*390+10;draw.text((x,y),'ID '+job['id'],fill='black');page.paste(im,(x,y+25))
        path=folder/f'contact-{offset//6+1}.png';page.save(path)
        pack['contact_sheets'].append(dict(path=str(path.relative_to(out)),sha256=digest(path.read_bytes())))
    # The package digest binds the generated evidence too.
    pack['binding_hash']=digest(canonical(pack));write_json(folder/'package.json',pack)
    write_json(folder/'input.json',dict(instruction='View each image. Preserve exact visible text, case, punctuation and numbers. Resolve candidates by pixels, not model vote. Return id,status=text/no_text/uncertain,reason,unreadable boolean,regions with text and normalized 0..1000 box. Unknown parts stay uncertain. No translation.',records=[dict(id=x['id'],image=x['evidence_view'],context=x['image'],purpose=x['purpose'],paddle=[dict(text=r['text'],box=r['box']) for r in x['paddle']],qwen=x['qwen']) for x in jobs]))
    return dict(package=str(folder/'package.json'),selected=len(jobs),pending=len(pending))

def import_decisions(out,package_path,reply_path,reviewer,model_label=None):
    out,m,s,p=load_plan(out);pack=read_json(package_path);reply=read_json(reply_path)
    if reviewer not in ('conversation_agent','human'):raise ValueError('Explicit visual reviewer type required')
    if pack['plan_hash']!=p['plan_hash'] or pack['binding_hash']!=digest(canonical({k:v for k,v in pack.items() if k!='binding_hash'})):raise ValueError('Changed or stale agent package')
    for sheet in pack.get('contact_sheets',[]):
        if digest((out/sheet['path']).read_bytes())!=sheet['sha256']:raise ValueError('Contact sheet changed after visual review')
    jobs={x['id']:x for x in pack['jobs']};images={x['image_id']:x for x in m['images']};seen=set();updates={}
    if not isinstance(reply.get('records'),list):raise ValueError('Explicit records required')
    for row in reply['records']:
        ref=row.get('id')
        if ref not in jobs or ref in seen:raise ValueError('Unknown or duplicate review id')
        seen.add(ref);job=jobs[ref];key=job['image_id'];im=load_image(out/job['image'])
        if pixel_hash(im)!=key or digest((out/job['evidence_view']).read_bytes())!=job['evidence_sha256']:raise ValueError('Visual evidence changed')
        state=row.get('status');reason=row.get('reason');regions=row.get('regions');unreadable=row.get('unreadable')
        if state not in ('text','no_text','uncertain') or not isinstance(reason,str) or not reason.strip() or type(unreadable) is not bool or not isinstance(regions,list):raise ValueError('Missing visual decision, reason, unreadable flag or regions')
        if (state=='text')!=bool(regions) or state=='no_text' and unreadable:raise ValueError('Contradictory visual decision')
        restored=[]
        for r in regions:
            if not isinstance(r.get('text'),str) or not r['text'].strip():raise ValueError('Empty confirmed text')
            validate_box(r.get('box'),1000,1000);w,h=job['size'];box=[round(v*(w if i%2==0 else h)/1000,4) for i,v in enumerate(r['box'])];validate_box(box,w,h)
            restored.append(dict(text=r['text'],box=box))
        updates[key]=dict(image_id=key,status='agent_visual_confirmed' if state=='text' and reviewer=='conversation_agent' else 'human_confirmed' if state=='text' else 'visual_confirmed_no_text' if state=='no_text' else 'unreadable',
                          regions=restored,unreadable=unreadable or state=='uncertain',reason=reason,reviewer_type=reviewer,model_label=model_label,evidence=job['evidence_view'],package_hash=pack['binding_hash'],sources=images[key]['sources'])
    target=out/'tiered/decisions.json';current=read_json(target) if target.exists() else dict(plan_hash=p['plan_hash'],images={})
    if current['plan_hash']!=p['plan_hash']:raise ValueError('Stale saved decisions')
    for key,row in updates.items():
        if key in current['images'] and current['images'][key]!=row:
            old=current['images'][key]
            if not old['unreadable']:raise ValueError('Conflicting visual decision; preserve existing revision')
            current.setdefault('revisions',[]).append(dict(image_id=key,superseded=old,replacement_package_hash=pack['binding_hash']))
    archive=out/'tiered/agent_replies'/f'{digest(Path(reply_path).read_bytes())}.json';archive.parent.mkdir(parents=True,exist_ok=True);archive.write_bytes(Path(reply_path).read_bytes())
    current['images'].update(updates);write_json(target,current);return dict(imported=len(updates),human_approval=reviewer=='human')

def export(out,target,allow_partial=False):
    out,m,s,p=load_plan(out);decisions=read_json(out/'tiered/decisions.json')
    if decisions['plan_hash']!=p['plan_hash']:raise ValueError('Stale confirmation')
    expected={x['image_id'] for x in p['routes'] if x['route']!='excluded'}
    # Audited weak/no-text images do not need invented text or an automatic visual confirmation.
    finding=read_json(out/'tiered/findings.json') if (out/'tiered/findings.json').exists() else dict(images={})
    for row in p['routes']:
        v=finding['images'].get(row['image_id'])
        if row['reason'].endswith('_audit') and row['route']=='qwen' and v and v['presence']=='no_text' and row['reason']!='ordinary_audit':expected.discard(row['image_id'])
    unresolved=expected-set(decisions['images'])
    unresolved|={k for k,x in decisions['images'].items() if x['unreadable']}
    if unresolved and not allow_partial:raise ValueError('Unreviewed/unreadable items remain; finish review or explicitly export confirmed subset')
    target=Path(target);mapping=target.with_suffix('.mapping.json')
    if target.exists() or mapping.exists():raise ValueError('Refusing to overwrite existing source/mapping')
    texts=[];regions=[]
    for image in m['images']:
        key=image['image_id'];row=decisions['images'].get(key)
        if not row or row['status'] not in ('agent_visual_confirmed','human_confirmed'):continue
        if pixel_hash(load_image(out/'images'/f'{key}.png'))!=key:raise ValueError('Changed confirmed image')
        for i,r in enumerate(row['regions']):
            if r['text'] not in texts:texts.append(r['text'])
            regions.append(dict(csv_row=texts.index(r['text'])+1,image_id=key,region_id=f'confirmed-{key[:16]}-{i}',box=r['box'],sources=row['sources'],confirmation={k:row[k] for k in ('status','reason','reviewer_type','model_label','evidence','package_hash')}))
    if not texts:raise ValueError('No visually confirmed text')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('w',encoding='utf-8-sig',newline='') as f:csv.writer(f).writerows([[x] for x in texts])
    write_json(mapping,dict(schema=2,csv_sha256=digest(target.read_bytes()),records=len(texts),regions=regions,scan_hash=p['scan_hash'],plan_hash=p['plan_hash'],unresolved_image_ids=sorted(unresolved),scope='visually_confirmed_subset',human_approval=False))
    return dict(records=len(texts),positions=len(regions),unresolved=len(unresolved))
