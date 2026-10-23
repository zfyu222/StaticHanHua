"""Cost-screening tradeoffs are explicit and never turn rejected images into ground truth."""
import copy
import unittest
from core import read_json
from handoff import prepare
from screening import readable, select
import test_handoff as fixtures


class ScreeningRules(unittest.TestCase):
    def region(self,text,score):return dict(text=text,recognition_score=score)

    def test_threshold_repeated_shape_and_invalid_glyphs(self):
        self.assertTrue(readable(self.region('OPEN',.61)))
        self.assertFalse(readable(self.region('OPEN',.59)))
        self.assertFalse(readable(self.region('LLLL',.99)))
        self.assertFalse(readable(self.region('\ufffdA',.99)))
        self.assertFalse(readable(self.region('I',.99)))

    def test_numbers_and_single_cjk_are_preserved(self):
        self.assertTrue(readable(self.region('0000',.95)))
        self.assertTrue(readable(self.region('是',.95)))
        self.assertFalse(readable(self.region('是',.95),script='latin'))
        self.assertTrue(readable(self.region('été',.95),script='latin'))

    def test_errors_are_never_screened_out_and_raw_data_is_unchanged(self):
        scan=dict(images=[dict(image_id='error',errors=[dict(error='failed')],regions=[]),
                          dict(image_id='weak',errors=[],regions=[self.region('L',.3)])])
        original=copy.deepcopy(scan);result=select(scan)
        self.assertTrue(result['images'][0]['keep']);self.assertFalse(result['images'][1]['keep'])
        self.assertEqual(scan,original)


class ScreeningIntegration(unittest.TestCase):
    setUp=fixtures.HandoffTests.setUp
    tearDown=fixtures.HandoffTests.tearDown
    def test_default_screening_drops_weak_candidates_but_keeps_audits(self):
        scan=copy.deepcopy(self.scan)
        scan['images'][0]['regions'][0].update(text='OPEN',recognition_score=.9)
        scan['images'][1].update(status='needs_review',regions=[dict(region_id='weak',box=[1,1,4,6],text='L',recognition_score=.2,detection_score=.5,disagreement=False,status='needs_review')])
        from core import write_json
        write_json(self.out/'scan.json',scan)
        result=prepare(self.out,negative_samples=0,low_signal_samples=0)
        self.assertEqual(result['policy'],'screened')
        self.assertEqual(result['candidate_image_ids'],[self.ids[0]])
        sampled=prepare(self.out,negative_samples=0,low_signal_samples=1)
        self.assertIn(self.ids[1],sampled['candidate_image_ids'])
        self.assertEqual(sampled['reserved_low_signal_audit'],1)
        self.assertEqual(read_json(self.out/'scan.json'),scan)

    def test_cli_defaults_include_seed_and_screening_settings(self):
        import subprocess,sys
        from pathlib import Path
        result=subprocess.run([sys.executable,str(Path(__file__).parent/'cli.py'),'queue','--out',str(self.out),'--max-jobs','1'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        queue=read_json(self.out/'queue/jobs.json')
        self.assertEqual(queue['policy'],'screened');self.assertEqual(queue['seed'],42)
        self.assertEqual(queue['screening']['score'],.6)
