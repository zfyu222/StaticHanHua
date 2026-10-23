"""Real kernel-provider smoke test; writes diagnostic evidence, never game files."""
import argparse
from collections import Counter
import json
from pathlib import Path
from unittest.mock import patch
from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
from core import load_image,pixel_hash,write_json
from engine import Engine,analyze

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--image',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    original=OrtInferSession._init_sess_opts
    def options(cfg):
        result=original(cfg);result.enable_profiling=True;result.profile_file_prefix=str(out/'ort');return result
    with patch.object(OrtInferSession,'_init_sess_opts',staticmethod(options)):
        engine=Engine(model_size='medium',device='cuda')
    im=load_image(args.image);result=analyze(engine,im,pixel_hash(im),'balanced')
    providers={}
    for name,model in [('Det',engine.ocr.text_det),('Rec',engine.ocr.text_rec)]:
        path=model.session.session.end_profiling()
        events=json.loads(Path(path).read_text(encoding='utf-8'))
        providers[name]=dict(Counter(x.get('args',{}).get('provider') for x in events if x.get('args',{}).get('provider')))
        if not providers[name].get('CUDAExecutionProvider'):raise RuntimeError(f'No CUDA kernels executed: {name}')
    report=dict(engine=engine.metadata,kernel_events=providers,result=result)
    write_json(out/'gpu_smoke.json',report);print(json.dumps(providers),flush=True)

if __name__=='__main__':main()
