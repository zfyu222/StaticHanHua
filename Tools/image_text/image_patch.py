"""Prepare image-text patch resources and previews; engine import remains explicit."""
import html
import math
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageFilter
from core import digest,load_image,pixel_hash,read_json,validate_box,write_json
from translation_bridge import validate

DEFAULT_MASK_ALPHA=64
DEFAULT_STROKE_WIDTH=1

def clean_map_ink(image,prefer_dark=False,prefer_light=False):
    """Local color mask + deterministic inpainting inside a confirmed text box.

    This approximates paper under old lettering; it cannot recover hidden art.
    Only an explicit texture style enables it. Original evidence is unchanged.
    prefer_light targets pale carved/embossed lettering on darker art.
    """
    import cv2
    import numpy as np
    rgb=np.array(image.convert('RGB'));r,g,b=[rgb[:,:,i].astype('float32') for i in range(3)]
    red=(r>2.2*g)&(r-b>45)&(g<130)
    light=(r>185)&(g>180)&(b>150)
    dark=prefer_dark or int(red.sum())<5
    selected=(r<170)&(g<145)&(b<115) if dark else red
    if prefer_light:selected=light
    mask=cv2.dilate(selected.astype('uint8')*255,np.ones((3,3),dtype='uint8'),iterations=2 if (dark or prefer_light) else 1)
    if int((mask>0).sum())>=mask.size*0.98:raise ValueError('Ink mask leaves insufficient texture; inspect the confirmed region/style')
    result=Image.fromarray(cv2.inpaint(rgb,mask,3,cv2.INPAINT_TELEA)).convert('RGBA');result.putalpha(image.getchannel('A'))
    return result,int((mask>0).sum())

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

