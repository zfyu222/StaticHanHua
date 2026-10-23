"""Separate image translation dataset, reusing the existing formal translator unchanged."""
import csv
from pathlib import Path
from core import digest,read_json,write_json

def rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:values=list(csv.reader(f))
    if not values or any(len(x)!=1 or not x[0].strip() for x in values):raise ValueError('Nonempty single-column CSV required')
    return [x[0] for x in values]

def prepare(project,source):
    from project_options import require_enabled
    project=Path(project).resolve();require_enabled(project);source=Path(source).resolve()
    if source.parent!=project or not source.name.endswith('_text.csv'):raise ValueError('Image source must be a project-root *_text.csv')
    mapping=read_json(source.with_suffix('.mapping.json'))
    if mapping['csv_sha256']!=digest(source.read_bytes()) or mapping['records']!=len(rows(source)):raise ValueError('Source mapping mismatch')
    workspace=project/'TranslateFiles/image-text';copy=workspace/source.name
    if copy.exists() and copy.read_bytes()!=source.read_bytes():raise ValueError('Image source changed; preserve existing translation workspace')
    copy.parent.mkdir(parents=True,exist_ok=True);copy.write_bytes(source.read_bytes())
    readme=workspace/'README.md'
    if not readme.exists():
        original=(project/'README.md').read_text(encoding='utf-8') if (project/'README.md').exists() else ''
        readme.write_text(original+'\n\n## 本次数据集\n仅翻译已视觉确认的图片原文；普通文本翻译状态保持独立。\n',encoding='utf-8')
    context={str(i):dict(text_type='image_text',resource='; '.join(sorted({s['path'] for r in mapping['regions'] if r['csv_row']==i for s in r['sources']}))) for i in range(1,mapping['records']+1)}
    write_json(workspace/'image_context.json',context)
    result=dict(schema=1,source=str(source),source_sha256=digest(source.read_bytes()),mapping_sha256=digest(source.with_suffix('.mapping.json').read_bytes()),
                workspace=str(workspace),translation=str(copy.with_name(copy.stem+'_translated.csv')),validation=str(workspace/'TranslateFiles/reports/final_validation.json'))
    write_json(workspace/'image_bridge.json',result);return result

def validate(source,translated,validation):
    source=Path(source);translated=Path(translated);report=read_json(validation);original=rows(source);target=rows(translated)
    if len(original)!=len(target) or report.get('status')!='passed' or report.get('semantic_review')!='full' or report.get('source_sha256')!=digest(source.read_bytes()) or report.get('translation_sha256')!=digest(translated.read_bytes()) or report.get('records')!=len(original):
        raise ValueError('Formal translation export/hash/row validation failed')
    return original,target,report

def collect(project):
    from project_options import require_enabled
    project=Path(project).resolve();require_enabled(project);workspace=project/'TranslateFiles/image-text';bridge=read_json(workspace/'image_bridge.json')
    source=Path(bridge['source']);translated=Path(bridge['translation']);validation=Path(bridge['validation'])
    if source.parent!=project or digest(source.read_bytes())!=bridge['source_sha256'] or digest(source.with_suffix('.mapping.json').read_bytes())!=bridge['mapping_sha256'] or (workspace/source.name).read_bytes()!=source.read_bytes():raise ValueError('Stale translation bridge')
    validate(source,translated,validation);target=source.with_name(source.stem+'_translated.csv')
    if target.exists() and target.read_bytes()!=translated.read_bytes():raise ValueError('Refusing to overwrite existing image translation')
    target.write_bytes(translated.read_bytes());return dict(translation=str(target),validation=str(validation))
