"""Image localization stages: opt-in, routing, local review, visual decisions, patch preparation."""
import argparse
from collections import Counter
from pathlib import Path
import subprocess
import sys
from core import read_json

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='command',required=True)
    q=subs.add_parser('configure');q.add_argument('--project',required=True);q.add_argument('--choice',choices=['enabled','disabled','undecided'],required=True);q.add_argument('--reason',required=True)
    for name in ('plan','pipeline'):
        q=subs.add_parser(name);q.add_argument('--out',required=True);q.add_argument('--policy');q.add_argument('--project',required=name=='pipeline')
    q=subs.add_parser('qwen');q.add_argument('--out',required=True);q.add_argument('--batch',type=int)
    q=subs.add_parser('agent-pack');q.add_argument('--out',required=True);q.add_argument('--limit',type=int,default=20)
    q=subs.add_parser('agent-import');q.add_argument('--out',required=True);q.add_argument('--package',required=True);q.add_argument('--reply',required=True);q.add_argument('--reviewer',choices=['conversation_agent','human'],required=True);q.add_argument('--model-label')
    q=subs.add_parser('export');q.add_argument('--out',required=True);q.add_argument('--csv',required=True);q.add_argument('--allow-partial',action='store_true')
    q=subs.add_parser('prepare-translation');q.add_argument('--project',required=True);q.add_argument('--source',required=True)
    q=subs.add_parser('collect-translation');q.add_argument('--project',required=True)
    q=subs.add_parser('build');q.add_argument('--out',required=True);q.add_argument('--source',required=True);q.add_argument('--translated',required=True);q.add_argument('--validation',required=True);q.add_argument('--font',required=True);q.add_argument('--destination',required=True);q.add_argument('--mode',choices=['texture','overlay'],default='texture');q.add_argument('--mask-alpha',type=int,default=64)
    q.add_argument('--text-rgba',type=int,nargs=4,default=[255,255,255,255],metavar=('R','G','B','A'))
    q.add_argument('--texture-styles',type=Path,help='Image-ID-bound JSON style overrides for opaque texture regions')
    a=p.parse_args(argv)
    if a.command=='configure':
        from project_options import configure
        result=configure(a.project,a.choice,a.reason)
    elif a.command in ('plan','pipeline'):
        import tiered
        if a.project:
            from project_options import require_enabled
            require_enabled(a.project)
        if a.command=='pipeline':
            subprocess.run([sys.executable,str(Path(__file__).with_name('cli.py')),'scan','--out',a.out,'--device','cuda','--model-size','medium'],check=True)
        plan=tiered.plan(a.out,read_json(a.policy) if a.policy else None)
        result=dict(routes=dict(Counter(x['route'] for x in plan['routes'])),plan_hash=plan['plan_hash'])
        if a.command=='pipeline':tiered.qwen_batches(a.out);result['agent']=tiered.package(a.out)
    elif a.command=='qwen':
        from tiered import qwen_batches
        result=qwen_batches(a.out,a.batch);result=dict(completed=len(result['images']),invocation=result['invocation'])
    elif a.command=='agent-pack':
        from tiered import package
        result=package(a.out,a.limit)
    elif a.command=='agent-import':
        from tiered import import_decisions
        result=import_decisions(a.out,a.package,a.reply,a.reviewer,a.model_label)
    elif a.command=='export':
        from tiered import export
        result=export(a.out,a.csv,a.allow_partial)
    elif a.command=='prepare-translation':
        from translation_bridge import prepare
        result=prepare(a.project,a.source)
    elif a.command=='collect-translation':
        from translation_bridge import collect
        result=collect(a.project)
    elif a.command=='build':
        from image_patch import build
        result=build(a.out,a.source,a.translated,a.validation,a.font,a.destination,a.mode,a.mask_alpha,a.text_rgba,read_json(a.texture_styles) if a.texture_styles else None)
        result=dict(status=result['status'],mode=result['mode'],files=len(result['files']),entries=len(result['entries']),installed=False)
    import json
    print(json.dumps(result,ensure_ascii=False))
    return result

if __name__=='__main__':main()
