"""Conservative review prioritization; never changes detections or confirms absence."""
from core import canonical, digest

POLICY_VERSION = 1


def signal(region):
    text = region['text'].strip()
    letters = [x for x in text if x.isalnum()]
    readable = len(letters) >= 2 and len(set(x.casefold() for x in letters)) >= 2 and '\ufffd' not in text
    if region['status'] == 'candidate':
        return 'high', 'stable_ocr_candidate'
    if readable and region['recognition_score'] >= .6:
        return 'normal', 'plausible_reading_needs_confirmation'
    return 'low', 'empty_single_repeated_or_low_score_reading'


def prioritize(scan, manifest):
    sizes = {x['image_id']: (x['width'], x['height']) for x in manifest['images']}
    rows = []
    for image in scan['images']:
        regions = [dict(region_id=r['region_id'], priority=signal(r)[0], reason=signal(r)[1]) for r in image['regions']]
        if image['errors']:
            priority, reason = 'high', 'scan_error'
        elif image['status'] == 'not_detected':
            priority, reason = 'audit', 'not_detected_not_confirmed_absent'
        elif any(x['priority'] == 'high' for x in regions):
            priority, reason = 'high', 'contains_stable_candidate'
        elif any(x['priority'] == 'normal' for x in regions) or max(sizes[image['image_id']]) > 64:
            priority, reason = 'normal', 'plausible_or_large_image'
        else:
            priority, reason = 'low', 'small_sprite_with_only_weak_ocr_signal'
        rows.append(dict(image_id=image['image_id'], priority=priority, reason=reason, regions=regions))
    return dict(schema=1, policy=POLICY_VERSION, scan_hash=digest(canonical(scan)), images=rows,
                note='Review order only. Low priority never means no text, and detections remain untouched.')
