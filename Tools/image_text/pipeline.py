"""Sequential GPU OCR child exits before the local VLM server is started."""
import argparse
from pathlib import Path
import subprocess
import sys
from core import read_json,write_json
from handoff import prepare
from local_vlm import MODEL,run
from ollama_server import Server
from review import build_review
from ocr_policy import require_standard_scan

def execute(out,script='any',policy='screened',limit=None,pull=False):
    out=Path(out).resolve()
    # A separate process owns every OCR/CUDA session; wait for complete exit.
    subprocess.run([sys.executable,str(Path(__file__).with_name('cli.py')),'scan','--out',str(out),
                    '--device','cuda','--model-size','medium'],check=True)
    scan=read_json(out/'scan.json');manifest=read_json(out/'manifest.json')
    require_standard_scan(scan,manifest,complete=True)
    prepare(out,max_jobs=len(manifest['images']),negative_samples=20,low_signal_samples=10,
            seed=42,policy=policy,screen_score=.6,screen_script=script)
    write_json(out/'pipeline.json',dict(ocr_process_exited=True,model=MODEL,policy=policy,screen_script=script))
    with Server() as server:
        if pull:server.pull(MODEL)
        report=run(out,server.endpoint,limit=limit)
    build_review(out)
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    parser.add_argument('--screen-script',choices=['any','latin'],default='any')
    parser.add_argument('--policy',choices=['screened','recall-first','full-audit'],default='screened')
    parser.add_argument('--limit',type=int);parser.add_argument('--pull',action='store_true')
    args=parser.parse_args()
    if args.limit is not None and args.limit<1:parser.error('limit must be positive')
    execute(args.out,args.screen_script,args.policy,args.limit,args.pull)

if __name__=='__main__':main()
