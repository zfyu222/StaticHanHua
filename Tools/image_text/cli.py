"""Reusable local image-text discovery workflow. Never modifies input or game assets."""
import argparse
from collections import Counter
import importlib.metadata
import json
from pathlib import Path
import random
import time
from core import canonical, digest, export_review, inventory, load_image, pixel_hash, read_json, validate_box, write_json


def scan(args):
    invocation_started=time.perf_counter()
    from engine import Engine, analyze
    out = Path(args.out).resolve()
    manifest = read_json(out/'manifest.json')
    engine = Engine(threads=args.threads, box_threshold=args.threshold, language=args.language)
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
    """Budgeted, offline handoff. No request is sent and no paid cost is incurred."""
    out = Path(args.out)
    manifest, scan = read_json(out/'manifest.json'), read_json(out/'scan.json')
    items, negatives = [], []
    byid = {x['image_id']: x for x in manifest['images']}
    for image in scan['images']:
        if image['status'] == 'scan_failed':
            items.append(dict(image_id=image['image_id'], region_id=None, reason='scan_failed'))
        elif not image['regions']:
            negatives.append(image['image_id'])
        for region in image['regions']:
            if region['status'] == 'needs_review':
                items.append(dict(image_id=image['image_id'], region_id=region['region_id'],
                                  box=region['box'], reason='empty_low_score_or_disagreement', ocr_text=region['text']))
    region_lookup={r['region_id']:r for image in scan['images'] for r in image['regions']}
    def priority(item):
        if item['region_id'] is None:return 0
        region=region_lookup[item['region_id']]
        if region['disagreement']:return 1
        image=byid[item['image_id']]
        return 3 if max(image['width'],image['height'])<=32 else 2
    items.sort(key=priority)
    # Deterministic strata by image area; negative audit cannot only sample low OCR scores.
    negatives.sort(key=lambda key: byid[key]['width']*byid[key]['height'])
    rng = random.Random(args.seed)
    strata = [negatives[i*len(negatives)//3:(i+1)*len(negatives)//3] for i in range(3)]
    chosen = []
    while len(chosen) < min(args.negative_samples, len(negatives)):
        for stratum in strata:
            if stratum and len(chosen) < args.negative_samples:
                chosen.append(stratum.pop(rng.randrange(len(stratum))))
    # Put the reserved negative audit sample before difficult positives so it cannot be starved by budget.
    ordered = [dict(image_id=key, region_id=None, reason='not_detected_audit_sample') for key in chosen]+items
    jobs = []
    for item in ordered[:args.max_jobs]:
        job = dict(item, id=str(len(jobs)+1), context=f'images/{item["image_id"]}.png', sources=byid[item['image_id']]['sources'])
        if 'box' in item:
            im = load_image(out/job['context'])
            x, y, right, bottom = item['box']
            box = (max(0, int(x)-8), max(0, int(y)-8), min(im.width, int(right)+9), min(im.height, int(bottom)+9))
            crop = f'queue/crops/{job["id"]}.png'
            (out/crop).parent.mkdir(parents=True, exist_ok=True)
            im.crop(box).save(out/crop)
            job['crop'], job['crop_box'] = crop, list(box)
        jobs.append(job)
    result = dict(schema=1, scan_hash=digest(canonical(scan)), mode='offline_no_requests_sent',
                  total_candidates=len(ordered), selected=len(jobs), deferred=max(0, len(ordered)-len(jobs)),
                  reserved_negative_audit=sum(job['reason']=='not_detected_audit_sample' for job in jobs),
                  negative_audit_candidates=len(chosen), seed=args.seed, jobs=jobs,
                  instruction='Read only visible text, preserve line order; unreadable is uncertain, never invent. Use original-pixel boxes. Findings require human review; no response means pending.')
    write_json(out/'queue/jobs.json', result)
    write_json(out/'queue/input.json',dict(instruction=result['instruction'],records=[
        {k:job[k] for k in ('id','context','crop','ocr_text') if k in job} for job in jobs],
        response_contract=dict(records=[dict(id='1',presence='text|no_text|uncertain',text='only visibly readable text',reason='brief evidence')]),
        note='Offline handoff only. Findings are not automatically accepted as human-confirmed text.'))
    print(json.dumps({k:result[k] for k in ['mode', 'total_candidates', 'selected', 'deferred']}))


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
    p = commands.add_parser('scan')
    p.add_argument('--out', required=True)
    p.add_argument('--profile', choices=['fast', 'balanced', 'careful'], default='balanced')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--threshold', type=float, default=.35)
    p.add_argument('--language', default='ch', help='RapidOCR language; default bundled Chinese/English-capable model')
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
    p.add_argument('--seed', type=int, default=42)
    p = commands.add_parser('export')
    p.add_argument('--out', required=True)
    p.add_argument('--review', required=True)
    p.add_argument('--csv', required=True)
    p = commands.add_parser('benchmark')
    p.add_argument('--out', required=True)
    p.add_argument('--truth', required=True)
    args = parser.parse_args()
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
        if args.max_jobs < 1 or args.negative_samples < 0:
            parser.error('invalid queue limits')
        queue(args)
    elif args.command == 'export':
        print(json.dumps(export_review(args.out, args.review, args.csv)))
    elif args.command == 'benchmark':
        benchmark(args)


if __name__ == '__main__':
    main()
