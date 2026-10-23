"""Reusable local image-text discovery workflow. Never modifies input or game assets."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
from core import canonical, digest, export_review, inventory, load_image, pixel_hash, read_json, validate_box, write_json


def scan(args):
    invocation_started=time.perf_counter()
    from engine import Engine, analyze
    out = Path(args.out).resolve()
    manifest = read_json(out/'manifest.json')
    options=dict(threads=args.threads,box_threshold=args.threshold,language=args.language)
    if getattr(args,'model_size','small')!='small' or getattr(args,'device','cpu')!='cpu':
        options.update(model_size=args.model_size,device=args.device)
    engine = Engine(**options)
    engine_init_seconds=time.perf_counter()-invocation_started
    config = dict(engine=engine.metadata, profile=args.profile, sprite_crops=args.sprite_crops)
    fingerprint = digest(canonical(config))
    selected = set(read_json(args.ids)) if args.ids else None
    available = {x['image_id'] for x in manifest['images']}
    if selected is not None and not selected <= available:
        raise ValueError('Unknown selected image ID')
    images = [x for x in manifest['images'] if selected is None or x['image_id'] in selected]
    if args.limit:
        images = images[:args.limit]
    old = read_json(out/'scan.json') if (out/'scan.json').exists() else {}
    if old and (old['fingerprint'] != fingerprint or old['manifest_hash'] != manifest['manifest_hash']):
        raise ValueError('Scan settings/input changed; use a separate output directory or explicitly archive scan.json first')
    records = {x['image_id']: x for x in old.get('images', [])}
    started = time.perf_counter()
    counts = Counter()
    for number, image in enumerate(images, 1):
        image_id = image['image_id']
        source_image = load_image(out/'images'/f'{image_id}.png')
        if pixel_hash(source_image) != image_id:
            raise ValueError('Snapshot image changed: '+image_id)
        extra_boxes=[]
        if args.sprite_crops:
            for source in image['sources']:
                for sprite in source['provenance'].get('sprites', []):
                    box=sprite['box']
                    validate_box(box,image['width'],image['height'])
                    if box != [0,0,image['width'],image['height']] and box not in extra_boxes:
                        extra_boxes.append(box)
        cache_key=digest(canonical([image_id,extra_boxes]))
        cache = out/'cache'/fingerprint/f'{cache_key}.json'
        if cache.exists() and not args.refresh:
            record = read_json(cache)
            counts['cached_images'] += 1
        else:
            record = analyze(engine, source_image, image_id, args.profile, extra_boxes)
            # Failed scans are saved for diagnosis but never reusable as successful results.
            if not record['errors']:
                write_json(cache, record)
            counts['computed_images'] += 1
        records[image_id] = record
        snapshot = dict(schema=1, manifest_hash=manifest['manifest_hash'], fingerprint=fingerprint,
                        config=config, images=[records[x['image_id']] for x in manifest['images'] if x['image_id'] in records])
        if number == 1 or number % 25 == 0 or number == len(images) or record['errors']:
            write_json(out/'scan.json', snapshot)
        if number == 1 or number % 25 == 0 or number == len(images):
            print(f'scan {number}/{len(images)}; regions={sum(len(x["regions"]) for x in records.values())}; elapsed={time.perf_counter()-started:.1f}s', flush=True)
    if not images:
        raise ValueError('No selected images')
    status = Counter(x['status'] for x in records.values())
    summary = dict(total_unique_images=len(manifest['images']), scanned_images=len(records),
                   not_scanned=len(manifest['images'])-len(records), status=dict(status), invocation=dict(counts),
                   wall_seconds=round(time.perf_counter()-invocation_started, 3),
                   engine_init_seconds=round(engine_init_seconds,3), scan_seconds=round(time.perf_counter()-started,3), online_requests=0,
                   regions=sum(len(x['regions']) for x in records.values()), fingerprint=fingerprint,
                   note='not_detected does not mean confirmed_no_text; OCR scores are not calibrated correctness probabilities')
    write_json(out/'summary.json', summary)
    print(json.dumps(summary))


def review_page(args):
    from review import build_review
    print(json.dumps(build_review(Path(args.out))))


def queue(args):
    from handoff import prepare
    result=prepare(args.out,args.max_jobs,args.negative_samples,getattr(args,'low_signal_samples',10),args.seed,
                   getattr(args,'policy','screened'),getattr(args,'screen_score',.6),getattr(args,'screen_script','any'))
    print(json.dumps({k:result[k] for k in ('mode','total_candidates','selected','deferred','raw_uncertain_regions','reserved_negative_audit','reserved_low_signal_audit')}))


def benchmark(args):
    """Measured detection recall against explicit known-positive boxes, not total game coverage."""
    from core import overlap
    out = Path(args.out)
    manifest, scan, truth = read_json(out/'manifest.json'), read_json(out/'scan.json'), read_json(args.truth)
    sources = {s['path']: x['image_id'] for x in manifest['images'] for s in x['sources']}
    detected = {x['image_id']: x for x in scan['images']}
    rows = []
    for item in truth['cases']:
        image_id = sources[item['path']]
        result = detected.get(image_id)
        if result is None or result['errors']:
            rows.append(dict(**item, evaluated=False, error='not_scanned_or_scan_error'))
            continue
        if item.get('has_text') is False:
            rows.append(dict(**item, evaluated=True, false_positive=bool(result['regions']),
                             stable_candidate=result['status']=='text_candidate'))
            continue
        boxes = item.get('boxes', [])
        hits = [any(overlap(box, r['box']) >= .1 or coverage(box, r['box']) >= .5 for r in result['regions']) for box in boxes]
        rows.append(dict(**item, evaluated=True, detected=bool(result['regions']), stable_candidate=result['status']=='text_candidate', box_hits=hits,
                         ocr_text='\n'.join(r['text'] for r in result['regions'])))
    positives = [x for x in rows if x['evaluated'] and x.get('has_text', True)]
    negatives = [x for x in rows if x['evaluated'] and x.get('has_text') is False]
    report = dict(schema=1, scope=truth.get('scope', 'labeled sample only'), cases=rows,
                  evaluated=len(positives)+len(negatives), unevaluated=sum(not x['evaluated'] for x in rows),
                  positive_images=len(positives), detected_positive_images=sum(x['detected'] for x in positives),
                  stable_positive_candidates=sum(x['stable_candidate'] for x in positives),
                  labeled_boxes=sum(len(x.get('box_hits', [])) for x in positives),
                  hit_boxes=sum(sum(x.get('box_hits', [])) for x in positives),
                  negative_images=len(negatives), false_positive_images=sum(x['false_positive'] for x in negatives),
                  stable_negative_candidates=sum(x['stable_candidate'] for x in negatives),
                  note='Detection overlap is not transcription accuracy. No ground-truth negatives means false-positive rate unavailable; no full-game coverage claim.')
    write_json(out/'benchmark.json', report)
    print(json.dumps({k:v for k,v in report.items() if k != 'cases'}))


def coverage(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
    return intersection/max(1, (a[2]-a[0])*(a[3]-a[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('inventory')
    p.add_argument('--input', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--provenance', help='JSON mapping relative image paths to engine-specific source metadata')
    p=commands.add_parser('local-vlm',help='Run local Qwen on an existing queue; OCR must already have exited')
    p.add_argument('--out',required=True);p.add_argument('--limit',type=int);p.add_argument('--pull',action='store_true')
    p=commands.add_parser('local-pipeline',help='Full GPU medium OCR, process exit, then local Qwen')
    p.add_argument('--out',required=True);p.add_argument('--limit',type=int);p.add_argument('--pull',action='store_true')
    p.add_argument('--screen-script',choices=['any','latin'],default='any')
    p.add_argument('--policy',choices=['screened','recall-first','full-audit'],default='screened')
    p = commands.add_parser('scan')
    p.add_argument('--out', required=True)
    p.add_argument('--profile', choices=['fast', 'balanced', 'careful'], default='balanced')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--threshold', type=float, default=.35)
    p.add_argument('--language', default='ch', help='RapidOCR language; default bundled Chinese/English-capable model')
    p.add_argument('--model-size',choices=['tiny','small','medium'],default='small')
    p.add_argument('--device',choices=['cpu','cuda'],default='cpu')
    p.add_argument('--limit', type=int)
    p.add_argument('--ids', help='JSON array of image hashes to scan')
    p.add_argument('--refresh', action='store_true', help='Explicitly recompute selected images; failed scans are always retried')
    p.add_argument('--sprite-crops', action='store_true', help='Also scan provenance sprite rectangles; extra CPU cost')
    p = commands.add_parser('review')
    p.add_argument('--out', required=True)
    p = commands.add_parser('queue')
    p.add_argument('--out', required=True)
    p.add_argument('--max-jobs', type=int, default=100)
    p.add_argument('--negative-samples', type=int, default=20)
    p.add_argument('--low-signal-samples', type=int, default=10)
    p.add_argument('--policy', choices=['screened','recall-first','uncertain-only','full-audit'], default='screened')
    p.add_argument('--screen-score',type=float,default=.6)
    p.add_argument('--screen-script',choices=['any','latin'],default='any')
    p.add_argument('--seed', type=int, default=42)
    p = commands.add_parser('screen-report')
    p.add_argument('--out',required=True)
    p.add_argument('--truth')
    p.add_argument('--screen-script',choices=['any','latin'],default='any')
    p = commands.add_parser('triage')
    p.add_argument('--out', required=True)
    p = commands.add_parser('import-findings')
    p.add_argument('--out', required=True)
    p.add_argument('--reply', required=True)
    p.add_argument('--binding', help='Saved queue/binding.json; keeps long hashes out of model replies')
    p = commands.add_parser('export')
    p.add_argument('--out', required=True)
    p.add_argument('--review', required=True)
    p.add_argument('--csv', required=True)
    p = commands.add_parser('benchmark')
    p.add_argument('--out', required=True)
    p.add_argument('--truth', required=True)
    args = parser.parse_args()
    if args.command in ('local-vlm','local-pipeline'):
        if args.limit is not None and args.limit<1:parser.error('limit must be positive')
        if args.command=='local-pipeline':
            from pipeline import execute
            execute(args.out,args.screen_script,args.policy,args.limit,args.pull)
        else:
            from local_vlm import run,MODEL
            from ollama_server import Server
            with Server() as server:
                if args.pull:server.pull(MODEL)
                run(args.out,server.endpoint,limit=args.limit)
            review_page(args)
        return
    if args.command == 'inventory':
        result = inventory(args.input, args.out, read_json(args.provenance) if args.provenance else None)
        print(json.dumps(dict(files=len(result['files']), unique=len(result['images']), errors=len(result['errors']))))
    elif args.command == 'scan':
        if args.threads < 1 or args.limit is not None and args.limit < 1 or not 0 < args.threshold < 1:
            parser.error('threads/limit must be positive and threshold must be in (0,1)')
        scan(args)
    elif args.command == 'review':
        review_page(args)
    elif args.command == 'queue':
        if args.max_jobs < 1 or args.negative_samples < 0 or args.low_signal_samples < 0:
            parser.error('invalid queue limits')
        queue(args)
    elif args.command == 'triage':
        from triage import prioritize
        out=Path(args.out)
        result=prioritize(read_json(out/'scan.json'),read_json(out/'manifest.json'))
        write_json(out/'triage.json',result)
        print(json.dumps(dict(Counter(x['priority'] for x in result['images']))))
    elif args.command == 'screen-report':
        from screening import compare
        out=Path(args.out)
        result=compare(read_json(out/'scan.json'),read_json(out/'manifest.json'),read_json(args.truth) if args.truth else None,script=args.screen_script)
        write_json(out/'screen_comparison.json',result)
        print(json.dumps(result))
    elif args.command == 'import-findings':
        from handoff import import_findings
        print(json.dumps(import_findings(args.out,args.reply,args.binding)))
    elif args.command == 'export':
        print(json.dumps(export_review(args.out, args.review, args.csv)))
    elif args.command == 'benchmark':
        benchmark(args)


if __name__ == '__main__':
    main()
