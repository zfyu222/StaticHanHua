"""Behavioral tests with explicitly synthetic images and reviewer/API fixtures."""
import copy
import csv
import tempfile
from pathlib import Path
import unittest
from PIL import Image
from core import canonical,digest,inventory,read_json,write_json,merge_regions
from tiered import plan,package,import_decisions,export
from project_options import configure,require_enabled
from screening import readable
from test_ocr_policy import standard_config

class TieredTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.input=self.root/'input';self.input.mkdir();self.out=self.root/'work'
        for i,name in enumerate(['normal','title','tiny','weak','unknown','broken']):Image.new('RGBA',(100,60),(i*30,20,40,255)).save(self.input/f'{name}.png')
        self.m=inventory(self.input,self.out);self.ids={x['sources'][0]['path'][:-4]:x['image_id'] for x in self.m['images']}
        rows=[]
        for name,key in self.ids.items():
            r=dict(region_id=name,text='GAMEOYER' if name=='tiny' else 'C' if name=='weak' else 'CONTINUE',box=[5,5,90,12 if name=='tiny' else 30],recognition_score=.2 if name=='weak' else .99,detection_score=.9,status='candidate',disagreement=False)
            rows.append(dict(image_id=key,status='text_candidate',errors=[dict(stage='detection',error='fixture')] if name=='broken' else [],regions=[r]))
        write_json(self.out/'scan.json',dict(config=standard_config(),images=rows))
        self.policy=dict(script='latin',negative_samples=0,weak_samples=0,ordinary_samples=0,families={'ui':dict(kind='normal',validated=True,evidence='Synthetic behavioral fixture only',glyph_height_range=[20,30])},images={'normal.png':dict(family='ui'),'title.png':dict(risks=['art'])})

    def tearDown(self):self.temp.cleanup()

    def test_cpu_scan_rejected_without_rewriting_prior_evidence(self):
        scan=read_json(self.out/'scan.json');scan['config']['engine']['device']='cpu';write_json(self.out/'scan.json',scan)
        before=(self.out/'scan.json').read_bytes()
        with self.assertRaises(ValueError):plan(self.out,self.policy)
        self.assertEqual(before,(self.out/'scan.json').read_bytes())
        self.assertFalse((self.out/'tiered/plan.json').exists())

    def test_qwen_resume_also_rejects_nonstandard_scan(self):
        from unittest.mock import patch
        from tiered import qwen_batches
        planned=plan(self.out,self.policy);scan=read_json(self.out/'scan.json');scan['config']['engine']['device']='cpu'
        with patch('tiered.load_plan',return_value=(self.out,self.m,scan,planned)),patch('ollama_server.Server') as server,self.assertRaises(ValueError):
            qwen_batches(self.out)
        server.assert_not_called()

    def test_selective_routes_preserve_unknown_and_extreme_cases(self):
        result=plan(self.out,self.policy);routes={x['image_id']:x['route'] for x in result['routes']}
        self.assertEqual({name:routes[key] for name,key in self.ids.items()},dict(normal='candidate',title='qwen',tiny='agent',weak='excluded',unknown='qwen',broken='blocked'))

    def test_missing_calibration_does_not_bypass_qwen(self):
        del self.policy['families']['ui']['evidence'];r=plan(self.out,self.policy)
        self.assertEqual(next(x['route'] for x in r['routes'] if x['image_id']==self.ids['normal']),'qwen')

    def test_explicit_direct_review_preserves_missing_qwen_and_export_gate(self):
        plan(self.out,self.policy)
        before=(self.out/'tiered/plan.json').read_bytes()
        default=read_json(package(self.out)['package'])
        self.assertNotIn(self.ids['title'],[x['image_id'] for x in default['jobs']])
        direct_path=package(self.out,direct_image_ids=[self.ids['title']])['package']
        direct=read_json(direct_path)
        self.assertEqual(len(direct['jobs']),1)
        job=direct['jobs'][0];self.assertIsNone(job['qwen'])
        self.assertEqual(job['image_id'],self.ids['title'])
        self.assertEqual(before,(self.out/'tiered/plan.json').read_bytes())
        self.assertFalse((self.out/'tiered/findings.json').exists())
        reply=self.root/'reply.json'
        write_json(reply,dict(records=[dict(id=job['id'],status='text',reason='Synthetic direct visual review',unreadable=False,regions=[dict(text='TITLE',box=[0,0,1000,1000])])]))
        import_decisions(self.out,direct_path,reply,'conversation_agent','fixture')
        self.assertEqual(package(self.out,direct_image_ids=[self.ids['title']])['selected'],0)
        with self.assertRaises(ValueError):export(self.out,self.root/'source.csv')
        for name in ('weak','broken','absent'):
            with self.assertRaises(ValueError):package(self.out,direct_image_ids=[self.ids.get(name,'unknown')])

    def test_review_is_bound_visual_confirmation_not_model_consensus(self):
        plan(self.out,self.policy);p=package(self.out);pack=read_json(p['package']);job=pack['jobs'][0]
        self.assertEqual(job['image_id'],self.ids['tiny'])
        reply=self.root/'reply.json';write_json(reply,dict(records=[dict(id=job['id'],status='text',reason='Synthetic visual-review fixture',unreadable=False,regions=[dict(text='GAME OVER',box=[0,0,1000,1000])])]))
        import_decisions(self.out,p['package'],reply,'conversation_agent','fixture')
        saved=read_json(self.out/'tiered/decisions.json')['images'][job['image_id']]
        self.assertEqual(saved['status'],'agent_visual_confirmed');self.assertEqual(saved['reviewer_type'],'conversation_agent')
        with self.assertRaises(ValueError):export(self.out,self.root/'source_text.csv')
        result=export(self.out,self.root/'subset_text.csv',True);self.assertEqual(result['records'],1)
        mapping=read_json(self.root/'subset_text.mapping.json');self.assertFalse(mapping['human_approval']);self.assertGreater(result['unresolved'],0)

    def test_changed_evidence_and_unknown_ids_are_rejected(self):
        plan(self.out,self.policy);p=package(self.out);reply=self.root/'reply.json'
        row=dict(id='999',status='no_text',reason='fixture',unreadable=False,regions=[]);write_json(reply,dict(records=[row]))
        with self.assertRaises(ValueError):import_decisions(self.out,p['package'],reply,'human')
        pack=read_json(p['package']);row['id']=pack['jobs'][0]['id'];write_json(reply,dict(records=[row]))
        (self.out/pack['jobs'][0]['evidence_view']).write_bytes(b'changed')
        with self.assertRaises(ValueError):import_decisions(self.out,p['package'],reply,'human')

    def test_changed_plan_does_not_replace_old_bindings(self):
        plan(self.out,self.policy);before=(self.out/'tiered/plan.json').read_bytes();self.policy['seed']=99
        with self.assertRaises(ValueError):plan(self.out,self.policy)
        self.assertEqual(before,(self.out/'tiered/plan.json').read_bytes())

    def test_unreadable_can_be_reviewed_again_with_history(self):
        plan(self.out,self.policy);p=package(self.out);pack=read_json(p['package']);job=pack['jobs'][0];reply=self.root/'reply.json'
        write_json(reply,dict(records=[dict(id=job['id'],status='uncertain',reason='Unreadable fixture',unreadable=True,regions=[])]))
        import_decisions(self.out,p['package'],reply,'conversation_agent','fixture')
        again=package(self.out);self.assertEqual(again['pending'],p['pending'])
        write_json(reply,dict(records=[dict(id=job['id'],status='text',reason='Second visual review fixture',unreadable=False,regions=[dict(text='GAME OVER',box=[0,0,1000,1000])])]))
        import_decisions(self.out,again['package'],reply,'human')
        saved=read_json(self.out/'tiered/decisions.json');self.assertEqual(len(saved['revisions']),1)
        self.assertEqual(saved['images'][job['image_id']]['status'],'human_confirmed')

    def test_roman_numeral_is_preserved_but_invalid_repeat_is_not(self):
        self.assertTrue(readable(dict(text='III',recognition_score=.99),script='latin'))
        self.assertFalse(readable(dict(text='LLLL',recognition_score=.99),script='latin'))

    def test_partial_tile_suffix_does_not_duplicate_or_replace_full_word(self):
        base=dict(polygon=[],detection_score=.9)
        rows=merge_regions([dict(base,box=[0,0,100,20],text='CONTINUE!',recognition_score=.9,**{'pass':'whole'}),dict(base,box=[60,0,100,20],text='NUE!',recognition_score=.99,**{'pass':'tile'})])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['text'],'CONTINUE!');self.assertEqual(len(rows[0]['alternatives']),2)
        # Genuine repeated text at another position is not a duplicate.
        rows=merge_regions([dict(base,box=[0,0,100,20],text='ERROR!',recognition_score=.9,**{'pass':'whole'}),dict(base,box=[0,40,100,60],text='ERROR!',recognition_score=.99,**{'pass':'tile'})])
        self.assertEqual(len(rows),2)

    def test_project_choice_is_not_implicitly_enabled(self):
        with self.assertRaises(ValueError):require_enabled(self.root)
        configure(self.root,'disabled','fixture user chose text only')
        with self.assertRaises(ValueError):require_enabled(self.root)
        configure(self.root,'enabled','fixture explicit choice');self.assertEqual(require_enabled(self.root)['image_translation']['status'],'enabled')

    def test_clipped_conflicting_tile_keeps_whole_box_and_disagreement(self):
        base=dict(polygon=[],detection_score=.9)
        rows=merge_regions([dict(base,box=[0,0,140,20],text='LAKEVIEW',recognition_score=.8,**{'pass':'whole'}),dict(base,box=[128,0,142,20],text='VW?',recognition_score=.99,**{'pass':'tile-bg0-128-0'})])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['text'],'LAKEVIEW');self.assertTrue(rows[0]['disagreement']);self.assertEqual(rows[0]['alternatives'][1]['box'],[128,0,142,20])

if __name__=='__main__':unittest.main()
