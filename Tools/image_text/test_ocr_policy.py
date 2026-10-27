"""Entry-point safety tests. Model metadata below is explicitly synthetic."""
import copy
import unittest
from unittest.mock import patch
from ocr_policy import validate_options,require_standard_scan


def standard_config():
    return {'engine':{'model_size':'medium','device':'cuda','synthetic_fixture':True,'models':{
        kind:{'file':'PP-OCRv6_'+suffix+'_medium.onnx','sha256':'0'*64,
              'providers':['CUDAExecutionProvider','CPUExecutionProvider']}
        for kind,suffix in [('Det','det'),('Rec','rec')]}}}


class OcrPolicyTests(unittest.TestCase):
    def test_scan_defaults_are_gpu_medium_and_single_stage_is_explicit(self):
        from cli import main
        with patch('cli.scan') as scan:
            main(['scan','--out','not-accessed'])
        args=scan.call_args.args[0]
        self.assertEqual((args.model_size,args.device),('medium','cuda'))
        self.assertFalse(args.allow_nonstandard_ocr)

    def test_cpu_or_small_rejected_before_model_load_or_file_access(self):
        from cli import main
        for options in [['--device','cpu'],['--model-size','small'],['--device','cpu','--model-size','small']]:
            with patch('cli.scan') as scan,patch('sys.stderr'),self.assertRaises(SystemExit) as error:
                main(['scan','--out','not-accessed']+options)
            self.assertEqual(error.exception.code,2);scan.assert_not_called()

    def test_explicit_nonstandard_experiment_is_not_the_default(self):
        from cli import main
        with patch('cli.scan') as scan:
            main(['scan','--out','not-accessed','--device','cpu','--model-size','small','--allow-nonstandard-ocr'])
        args=scan.call_args.args[0];self.assertTrue(args.allow_nonstandard_ocr)
        validate_options(args.model_size,args.device,args.allow_nonstandard_ocr)

    def test_missing_or_cpu_evidence_cannot_enter_production(self):
        scan={'config':standard_config()}
        require_standard_scan(scan)
        for key,value in [('device','cpu'),('model_size','small')]:
            changed=copy.deepcopy(scan);changed['config']['engine'][key]=value
            with self.assertRaises(ValueError):require_standard_scan(changed)
        with self.assertRaises(ValueError):require_standard_scan({'images':[]})
        for kind in ('Det','Rec'):
            changed=copy.deepcopy(scan);changed['config']['engine']['models'][kind]['providers']=['CPUExecutionProvider']
            with self.assertRaises(ValueError):require_standard_scan(changed)

    def test_incomplete_or_duplicate_scan_blocks_qwen(self):
        manifest={'manifest_hash':'fixture','images':[{'image_id':'1'},{'image_id':'2'}]}
        for rows in [[{'image_id':'1'}],[{'image_id':'1'},{'image_id':'1'}],[{'image_id':'1'},{'image_id':'2','errors':['failed']}]]:
            with self.assertRaises(RuntimeError):require_standard_scan({'config':standard_config(),'images':rows,'manifest_hash':'fixture'},manifest,complete=True)

    def test_gpu_missing_never_retries_on_cpu(self):
        import subprocess
        from image_workflow import main
        from pathlib import Path
        import tempfile
        from project_options import configure
        with tempfile.TemporaryDirectory() as temp:
            configure(temp,'enabled','Synthetic entry-point fixture')
            with patch('image_workflow.subprocess.run',side_effect=subprocess.CalledProcessError(1,'gpu')) as child,patch('tiered.qwen_batches') as qwen,self.assertRaises(subprocess.CalledProcessError):
                main(['pipeline','--project',temp,'--out',str(Path(temp)/'unused')])
            self.assertEqual(child.call_count,1);qwen.assert_not_called()

    def test_engine_stops_before_inference_when_cuda_provider_is_missing(self):
        from engine import Engine
        with patch('onnxruntime.get_available_providers',return_value=['CPUExecutionProvider']),patch('rapidocr.RapidOCR') as backend,self.assertRaisesRegex(RuntimeError,'CUDA provider missing'):
            Engine()
        backend.assert_not_called()

if __name__=='__main__':unittest.main()
