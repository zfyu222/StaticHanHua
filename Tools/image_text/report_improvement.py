"""Validate v0.2 routing against unchanged real OCR evidence; no paid API request."""
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
from core import canonical, digest, read_json, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--truth',required=True)
    parser.add_argument('--proposal-ui',required=True)
    args=parser.parse_args()
    here=Path(__file__).resolve().parent
    base,out=Path(args.baseline),Path(args.run)
    oldscan,scan=read_json(base/'scan.json'),read_json(out/'scan.json')
    oldqueue,queue=read_json(base/'queue/jobs.json'),read_json(out/'queue/jobs.json')
    manifest,tiers=read_json(out/'manifest.json'),read_json(out/'triage.json')
    assert digest(canonical(oldscan))==digest(canonical(scan)),'Raw OCR evidence changed'
    detected={x['image_id'] for x in scan['images'] if x['regions']}
    assert detected<=set(queue['candidate_image_ids']),'Recall-first dropped detected images'
    assert queue['policy']=='recall-first'
    assert len({x['image_id'] for x in queue['jobs']})==queue['selected']
    bypath={s['path']:x['image_id'] for x in manifest['images'] for s in x['sources']}
    priorities={x['image_id']:x['priority'] for x in tiers['images']}
    truth=read_json(args.truth)
    sample={name:dict(Counter(priorities[bypath[x['path']]] for x in truth['cases'] if x['has_text']==positive))
            for name,positive in (('known_positive',True),('known_negative',False))}
    browser=read_json(out/'browser-test.json')
    proposal_ui=read_json(Path(args.proposal_ui)/'proposal-browser-test.json')
    assert browser['status']==proposal_ui['status']=='passed'
    tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(here),'-p','test_*.py','-v'],capture_output=True,text=True)
    (here/'reports/unit_tests_v02.txt').write_text(tests.stdout+tests.stderr,'utf-8')
    if tests.returncode:raise RuntimeError('Regression suite failed')
    count=int(re.search(r'Ran (\d+) tests?',tests.stderr).group(1))
    report=dict(version='0.2',date='2026-10-23',routing_policy=queue['policy'],raw_scan_unchanged=True,
                total_images=len(scan['images']),all_detected_images=len(detected),detected_images_dropped=0,
                raw_not_detected_images=sum(x['status']=='not_detected' for x in scan['images']),
                not_detected_disposition=queue['not_detected_disposition'],
                review_priority=dict(Counter(x['priority'] for x in tiers['images'])),sample_priority=sample,
                old_region_tasks=oldqueue['total_candidates'],new_image_tasks=queue['total_candidates'],
                old_selected_unique_images=len({x['image_id'] for x in oldqueue['jobs']}),new_selected_unique_images=queue['selected'],
                selected_negative_audits=queue['reserved_negative_audit'],selected_low_signal_audits=queue['reserved_low_signal_audit'],
                deferred_image_tasks=queue['deferred'],tests=dict(status='passed',count=count),
                browser=browser,proposal_import_ui=proposal_ui,
                online_model_requests=0,real_local_ocr_used='Existing 1666-image inference evidence retained; no full-dataset rerun or changed detector claimed. Default model fingerprint and newspaper OCR separately compared with baseline and matched.',
                english_model_probe='Four actual local images tested; high-score texture errors remain. Explicit en option only; default unchanged.',
                limits=['No proof of zero detection false negatives; raw not-detected images remain pending.',
                        'Low-priority status only schedules review; it does not remove any candidate.',
                        '75 positives/12 negatives are development samples, not a held-out benchmark.',
                        'Proposal import tests use synthetic offline fixtures, not real multimodal verdicts.',
                        'No paid API transport or actual provider cost comparison; task reduction is not a measured billing reduction.'])
    write_json(here/'reports/improvement_v02.json',report)
    print({key:report[key] for key in ('all_detected_images','detected_images_dropped','old_region_tasks','new_image_tasks','tests')})


if __name__=='__main__':main()