def build(out,source,translated,validation,font_path,destination,mode='texture',mask_alpha=DEFAULT_MASK_ALPHA,text_rgba=(255,255,255,255),texture_styles=None):
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
    texture_styles=texture_styles or {}
    if not isinstance(texture_styles,dict) or set(texture_styles)-set(images):raise ValueError('Unknown texture style image')
    for style in texture_styles.values():
        if not isinstance(style,dict) or set(style)-{'mask_rgba','blur_radius','stroke_width','erase_text_ink','erase_light_ink','clear_transparent_bg','dark_rows','max_font_size','text_rgba'}:raise ValueError('Unknown texture style field')
        if type(style.get('erase_light_ink',False)) is not bool or type(style.get('clear_transparent_bg',False)) is not bool:raise ValueError('Invalid ink cleanup flag')
        if 'max_font_size' in style:
            mfs=style['max_font_size']
            if type(mfs) is int:
                if not 6<=mfs<=400:raise ValueError('Invalid max font size')
            elif isinstance(mfs,dict):
                if any(not str(k).isdigit() or type(v) is not int or not 6<=v<=400 for k,v in mfs.items()):raise ValueError('Invalid per-row max font size')
            else:raise ValueError('Invalid max font size')
        if 'text_rgba' in style and (not isinstance(style['text_rgba'],(list,tuple)) or len(style['text_rgba'])!=4 or any(type(c) is not int or not 0<=c<=255 for c in style['text_rgba']) or style['text_rgba'][3]==0):raise ValueError('Invalid style text color')
        if not isinstance(style.get('dark_rows',[]),list) or any(type(n) is not int or n<1 for n in style.get('dark_rows',[])):raise ValueError('Invalid dark-ink row list')
        if type(style.get('erase_text_ink',False)) is not bool:raise ValueError('Invalid ink cleanup flag')
        color=style.get('mask_rgba',[96,96,96,mask_alpha])
        if not isinstance(color,(list,tuple)) or len(color)!=4 or any(type(c) is not int or not 0<=c<=255 for c in color):raise ValueError('Invalid texture style color')
        if type(style.get('blur_radius',0)) not in (int,float) or not 0<=style.get('blur_radius',0)<=8:raise ValueError('Invalid texture blur radius')
        if type(style.get('stroke_width',0)) is not int or not 0<=style.get('stroke_width',0)<=2:raise ValueError('Invalid text stroke')
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
                style=texture_styles.get(key,{})
                mask=list(style.get('mask_rgba',[96,96,96,mask_alpha]));rendering={}
                if mode=='texture':
                    alpha=original_image.crop(rect).getchannel('A').histogram();pixels=sum(alpha)
                    transparent_ink=(alpha[0]/pixels>0.1 and alpha[255]/pixels<0.5) or style.get('clear_transparent_bg',False)
                    width,height=rect[2]-rect[0],rect[3]-rect[1]
                    padding=(2 if min(width,height)>=28 else 1) if transparent_ink else 0
                    row_limit=style.get('max_font_size',96)
                    if isinstance(row_limit,dict):row_limit=row_limit.get(str(row['csv_row']),96)
                    font,lines,step=layout(text,font_path,width-2*padding,height-2*padding,max_size=row_limit)
                    if transparent_ink:mask=[0,0,0,0]
                    radius=0 if transparent_ink else style.get('blur_radius',0)
                    erased=0
                    background=original_image.crop(rect)
                    if not transparent_ink and (style.get('erase_text_ink') or style.get('erase_light_ink')):
                        background,erased=clean_map_ink(background,row['csv_row'] in style.get('dark_rows',[]),style.get('erase_light_ink',False))
                    if radius:background=background.filter(ImageFilter.GaussianBlur(radius))
                    if erased or radius:im.paste(background,tuple(rect))
                    patch=Image.new('RGBA',(width,height),tuple(mask));draw=ImageDraw.Draw(patch)
                    y=max(padding,(patch.height-step*len(lines))//2)
                    ink=tuple(style.get('text_rgba',text_rgba))
                    for line in lines:
                        x=max(padding,(patch.width-font.getlength(line))/2);draw.text((x,y),line,font=font,fill=ink,anchor='lt',stroke_width=1 if transparent_ink else style.get('stroke_width',DEFAULT_STROKE_WIDTH),stroke_fill='black');y+=step
                    if transparent_ink:im.paste((0,0,0,0),tuple(rect))
                    im.alpha_composite(patch,(rect[0],rect[1]))
                    rendering=dict(font_size=font.size,lines=lines,background_mode='transparent_text_replacement' if transparent_ink else 'inpainted_texture_mask' if erased else 'softened_texture_mask' if radius else 'mask',blur_radius=radius,erased_ink_pixels=erased,stroke_width=1 if transparent_ink else style.get('stroke_width',DEFAULT_STROKE_WIDTH),style_flags=dict(clear_transparent_bg=bool(style.get('clear_transparent_bg')),erase_light_ink=bool(style.get('erase_light_ink')),erase_text_ink=bool(style.get('erase_text_ink')),max_font_size=style.get('max_font_size')))
                entries.append(dict(image_id=key,box=row['box'],text=text,csv_row=row['csv_row'],sources=row['sources'],mask_rgba=mask,text_rgba=list(text_rgba),rendering=rendering))
            if mode=='texture':
                name=f'images/{key}.png';path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);im.save(path)
                files.append(dict(path=name,sha256=digest(path.read_bytes()),image_id=key,sources=images[key]['sources']))
        payload=dict(schema=1,status='ready_for_engine_adapter',mode=mode,installed=False,source_sha256=digest(source.read_bytes()),translation_sha256=digest(Path(translated).read_bytes()),
                     mapping_sha256=digest(source.with_suffix('.mapping.json').read_bytes()),font=dict(name=font_path.name,sha256=digest(font_path.read_bytes()),variable_weight_requested=400),files=files,entries=entries,preserved_regions=preserved,
                     visual_review=dict(status='pending',policy='preserve_art_v1',checks=['original_art_visibility','no_old_text_ghosting','style_and_legibility','font_size_and_edges']),
                     note='Resource candidate only. Visual review and engine-specific import remain required; low mask opacity alone does not establish acceptable appearance.')
        write_json(destination/'IMAGE_PATCH_MANIFEST.json',payload)
        cards=''.join(f'<article><h2>{html.escape(x["image_id"][:16])}</h2><img src="{x["path"]}" style="max-width:100%;image-rendering:pixelated;background:#888"></article>' for x in files)
        (destination/'preview.html').write_text('<!doctype html><meta charset="utf-8"><title>图片文字合入预览</title><p>资源预览，尚未导入游戏。覆盖层模式需要引擎适配器预览。</p>'+cards,encoding='utf-8')
        return payload
    except Exception as exc:
        write_json(destination/'BUILD_FAILED.json',dict(status='failed',error=str(exc),installed=False));raise
