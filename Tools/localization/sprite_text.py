"""Replace lettering inside a reviewed rectangle; preserve native uncompressed TGA.

Requires Pillow, NumPy and OpenCV. No original file is overwritten by this module.
"""
import hashlib
import io
import struct
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def replace_text(source, label, box, font_path, font_size=23, color=(239, 224, 174), erase_threshold=150, background_box=None):
    raw = Path(source).read_bytes()
    assert raw[1] == 0 and raw[2] == 2, 'Only uncompressed, non-paletted TGA supported'
    width, height = struct.unpack_from('<HH', raw, 12)
    bits, descriptor = raw[16:18]
    assert bits in (16, 24, 32) and not descriptor & 16
    original = np.array(Image.open(io.BytesIO(raw)).convert('RGBA'))
    left, top, right, bottom = box
    assert 0 <= left < right <= width and 0 <= top < bottom <= height
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[top:bottom, left:right] = 255
    # Some 16-bit game sprites declare zero alpha bits and store bit 15 as zero.
    # Pillow exposes that unused bit as alpha; it must not disable RGB editing.
    if descriptor & 15:
        mask[original[:,:,3] != 255] = 0
    mask[np.all(original[:,:,:3] == (255, 0, 255), axis=2)] = 0
    # Erase bright letter strokes and their outlines, rather than the whole panel.
    # This retains the original wood/stone texture between letter strokes.
    letters = np.zeros_like(mask)
    letters[(original[:,:,:3].mean(axis=2) > erase_threshold) & (mask != 0)] = 255
    letters = cv2.dilate(letters, np.ones((5,5),dtype=np.uint8))
    letters[mask == 0] = 0
    clean = cv2.inpaint(original[:, :, :3].copy(), letters, 3, cv2.INPAINT_TELEA)
    if background_box is not None:
        background = Image.fromarray(original[:,:,:3]).crop(background_box).resize((right-left,bottom-top))
        clean[top:bottom,left:right] = np.array(background)
    rendered = Image.fromarray(clean)
    draw = ImageDraw.Draw(rendered)
    while font_size >= 10:
        font = ImageFont.truetype(str(font_path), font_size)
        bounds = draw.textbbox((0, 0), label, font=font, stroke_width=1)
        tw, th = bounds[2]-bounds[0], bounds[3]-bounds[1]
        if tw <= right-left-4 and th <= bottom-top-2:
            break
        font_size -= 1
    else:
        raise ValueError(f'Text does not fit: {label}')
    x = (left+right-tw)//2-bounds[0]
    y = (top+bottom-th)//2-bounds[1]
    draw.text((x+1, y+1), label, font=font, fill=(30, 23, 17), stroke_width=1, stroke_fill=(30, 23, 17))
    draw.text((x, y), label, font=font, fill=tuple(color))
    edited = np.array(rendered)
    # Even stroke/shadow pixels are confined to the agreed rectangle.
    edited[mask == 0] = original[:, :, :3][mask == 0]
    offset = 18 + raw[0]
    size = width*height*(bits//8)
    assert len(raw) >= offset+size
    rgb = edited if descriptor & 32 else edited[::-1]
    old = raw[offset:offset+size]
    if bits == 16:
        words = np.frombuffer(old, dtype='<u2').reshape(height, width).copy()
        rgb = rgb.astype(np.uint16)
        encoded = (words & 0x8000) | ((rgb[:,:,0] >> 3) << 10) | ((rgb[:,:,1] >> 3) << 5) | (rgb[:,:,2] >> 3)
        payload = encoded.astype('<u2').tobytes()
    else:
        pixels = np.frombuffer(old, dtype=np.uint8).reshape(height, width, bits//8).copy()
        pixels[:,:,:3] = rgb[:,:,::-1]
        payload = pixels.tobytes()
    output = raw[:offset] + payload + raw[offset+size:]
    result = np.array(Image.open(io.BytesIO(output)).convert('RGBA'))
    assert np.array_equal(result[:,:,3], original[:,:,3])
    assert np.array_equal(result[mask == 0], original[mask == 0]), 'Pixels outside text box changed'
    assert len(output) == len(raw) and output[:18] == raw[:18]
    return output, dict(label=label, box=list(box), font_size=font_size, size=[width,height],
                        bits=bits, source_sha256=hashlib.sha256(raw).hexdigest(),
                        sha256=hashlib.sha256(output).hexdigest(),
                        outside_box_unchanged=True, alpha_unchanged=True)
