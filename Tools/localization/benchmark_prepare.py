"""Prepare a real-data slice in a disposable fixture; never send API requests.

Only meta, records and empty task/event schemas are copied from a read-only project
database. All fixture state changes are confined to a new temporary directory.
The original CSV stays intact in the fixture for snapshot-hash verification;
only the selected records are presented to the production prepare() method.
"""
import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from translation_workflow import Workflow, digest, write_json
from workflow_profile import RunProfile


def benchmark(project, output, denominator=4):
    project, output = Path(project).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = project / 'TranslateFiles'
    with closing(sqlite3.connect((original / 'state/workflow.sqlite3').as_uri() + '?mode=ro', uri=True)) as source_db:
        source_db.row_factory = sqlite3.Row
        source_db.execute('BEGIN')  # One consistent, read-only snapshot.
        meta_rows = source_db.execute('SELECT key,value FROM meta').fetchall()
        meta = {r['key']: json.loads(r['value']) for r in meta_rows}
        count = source_db.execute('SELECT count(*) FROM records').fetchone()[0]
        sample_count = math.ceil(count / denominator)
        records = source_db.execute('SELECT * FROM records ORDER BY ordinal LIMIT ?', (sample_count,)).fetchall()
        schemas = {name: source_db.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?',
                                          (name,)).fetchone()[0] for name in ('meta', 'records', 'tasks', 'events')}
    if not meta.get('pilot_selection'):
        raise ValueError('Benchmark requires an existing saved pilot selection; no synthetic quality approval is created')
    if meta['config']['backend'] != 'api':
        raise ValueError('Benchmark expects an existing API workflow configuration')
    with tempfile.TemporaryDirectory(prefix='shh_prepare_benchmark_') as temporary:
        fixture = Path(temporary).resolve()
        root = fixture / 'TranslateFiles'
        (root / 'state').mkdir(parents=True)
        source_path = Path(meta['source_path'])
        fixture_source = fixture / source_path.name
        shutil.copy2(source_path, fixture_source)
        if digest(fixture_source) != meta['source_hash']:
            raise ValueError('Source snapshot changed; benchmark cancelled')
        glossary_path = Path(meta['glossary']['path'])
        if glossary_path.is_absolute() or '..' in glossary_path.parts:
            raise ValueError('Expected project-relative frozen glossary path')
        destination = root / glossary_path
        destination.parent.mkdir(parents=True)
        shutil.copy2(original / glossary_path, destination)
        receipt = meta['quality_approvals']['pilot']
        accepted = Path('reports/accepted') / ('pilot_' + receipt['report_hash'][:16]) / 'report.json'
        (root / accepted).parent.mkdir(parents=True)
        shutil.copy2(original / accepted, root / accepted)
        write_json(root / 'config/workflow.json', meta['config'])
        with closing(sqlite3.connect(root / 'state/workflow.sqlite3')) as db:
            for schema in schemas.values():
                db.execute(schema)
            db.executemany('INSERT INTO meta(key,value) VALUES (?,?)', [(r['key'], r['value']) for r in meta_rows])
            db.execute('UPDATE meta SET value=? WHERE key="source_path"', (json.dumps(str(fixture_source)),))
            db.execute('UPDATE meta SET value="false" WHERE key="paused"')
            columns = records[0].keys()
            db.executemany('INSERT INTO records(' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                           [tuple(r) for r in records])
            db.execute("UPDATE records SET stage='source',translation=NULL,locked=0,exceptions='[]'")
            db.commit()
        flow = Workflow(fixture)
        profile = RunProfile(root, 'translate')
        stats = {}
        with profile.activate():
            try:
                profile.emit('phase_started', phase='prepare')
                start = time.perf_counter()
                prepared = flow.prepare('translate')
                stats['prepare_seconds'] = round(time.perf_counter() - start, 4)
                profile.emit('phase_finished', phase='prepare', prepared_batches=len(prepared['tasks']))
                with flow.db() as db:
                    task_rows = db.execute('SELECT ids FROM tasks WHERE stage="translate" AND status="pending"').fetchall()
                    ids = [i for row in task_rows for i in json.loads(row['ids'])]
                    expected = {r['id'] for r in records}
                    if len(ids) != sample_count or set(ids) != expected:
                        raise AssertionError('Prepared scope does not cover the sample exactly once')
                start = time.perf_counter()
                session = flow.batch_open('translate', 'benchmark-no-api', backend='api', prepared=True)
                stats['first_batch_open_seconds'] = round(time.perf_counter() - start, 4)
                if session['status'] != 'ready':
                    raise AssertionError('First real API handoff was not ready')
                stats['ready_to_send_seconds'] = round(profile.first_request_seconds or time.perf_counter() - profile.started, 4)
                start = time.perf_counter()
                flow.prepare('translate')
                stats['repeat_prepare_seconds'] = round(time.perf_counter() - start, 4)
                profile_data = profile.finish('benchmark_completed')
            except BaseException as error:
                profile.finish('failed', error_type=type(error).__name__)
                shutil.copy2(profile.log_path, output / 'profile.jsonl')
                shutil.copy2(profile.summary_path, output / 'profile.json')
                raise
        shutil.copy2(profile.log_path, output / 'profile.jsonl')
        shutil.copy2(profile.summary_path, output / 'profile.json')
        config = meta['config']
        result = dict(project=str(project), source_hash=meta['source_hash'], glossary_hash=meta['glossary']['hash'],
                      revision=meta['revision'], full_records=count, sample_records=sample_count,
                      selection='First ceil(record_count / denominator) CSV logical records in source order',
                      ordinal_start=records[0]['ordinal'], ordinal_end=records[-1]['ordinal'], denominator=denominator,
                      prepared_batches=len(prepared['tasks']), scope_verified=True, api_requests=0,
                      config={key: config.get(key) for key in ('batch_tokens', 'batch_records', 'task_input_tokens',
                              'case_sensitive', 'whole_word', 'protect_named_curly', 'placeholder_family_overrides')},
                      timings=stats, profile=profile_data)
        # Profile paths in the fixture no longer exist; point reports at retained artifacts.
        for key, filename in (('profile_log', 'profile.jsonl'), ('profile_summary', 'profile.json')):
            result['profile'][key] = str(output / filename)
        write_json(output / 'profile.json', result['profile'])
        write_json(output / 'benchmark.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', required=True, help='New directory for the retained numeric reports')
    parser.add_argument('--denominator', type=int, default=4)
    args = parser.parse_args()
    if args.denominator < 1:
        parser.error('denominator must be positive')
    benchmark(args.project, args.output, args.denominator)
