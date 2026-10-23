"""Generate a self-contained local review UI; images remain separate and unmodified."""
import json
from pathlib import Path
from core import canonical, digest, read_json


def build_review(out):
    manifest, scan = read_json(out/'manifest.json'), read_json(out/'scan.json')
    records = {x['image_id']: x for x in scan['images']}
    proposals={}
    findings=read_json(out/'vision_findings.json') if (out/'vision_findings.json').exists() else None
    if findings:
        if findings['scan_hash']!=digest(canonical(scan)):
            raise ValueError('Vision findings belong to another scan')
        for row in findings['records']:
            proposals.setdefault(row['image_id'],[]).append(row)
    triage=read_json(out/'triage.json') if (out/'triage.json').exists() else None
    if triage and triage['scan_hash']!=digest(canonical(scan)):
        raise ValueError('Triage belongs to another scan')
    tiers={x['image_id']:x['priority'] for x in triage['images']} if triage else {}
    items = []
    for image in manifest['images']:
        item = dict(image)
        item.update(records.get(image['image_id'], dict(status='not_scanned', regions=[], errors=[])))
        item['vision']=proposals.get(image['image_id'],[])
        item['priority']=tiers.get(image['image_id'],'unassigned')
        item['regions']=list(item['regions'])+[p for row in item['vision'] for p in row['proposals']]
        items.append(item)
    data = dict(scan_hash=digest(canonical(scan)), images=items, inventory_errors=manifest['errors'])
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = (Path(__file__).parent/'review_template.html').read_text('utf-8')
    page = template.replace('/*DATA*/null', payload)
    (out/'review.html').write_text(page, 'utf-8')
    return dict(page=str(out/'review.html'), images=len(items), inventory_errors=len(manifest['errors']))
