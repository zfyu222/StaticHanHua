"""Persistent opt-in shared by project initialization and the two image skills."""
from pathlib import Path
from core import read_json,write_json

NAME='localization_project.json'

def configure(project,choice,reason):
    project=Path(project).resolve()
    if choice not in ('enabled','disabled','undecided') or not isinstance(reason,str) or not reason.strip():
        raise ValueError('Explicit choice and reason required')
    path=project/NAME;value=read_json(path) if path.exists() else dict(schema=1)
    value['image_translation']=dict(status=choice,reason=reason)
    write_json(path,value);return value

def require_enabled(project):
    path=Path(project)/NAME
    value=read_json(path) if path.exists() else {}
    status=value.get('image_translation',{}).get('status','undecided')
    if status!='enabled':raise ValueError(f'Image translation is {status}; obtain the missing user preference before image work')
    return value
