"""Isolated real review regression, at most two configured-priority wire requests.

The glossary and draft translations are explicitly seeded test inputs, not
claimed extraction/review results. Only the API review produces semantic votes.
No production state, provider choice, concurrency or token limits are changed.
"""
import argparse
import csv
import json
from pathlib import Path
import threading
import uuid

import ainiee_bridge as bridge
import translation_workflow as w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    repo = Path(__file__).resolve().parents[2]
    base = Path(args.output).resolve()
    if base.exists() or not base.is_relative_to(repo / 'Development'):
        raise ValueError('Fresh isolated Development directory required')
    game = repo / 'Projects/torment-tides-of-numenera'
    production = game / 'torment-tides-of-numenera_text_translated.csv'
    with (game / 'torment-tides-of-numenera_text.csv').open(encoding='utf-8-sig', newline='') as f:
        source = [r[0] for r in csv.reader(f)]
    actual = source[54029]
    assert actual == 'Leave the shade alone. '
    cases = [
        dict(kind='actual_reported_error', source=actual, before='别打扰谢德。 ',
             context='A woman-shaped hole in the air stands before you. This woman-shade knows she does not belong here in your mind. You turn back to the woman-shaped void. She continues to face you, hands held out imploringly, ignoring the Specter completely.'),
        dict(kind='named_error', source='Qira pointed to her brother. "Shade, come here."',
             before='琪拉指着她的兄弟。“幽影，过来。”',
             seed_exceptions=[dict(source='Shade', reason='Shade denotes a ghost rather than a named person here.')]),
        dict(kind='named_correct', source='Qira pointed to her brother. "Shade, come here." ',
             before='琪拉指着她的兄弟。“谢德，过来。” '),
        dict(kind='ordinary_correct', source='They rested in the shade of a tree.', before='他们在树荫下休息。',
             seed_exceptions=[dict(source='shade', reason='In the shade of a tree denotes shelter from sunlight, not a person.')]),
        dict(kind='ordinary_error', source='They sat in the shade of a tree.', before='他们坐在一棵树的谢德里。'),
        dict(kind='held_out_flower_error', source='She picked a rose from the garden.', before='她从花园里摘了一朵萝丝。'),
        dict(kind='held_out_named_correct', source='Rose, the gardener, handed me a flower.', before='园丁萝丝递给我一朵花。'),
    ]
    base.mkdir(parents=True)
    project = base / 'Review'
    project.mkdir()
    src = project / 'cases_text.csv'
    with src.open('w', encoding='utf-8', newline='') as f:
        csv.writer(f).writerows([[c['source']] for c in cases])
    flow = w.Workflow(project)
    flow.init(src, dict(backend='api', pilot_records=len(cases),
        stage_backends={'pilot_translate':'subagent', 'translate':'subagent'},
        game_background='科幻奇幻角色扮演游戏。',
        style='忠实原文，自然简体中文；只修复明确错误，正确译文保持不变。'))
    contexts = {str(i+1): {'source_context': c['context']} if c.get('context') else {}
                for i,c in enumerate(cases)}
    flow.pause(True)
    flow.replace_context(contexts, 'Isolated regression source excerpts, no screenshot or expected correction')
    flow.pause(False)
    glossary = dict(characters=[
        dict(source='Shade', recommended_translation='谢德', gender='男性', term_kind='proper_name',
             enforcement='required', term_reason='Shade is the name of Qira\'s brother.'),
        dict(source='Rose', recommended_translation='萝丝', gender='女性', term_kind='proper_name',
             enforcement='required', term_reason='Rose is the name of a gardener.')], terms=[], non_translate=[])
    # Test fixture setup only: no invented glossary semantic review or production edits.
    gp = flow.root / 'fixture_glossary.json'
    w.write_json(gp, glossary)
    with flow.db() as db:
        flow.put(db, 'glossary', dict(path=str(gp.relative_to(flow.root)), hash=w.digest(gp), revision=flow.meta(db,'revision')))
    w.sync_api_batching(flow)
    report = dict(wire_limit=2, fixture_cases=cases,
        production_sha256_before=w.digest(production), fixture_note=__doc__)
    calls = [0]
    lock = threading.Lock()
    class BoundedAPI(bridge.AINieeAPI):
        def request(self, system, user):
            with lock:
                if calls[0] >= 2:
                    raise bridge.ApiFailure('probe_wire_limit_before_transport', False, usage={'usage_reported':False})
                calls[0] += 1
            return super().request(system, user)
    def seed(stage):
        flow.prepare(stage)
        while claim := flow.claim(stage, 'fixture-input-'+uuid.uuid4().hex, backend='subagent'):
            p = claim['payload']
            flow.submit(claim['task_id'], claim['lease_token'], dict(task_id=claim['task_id'], input_hash=p['input_hash'],
                records=[dict(id=r['id'], translation=cases[r['ordinal']-1]['before'],
                              exceptions=cases[r['ordinal']-1].get('seed_exceptions', [])) for r in p['records']]))
    try:
        seed('pilot_translate')
        pr = flow.pilot_report()
        flow.pilot_decide(dict(report_hash=pr['report_hash'], reviewer='main', decision='accept',
            summary='Isolated seeded regression inputs contain deliberate errors, not approved production translations.'))
        seed('translate')
        flow.pause(True)
        flow.configure({'stage_backends': {'pilot_translate':'subagent','translate':'api'}},
            'Test input seeding finished; all actual review/repair requests use configured priority', impact='none')
        flow.pause(False)
        result = w.run_api(flow, 'review', requester_factory=BoundedAPI, continuous=True)
        report['runner'] = {k:result.get(k) for k in ('accepted','failed','stage_clean','needs_attention','pending_handoffs')}
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        flow.pause(True)
        with flow.db() as db:
            report['after'] = [dict(r) for r in db.execute('SELECT ordinal,source,translation,stage,exceptions FROM records ORDER BY ordinal')]
            report['api_calls'] = [dict(r) for r in db.execute('SELECT status,usage FROM api_calls WHERE status!="cancelled"')]
        report['wire_requests'] = calls[0]
        report['production_sha256_after'] = w.digest(production)
        assert report['production_sha256_before'] == report['production_sha256_after']
        w.write_json(base/'report.json', report)
        print(json.dumps(dict(report=str(base/'report.json'), wire_requests=calls[0], runner=report.get('runner'), error=report.get('error')), ensure_ascii=False))


if __name__ == '__main__':
    main()
