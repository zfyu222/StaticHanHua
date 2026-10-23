"""Cost-oriented image screening. Rejected means predicted low value, never proven no text."""
import unicodedata
from core import canonical, digest

SCREEN_VERSION=1


def readable(region, score=.6, script='any'):
    text=region['text'].strip()
    chars=[c for c in text if c.isalnum()]
    if region['recognition_score']<score or '\ufffd' in text or not chars:
        return False
    if script=='latin' and any(not c.isdecimal() and 'LATIN' not in unicodedata.name(c,'') for c in chars):
        return False
    if len(chars)==1:
        # One CJK/other uncased letter can be a complete word or button caption.
        return script=='any' and unicodedata.category(chars[0])=='Lo'
    return len({c.casefold() for c in chars})>=2 or all(c.isdecimal() for c in chars)


def select(scan, score=.6, script='any'):
    if not 0<score<=1 or script not in ('any','latin'):
        raise ValueError('Invalid screening settings')
    rows=[]
    for image in scan['images']:
        keep=bool(image['errors']) or any(readable(r,score,script) for r in image['regions'])
        rows.append(dict(image_id=image['image_id'],keep=keep,
                         reason='scan_failed_force_review' if image['errors'] else 'plausible_ocr' if keep else 'predicted_no_text_or_unreadable'))
    return dict(schema=1,version=SCREEN_VERSION,scan_hash=digest(canonical(scan)),score=score,script=script,images=rows,
                note='Deliberately allows some false negatives. Weak art lettering and isolated Latin characters may be rejected; audit excluded images.')


def compare(scan, manifest, truth=None, scores=(.5,.6,.75,.85), script='any'):
    paths={s['path']:x['image_id'] for x in manifest['images'] for s in x['sources']}
    known_positive={paths[x['path']] for x in truth['cases'] if x['has_text']} if truth else set()
    known_negative={paths[x['path']] for x in truth['cases'] if not x['has_text']} if truth else set()
    results=[]
    for score in scores:
        screening=select(scan,score,script)
        kept={x['image_id'] for x in screening['images'] if x['keep']}
        missing=known_positive-kept
        results.append(dict(score=score,kept_images=len(kept),excluded_images=len(scan['images'])-len(kept),
                            known_positive_images=len(known_positive),known_positive_misses=len(missing),
                            known_negative_images=len(known_negative),known_negative_passes=len(known_negative&kept),
                            missed_paths=[s['path'] for x in manifest['images'] if x['image_id'] in missing for s in x['sources']]))
    return dict(schema=1,script=script,scan_hash=digest(canonical(scan)),rows=results,
                scope='Development sample only, not measured full-game recall or billing. Counts exclude audit add-ons.')
