"""Patch/translation contracts; semantic validation reports here are test fixtures."""
import csv
from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageDraw
from core import digest,inventory,load_image,read_json,write_json
from image_patch import build,clean_map_ink
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

    def test_ink_cleanup_is_opt_in_and_keeps_unselected_art(self):
        image=Image.new('RGBA',(120,60),(210,160,90,255));draw=ImageDraw.Draw(image)
        draw.rectangle((30,15,45,30),fill=(140,20,15,255));draw.line((5,45,110,45),fill='black',width=1)
        before=image.tobytes();clean,count=clean_map_ink(image)
        self.assertGreater(count,0);self.assertEqual(image.tobytes(),before)
        self.assertEqual(clean.getpixel((70,45)),image.getpixel((70,45)))
        self.assertNotEqual(clean.getpixel((35,20)),image.getpixel((35,20)))

    def test_per_image_soft_plate_keeps_texture_and_outside_pixels(self):
        result=build(self.out,self.source,self.translated,self.validation,FONT,self.root/'soft',
            texture_styles={self.key:dict(mask_rgba=[72,50,25,96],blur_radius=2,stroke_width=1)})
        image=load_image(self.root/'soft'/result['files'][0]['path'])
        self.assertEqual(image.getpixel((0,0)),(10,40,70,255))
        self.assertEqual(result['entries'][0]['mask_rgba'][3],96)
        self.assertEqual(result['entries'][0]['rendering']['background_mode'],'softened_texture_mask')
        self.assertNotEqual(image.getpixel((12,12))[:3],(72,50,25))
        with self.assertRaises(ValueError):
            build(self.out,self.source,self.translated,self.validation,FONT,self.root/'bad_style',texture_styles={'wrong':{}})

    def test_engine_safe_text_color_is_not_replaced_by_white(self):
        result=build(self.out,self.source,self.translated,self.validation,FONT,self.root/'ivory',text_rgba=(245,240,220,255))
        image=load_image(self.root/'ivory'/result['files'][0]['path']);data=image.tobytes()
        self.assertTrue(any(data[i:i+3]==bytes((245,240,220)) and data[i+3]>0 for i in range(0,len(data),4)))
        self.assertEqual(result['entries'][0]['text_rgba'],[245,240,220,255])
        with self.assertRaises(ValueError):build(self.out,self.source,self.translated,self.validation,FONT,self.root/'invisible',text_rgba=(1,2,3,0))

    def test_transparent_text_assets_clear_old_ink_without_opaque_rectangles(self):
        source_images=self.root/'transparent';source_images.mkdir()
        image=Image.new('RGBA',(180,80),(0,0,0,0));ImageDraw.Draw(image).rectangle((30,20,90,40),fill='white');image.save(source_images/'title.png')
        out=self.root/'transparent_work';manifest=inventory(source_images,out);key=manifest['images'][0]['image_id']
        mapping=dict(self.mapping,regions=[dict(self.mapping['regions'][0],image_id=key,sources=manifest['images'][0]['sources'])])
        write_json(self.source.with_suffix('.mapping.json'),mapping)
        result=build(out,self.source,self.translated,self.validation,FONT,self.root/'transparent_patch')
        image=load_image(self.root/'transparent_patch'/result['files'][0]['path'])
        self.assertEqual(image.getpixel((11,11))[3],0)
        self.assertEqual(image.getpixel((0,0))[3],0)
        self.assertEqual(result['entries'][0]['rendering']['background_mode'],'transparent_text_replacement')
        self.assertGreater(sum(image.getchannel('A').getdata()),0)

    def test_preserved_glyphs_and_brands_are_not_masked_or_repacked(self):
        self.csv(self.source,['Continue','TM'])
        self.csv(self.translated,['继续游戏','TM'])
        self.mapping['records']=2;self.mapping['csv_sha256']=digest(self.source.read_bytes())
        region=dict(self.mapping['regions'][0],csv_row=2,box=[0,0,8,5])
        self.mapping['regions'].append(region)
        write_json(self.source.with_suffix('.mapping.json'),self.mapping)
        write_json(self.validation,dict(status='passed',semantic_review='full',source_sha256=digest(self.source.read_bytes()),translation_sha256=digest(self.translated.read_bytes()),records=2,test_fixture=True))
        result=build(self.out,self.source,self.translated,self.validation,FONT,self.root/'mixed')
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(len(result['preserved_regions']),1)
        image=load_image(self.root/'mixed'/result['files'][0]['path'])
        original=load_image(self.out/'images'/f'{self.key}.png')
        self.assertEqual(image.crop((0,0,8,5)).tobytes(),original.crop((0,0,8,5)).tobytes())
        self.csv(self.translated,['Continue','TM'])
        write_json(self.validation,dict(status='passed',semantic_review='full',source_sha256=digest(self.source.read_bytes()),translation_sha256=digest(self.translated.read_bytes()),records=2,test_fixture=True))
        preserved=build(self.out,self.source,self.translated,self.validation,FONT,self.root/'preserved')
        self.assertFalse(preserved['files']);self.assertFalse(preserved['entries'])
        self.assertEqual(len(preserved['preserved_regions']),2)

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
