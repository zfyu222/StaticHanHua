"""Engine-independent image inventory, geometry, and strict review/export contracts."""
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from PIL import Image, ImageOps

SCHEMA = 1
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tga', '.tif', '.tiff', '.dds'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), 'utf-8')
    temp.replace(path)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def load_image(path):
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert('RGBA')


def pixel_hash(image):
    # RGB behind alpha=0 is invisible and not used by any preprocessing pass.
    clean = image.copy()
    clean.paste((0, 0, 0, 0), (0, 0, image.width, image.height), image.getchannel('A').point(lambda a: 255 if a == 0 else 0))
    return digest(str(image.size).encode() + clean.tobytes())


def inventory(source, out, provenance=None):
    source, out = Path(source).resolve(), Path(out).resolve()
    if not source.is_dir():
        raise ValueError('Input directory does not exist')
    if out == source or out.is_relative_to(source):
        raise ValueError('Output must be outside input directory')
    provenance = provenance or {}
    unique, files, errors = {}, [], []
    for path in sorted(source.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        relative = path.relative_to(source).as_posix()
        try:
            im = load_image(path)
            key = pixel_hash(im)
            row = dict(path=relative, file_sha256=digest(path.read_bytes()), image_id=key,
                       width=im.width, height=im.height, provenance=provenance.get(relative, {}))
            files.append(row)
            if key not in unique:
                unique[key] = dict(image_id=key, width=im.width, height=im.height, sources=[])
                target = out/'images'/f'{key}.png'
                target.parent.mkdir(parents=True, exist_ok=True)
                im.save(target)
            unique[key]['sources'].append(row)
        except Exception as exc:
            errors.append(dict(path=relative, error=str(exc)))
    result = dict(schema=SCHEMA, input_root=str(source), images=list(unique.values()), files=files, errors=errors)
    result['manifest_hash'] = digest(canonical(result))
    write_json(out/'manifest.json', result)
    return result


def rect_of(points):
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def overlap(a, b):
    ax, ay, ar, ab = a
    bx, by, br, bb = b
    common = max(0, min(ar, br)-max(ax, bx))*max(0, min(ab, bb)-max(ay, by))
    return common/max(1e-6, (ar-ax)*(ab-ay)+(br-bx)*(bb-by)-common)


def merge_regions(candidates):
    """Keep disagreement evidence; merge overlapping passes, never merge adjacent lines."""
    merged = []
    def partial(a,b):
        if a['pass']==b['pass']:return False
        aa,bb=a['box'],b['box']
        common=max(0,min(aa[2],bb[2])-max(aa[0],bb[0]))*max(0,min(aa[3],bb[3])-max(aa[1],bb[1]))
        area=lambda box:max(1e-6,(box[2]-box[0])*(box[3]-box[1]))
        norm=lambda s:''.join(s.split()).casefold()
        x,y=norm(a['text']),norm(b['text'])
        small=a if area(aa)<area(bb) else b
        clipped=re.fullmatch(r'tile-bg\d+-(\d+)-(\d+)',small['pass'])
        clipped=bool(clipped and (abs(small['box'][0]-int(clipped[1]))<=2 or abs(small['box'][2]-(int(clipped[1])+768))<=2)
                     and min(area(aa),area(bb))/max(area(aa),area(bb))<.7)
        return bool(x and y and min(len(x),len(y))>=2 and ((x in y or y in x) or clipped)
                    and common/min(area(aa),area(bb))>=.85
                    and min(aa[3],bb[3])-max(aa[1],bb[1])>=.7*min(aa[3]-aa[1],bb[3]-bb[1]))
    for candidate in candidates:
        target = next((x for x in merged if overlap(x['box'], candidate['box']) >= .55 or partial(x,candidate)), None)
        evidence = {k: candidate[k] for k in ('text', 'recognition_score', 'detection_score', 'pass','box')}
        if target is None:
            target = dict(candidate, alternatives=[evidence])
            merged.append(target)
        else:
            contained=partial(target,candidate) and ''.join(target['text'].split()).casefold()!=''.join(candidate['text'].split()).casefold()
            area=lambda x:(x['box'][2]-x['box'][0])*(x['box'][3]-x['box'][1])
            longer=area(candidate)>area(target)
            if contained:
                if longer:
                    for previous in target['alternatives']:
                        if ''.join(previous['text'].split()).casefold() in ''.join(candidate['text'].split()).casefold():previous['partial_duplicate']=True
                elif ''.join(candidate['text'].split()).casefold() in ''.join(target['text'].split()).casefold():evidence['partial_duplicate']=True
                else:evidence['partial_competing_reading']=True
            target['alternatives'].append(evidence)
            if (contained and longer) or (not contained and candidate['recognition_score'] > target['recognition_score']):
                target.update({k: candidate[k] for k in ('text', 'recognition_score', 'detection_score', 'box', 'polygon')})
    for row in merged:
        words = {x['text'].strip().casefold() for x in row['alternatives'] if x['text'].strip() and not x.get('partial_duplicate')}
        row['disagreement'] = len(words) > 1
        row['status'] = 'needs_review' if not row['text'].strip() or row['recognition_score'] < .85 or row['disagreement'] else 'candidate'
    return sorted(merged, key=lambda x: (round(x['box'][1]/8), x['box'][0]))


def region_id(image_id, box):
    return digest(canonical([image_id, [round(x, 2) for x in box]]))[:24]


def validate_box(box, width, height):
    if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in box):
        raise ValueError('Invalid rectangle')
    if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
        raise ValueError('Rectangle outside image')


