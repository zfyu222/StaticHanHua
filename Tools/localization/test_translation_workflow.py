"""Regression checks for alignment, glossary gates, leases, API and local AINiee fixes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import subprocess
import sys
import unittest
from unittest.mock import patch

import ainiee_bridge as bridge
import translation_workflow as workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shh_workflow_')
        self.project = Path(self.temp.name) / 'Sample'
        self.project.mkdir()
        self.source = self.project / 'sample_text.csv'
        self.texts = ['Alice sees a Wolf. [mc]\\n', 'The Wolf has {0} gold.', 'text', '']
        with self.source.open('w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerows([[s] for s in self.texts])
        self.flow = workflow.Workflow(self.project)
        self.flow.init(self.source, {'batch_records': 2})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def envelope(claim):
        return {'task_id': claim['task_id'], 'input_hash': claim['payload']['input_hash']}

    def extract(self, analysis=False):
        self.flow.prepare('extract')
        while claim := self.flow.claim('extract', 'extractor'):
            payload = claim['payload']
            table = {'characters': [], 'terms': [], 'non_translate': []}
            if analysis and any('Alice' in r['source'] for r in payload['records']):
                table['characters'].append({'source': 'Alice', 'recommended_translation': '爱丽丝',
                                            'gender': '女性', 'evidence_rows': [1]})
                table['terms'].append({'source': 'Wolf', 'recommended_translation': '狼',
                                      'category_path': '种族', 'evidence_rows': [1, 2]})
            self.flow.submit(claim['task_id'], claim['lease_token'], {**self.envelope(claim), 'analysis': table})

    def freeze(self, analysis=False):
        self.extract(analysis)
        self.flow.prepare('glossary_review')
        while claim := self.flow.claim('glossary_review', 'glossary-reviewer'):
            payload = claim['payload']
            decisions = {'snapshot_sha256': payload['snapshot_sha256'], 'source_sha256': payload['source_hash'],
                         'review_complete': True, 'review_summary': '逐条核对来源和译名；无待确认项。',
                         'reviewed_refs': [e['ref'] for e in payload['entries']],
                         'acknowledged_findings': [f['id'] for f in payload['audit']['findings']],
                         'edits': [], 'quarantine': [], 'additions': [], 'allow_no_match': []}
            self.flow.submit(claim['task_id'], claim['lease_token'], {**self.envelope(claim), 'decisions': decisions})
        self.flow.freeze_glossary()

    def translate(self):
        self.flow.prepare('translate')
        while claim := self.flow.claim('translate', 'translator'):
            records = []
            for row in claim['payload']['records']:
                value = row['source'].replace('Alice sees a Wolf.', '爱丽丝看见一只狼。').replace(
                    'The Wolf has', '这只狼拥有').replace('gold.', '金币。').replace('text', '文本')
                records.append({'id': row['id'], 'translation': value})
            self.flow.submit(claim['task_id'], claim['lease_token'], {**self.envelope(claim), 'records': records})

    def review(self, stage='review'):
        self.flow.prepare(stage)
        while claim := self.flow.claim(stage, 'reviewer'):
            rows = [{'id': r['id'], 'translation': r['translation'], 'reviewed': True,
                     'reason': '对照原文核对称谓、动作、变量及含义。'} for r in claim['payload']['records']]
            self.flow.submit(claim['task_id'], claim['lease_token'], {**self.envelope(claim), 'records': rows})

    def test_end_to_end_and_csv_alignment(self):
        self.freeze(True)
        self.translate()
        with self.assertRaisesRegex(ValueError, 'semantic review'):
            self.flow.export()
        self.review()
        report = self.flow.export()
        with Path(report['path']).open(encoding='utf-8-sig', newline='') as f:
            texts = [r[0] for r in csv.reader(f)]
        self.assertEqual(len(texts), 4)
        self.assertEqual(texts[2], '文本')
        self.assertEqual(texts[3], '')
        self.assertIn('[mc]\\n', texts[0])
        self.assertEqual(self.flow.freeze_glossary()['status'], 'already_frozen')

    def test_glossary_is_required(self):
        with self.assertRaisesRegex(ValueError, 'glossary'):
            self.flow.prepare('translate')
        with self.assertRaisesRegex(ValueError, 'every glossary extraction'):
            self.flow.prepare('glossary_review')

    def test_empty_glossary_still_reviewed(self):
        self.freeze()
        self.assertTrue(self.flow.status()['glossary_frozen'])

    def test_atomic_claim_and_duplicate_submit(self):
        prepared = self.flow.prepare('extract')['tasks']
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(lambda n: workflow.Workflow(self.project).claim('extract', f'w{n}'), range(6)))
        actual = [c for c in claims if c]
        self.assertEqual(len(actual), len(prepared))
        self.assertEqual(len({c['task_id'] for c in actual}), len(actual))
        claim = actual[0]
        result = {**self.envelope(claim), 'analysis': {'characters': [], 'terms': [], 'non_translate': []}}
        self.flow.submit(claim['task_id'], claim['lease_token'], result)
        self.assertEqual(self.flow.submit(claim['task_id'], claim['lease_token'], result)['status'], 'already_submitted')
        with self.assertRaises(ValueError):
            self.flow.submit(claim['task_id'], 'wrong', result)

    def test_expired_lease_and_recovery(self):
        self.flow.prepare('extract')
        claim = self.flow.claim('extract', 'w')
        with self.flow.db() as db:
            db.execute('UPDATE tasks SET lease_until=? WHERE id=?', (time.time()-1, claim['task_id']))
        result = {**self.envelope(claim), 'analysis': {'characters': [], 'terms': [], 'non_translate': []}}
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.flow.submit(claim['task_id'], claim['lease_token'], result)
        with self.assertRaises(ValueError): self.flow.recover(claim['task_id'])
        self.flow.recover(claim['task_id'], True)
        newer = self.flow.claim('extract', 'w2')
        self.assertNotEqual(claim['lease_token'], newer['lease_token'])
        with self.assertRaises(ValueError): self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_pause_and_revision_obsoletes_old_result(self):
        self.flow.prepare('extract')
        claim = self.flow.claim('extract', 'w')
        self.flow.pause(True)
        self.assertIsNone(self.flow.claim('extract', 'w2'))
        with self.assertRaises(ValueError): self.flow.configure({'batch_records': 3}, '重新分批')
        self.flow.recover(claim['task_id'], True)
        self.flow.configure({'batch_records': 3}, '重新分批')
        result = {**self.envelope(claim), 'analysis': {'characters': [], 'terms': [], 'non_translate': []}}
        with self.assertRaises(ValueError): self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_source_and_config_change_rejected(self):
        settings = workflow.read_json(self.flow.root / 'config/workflow.json')
        settings['style'] = 'changed'
        workflow.write_json(self.flow.root / 'config/workflow.json', settings)
        with self.assertRaisesRegex(ValueError, 'Configuration changed'): self.flow.prepare('extract')
        settings['style'] = workflow.DEFAULT['style']
        workflow.write_json(self.flow.root / 'config/workflow.json', settings)
        self.source.write_text('different\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Source CSV changed'): self.flow.prepare('extract')

    def test_invalid_id_and_marker_rejected_without_partial_commit(self):
        self.freeze(True)
        self.flow.prepare('translate')
        claim = self.flow.claim('translate', 'w')
        rows = [{'id': r['id'], 'translation': 'wrong'} for r in claim['payload']['records']]
        result = {**self.envelope(claim), 'records': rows}
        result['records'][1]['id'] = result['records'][0]['id']
        with self.assertRaisesRegex(ValueError, 'assigned ID'): self.flow.submit(claim['task_id'], claim['lease_token'], result)
        result['records'][1]['id'] = claim['payload']['records'][1]['id']
        with self.assertRaisesRegex(ValueError, 'Protected token'): self.flow.submit(claim['task_id'], claim['lease_token'], result)
        self.assertEqual(self.flow.status()['records'], {'source': 4})

    def test_missing_term_and_context_exception(self):
        self.freeze(True)
        self.flow.prepare('translate')
        claim = self.flow.claim('translate', 'w')
        rows = [{'id': r['id'], 'translation': r['source']} for r in claim['payload']['records']]
        result = {**self.envelope(claim), 'records': rows}
        with self.assertRaisesRegex(ValueError, 'terminology mismatch'): self.flow.submit(claim['task_id'], claim['lease_token'], result)
        for item, row in zip(rows, claim['payload']['records']):
            item['exceptions'] = [{'source': t['src'], 'reason': '测试：专名保留英文的局部例外。'}
                                  for t in bridge.matched_terms({'characters': [{'source': 'Alice', 'recommended_translation': '爱丽丝'}],
                                                               'terms': [{'source': 'Wolf', 'recommended_translation': '狼'}], 'non_translate': []},
                                                               [self.texts[row['ordinal']-1]], workflow.DEFAULT)]
        self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_review_requires_evidence_and_lock(self):
        self.freeze(True); self.translate()
        self.flow.prepare('review')
        claim = self.flow.claim('review', 'w')
        row = claim['payload']['records'][0]
        self.flow.lock(row['id'], True, '已确认')
        result = {**self.envelope(claim), 'records': [
            {'id': r['id'], 'translation': r['translation']} for r in claim['payload']['records']]}
        with self.assertRaisesRegex(ValueError, 'semantic review'): self.flow.submit(claim['task_id'], claim['lease_token'], result)
        for item in result['records']: item.update(reviewed=True, reason='逐条核对')
        result['records'][0]['translation'] += '改'
        with self.assertRaisesRegex(ValueError, 'locked'): self.flow.submit(claim['task_id'], claim['lease_token'], result)

    def test_polish_requires_final_semantic_review(self):
        self.freeze(True); self.translate(); self.review()
        self.flow.pause(True); self.flow.configure({'polish': True}, '剧情润色'); self.flow.pause(False)
        self.flow.prepare('polish')
        while claim := self.flow.claim('polish', 'w'):
            result = {**self.envelope(claim), 'records': [{'id': r['id'], 'translation': r['translation']} for r in claim['payload']['records']]}
            self.flow.submit(claim['task_id'], claim['lease_token'], result)
        with self.assertRaises(ValueError): self.flow.export()
        self.review('final_review'); self.flow.export()

    def test_ainiee_choice_disables_custom_execution(self):
        self.flow.pause(True); self.flow.configure({'workflow': 'ainiee'}, '选择原工作流')
        with self.assertRaisesRegex(ValueError, 'AINiee GUI'): self.flow.prepare('extract')

    def test_prompt_heartbeat_and_tampered_task(self):
        self.flow.prepare('extract')
        claim = self.flow.claim('extract', 'w')
        paths = self.flow.task_prompt(claim['task_file'])
        self.assertTrue(Path(paths['prompt_file']).exists())
        self.flow.heartbeat(claim['task_id'], claim['lease_token'])
        content = workflow.read_json(claim['task_file'])
        content['records'][0]['source'] = 'tampered'
        workflow.write_json(claim['task_file'], content)
        with self.assertRaisesRegex(ValueError, 'content changed'): self.flow.task_prompt(claim['task_file'])

    def test_backend_change_preserves_partial_extraction_coverage(self):
        self.flow.prepare('extract')
        claim = self.flow.claim('extract', 'w')
        self.flow.submit(claim['task_id'], claim['lease_token'], {**self.envelope(claim), 'analysis': {'characters': [], 'terms': [], 'non_translate': []}})
        self.flow.pause(True); self.flow.configure({'batch_records': 3}, '调整调度'); self.flow.pause(False)
        self.flow.prepare('extract')
        remaining = self.flow.claim('extract', 'w2')
        self.assertEqual([r['ordinal'] for r in remaining['payload']['records']], [3, 4])

    def test_legacy_artifacts_stay_in_translatefiles(self):
        analyzer = bridge.load_script('analyze_placeholders')
        rows, logical, _ = analyzer.load_texts(self.source, 0, True)
        self.assertEqual(logical, 4)
        self.assertEqual(rows[2], 'text')
        translated = self.project / 'sample_text_translated.csv'
        with translated.open('w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerows([[s.replace('[mc]', '[名字]')] for s in self.texts])
        script = bridge.ROOT / '.agents/skills/game-translate/scripts/validate_translation.py'
        result = subprocess.run([sys.executable, str(script), str(self.source), str(translated),
                                 '--src-col', '0', '--dst-col', '0', '--fix'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.flow.root / 'backups/sample_text_translated.csv.bak').exists())
        self.assertFalse(translated.with_suffix('.csv.bak').exists())

    def test_related_glossary_entries_and_reopen(self):
        self.extract(True)
        self.flow.prepare('glossary_review')
        claim = self.flow.claim('glossary_review', 'w')
        p = claim['payload']
        result = {**self.envelope(claim), 'decisions': {
            'snapshot_sha256': p['snapshot_sha256'], 'source_sha256': p['source_hash'],
            'review_complete': True, 'review_summary': '已核对', 'reviewed_refs': [e['ref'] for e in p['entries']],
            'acknowledged_findings': [f['id'] for f in p['audit']['findings']], 'edits': [], 'quarantine': [], 'additions': [], 'allow_no_match': []}}
        self.flow.submit(claim['task_id'], claim['lease_token'], result)
        self.flow.reopen_review(claim['task_id'], '补充审校依据')
        next_claim = self.flow.claim('glossary_review', 'w2')
        self.assertEqual(next_claim['payload']['attempt'], 2)

    def test_api_transport_retains_local_http2_and_sse_fixes(self):
        import inspect
        from ModuleFolders.Infrastructure.LLMRequester.LLMClientFactory import create_httpx_client
        from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester
        self.assertFalse(inspect.signature(create_httpx_client).parameters['http2'].default)
        parser = OpenaiRequester()
        _, content, prompt, completion = parser._parse_sse_response(
            'data: {"choices":[{"delta":{"content":"hello"}}]}\n'
            'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\ndata: [DONE]\n')
        self.assertEqual((content, prompt, completion), ('hello', 2, 1))

    def test_local_http_api_transport_and_429_retry(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        count = [0]
        counter_lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                payload = json.loads(body['messages'][-1]['content'])
                with counter_lock:
                    count[0] += 1
                    first = count[0] == 1
                if first:
                    response = {'error': {'message': 'synthetic rate limit', 'type': 'rate_limit', 'code': 'rate_limit'}}
                    self.send_response(429)
                else:
                    result = {'task_id': payload['task_id'], 'input_hash': payload['input_hash'],
                              'analysis': {'characters': [], 'terms': [], 'non_translate': []}}
                    response = {'id': 'shh-mock', 'object': 'chat.completion', 'created': 1, 'model': 'mock',
                                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(result)},
                                             'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
                    self.send_response(200)
                data = json.dumps(response).encode()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.flow.pause(True)
            self.flow.configure({'backend': 'api', 'api': {'api_url': f'http://127.0.0.1:{server.server_port}/v1',
                                                         'model_name': 'shh-mock', 'max_requests': 10,
                                                         'requests_per_minute': 100000}}, '本地 API 传输验证')
            self.flow.pause(False); self.flow.prepare('extract')
            with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'local-test-only', 'NO_PROXY': '127.0.0.1,localhost'}):
                summary = workflow.run_api(self.flow, 'extract')
            self.assertEqual(summary['accepted'], 2)
            self.assertEqual(summary['failed'], 1)
            self.assertEqual(count[0], 3)  # No invisible SDK retry outside the queue budget.
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_semantic_configuration_resets_to_source(self):
        self.freeze(True); self.translate(); self.review()
        self.flow.pause(True)
        self.flow.configure({'style': '更简洁但保持原意'}, '改变文体')
        self.assertEqual(self.flow.status()['records'], {'source': 4})

    def test_no_header_mode_preserves_first_text_record(self):
        source = self.flow.root / 'source/header_test.csv'
        source.write_text('text\n[mc]\n', encoding='utf-8')
        analyzer = bridge.load_script('analyze_placeholders')
        texts, logical, _ = analyzer.load_texts(source, 0, True)
        self.assertEqual(texts, ['text', '[mc]'])
        self.assertEqual(logical, 2)

    def test_api_mock_uses_same_submission_gate_and_budget(self):
        self.flow.pause(True)
        self.flow.configure({'backend': 'api', 'api': {'api_url': 'https://example.invalid/v1', 'model_name': 'test',
                                                     'max_requests': 1, 'requests_per_minute': 100000}}, '测试接口')
        self.flow.pause(False); self.flow.prepare('extract')
        class Mock:
            def request(self, system, user):
                payload = json.loads(user)
                return json.dumps({'task_id': payload['task_id'], 'input_hash': payload['input_hash'],
                                   'analysis': {'characters': [], 'terms': [], 'non_translate': []}}), {'prompt_tokens': 10, 'completion_tokens': 10}
            def close(self): pass
        with patch.dict(os.environ, {'SHH_TRANSLATION_API_KEY': 'mock-only'}):
            summary = workflow.run_api(self.flow, 'extract', lambda api, key: Mock())
        self.assertEqual(summary['accepted'], 1)
        self.assertTrue(self.flow.status()['paused'])
        self.assertEqual(self.flow.status()['api_requests'], 1)

    def test_glossary_revision_only_invalidates_affected(self):
        self.freeze(True); self.translate(); self.review()
        self.flow.pause(True)
        with self.flow.db() as db: old = self.flow.glossary(db)
        snapshot = self.flow.root / 'glossary/revise_snapshot.json'
        workflow.write_json(snapshot, old)
        audit = bridge.load_script('analyze_placeholders')  # Imports should not launch the GUI.
        decisions = {'snapshot_sha256': workflow.digest(snapshot), 'source_sha256': workflow.digest(self.source),
                     'review_complete': True, 'review_summary': '调整角色音译并逐条核对',
                     'reviewed_refs': ['characters:0', 'terms:0'], 'acknowledged_findings': [],
                     'edits': [{'table': 'characters', 'index': 0, 'patch': {'recommended_translation': '艾丽丝'},
                                'reason': '角色音译调整', 'evidence_rows': [1]}], 'quarantine': [], 'additions': [], 'allow_no_match': []}
        decision_path = self.flow.root / 'glossary/revise_decisions.json'
        workflow.write_json(decision_path, decisions)
        changed = self.flow.revise_glossary(snapshot, decision_path, '角色译名调整')
        self.assertEqual(len(changed['affected_records']), 1)
        self.flow.pause(False)
        self.flow.prepare('translate')
        claim = self.flow.claim('translate', 'w')
        self.assertEqual(len(claim['payload']['records']), 1)


class LocalFixTests(unittest.TestCase):
    def test_bracket_literal_and_family_rules(self):
        h = bridge.helper()
        self.assertTrue(h.source_matches_text('[mc]', 'Hello [mc]'))
        self.assertFalse(h.source_matches_text('[mc]', 'm'))
        rows = [{'src': 'Wolf', 'dst': '狼'}, {'src': 'the Wolves', 'dst': '错误'}]
        self.assertEqual(h.unify_term_family_translations(rows, 'src', 'dst')[1]['dst'], '狼')
        self.assertTrue(h.is_keep_original_translation('“保留原文”'))

    def test_longest_term_wins(self):
        table = {'characters': [], 'terms': [{'source': 'Sword', 'recommended_translation': '剑'},
                  {'source': 'Magic Sword', 'recommended_translation': '魔法剑'}], 'non_translate': []}
        rows = bridge.matched_terms(table, ['Magic Sword'], workflow.DEFAULT)
        self.assertEqual([r['dst'] for r in rows], ['魔法剑'])

    def test_task_only_gets_relevant_terms(self):
        table = {'characters': [], 'terms': [{'source': 'Wolf', 'recommended_translation': '狼'},
                  {'source': 'Dragon', 'recommended_translation': '龙'}], 'non_translate': []}
        self.assertEqual(len(bridge.matched_terms(table, ['a Wolf'], workflow.DEFAULT)), 1)

    def test_unlimited_markers_and_original_p_tokens(self):
        text = '[P1] ' + ' '.join(f'[Tag:{i}]' for i in range(65)) + ' \\n {0:0.00} %s $player'
        rules = bridge.patterns()
        protected, mapping = bridge.protect(text, 'R1', rules)
        self.assertGreater(len(mapping), 50)
        self.assertEqual(bridge.restore(protected, mapping), text)
        self.assertEqual(bridge.structural_issues(text, text, rules), [])
        self.assertIn('curly_format', bridge.structural_issues('{0:0.00}', '{0:0.0}', rules))
        with self.assertRaises(ValueError): bridge.restore(protected + mapping[0]['placeholder'], mapping)

    def test_player_choice_and_blank_exclusion_note(self):
        rules = bridge.patterns(False, [{'markers': '[Custom]', 'regex': '', 'info': ''}])
        self.assertEqual(bridge.structural_issues('{Attack}', '{攻击}', rules), [])
        self.assertTrue(bridge.structural_issues('[Custom]', '内容', rules))

    def test_strict_json_parser(self):
        self.assertEqual(bridge.parse_response('```json\n{"records": []}\n```'), {'records': []})
        with self.assertRaises(ValueError): bridge.parse_response('1. hello')


if __name__ == '__main__':
    unittest.main()
