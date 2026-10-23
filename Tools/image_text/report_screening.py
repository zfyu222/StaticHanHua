"""Record cost-screen tradeoffs using cached real OCR, without claiming full-game accuracy."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
from core import canonical,digest,read_json,write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--baseline',required=True)
    args=parser.parse_args()
    here=Path(__file__).resolve().parent;out=Path(args.run)
    scan=read_json(out/'scan.json');baseline=read_json(Path(args.baseline)/'scan.json')
    assert digest(canonical(scan))==digest(canonical(baseline))
    queue=read_json(out/'queue/jobs.json');screen=read_json(out/'screening.json')
    kept={x['image_id'] for x in screen['images'] if x['keep']}
    assert kept<=set(queue['candidate_image_ids'])
    browser=read_json(out/'browser-test.json');assert browser['status']=='passed' and browser['excludedFilterChecked']
    tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(here),'-p','test_*.py','-v'],capture_output=True,text=True)
    (here/'reports/unit_tests_v03.txt').write_text(tests.stdout+tests.stderr,'utf-8')
    if tests.returncode:raise RuntimeError('Regression suite failed')
    count=int(re.search(r'Ran (\d+) tests?',tests.stderr).group(1))
    report=dict(version='0.3',date='2026-10-23',raw_ocr_unchanged=True,online_model_requests=0,
                general_comparison=read_json(out/'screen_comparison_any.json'),
                latin_comparison=read_json(out/'screen_comparison_latin.json'),
                selected_policy=dict(policy=queue['policy'],**queue['screening']),
                total_images=len(scan['images']),main_candidates=len(kept),audit_addons=queue['total_candidates']-len(kept),
                total_vision_candidates=queue['total_candidates'],first_package=queue['selected'],deferred=queue['deferred'],
                previous_vision_candidates=1410,browser=browser,tests=dict(status='passed',count=count),
                limitations=['84 positives and 12 negatives are development examples; no full-game false-negative rate measured.',
                             'One known image 0130.png (glitched ERROR artwork) is screened out at score 0.6.',
                             'Latin-script screening is appropriate only when source text is expected to use Latin script.',
                             'Filtering changes routing, not raw detection. Excluded images remain available to audit and rescue.',
                             'Image counts are not provider billing. No paid multimodal verdicts have been obtained.'])
    write_json(here/'reports/screening_v03.json',report)
    print({k:report[k] for k in ('main_candidates','audit_addons','total_vision_candidates','tests')})


if __name__=='__main__':main()
