"""Production OCR contract, checked before inference and before tiered routing."""
GPU_HELP = ('Production image extraction requires PP-OCRv6 medium on CUDA. '
            'Use Tools/image_text/run.ps1 pipeline --project <project> --out <work> '
            '(or .venv-gpu/Scripts/python.exe image_workflow.py pipeline). '
            'Do not replace this with a CPU scan when CUDA is unavailable.')


def validate_options(model_size, device, allow_nonstandard=False):
    if model_size not in ('tiny', 'small', 'medium') or device not in ('cpu', 'cuda'):
        raise ValueError('Unsupported OCR device or size')
    if (model_size, device) != ('medium', 'cuda') and not allow_nonstandard:
        raise ValueError(GPU_HELP + ' CPU/non-medium experiments require --allow-nonstandard-ocr explicitly.')


def require_standard_scan(scan, manifest=None, complete=False):
    engine = scan.get('config', {}).get('engine', {})
    if engine.get('model_size') != 'medium' or engine.get('device') != 'cuda':
        raise ValueError(GPU_HELP + ' Saved scan is nonstandard or lacks device/model evidence; keep it and use a new work directory.')
    for name, filename in [('Det', 'PP-OCRv6_det_medium.onnx'), ('Rec', 'PP-OCRv6_rec_medium.onnx')]:
        model = engine.get('models', {}).get(name, {})
        providers = model.get('providers', [])
        if model.get('file') != filename or not model.get('sha256') or not providers or providers[0] != 'CUDAExecutionProvider':
            raise ValueError(GPU_HELP + ' Saved '+name+' model/provider evidence does not match production OCR.')
    if complete:
        rows = scan.get('images', [])
        expected = {x['image_id'] for x in manifest['images']}
        actual = [x['image_id'] for x in rows]
        if (len(actual) != len(expected) or set(actual) != expected
                or scan.get('manifest_hash') != manifest['manifest_hash'] or any(x.get('errors') for x in rows)):
            raise RuntimeError('Full OCR did not finish successfully; Qwen not started')
