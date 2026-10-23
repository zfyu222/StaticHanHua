"""Local RapidOCR adapter. Detection results survive empty/failed recognition."""
import importlib.metadata
import time
from pathlib import Path
import numpy as np
from PIL import Image
from core import digest, canonical, rect_of, merge_regions, region_id

ENGINE_VERSION = 3


class Engine:
    def __init__(self, threads=4, box_threshold=.35, language='ch', model_size='small', device='cpu'):
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
        import onnxruntime as ort
        if device not in ('cpu','cuda') or model_size not in ('tiny','small','medium'):
            raise ValueError('Unsupported OCR device or size')
        if device=='cuda':
            if 'CUDAExecutionProvider' not in ort.get_available_providers():
                raise RuntimeError('CUDA provider missing; use the isolated GPU environment')
            ort.preload_dlls(directory='')
        params={
            'Global.log_level': 'error', 'Global.use_cls': False,
            'EngineConfig.onnxruntime.intra_op_num_threads': threads,
            'EngineConfig.onnxruntime.inter_op_num_threads': 1,
            'Det.box_thresh': box_threshold, 'Det.limit_side_len': 960, 'Det.limit_type': 'max',
            'Rec.lang_type': LangRec(language),
            'Det.model_type':ModelType(model_size),'Rec.model_type':ModelType(model_size),
            'EngineConfig.onnxruntime.use_cuda':device=='cuda',
        }
        if language == 'en' and model_size=='small':
            # PP-OCRv6's bundled small recognizer is multilingual; the English-only
            # model is published under PP-OCRv5/mobile in the pinned backend.
            params.update({'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE})
        self.ocr = RapidOCR(params=params)
        # Load explicitly so fingerprints include actual selected model files, not just names.
        self.ocr._load_det_model()
        self.ocr._load_rec_model()
        try:ort_version=importlib.metadata.version('onnxruntime-gpu')
        except importlib.metadata.PackageNotFoundError:ort_version=importlib.metadata.version('onnxruntime')
        self.metadata = dict(adapter=ENGINE_VERSION, rapidocr=importlib.metadata.version('rapidocr'),
                             onnxruntime=ort_version, language=language,
                             numpy=importlib.metadata.version('numpy'), pillow=importlib.metadata.version('Pillow'),
                             opencv=importlib.metadata.version('opencv-python-headless'),
                             box_threshold=box_threshold, threads=threads, models={})
        if model_size!='small' or device!='cpu':
            self.metadata.update(model_size=model_size,device=device)
        for name in ('Det', 'Rec'):
            cfg = getattr(self.ocr.cfg, name)
            # RapidOCR engine config resolves the concrete path at initialization.
            model = self.ocr.text_det if name == 'Det' else self.ocr.text_rec
            session = model.session
            providers=session.session.get_providers()
            if device=='cuda' and (not providers or providers[0]!='CUDAExecutionProvider'):
                raise RuntimeError(f'{name} did not initialize CUDA; refusing silent CPU fallback: {providers}')
            path = getattr(session.session, '_model_path', None)
            if path is None:
                raise RuntimeError('Cannot determine model path for cache fingerprint')
            path = Path(path)
            self.metadata['models'][name] = dict(file=path.name, sha256=digest(path.read_bytes()))
            if device=='cuda':self.metadata['models'][name]['providers']=providers

    def run(self, image, name, offset=(0, 0), scale=1):
        """Detect separately; recognize rectified lines without filtering any detected box."""
        from rapidocr.utils.process_img import get_rotate_crop_image
        from rapidocr.ch_ppocr_rec import TextRecInput
        data = np.array(image.convert('RGB'))[:, :, ::-1].copy()
        detected = self.ocr(data, use_det=True, use_cls=False, use_rec=False)
        boxes = getattr(detected, 'boxes', None)
        if boxes is None:
            return [], []
        lines = [get_rotate_crop_image(data, box.copy()) for box in boxes]
        errors = []
        try:
            rec = self.ocr.text_rec(TextRecInput(img=lines))
            texts = rec.txts if rec.txts is not None else ['']*len(boxes)
            scores = rec.scores
            if len(texts) != len(boxes) or len(scores) != len(boxes):
                raise RuntimeError('Recognition result count does not match detection')
        except Exception as exc:
            texts, scores = ['']*len(boxes), [0.0]*len(boxes)
            errors.append(dict(pass_name=name, stage='recognition', error=str(exc)))
        detections = getattr(detected, 'scores', [0.0]*len(boxes))
        result = []
        for box, text, score, det_score in zip(boxes, texts, scores, detections):
            points = [[float(x)/scale+offset[0], float(y)/scale+offset[1]] for x, y in box]
            result.append(dict(polygon=points, box=rect_of(points), text=str(text), recognition_score=float(score),
                               detection_score=float(det_score), **{'pass': name}))
        return result, errors


def positions(length, tile, overlap):
    if length <= tile:
        return [0]
    step = tile-overlap
    values = list(range(0, length-tile+1, step))
    if values[-1] != length-tile:
        values.append(length-tile)
    return values


def views(image, profile):
    """Whole view plus overlapping native-resolution tiles; alpha gets both backgrounds."""
    w, h = image.size
    alpha = image.getextrema()[3][0] < 255
    backgrounds = [(240, 240, 240), (24, 24, 24)] if alpha else [(240, 240, 240)]
    for number, color in enumerate(backgrounds):
        background = Image.new('RGBA', image.size, color+(255,))
        background.alpha_composite(image)
        rgb = background.convert('RGB')
        import math
        scale = min(4, max(1, math.ceil(768/max(w, h))))
        resized = rgb.resize((round(w*scale), round(h*scale)), Image.Resampling.NEAREST)
        yield resized, f'whole-bg{number}-nearest', (0, 0), resized.width/w
        if profile == 'careful' and min(w, h) < 512:
            resized = rgb.resize((w*3, h*3), Image.Resampling.BICUBIC)
            yield resized, f'whole-bg{number}-smooth', (0, 0), 3
        if max(w, h) > 960:
            for y in positions(h, 768, 128):
                for x in positions(w, 768, 128):
                    yield rgb.crop((x, y, min(w, x+768), min(h, y+768))), f'tile-bg{number}-{x}-{y}', (x, y), 1


def analyze(engine, image, image_id, profile, extra_boxes=()):
    started = time.perf_counter()
    candidates, errors, passes = [], [], 0
    for view, name, offset, scale in views(image, profile):
        passes += 1
        try:
            rows, failures = engine.run(view, name, offset, scale)
            candidates.extend(rows)
            errors.extend(failures)
        except Exception as exc:
            errors.append(dict(pass_name=name, stage='detection', error=str(exc)))
    if profile == 'balanced' and not candidates and min(image.size) < 512:
        for view, name, offset, scale in views(image, 'careful'):
            if 'smooth' not in name:
                continue
            passes += 1
            try:
                rows, failures = engine.run(view, name, offset, scale)
                candidates.extend(rows)
                errors.extend(failures)
            except Exception as exc:
                errors.append(dict(pass_name=name, stage='detection', error=str(exc)))
    for number, box in enumerate(extra_boxes):
        import math
        left, top, right, bottom = box
        x, y = math.floor(left), math.floor(top)
        crop = image.crop((x, y, math.ceil(right), math.ceil(bottom)))
        for view, name, offset, scale in views(crop, 'careful' if profile == 'careful' else 'fast'):
            passes += 1
            try:
                rows, failures = engine.run(view, f'sprite-{number}-{name}', (x+offset[0], y+offset[1]), scale)
                candidates.extend(rows)
                errors.extend(failures)
            except Exception as exc:
                errors.append(dict(pass_name=f'sprite-{number}-{name}', stage='detection', error=str(exc)))
    regions = merge_regions(candidates)
    for row in regions:
        row['box'] = [max(0, min(image.width if i % 2 == 0 else image.height, x)) for i, x in enumerate(row['box'])]
        row['region_id'] = region_id(image_id, row['box'])
    reliable = any(row['status'] == 'candidate' for row in regions)
    status = 'scan_failed' if errors else ('text_candidate' if reliable else 'needs_review' if regions else 'not_detected')
    return dict(image_id=image_id, status=status, regions=regions, errors=errors, passes=passes,
                elapsed_seconds=round(time.perf_counter()-started, 4), human_review='unreviewed')
