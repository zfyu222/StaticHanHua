"""Bound identifiers and retry hints cover terminology and body stages alike."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import test_api_minimal as minimal
import test_api_term_refs as term_refs
import test_translation_workflow as workflow_fixtures
import translation_workflow as workflow
from workflow_api_reliability import format_retry_feedback, retry_feedback_text


class ReliabilityTests(unittest.TestCase):
    def test_glossary_integer_refs_are_lossless(self):
        fixture = minimal.MinimalAPITests()
        original = fixture.decisions()
        numeric = copy.deepcopy(original)
        numeric['entry_reviews'][0]['ref'] = 1
        numeric['acknowledged_findings'] = [1]
        numeric['edits'][0]['ref'] = 1
        self.assertEqual(fixture.decode(fixture.glossary(), {'decisions': original}),
                         fixture.decode(fixture.glossary(), {'decisions': numeric}))

    def test_glossary_invalid_related_duplicate_refs_stay_rejected(self):
        fixture = minimal.MinimalAPITests()
        for ref in (True, 1.0, 999, 2):
            value = fixture.decisions(); value['entry_reviews'][0]['ref'] = ref
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                fixture.decode(fixture.glossary(), {'decisions':value})
        for values in ([1, 'f1'], [True], [999]):
            value = fixture.decisions(); value['acknowledged_findings'] = values
            with self.subTest(values=values), self.assertRaises(ValueError):
                fixture.decode(fixture.glossary(), {'decisions':value})

    def test_body_integer_term_refs_are_lossless_and_record_bound(self):
        fixture = term_refs.TermRefContractTests()
        fixture.setUp()
        original = [{'id':'1','translation':'放在这里。','exceptions':[{'term_ref':'t1','reason':'rest 表示放置。'}]}]
        value = copy.deepcopy(original); value[0]['exceptions'][0]['term_ref'] = 1
        self.assertEqual(fixture.decode(value), fixture.decode(original))
        for ref in (True, 1.0, 2, 999):
            value = copy.deepcopy(original); value[0]['exceptions'][0]['term_ref'] = ref
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                fixture.decode(value)
        value = copy.deepcopy(original)
        value[0]['exceptions'] += [{'term_ref':1, 'reason':'Duplicate typed form.'}]
        with self.assertRaises(ValueError): fixture.decode(value)

    def test_feedback_uses_each_stage_contract(self):
        for stage in ('extract','glossary_review','pilot_translate','translate','review','polish','final_review'):
            text = retry_feedback_text({'stage':stage,'error':'fixture failure'})
            with self.subTest(stage=stage):
                if stage in ('extract','glossary_review','translate','pilot_translate'):
                    self.assertNotIn('keep:true', text)
                else:
                    self.assertIn('keep:true', text)
                if stage == 'extract': self.assertIn('仅返回 analysis',text)
                if stage == 'glossary_review': self.assertIn('仅返回 decisions',text)

    def test_feedback_binds_stage_and_bounded_fragment_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'rejection.json').write_text(json.dumps({'error':'terms:4 invalid'}),encoding='utf8')
            (folder/'api_id_transport.v2.json').write_text(json.dumps({'stage':'glossary_review',
                'glossary':{'entry_refs':{'e1':'terms:4'}}}),encoding='utf8')
            raw = '{"decisions":'+(' '*2000)+'bad}'
            (folder/'response.raw.txt').write_text(raw,encoding='utf8')
            (folder/'response.metadata.json').write_text('{"finish_reason":"length"}',encoding='utf8')
            value = format_retry_feedback(folder/'result.json')
            self.assertEqual(value['stage'],'glossary_review')
            self.assertEqual(value['error'],'e1 invalid')
            self.assertEqual(value['finish_reason'],'length')
            self.assertLessEqual(len(value['response_fragment']),200)
            self.assertEqual((folder/'response.raw.txt').read_text(encoding='utf8'),raw)

    def test_feedback_does_not_guess_prefix_references(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            workflow.write_json(folder/'rejection.json',{'error':'terms:1 terms:12 terms:123'})
            workflow.write_json(folder/'api_id_transport.v2.json',{'stage':'glossary_review',
                'glossary':{'entry_refs':{'e1':'terms:1','e2':'terms:12'}}})
            self.assertEqual(format_retry_feedback(folder/'result.json')['error'],'e1 e2 terms:123')

    def test_json_feedback_positions_use_the_same_fence_handling_as_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            workflow.write_json(folder/'rejection.json',{'error':'JSON value missing'})
            (folder/'response.raw.txt').write_text('```json\n{"decisions":bad}\n```',encoding='utf8')
            feedback=format_retry_feedback(folder/'result.json','glossary_review')
            self.assertEqual(feedback['json_line'],1)
            self.assertEqual(feedback['json_column'],14)
            self.assertNotIn('```',feedback['response_fragment'])

    def test_term_stage_retry_pack_uses_shared_generation_and_keeps_original(self):
        for stage in ('extract','glossary_review'):
            fixture = workflow_fixtures.WorkflowTests(); fixture.setUp()
            try:
                fixture.flow.pause(True)
                fixture.flow.configure({'stage_backends':{stage:'api'}},'Isolated format retry validation',impact='none')
                fixture.flow.pause(False)
                if stage == 'glossary_review': fixture.extract(True)
                session = fixture.flow.batch_open(stage,'first-format-instance',backend='api')
                path = Path(session['result_file']).parent
                old = Path(session['input_file']).read_bytes()
                workflow.write_json(path/'rejection.json',{'error':'JSON delimiter missing'})
                (path/'response.raw.txt').write_text('{"broken":',encoding='utf8')
                retry = fixture.flow.batch_retry(session['session_id'],'new-format-instance','invalid_model_result',format_error=True)
                text = Path(retry['input_file']).read_text(encoding='utf8')
                self.assertIn('本次格式重试',text)
                self.assertIn('仅返回 analysis' if stage == 'extract' else '仅返回 decisions',text)
                self.assertNotIn('keep:true',text)
                self.assertEqual(Path(session['input_file']).read_bytes(),old)
            finally: fixture.tearDown()

    def test_resume_old_saved_response_preserves_unknown_usage_metadata(self):
        fixture = workflow_fixtures.WorkflowTests(); fixture.setUp()
        try:
            fixture.flow.pause(True)
            fixture.flow.configure({'stage_backends':{'extract':'api'}},'Isolated resume validation',impact='none')
            fixture.flow.pause(False)
            session = fixture.flow.batch_open('extract','saved-api-author',backend='api')
            path = Path(session['result_file']).parent
            value = {'analysis':{'characters':[],'terms':[],'non_translate':[]}}
            workflow.write_json(path/'response.raw.txt',value)
            count = workflow.resume_api_tasks(fixture.flow,'extract')
            self.assertEqual(count,1)
            self.assertEqual(workflow.read_json(path/'response.parsed.json'),value)
            metadata = workflow.read_json(path/'response.metadata.json')
            self.assertIsNone(metadata['finish_reason'])
            self.assertIsNone(metadata['response_model'])
            self.assertFalse(metadata['usage_reported'])
        finally: fixture.tearDown()

    def test_invalid_saved_reply_resume_attaches_diagnostic_without_resetting_attempts(self):
        fixture = workflow_fixtures.WorkflowTests(); fixture.setUp()
        try:
            fixture.flow.pause(True)
            fixture.flow.configure({'stage_backends':{'extract':'api'}},'Isolated invalid reply resume',impact='none')
            fixture.flow.pause(False)
            session=fixture.flow.batch_open('extract','old-api-instance',backend='api')
            folder=Path(session['result_file']).parent
            (folder/'response.raw.txt').write_text('{"analysis":',encoding='utf8')
            self.assertEqual(workflow.resume_api_tasks(fixture.flow,'extract'),0)
            fresh=fixture.flow.batch_open('extract','resumed-api-instance',backend='api')
            text=Path(fresh['input_file']).read_text(encoding='utf8')
            self.assertIn('本次格式重试',text)
            self.assertIn('仅返回 analysis',text)
            self.assertIn('json_column',text)
            self.assertEqual((folder/'response.raw.txt').read_text(encoding='utf8'),'{"analysis":')
            with fixture.flow.db() as db:
                self.assertEqual(db.execute('SELECT attempt FROM tasks WHERE id=?',(session['task_id'],)).fetchone()[0],2)
        finally: fixture.tearDown()


if __name__ == '__main__': unittest.main()
