"""Offline handoff contracts; fixtures are not real multimodal/API verdicts."""
import copy
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from core import canonical, digest, export_review, inventory, read_json, validate_review, write_json
from handoff import import_findings, prepare
from review import build_review
from triage import prioritize


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        source=self.root/'input';source.mkdir();self.out=self.root/'work'
        for i,color in enumerate(('white','red','blue','green')):Image.new('RGBA',(100,60),color).save(source/f'{i}.png')
        self.manifest=inventory(source,self.out)
        self.ids=[x['image_id'] for x in self.manifest['images']]
        def region(key,box,text='',score=0):return dict(region_id=key,box=box,text=text,recognition_score=score,detection_score=.9,disagreement=False,status='needs_review')
        self.scan=dict(images=[dict(image_id=self.ids[0],status='needs_review',errors=[],regions=[region('a',[10,10,40,30]),region('b',[40,10,80,30])]),
                              dict(image_id=self.ids[1],status='not_detected',errors=[],regions=[]),
                              dict(image_id=self.ids[2],status='not_detected',errors=[],regions=[]),
                              dict(image_id=self.ids[3],status='not_detected',errors=[],regions=[])])
        write_json(self.out/'scan.json',self.scan)

    def tearDown(self):self.temp.cleanup()

    def queue(self,**kw):
        kw.setdefault('policy','recall-first')
        return prepare(self.out,**kw)

    def reply(self,queue,job=None,presence='text'):
        job=job or queue['jobs'][0]
        item=dict(id=job['id'],presence=presence,reason='offline contract fixture',regions=[dict(text='HELLO',box=[0,0,1000,1000])] if presence=='text' else [])
        return dict(queue_hash=queue['queue_hash'],records=[item])

    def save_reply(self,reply):
        path=self.root/'reply.json';write_json(path,reply);return path

    def test_groups_multiple_regions_into_one_image_job(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        self.assertEqual(queue['selected'],1)
        self.assertEqual(len(queue['jobs'][0]['regions']),2)
        self.assertEqual(queue['jobs'][0]['crop_box'],[0,0,100,60])
        self.assertEqual(len({job['image_id'] for job in queue['jobs']}),queue['selected'])

    def test_budget_is_hard_and_zero_does_not_mean_completed(self):
        queue=self.queue(max_jobs=1,negative_samples=3,low_signal_samples=0)
        self.assertEqual(queue['selected'],1);self.assertGreater(queue['deferred'],0)
        self.assertEqual(queue['reserved_negative_audit'],1)
        self.assertEqual(queue['jobs'][0]['reason'],'not_detected_audit_sample')

    def test_partial_response_stays_pending_and_is_never_approval(self):
        queue=self.queue(negative_samples=2)
        result=import_findings(self.out,self.save_reply(self.reply(queue)))
        self.assertEqual(result['pending'],queue['selected']-1)
        self.assertFalse(result['human_approval'])

    def test_crop_coordinates_restored_and_require_explicit_confirmation(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0,policy='uncertain-only')
        import_findings(self.out,self.save_reply(self.reply(queue)))
        findings=read_json(self.out/'vision_findings.json')
        proposal=findings['records'][0]['proposals'][0]
        self.assertEqual(proposal['box'],[2,2,88,38])
        self.assertEqual(proposal['decision'],'pending')
        review=dict(scan_hash=digest(canonical(self.scan)),regions=[dict(proposal)],images=[])
        self.assertEqual(validate_review(review,self.scan,self.manifest),[])
        review['regions'][0]['decision']='accept'
        self.assertEqual(len(validate_review(review,self.scan,self.manifest)),1)
        build_review(self.out)
        page=(self.out/'review.html').read_text('utf-8')
        self.assertIn('offline_vision_proposal',page)

    def test_no_text_proposal_does_not_confirm_image_absence(self):
        queue=self.queue(negative_samples=1)
        import_findings(self.out,self.save_reply(self.reply(queue,presence='no_text')))
        result=read_json(self.out/'vision_findings.json')
        self.assertEqual(result['records'][0]['proposals'],[])
        self.assertFalse(result['human_approval'])

    def test_reject_unknown_duplicate_missing_and_empty_contracts_atomically(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        base=self.reply(queue);variants=[]
        stale=copy.deepcopy(base);stale['queue_hash']='wrong';variants.append(stale)
        unknown=copy.deepcopy(base);unknown['records'][0]['id']='999';variants.append(unknown)
        duplicate=copy.deepcopy(base);duplicate['records']*=2;variants.append(duplicate)
        missing=copy.deepcopy(base);del missing['records'][0]['presence'];variants.append(missing)
        empty=copy.deepcopy(base);empty['records'][0]['regions']=[];variants.append(empty)
        outside=copy.deepcopy(base);outside['records'][0]['regions'][0]['box']=[-1,0,1000,1000];variants.append(outside)
        for item in variants:
            with self.assertRaises(ValueError):import_findings(self.out,self.save_reply(item))
            self.assertFalse((self.out/'vision_findings.json').exists())

    def test_idempotent_reimport_and_conflicting_results_are_protected(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        reply=self.reply(queue);path=self.save_reply(reply)
        import_findings(self.out,path);import_findings(self.out,path)
        previous=(self.out/'vision_findings.json').read_bytes()
        reply['records'][0]['regions'][0]['text']='CHANGED'
        with self.assertRaises(ValueError):import_findings(self.out,self.save_reply(reply))
        self.assertEqual(previous,(self.out/'vision_findings.json').read_bytes())

    def test_modified_crop_is_not_accepted(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        (self.out/queue['jobs'][0]['crop']).write_bytes(b'changed')
        with self.assertRaises(ValueError):import_findings(self.out,self.save_reply(self.reply(queue)))

    def test_binding_stays_out_of_model_reply_and_raw_reply_is_preserved(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        reply=self.reply(queue);del reply['queue_hash']
        path=self.save_reply(reply);raw=path.read_bytes()
        result=import_findings(self.out,path,self.out/'queue/binding.json')
        self.assertEqual(result['imported'],1)
        self.assertEqual(next((self.out/'vision_replies').glob('*.json')).read_bytes(),raw)

    def test_modified_queue_and_context_are_not_accepted(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        reply=self.reply(queue)
        broken=copy.deepcopy(queue);broken['jobs'][0]['crop_box']=[0,0,90,60]
        write_json(self.out/'queue/jobs.json',broken)
        with self.assertRaises(ValueError):import_findings(self.out,self.save_reply(reply))
        write_json(self.out/'queue/jobs.json',queue)
        Image.new('RGBA',(100,60),'black').save(self.out/queue['jobs'][0]['context'])
        with self.assertRaises(ValueError):import_findings(self.out,self.save_reply(reply))

    def test_triage_only_changes_priority_not_source_detection_or_review(self):
        scan=copy.deepcopy(self.scan);image=scan['images'][0]
        image['regions'][0]['text']='LLLL';image['regions'][0]['recognition_score']=.7
        manifest=copy.deepcopy(self.manifest)
        for row in manifest['images']:row['width']=32;row['height']=32
        original=copy.deepcopy(scan)
        result=prioritize(scan,manifest)
        self.assertEqual(scan,original)
        self.assertEqual(result['images'][0]['priority'],'low')

    def test_recall_first_includes_stable_candidates_and_never_drops_weak_sprites(self):
        scan=copy.deepcopy(self.scan)
        scan['images'][0]['regions'][0]['status']='candidate'
        scan['images'][0]['regions'][1]['status']='candidate'
        scan['images'][0]['status']='text_candidate'
        scan['images'][1]['regions']=[dict(region_id='weak',box=[0,0,4,5],text='1',recognition_score=.1,detection_score=.5,disagreement=False,status='needs_review')]
        scan['images'][1]['status']='needs_review'
        write_json(self.out/'scan.json',scan)
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        self.assertEqual({x['image_id'] for x in queue['jobs']},{self.ids[0],self.ids[1]})
        self.assertEqual(queue['all_detected_images'],2)
        self.assertEqual(queue['not_detected_disposition'],'pending_negative_bucket_not_confirmed_absent')

    def test_full_audit_routes_every_image_even_if_nothing_was_detected(self):
        queue=self.queue(policy='full-audit',negative_samples=0,low_signal_samples=0)
        self.assertEqual(queue['total_candidates'],len(self.ids))

    def test_recall_first_keeps_entire_image_to_rescue_undetected_words(self):
        queue=self.queue(negative_samples=0,low_signal_samples=0)
        self.assertEqual(queue['jobs'][0]['input_size'],[100,60])
        package=read_json(self.out/'queue/input.json')
        self.assertNotIn('context',package['records'][0])


if __name__=='__main__':unittest.main()
