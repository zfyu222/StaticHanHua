"""Bounded live verification of the shared API stages, outside production state.

Prelude stops at the real pilot report for a human/main-agent decision. Body
requires that decision; it never inserts synthetic semantic approvals.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import csv
import json
from pathlib import Path
import sys
import threading

import ainiee_bridge as bridge
import translation_workflow as w
from workflow_profile import usage_totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--phase', choices=('prelude','body'), required=True)
    args = parser.parse_args()
    folder = args.directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    report_file = folder / 'validation_report.json'
    report = w.read_json(report_file) if report_file.exists() else dict(request_limit=12, requests=[], stages=[])
    if report.get('status') == 'completed':
        raise ValueError('Validation already completed; no paid replay')
    fixture = folder / 'Sample'
    if args.phase == 'prelude':
        fixture.mkdir()
        source = fixture / 'sample_text.csv'
        with source.open('w', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerows([[s] for s in ('Alice opens the gate.', '[i]Rest here.[/i]', 'The wolf has {0} gold.')])
        flow = w.Workflow(fixture)
        flow.init(source, {'backend':'api','source_language':'English','target_language':'简体中文','polish':True,
            'game_background':'中世纪冒险题材。叙事自然简洁，保留标签及变量。',
            'pilot_records':3, 'api':{'concurrency':1,'max_requests':12}})
    else:
        flow = w.Workflow(fixture)
    with flow.db() as db:
        config = flow.config(db)
    settings, key = w.load_api_settings(config)
    assert settings.get('extra_body',{}).get('response_format') == {'type':'json_object'}
    settings = {**settings,'concurrency':1}
    client = bridge.AINieeAPI(settings,key)
    del key
    request_lock = threading.Lock()
    class Bounded:
        def request(self, system, user):
            with request_lock:
                if len(report['requests']) >= report['request_limit']:
                    raise bridge.ApiFailure('validation_request_limit', False)
                entry = {'number':len(report['requests'])+1, 'usage':{'usage_reported':False}}
                report['requests'].append(entry)
                w.write_json(report_file,report)
            content,usage = client.request(system,user)
            with request_lock:
                entry['usage']=usage
                w.write_json(report_file,report)
            return content,usage
        def close(self): pass
    def factory(*_): return Bounded()
    stages = ('extract','glossary_review','pilot_translate') if args.phase == 'prelude' else ('translate','draft_check','review','polish','final_review')
    try:
        with (folder/'execution.log').open('a',encoding='utf-8') as log:
            for stage in stages:
                before = len(report['requests'])
                with redirect_stdout(log), redirect_stderr(log):
                    result = w.run_api(flow,stage,requester_factory=factory)
                row = {k:result.get(k) for k in ('accepted','failed','stage_clean','next_action','needs_attention','pending_handoffs')}
                row.update(stage=stage,requests=len(report['requests'])-before)
                report['stages'].append(row)
                w.write_json(report_file,report)
                print(json.dumps(row,ensure_ascii=True),flush=True)
                if result.get('needs_attention') or result.get('pending_handoffs') or result.get('paused'):
                    raise ValueError('Stage requires attention: '+stage)
                if stage == 'glossary_review':
                    with redirect_stdout(log), redirect_stderr(log):
                        report['frozen_glossary'] = flow.freeze_glossary()
                if stage == 'pilot_translate':
                    report['pilot_report'] = flow.pilot_report()
                    report['status'] = 'awaiting_main_pilot_decision'
                    w.write_json(report_file,report)
        if args.phase == 'body':
            report['status']='completed'
    except Exception as error:
        report['status']='needs_attention'
        report['error']=str(error)
        print(json.dumps({'status':'needs_attention','error':str(error)},ensure_ascii=True),flush=True)
    finally:
        client.close()
        report['actual_usage']=usage_totals(r['usage'] for r in report['requests'])
        report['production_project_unchanged']=True
        w.write_json(report_file,report)
    return 0 if report['status'] in ('completed','awaiting_main_pilot_decision') else 1


if __name__ == '__main__': sys.exit(main())
