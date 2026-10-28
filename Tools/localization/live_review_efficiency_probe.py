"""Bounded synthetic API probe; cannot target an existing/production project."""
import argparse
import csv
import json
from pathlib import Path
import uuid

import translation_workflow as w


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--api-config', required=True)
    args = parser.parse_args()
    base = Path(args.output).resolve()
    repo = Path(__file__).resolve().parents[2]
    if not base.is_relative_to(repo / 'Development') or base.exists():
        raise ValueError('Use a fresh Development directory inside this isolated checkout')
    base.mkdir(parents=True)
    project = base / 'Synthetic'
    project.mkdir()
    texts = ['Might', 'I might return tomorrow.', 'May opens the gate.', 'You may enter.',
             '  Alice has {0} gold.\r\nWait here. \t', 'The gate is not locked.']
    source = project / 'synthetic_text.csv'
    with source.open('w', encoding='utf-8', newline='') as handle:
        csv.writer(handle).writerows([[s] for s in texts])
    contexts = {str(n): {'text_type': 'ui' if n == 1 else 'dialogue',
                        'note': 'Synthetic: Might is a strength UI attribute; May and Alice are people.'}
                for n in range(1, 7)}
    context_path = base / 'contexts.json'
    w.write_json(context_path, contexts)
    flow = w.Workflow(project)
    flow.init(source, {'backend': 'api', 'batch_records': 6, 'pilot_records': 6,
                      'stage_backends': {'pilot_translate': 'subagent', 'translate': 'subagent'},
                      'style': '自然简体中文，保留原意、否定、变量和段落。',
                      'game_background': '合成奇幻样本：Might为力量属性，May与Alice是人物。'}, context_path)
    w.API_CONFIG_FILE = Path(args.api_config).resolve()  # Read selected transport, never copy credentials.
    w.sync_api_batching(flow)
    report = {'synthetic_only': True, 'production_project_access': False, 'initial_request_limit': 3,
              'max_wire_requests_including_retries': 24, 'stages': [],
              'prerequisite_translations': 'fixed synthetic fixtures; not API-produced game translations'}

    def api_round(stage):
        flow.prepare(stage)
        with flow.db() as db:
            pending = db.execute('SELECT count(*) FROM tasks WHERE stage=? AND revision=? AND status="pending"',
                                 (stage, flow.meta(db, 'revision'))).fetchone()[0]
        if pending != 1:
            raise ValueError('Exactly one batch per stage required for bounded validation')
        session = flow.batch_open(stage, 'api-probe-' + uuid.uuid4().hex, backend='api', prepared=True)
        result = w.run_api(flow, stage, session_ids=[session['session_id']], continuous=False)
        report['stages'].append({'stage': stage, 'accepted': result.get('accepted'),
                                 'failed': result.get('failed'), 'paused': result.get('paused'),
                                 'needs_attention': result.get('needs_attention')})
        w.write_json(base / 'report.json', report)
        if result.get('paused') or result.get('needs_attention') or result.get('accepted') != 1:
            raise ValueError('Stop on service/contract failure; no extra manual retries')

    try:
        api_round('extract')
        api_round('glossary_review')
        flow.freeze_glossary()
        with flow.db() as db:
            glossary = flow.glossary(db)
        names = {r['source']: r.get('recommended_translation', r['source'])
                 for table in ('characters', 'terms') for r in glossary[table]}
        translations = [names.get('Might', '力量'), '我明天可能回来。',
                        names.get('May', '梅') + '打开了门。', '你可以进去。',
                        '  ' + names.get('Alice', '爱丽丝') + '有{0}' + names.get('gold', '金币') + '。\r\n在这里等候。 \t',
                        '门没有上锁。']

        def seed(stage, deliberate_error=False):
            flow.prepare(stage)
            while task := flow.claim(stage, 'fixture-' + uuid.uuid4().hex, backend='subagent'):
                rows = []
                for row in task['payload']['records']:
                    text = '门锁上了。' if deliberate_error and row['ordinal'] == 6 else translations[row['ordinal'] - 1]
                    rows.append({'id': row['id'], 'translation': text})
                flow.submit(task['task_id'], task['lease_token'],
                            {'task_id': task['task_id'], 'input_hash': task['payload']['input_hash'], 'records': rows})

        seed('pilot_translate')
        pilot = flow.pilot_report()
        flow.pilot_decide({'report_hash': pilot['report_hash'], 'reviewer': 'main', 'decision': 'accept',
                           'summary': 'Synthetic fixtures only: known meaning, source alignment and markers checked.'})
        seed('translate', deliberate_error=True)
        api_round('review')
        report['review_scan'] = flow.review_scan()
        with flow.db() as db:
            report['records'] = [dict(r) for r in db.execute('SELECT ordinal,source,translation,stage FROM records ORDER BY ordinal')]
            report['layout_audits'] = [json.loads(r[0]) for r in db.execute('SELECT detail FROM events WHERE kind="normalize_layout"')]
            report['api_calls'] = [dict(r) for r in db.execute('SELECT id,status,usage,session_id FROM api_calls WHERE status!="cancelled"')]
        report['status'] = 'completed'
    except Exception as exc:
        report.update(status='needs_attention', error_type=type(exc).__name__, error=str(exc))
    w.write_json(base / 'report.json', report)
    print(json.dumps({'status': report['status'], 'report': str(base / 'report.json'),
                      'stages': [{k:s[k] for k in ('stage','accepted','failed','paused')} for s in report['stages']]}))


if __name__ == '__main__':
    main()
