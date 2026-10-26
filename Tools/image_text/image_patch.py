"""Prepare image-text patch resources and previews; engine import remains explicit."""
import html
import math
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from core import digest,load_image,pixel_hash,read_json,validate_box,write_json
from translation_bridge import validate

def layout(text,font_path,width,height,min_size=6,max_size=32):
    for size in range(max_size,min_size-1,-1):
        font=ImageFont.truetype(str(font_path),size);lines=[]
        try:
            axes=font.get_variation_axes()
            font.set_variation_by_axes([max(a['minimum'],min(a['maximum'],400)) if a['name']==b'Weight' else a['default'] for a in axes])
        except OSError:pass
        for paragraph in text.split('\n'):
            line=''
            for char in paragraph:
                if font.getlength(line+char)>width and line:lines.append(line);line=char
                else:line+=char
            lines.append(line)
        ascent,descent=font.getmetrics();step=ascent+descent
        if all(font.getlength(x)<=width for x in lines) and step*len(lines)<=height:return font,lines,step
    raise ValueError('Translation does not fit original rectangle; enlarge overlay region or use a runtime overlay')

def build(out,source,translated,validation,font_path,destination,mode='texture',mask_alpha=224,text_rgba=(255,255,255,255)):
    from fontTools.ttLib import TTFont
    if mode not in ('texture','overlay') or type(mask_alpha) is not int or not 0<=mask_alpha<=255:raise ValueError('Invalid patch mode or mask alpha')
    if not isinstance(text_rgba,(tuple,list)) or len(text_rgba)!=4 or any(type(c) is not int or not 0<=c<=255 for c in text_rgba) or text_rgba[3]==0:raise ValueError('Text RGBA must contain four visible color bytes')
    out=Path(out);source=Path(source);destination=Path(destination).resolve();font_path=Path(font_path).resolve()
    if destination.exists():raise ValueError('Use a new patch output directory')
    if destination==out.resolve() or out.resolve().is_relative_to(destination) or destination.is_relative_to((out/'images').resolve()):raise ValueError('Patch output must be separate from image evidence')
    original,target,report=validate(source,translated,validation);mapping=read_json(source.with_suffix('.mapping.json'));manifest=read_json(out/'manifest.json')
    if mapping['csv_sha256']!=digest(source.read_bytes()) or mapping['records']!=len(original):raise ValueError('Source mapping mismatch')
    if not font_path.is_file():raise FileNotFoundError(font_path)
    with TTFont(font_path) as ft:cmap=ft.getBestCmap()
    missing=sorted({c for text in target for c in text if not c.isspace() and ord(c) not in cmap})
    if missing:raise ValueError('Font missing characters: '+''.join(missing))
    images={x['image_id']:x for x in manifest['images']};grouped={};preserved=[]
    for row in mapping['regions']:
        if type(row['csv_row']) is not int or not 1<=row['csv_row']<=len(target) or row['image_id'] not in images:raise ValueError('Invalid source row/image mapping')
        image=images[row['image_id']];validate_box(row['box'],image['width'],image['height'])
        if row['sources']!=image['sources']:raise ValueError('Source resource provenance changed')
        if mapping.get('schema')==2 and row.get('confirmation',{}).get('status') not in ('agent_visual_confirmed','human_confirmed'):raise ValueError('Image region has no explicit visual confirmation')
        if target[row['csv_row']-1]==original[row['csv_row']-1]:
            preserved.append(dict(image_id=row['image_id'],box=row['box'],csv_row=row['csv_row'],sources=row['sources'],reason='Translation explicitly preserves the original text; original pixels unchanged'))
            continue
        grouped.setdefault(row['image_id'],[]).append(row)
    destination.mkdir(parents=True);files=[];entries=[]
    try:
        for key,regions in grouped.items():
            im=load_image(out/'images'/f'{key}.png')
            if pixel_hash(im)!=key:raise ValueError('Original image changed')
            original_image=im.copy()
            for row in regions:
                left,top,right,bottom=row['box'];rect=[math.floor(left),math.floor(top),math.ceil(right),math.ceil(bottom)]
                text=target[row['csv_row']-1]
                mask=[96,96,96,mask_alpha];rendering={}
                if mode=='texture':
                    alpha=original_image.crop(rect).getchannel('A').histogram();pixels=sum(alpha)
                    transparent_ink=alpha[0]/pixels>0.1 and alpha[255]/pixels<0.5
                    width,height=rect[2]-rect[0],rect[3]-rect[1]
                    padding=(2 if min(width,height)>=28 else 1) if transparent_ink else 0
                    font,lines,step=layout(text,font_path,width-2*padding,height-2*padding,max_size=96)
                    if transparent_ink:mask=[0,0,0,0]
                    patch=Image.new('RGBA',(width,height),tuple(mask));draw=ImageDraw.Draw(patch)
                    y=max(padding,(patch.height-step*len(lines))//2)
                    for line in lines:
                        x=max(padding,(patch.width-font.getlength(line))/2);draw.text((x,y),line,font=font,fill=tuple(text_rgba),anchor='lt',stroke_width=1 if transparent_ink else 0,stroke_fill='black');y+=step
                    if transparent_ink:im.paste((0,0,0,0),tuple(rect))
                    im.alpha_composite(patch,(rect[0],rect[1]))
                    rendering=dict(font_size=font.size,lines=lines,background_mode='transparent_text_replacement' if transparent_ink else 'mask')
                entries.append(dict(image_id=key,box=row['box'],text=text,csv_row=row['csv_row'],sources=row['sources'],mask_rgba=mask,text_rgba=list(text_rgba),rendering=rendering))
            if mode=='texture':
                name=f'images/{key}.png';path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);im.save(path)
                files.append(dict(path=name,sha256=digest(path.read_bytes()),image_id=key,sources=images[key]['sources']))
        payload=dict(schema=1,status='ready_for_engine_adapter',mode=mode,installed=False,source_sha256=digest(source.read_bytes()),translation_sha256=digest(Path(translated).read_bytes()),
                     mapping_sha256=digest(source.with_suffix('.mapping.json').read_bytes()),font=dict(name=font_path.name,sha256=digest(font_path.read_bytes()),variable_weight_requested=400),files=files,entries=entries,preserved_regions=preserved,
                     note='Exported image/overlay resources only. Engine-specific Texture2D/Sprite binding or runtime adapter and user in-game validation are still required.')
        write_json(destination/'IMAGE_PATCH_MANIFEST.json',payload)
        cards=''.join(f'<article><h2>{html.escape(x["image_id"][:16])}</h2><img src="{x["path"]}" style="max-width:100%;image-rendering:pixelated;background:#888"></article>' for x in files)
        (destination/'preview.html').write_text('<!doctype html><meta charset="utf-8"><title>图片文字合入预览</title><p>资源预览，尚未导入游戏。覆盖层模式需要引擎适配器预览。</p>'+cards,encoding='utf-8')
        return payload
    except Exception as exc:
        write_json(destination/'BUILD_FAILED.json',dict(status='failed',error=str(exc),installed=False));raise
