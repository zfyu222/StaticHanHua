"""Patch/translation contracts; semantic validation reports here are test fixtures."""
import csv
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from core import digest,inventory,load_image,read_json,write_json
from image_patch import build
from project_options import configure
from translation_bridge import prepare,collect

FONT=Path(__file__).resolve().parents[1]/'Fonts/NotoSC/NotoSansSC-variable.ttf'

class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source_images=self.root/'input';self.source_images.mkdir();self.out=self.root/'work'
        Image.new('RGBA',(180,80),(10,40,70,255)).save(self.source_images/'panel.png');self.m=inventory(self.source_images,self.out);self.key=self.m['images'][0]['image_id']
        self.source=self.root/'fixture_images_text.csv';self.translated=self.root/'fixture_images_text_translated.csv';self.validation=self.root/'fixture_validation.json'
        self.csv(self.source,['Continue']);self.csv(self.translated,['继续游戏'])
        self.mapping=dict(schema=2,csv_sha256=digest(self.source.read_bytes()),records=1,regions=[dict(csv_row=1,image_id=self.key,box=[10,10,170,70],sources=self.m['images'][0]['sources'],confirmation=dict(status='agent_visual_confirmed'))])
        write_json(self.source.with_suffix('.mapping.json'),self.mapping);self.report()

    def tearDown(self):self.tmp.cleanup()
    def csv(self,path,values):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8-sig',newline='') as f:csv.writer(f).writerows([[x] for x in values])
    def report(self):write_json(self.validation,dict(status='passed',semantic_review='full',source_sha256=digest(self.source.read_bytes()),translation_sha256=digest(self.translated.read_bytes()),records=1,test_fixture=True))

    def test_texture_preserves_outside_rectangle_and_never_installs(self):
        destination=self.root/'patch';result=build(self.out,self.source,self.translated,self.validation,FONT,destination)
        self.assertEqual(result['status'],'ready_for_engine_adapter');self.assertFalse(result['installed'])
        image=load_image(destination/result['files'][0]['path']);original=load_image(self.out/'images'/f'{self.key}.png')
        self.assertEqual(image.size,original.size);self.assertEqual(image.getpixel((0,0)),original.getpixel((0,0)))
        self.assertNotEqual(image.getpixel((12,12)),original.getpixel((12,12)))
        self.assertEqual(original.getpixel((12,12)),(10,40,70,255))

    def test_stale_translation_fails_before_output(self):
        self.csv(self.translated,['different'])
        with self.assertRaises(ValueError):build(self.out,self.source,self.translated,self.validation,FONT,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_too_small_texture_reports_failure_but_overlay_can_be_prepared(self):
        self.mapping['regions'][0]['box']=[0,0,8,5];write_json(self.source.with_suffix('.mapping.json'),self.mapping)
        with self.assertRaises(ValueError):build(self.out,self.source,self.translated,self.validation,FONT,self.root/'small')
        self.assertTrue((self.root/'small/BUILD_FAILED.json').exists());self.assertFalse((self.root/'small/IMAGE_PATCH_MANIFEST.json').exists())
        result=build(self.out,self.source,self.translated,self.validation,FONT,self.root/'overlay','overlay')
        self.assertEqual(len(result['entries']),1);self.assertFalse(result['files'])

    def test_translation_workspace_isolated_and_collect_requires_valid_export(self):
        configure(self.root,'enabled','fixture');result=prepare(self.root,self.source);workspace=Path(result['workspace'])
        self.assertEqual(workspace,self.root/'TranslateFiles/image-text');self.assertFalse((self.root/'TranslateFiles/state').exists())
        generated=Path(result['translation']);self.csv(generated,['继续游戏'])
        report=read_json(self.validation);write_json(Path(result['validation']),report)
        self.assertEqual(collect(self.root)['translation'],str(self.translated))
        self.csv(self.source,['changed'])
        with self.assertRaises(ValueError):collect(self.root)

if __name__=='__main__':unittest.main()