def validate_review(review, scan, manifest):
    """Review is bound to the exact scan, all IDs unique; arbitrary omissions are not approvals."""
    if review.get('scan_hash') != digest(canonical(scan)):
        raise ValueError('Review belongs to another scan; regenerate review page')
    images = {x['image_id']: x for x in manifest['images']}
    found = {r['region_id']: (item['image_id'], r) for item in scan['images'] for r in item['regions']}
    seen, accepted = set(), []
    for row in review.get('regions', []):
        key = row['region_id']
        if key in seen:
            raise ValueError('Duplicate review ID')
        seen.add(key)
        if row.get('decision') not in ('accept', 'reject', 'pending'):
            raise ValueError('Unknown review decision')
        if key in found:
            image_id, original = found[key]
            if row.get('image_id') != image_id or row.get('box') != original['box']:
                raise ValueError('Review changed region identity or geometry')
        elif row.get('manual') is True and key.startswith('manual-'):
            image_id = row['image_id']
            if image_id not in images:
                raise ValueError('Unknown manual image')
            validate_box(row['box'], images[image_id]['width'], images[image_id]['height'])
        else:
            raise ValueError('Unknown review ID')
        if row['decision'] == 'accept':
            text = row.get('text')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('Accepted text must be nonempty')
            accepted.append(row)
    image_decisions = review.get('images', [])
    image_seen = set()
    for row in image_decisions:
        image_id = row['image_id']
        if image_id in image_seen or image_id not in images:
            raise ValueError('Unknown or duplicate image review')
        image_seen.add(image_id)
        if row['decision'] not in ('unreviewed', 'confirmed_no_text', 'has_text', 'uncertain'):
            raise ValueError('Unknown image decision')
        if row['decision'] == 'confirmed_no_text' and any(x['image_id'] == image_id for x in accepted):
            raise ValueError('No-text image has accepted text')
    return accepted


def export_review(out, review_path, target):
    out, target = Path(out), Path(target)
    manifest, scan, review = read_json(out/'manifest.json'), read_json(out/'scan.json'), read_json(review_path)
    accepted = validate_review(review, scan, manifest)
    if not accepted:
        raise ValueError('No human-confirmed text; OCR candidates cannot be exported as confirmed text')
    source = {x['image_id']: x['sources'] for x in manifest['images']}
    texts, mapping = [], []
    for row in accepted:
        if row['text'] not in texts:
            texts.append(row['text'])
        mapping.append(dict(csv_row=texts.index(row['text'])+1, image_id=row['image_id'], region_id=row['region_id'],
                            box=row['box'], sources=source[row['image_id']]))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.with_suffix('.mapping.json').exists():
        raise ValueError('Export target exists; choose a new filename')
    with target.open('w', encoding='utf-8-sig', newline='') as stream:
        csv.writer(stream).writerows([text] for text in texts)
    write_json(target.with_suffix('.mapping.json'), dict(schema=SCHEMA, scan_hash=digest(canonical(scan)),
               csv_sha256=digest(target.read_bytes()), records=len(texts), regions=mapping,
               scope='human-confirmed subset only; omissions and pending images are not reviewed'))
    return dict(records=len(texts), regions=len(mapping), csv=str(target))
