"""Build read-only benchmark inputs from previously reviewed Lakeview assets, not game state."""
import argparse
from pathlib import Path
import shutil
from core import read_json, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--include-negatives', action='store_true', help='12 manually inspected character/item sprites without lettering')
    args=parser.parse_args()
    project, out=Path(args.project).resolve(), Path(args.out).resolve()
    regions=read_json(project/'Analysis/image_audit/text_regions.json')
    unique=read_json(project/'Analysis/image_audit/unique.json')
    # Diverse known-positive images, selected before observing this OCR's results.
    positives=[1078,143,279,323,319,201,130,1227,1575,1398,1395,1397,135,167,280,335,
               351,369,513,753,800,1090,1095,1115,1124,1538,379,62,64,66,245,342,896,
               124,359,742,1131,272,557,592,476,626,428,659,78,131,427,627,571,496,
               578,666,504,637,820,911,751,169,72,750,1214,1142,1625,1211,1613,375,587,
               586,878,815,913,1076,1105,539,813]
    negatives=[69,74,75,77,82,83,85,86,88,94,99,100] if args.include_negatives else []
    byindex={t['index']:t for t in unique}
    cases=[]
    metadata={}
    for index in positives+negatives:
        tex=byindex[index]
        name=f'{index:04d}.png'
        target=out/'input'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(project/'Analysis/image_audit/images'/name,target)
        boxes=[]
        sprites=[]
        for part in [r for r in regions if r['image_index']==index]:
            for sprite in part['sprites']:
                r=sprite['rect']
                sx,sy=r['x'],tex['height']-r['y']-r['h']
                x,y,w,h=part['box']
                box=[sx+x*r['w'],sy+y*r['h'],sx+(x+w)*r['w'],sy+(y+h)*r['h']]
                if box not in boxes:boxes.append(box)
                crop=[sx,sy,sx+r['w'],sy+r['h']]
                value=dict(name=sprite['name'],box=crop,asset=sprite['file'],path_id=sprite['path_id'])
                if value not in sprites:sprites.append(value)
        metadata[name]=dict(asset=tex['file'],path_id=tex['path_id'],texture_name=tex['name'],sprites=sprites)
        cases.append(dict(path=name,has_text=index in positives,boxes=boxes,texture_name=tex['name']))
    write_json(out/'provenance.json',metadata)
    write_json(out/'truth.json',dict(scope=f'75 known-positive images from earlier human transcription and {len(negatives)} manually inspected tiny no-text sprites; broad overlay boxes, not tight text annotations; development sample, not held-out or complete',cases=cases))
    print({'positive_images':len(positives),'negative_images':len(negatives),'out':str(out)})


if __name__=='__main__':main()
