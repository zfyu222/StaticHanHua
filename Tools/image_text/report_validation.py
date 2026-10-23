"""Archive reproducible local-model and behavioral-test evidence without game assets."""
import argparse
import re
from pathlib import Path
import subprocess
import sys
from core import canonical, digest, read_json, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    args=parser.parse_args()
    here=Path(__file__).resolve().parent
    run=Path(args.run).resolve()
    first=read_json(run/'first_run_summary.json')
    cached=read_json(run/'summary.json')
    scan=read_json(run/'scan.json')
    jobs=read_json(run/'queue/jobs.json')
    benchmark=read_json(run/'benchmark.json')
    browser=read_json(run/'browser-test.json')
    assert first['scanned_images']==cached['scanned_images']==1666
    assert cached['invocation']=={'cached_images':1666}
    assert digest(canonical(scan))==jobs['scan_hash'],'Cache changed review identity'
    assert browser['status']=='passed' and not browser['page_errors']
    assert jobs['selected']==100 and jobs['reserved_negative_audit']==20
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(here),'-p','test_*.py','-v'],capture_output=True,text=True)
    reports=here/'reports'
    reports.mkdir(exist_ok=True)
    (reports/'unit_tests.txt').write_text(result.stdout+result.stderr,'utf-8')
    if result.returncode:
        raise RuntimeError('Behavioral tests failed: see reports/unit_tests.txt')
    count=re.search(r'Ran (\d+) tests?',result.stderr)
    if count is None:raise RuntimeError('Could not determine executed test count')
    tests_run=int(count.group(1))
    report=dict(tool_version='0.1',date='2026-10-23',
                unit_tests=dict(status='passed',tests=tests_run,evidence='unit_tests.txt'),
                local_model=dict(config=scan['config'],first_scan=first,cache_replay=cached,review_identity_preserved=True),
                sample_detection={key:value for key,value in benchmark.items() if key!='cases'},
                browser=browser,
                handoff={key:jobs[key] for key in ('mode','total_candidates','selected','deferred','reserved_negative_audit','negative_audit_candidates')},
                online_model_requests=0,paid_multimodal_adapter='not_implemented_or_tested',
                game_files_modified=False,user_computer_control=False,
                limits=['75 positives/12 negatives are development samples, not a held-out benchmark.',
                        'Raw detector found candidates in 11/12 no-text sprites; none reached the stable-candidate tier. This is not proof of zero false positives.',
                        '101/118 broad known regions overlap detections; text presence is not full transcription accuracy.',
                        '1155/1666 images remain ambiguous. No unreviewed candidate is exported as confirmed text.',
                        'No complete negative ground truth, per-character accuracy benchmark or multilingual acceptance test.'],
                code_sha256={p.name:digest(p.read_bytes()) for p in here.iterdir() if p.suffix in ('.py','.html','.ps1','.cjs')})
    write_json(reports/'validation.json',report)
    print(f'Local OCR, cache replay, {tests_run} behavioral tests and headless UI checks archived in '+str(reports))


if __name__=='__main__':main()
