"""Opt-in import of existing source-only API drafts as fallible review context.

Import performs no API calls and never replaces a translation. Source generation
keeps its own recorded budget/usage; it is not a hidden stage of normal review.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing

import ainiee_bridge as bridge


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_source_readings(project):
    """Read a consistent, completed API pilot snapshot without writing to it."""
    project=Path(project).resolve();path=project/'TranslateFiles/state/workflow.sqlite3'
    if not path.is_file():raise ValueError('Independent source-reading workflow database missing')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        if db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone():
            raise ValueError('Finish source-reading API requests before importing')
        meta={r['key']:json.loads(r['value']) for r in db.execute('SELECT key,value FROM meta WHERE key IN ("source_path","source_hash")')}
        if hashlib.sha256(Path(meta['source_path']).read_bytes()).hexdigest()!=meta['source_hash']:
            raise ValueError('Independent source file changed after generation')
        originals={r['id']:dict(r) for r in db.execute('SELECT id,source,translation FROM records')}
        readings={}
        for task in db.execute('SELECT * FROM tasks WHERE stage="pilot_translate" AND backend="api" AND status="done"'):
            if not task['result']:continue
            if not db.execute('SELECT 1 FROM api_calls WHERE task_id=? AND status="received"',(task['id'],)).fetchone():
                continue
            payload=json.loads(task['payload'])
            result=json.loads(task['result'])
            if payload.get('source_hash')!=meta['source_hash'] or result.get('input_hash')!=task['input_hash']:
                raise ValueError('Independent reading task/result/source binding mismatch')
            if payload.get('approved_pilot_examples') or payload.get('context'):
                raise ValueError('Source-only reading must not contain previous translation examples/context')
            inputs={r['id']:r for r in payload['records']}
            for output in result.get('records',[]):
                original=originals.get(output.get('id'));given=inputs.get(output.get('id'))
                if not original or not given:raise ValueError('Independent reading ID not bound to original source')
                if given.get('translation') or given.get('candidate') or given.get('independent_source_reading') or given.get('metadata'):
                    raise ValueError('Independent reading saw an existing translation or candidate')
                source=bridge.restore(given['source'],given.get('protection',[]))
                if source!=original['source']:raise ValueError('Independent reading source/protection mismatch')
                translation=output.get('translation')
                if not isinstance(translation,str) or not translation.strip():
                    raise ValueError('Independent reading requires a nonempty API draft')
                translation=bridge.restore(translation,given.get('protection',[]),allow_original=True)
                if translation!=original['translation']:
                    raise ValueError('Independent reading no longer matches its accepted API draft')
                if source in readings:raise ValueError('Ambiguous duplicate source readings; select one source-only run')
                readings[source]={'source_sha256':text_hash(source),'reading':translation,
                    'proof':{'project':str(project),'task_id':task['id'],'input_hash':task['input_hash'],
                             'source_file_sha256':meta['source_hash']}}
        if not readings:raise ValueError('No completed, bound source-only API pilot drafts found')
        return readings


class ReviewReadingsMixin:
    def import_review_readings(self, source_project, record_ids, reason):
        """Attach only explicitly selected IDs; reopen only those already reviewed."""
        if Path(source_project).resolve()==self.project.resolve():
            raise ValueError('Use a separate independent source-only run, not this translation itself')
        if (not isinstance(record_ids,list) or not record_ids
                or any(not isinstance(i,str) or not i for i in record_ids) or len(set(record_ids))!=len(record_ids)
                or not isinstance(reason,str) or not reason.strip()):
            raise ValueError('Explicit unique IDs and a reason are required')
        readings=load_source_readings(source_project)
        with self.db() as db:
            config=self.config(db)
            if not self.meta(db,'paused'):raise ValueError('Pause before importing review readings')
            if hashlib.sha256(Path(self.meta(db,'source_path')).read_bytes()).hexdigest()!=self.meta(db,'source_hash'):
                raise ValueError('Parent source snapshot changed')
            rows={}
            for start in range(0,len(record_ids),800):
                ids=record_ids[start:start+800]
                rows.update({r['id']:dict(r) for r in db.execute(
                    'SELECT * FROM records WHERE id IN ('+','.join('?' for _ in ids)+')',ids)})
            if set(rows)!=set(record_ids):raise ValueError('Unknown review-reading target ID')
            selected={}
            for rid,row in rows.items():
                if row['source'] not in readings:raise ValueError('No exact source-only reading for '+rid)
                selected[rid]=readings[row['source']]
            current=self.meta(db,'review_readings',{})
            changed=[rid for rid,value in selected.items() if any(current.get(rid,{}).get(k)!=value[k]
                                                               for k in ('source_sha256','reading'))]
            if not changed:return {'status':'unchanged','affected_records':[],'api_requests':0}
            if db.execute('SELECT 1 FROM tasks WHERE status="running" OR (revision=? AND status IN ("pending","failed"))',
                          (self.meta(db,'revision'),)).fetchone():
                raise ValueError('Finish outstanding tasks before adding review readings')
            if db.execute('SELECT 1 FROM api_calls WHERE status="reserved"').fetchone():
                raise ValueError('Finish outstanding API requests before adding review readings')
            if any(rows[i]['locked'] for i in changed):raise ValueError('Unlock selected confirmed translations first')
            stages={rows[i]['stage'] for i in changed}
            if len(stages)!=1 or not stages.issubset({'translate','review'}):
                raise ValueError('Select records in one phase: completed drafts or completed review')
            stage=next(iter(stages));tasks=[]
            if stage=='review':
                self.require_review_round_complete(db,'review');self.guard_standard_dispatch(db)
            self.put(db,'review_readings',{**current,**{i:selected[i] for i in changed}})
            if stage=='review':
                tasks,blocked=self.plan_review_repairs(db,config,'review',
                    {i:'Recheck original meaning using the attached unreviewed source-only reading; '+reason for i in changed},
                    'independent-source-reading')
                if blocked:raise ValueError('Selected records reached existing repair limits; no budgets reset')
            self.event(db,'import_review_readings',{'record_ids':changed,'reason':reason,
                'source_project':str(Path(source_project).resolve()),'repair_tasks':tasks,
                'api_requests':0,'cost_note':'Generation usage and request budget remain in the separate source-reading project.'})
            return {'status':'imported','affected_records':changed,'repair_tasks':tasks,'api_requests':0,
                    'preserved_records':db.execute('SELECT count(*) FROM records').fetchone()[0]-len(changed)}

    def add_review_reading(self,db,row,source,stage,readings):
        if stage not in ('review','final_review'):return
        entry=readings.get(row['id'])
        if entry:
            if entry['source_sha256']!=text_hash(source):raise ValueError('Stale independent reading source binding')
            row['independent_source_reading']=entry['reading']
