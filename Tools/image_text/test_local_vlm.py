"""Local transport contract fixtures, distinct from real GPU model validation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import requests
from local_vlm import Client,run
import test_handoff as fixtures
from core import read_json
from pipeline import execute

class LocalTests(unittest.TestCase):
    setUp=fixtures.HandoffTests.setUp
    tearDown=fixtures.HandoffTests.tearDown

    def test_refuses_nonlocal_endpoint(self):
        for endpoint in ['https://example.com','http://example.com','http://user:pass@localhost']:
            with self.assertRaises(ValueError):Client(endpoint)

    def test_invalid_json_raw_reply_is_preserved(self):
        client=object.__new__(Client);client.endpoint='http://127.0.0.1:11435';client.model='fixture';client.options={}
        client.http=Mock();response=client.http.post.return_value
        response.content=b'not json';response.json.side_effect=ValueError('invalid json')
        path=self.root/'raw.json'
        with self.assertRaises(ValueError):client.request(b'image',path)
        self.assertEqual(path.read_bytes(),b'not json')

    def test_partial_output_is_rejected(self):
        client=object.__new__(Client);client.endpoint='http://localhost';client.model='fixture';client.options={};client.http=Mock()
        response=client.http.post.return_value;response.content=b'partial';response.json.return_value={'done':True,'done_reason':'length'}
        with self.assertRaises(RuntimeError):client.request(b'image',self.root/'partial.json')

    def test_resume_cache_does_not_repeat_model_call(self):
        fixtures.HandoffTests.queue(self,negative_samples=0,low_signal_samples=0)
        client=Mock();client.identity={'model':'fixture','digest':'fixture','runtime':'fixture'};client.options={}
        content=dict(presence='text',reason='visible letters fixture',regions=[dict(text='HELLO',box=[0,0,1000,1000])])
        client.request.return_value=(content,b'raw',{'eval_count':5});client.gpu_status.return_value={'size_vram':123}
        with patch('local_vlm.Client',return_value=client):
            first=run(self.out);second=run(self.out)
        self.assertEqual(first['requests'],1);self.assertEqual(second['requests'],0);self.assertEqual(second['cached'],1)
        self.assertEqual(client.request.call_count,1);self.assertEqual(client.unload.call_count,2)
        self.assertEqual(len(list((self.out/'local_vlm/reports').glob('*.json'))),2)
        self.assertFalse(read_json(self.out/'vision_findings.json')['human_approval'])

    def test_failure_is_counted_and_model_is_unloaded(self):
        fixtures.HandoffTests.queue(self,negative_samples=0,low_signal_samples=0)
        client=Mock();client.identity={};client.options={};client.request.side_effect=requests.Timeout('fixture timeout')
        with patch('local_vlm.Client',return_value=client),self.assertRaises(RuntimeError):run(self.out)
        self.assertEqual(read_json(self.out/'local_vlm/report.json')['requests'],1)
        client.unload.assert_called_once();client.http.close.assert_called_once()

    def test_pipeline_waits_for_full_ocr_before_starting_vlm(self):
        from test_ocr_policy import standard_config
        from core import write_json
        self.scan.update(config=standard_config(),manifest_hash=self.manifest['manifest_hash'])
        write_json(self.out/'scan.json',self.scan)
        events=[]
        def child(*args,**kwargs):events.append('ocr_child_exited')
        server=Mock();server.__enter__=Mock(side_effect=lambda:events.append('vlm_server') or server)
        server.__exit__=Mock(return_value=False)
        with patch('pipeline.subprocess.run',side_effect=child),patch('pipeline.Server',return_value=server),patch('pipeline.run',return_value={}),patch('pipeline.build_review'):
            execute(self.out)
        self.assertEqual(events,['ocr_child_exited','vlm_server'])

    def test_pipeline_refuses_incomplete_ocr(self):
        from core import write_json
        from test_ocr_policy import standard_config
        write_json(self.out/'scan.json',dict(config=standard_config(),manifest_hash=self.manifest['manifest_hash'],images=self.scan['images'][:1]))
        with patch('pipeline.subprocess.run'),patch('pipeline.Server') as server,self.assertRaises(RuntimeError):execute(self.out)
        server.assert_not_called()

    def test_changed_queue_preserves_existing_findings_and_crops(self):
        queue=fixtures.HandoffTests.queue(self,negative_samples=0,low_signal_samples=0)
        from core import write_json
        from handoff import import_findings
        reply=fixtures.HandoffTests.reply(self,queue)
        path=fixtures.HandoffTests.save_reply(self,reply);import_findings(self.out,path)
        before=(self.out/'queue/jobs.json').read_bytes()
        crop=(self.out/queue['jobs'][0]['crop']).read_bytes()
        with self.assertRaises(ValueError):fixtures.HandoffTests.queue(self,policy='full-audit')
        self.assertEqual(before,(self.out/'queue/jobs.json').read_bytes())
        self.assertEqual(crop,(self.out/queue['jobs'][0]['crop']).read_bytes())

    def test_real_empty_reason_fixture_is_rejected_and_usage_retained(self):
        fixtures.HandoffTests.queue(self,negative_samples=0,low_signal_samples=0)
        actual=read_json(Path(__file__).parent/'fixtures/qwen_empty_reason.json')
        client=Mock();client.identity={};client.options={};client.gpu_status.return_value={}
        def request(image,archive):
            archive.parent.mkdir(parents=True,exist_ok=True);archive.write_text(json.dumps(actual),encoding='utf-8')
            return json.loads(actual['message']['content']),archive.read_bytes(),actual
        client.request.side_effect=request
        with patch('local_vlm.Client',return_value=client),self.assertRaises(RuntimeError):run(self.out)
        report=read_json(self.out/'local_vlm/report.json')
        self.assertEqual(report['fresh_request_totals']['eval_count'],actual['eval_count'])
        self.assertEqual(report['completed'],0)
        self.assertFalse((self.out/'vision_findings.json').exists())

if __name__=='__main__':unittest.main()
