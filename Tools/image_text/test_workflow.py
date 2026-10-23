"""Behavioral regressions: identities, missed text, cache geometry and human export gates."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from core import (canonical, digest, export_review, inventory, merge_regions,
                  pixel_hash, read_json, validate_review, write_json)
from engine import analyze, positions


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.input=self.root/'input';self.input.mkdir()
        self.out=self.root/'out'
        Image.new('RGBA',(100,60),'white').save(self.input/'a.png')
        Image.new('RGBA',(100,60),'white').save(self.input/'duplicate.png')
        (self.input/'bad.png').write_bytes(b'not an image')
        self.manifest=inventory(self.input,self.out,{'a.png':{'asset':'source.assets','path_id':12}})
        self.image_id=self.manifest['images'][0]['image_id']
        self.region=dict(region_id='region-1',box=[10,10,80,30],text='original')
        self.scan=dict(images=[dict(image_id=self.image_id,regions=[self.region])])
        self.review=dict(scan_hash=digest(canonical(self.scan)),regions=[dict(region_id='region-1',image_id=self.image_id,
                         box=[10,10,80,30],text='Hello, world\nsecond line',decision='accept')],images=[])

    def tearDown(self):self.temp.cleanup()

    def test_dedupe_provenance_and_decode_failure(self):
        self.assertEqual(len(self.manifest['images']),1)
        self.assertEqual(len(self.manifest['images'][0]['sources']),2)
        self.assertEqual(len(self.manifest['errors']),1)
        self.assertEqual(self.manifest['files'][0]['provenance']['path_id'],12)

    def test_invisible_rgb_is_canonical_but_visible_pixels_are_not(self):
        a=Image.new('RGBA',(1,1),(255,0,0,0));b=Image.new('RGBA',(1,1),(0,255,0,0))
        self.assertEqual(pixel_hash(a),pixel_hash(b))
        self.assertNotEqual(pixel_hash(a),pixel_hash(Image.new('RGBA',(1,1),(255,0,0,1))))

    def test_no_recognition_does_not_discard_detection(self):
        class Fake:
            def run(self,*args):return [dict(box=[1,1,4,4],polygon=[[1,1],[4,1],[4,4],[1,4]],text='',recognition_score=0,detection_score=.8,**{'pass':'test'})],[]
        result=analyze(Fake(),Image.new('RGBA',(30,30),'white'),'image','fast')
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['regions'][0]['status'],'needs_review')

    def test_failure_is_distinct_from_no_detection(self):
        class Bad:
            def run(self,*args):raise RuntimeError('broken detector')
        result=analyze(Bad(),Image.new('RGBA',(30,30),'white'),'image','fast')
        self.assertEqual(result['status'],'scan_failed')
        self.assertTrue(result['errors'])

    def test_conflicting_passes_preserve_both_readings(self):
        base=dict(box=[0,0,50,20],polygon=[],recognition_score=.99,detection_score=.9,**{'pass':'light'})
        rows=merge_regions([dict(base,text='SAVE'),dict(base,text='SANE',**{'pass':'dark'})])
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['alternatives']),2)
        self.assertEqual(rows[0]['status'],'needs_review')

    def test_overlap_tiles_cover_edges(self):
        points=positions(2050,768,128)
        self.assertEqual(points[0],0);self.assertEqual(points[-1]+768,2050)
        self.assertTrue(all(b-a<768 for a,b in zip(points,points[1:])))

    def test_source_coordinates_survive_sprite_crop(self):
        class Fake:
            def run(self,image,name,offset,scale):
                if not name.startswith('sprite-'):return [],[]
                x,y=offset
                return [dict(box=[x,y,x+10,y+5],polygon=[],text='HELLO',recognition_score=.99,detection_score=.8,**{'pass':name})],[]
        result=analyze(Fake(),Image.new('RGBA',(200,200),'white'),'image','fast',[[50,60,90,80]])
        self.assertEqual(result['regions'][0]['box'],[50,60,60,65])

    def test_queue_respects_budget_preserves_negatives_and_sends_nothing(self):
        import argparse
        from cli import queue
        image=self.manifest['images'][0]
        scan=dict(images=[dict(image_id=self.image_id,status='not_detected',regions=[],errors=[])])
        write_json(self.out/'scan.json',scan)
        args=argparse.Namespace(out=str(self.out),max_jobs=1,negative_samples=10,seed=42)
        with patch('builtins.print'):queue(args)
        result=read_json(self.out/'queue/jobs.json')
        self.assertEqual(result['mode'],'offline_no_requests_sent')
        self.assertEqual(result['selected'],1)
        self.assertEqual(result['reserved_negative_audit'],1)
        self.assertEqual(result['jobs'][0]['reason'],'not_detected_audit_sample')

    def test_balanced_rescues_a_negative_without_discarding_first_pass(self):
        class Fake:
            def run(self,image,name,offset,scale):
                if 'smooth' not in name:return [],[]
                return [dict(box=[0,0,20,10],polygon=[],text='PLAY',recognition_score=.99,detection_score=.8,**{'pass':name})],[]
        result=analyze(Fake(),Image.new('RGBA',(30,30),'white'),'image','balanced')
        self.assertEqual(result['passes'],2)
        self.assertEqual(result['regions'][0]['text'],'PLAY')

    def test_resume_uses_cache_and_checks_snapshot_before_reusing(self):
        import argparse
        from cli import scan
        class Fake:
            metadata={'test':'local-engine-fixture'}
            def run(self,*args):return [],[]
        args=argparse.Namespace(out=str(self.out),threads=1,threshold=.35,language='ch',profile='fast',sprite_crops=False,ids=None,limit=None,refresh=False)
        with patch('engine.Engine',return_value=Fake()),patch('builtins.print'):
            scan(args)
            first=read_json(self.out/'scan.json')
            with patch.object(Fake,'run',side_effect=AssertionError('cache was not reused')):
                scan(args)
            self.assertEqual(read_json(self.out/'scan.json'),first)
            self.assertEqual(read_json(self.out/'summary.json')['invocation']['cached_images'],1)
            Image.new('RGBA',(100,60),'black').save(self.out/'images'/f'{self.image_id}.png')
            with self.assertRaises(ValueError):scan(args)

    def test_strict_review_rejects_stale_duplicate_unknown_and_moved(self):
        variants=[]
        stale=copy.deepcopy(self.review);stale['scan_hash']='old';variants.append(stale)
        duplicate=copy.deepcopy(self.review);duplicate['regions']*=2;variants.append(duplicate)
        unknown=copy.deepcopy(self.review);unknown['regions'][0]['region_id']='other';variants.append(unknown)
        moved=copy.deepcopy(self.review);moved['regions'][0]['box']=[0,0,30,20];variants.append(moved)
        for review in variants:
            with self.assertRaises(ValueError):validate_review(review,self.scan,self.manifest)

    def test_manual_missed_text_coordinates_and_no_text_conflict(self):
        manual=copy.deepcopy(self.review)
        manual['regions']=[dict(region_id='manual-new',image_id=self.image_id,box=[0,0,30,30],text='missed text',decision='accept',manual=True)]
        self.assertEqual(len(validate_review(manual,self.scan,self.manifest)),1)
        manual['regions'][0]['box']=[-1,0,30,30]
        with self.assertRaises(ValueError):validate_review(manual,self.scan,self.manifest)
        conflict=copy.deepcopy(self.review);conflict['images']=[dict(image_id=self.image_id,decision='confirmed_no_text')]
        with self.assertRaises(ValueError):validate_review(conflict,self.scan,self.manifest)

    def test_csv_export_requires_confirmation_and_preserves_multiline_mapping(self):
        write_json(self.out/'scan.json',self.scan)
        write_json(self.out/'review.json',self.review)
        target=self.root/'text.csv'
        result=export_review(self.out,self.out/'review.json',target)
        self.assertEqual(result['records'],1)
        import csv
        with target.open(encoding='utf-8-sig',newline='') as f:self.assertEqual(list(csv.reader(f)),[['Hello, world\nsecond line']])
        mapping=read_json(target.with_suffix('.mapping.json'))
        self.assertEqual(len(mapping['regions'][0]['sources']),2)
        with self.assertRaises(ValueError):export_review(self.out,self.out/'review.json',target)
        self.review['regions'][0]['decision']='pending';write_json(self.out/'review.json',self.review)
        with self.assertRaises(ValueError):export_review(self.out,self.out/'review.json',self.root/'pending.csv')


if __name__=='__main__':unittest.main()
