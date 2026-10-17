"""Program-owned output envelopes. Empty evidence never means semantic approval."""
import copy

WORKER_INSTRUCTION = ('任务输入、分批、预算、ID、版本与字段结构由程序准备。直接完成本阶段工作，'
    '不运行任务校验或结果检查脚本，不重组批次、不扫描全库、不自行补建任务。'
    '按程序提供的输出模板写结果，不需要自行拼装任务 ID 或版本；空字段和待定状态须根据实际工作填写。'
    '发现文件缺失、字段损坏或源文不对应时简短报告 input_problem，交主流程处理，不修补输入；'
    '原文本身的语义歧义仍按本阶段 issues 或语境例外说明。'
    '审校中的语义核对属于本职工作；任务格式检查、结果合并和产出验收由程序负责。')


def output_template(payload, scope=None):
    stage = payload['stage']
    result = {k: payload.get(k) for k in ('task_id', 'input_hash')}
    if stage == 'extract':
        return {**result, 'analysis': {'characters': [], 'terms': [], 'non_translate': []}}
    def checks(names):
        return {name: {'status': None, 'evidence': ''} for name in names}
    dimensions = ('accuracy', 'completeness', 'terminology', 'tone', 'logic', 'placeholders')
    if stage == 'glossary_review':
        from glossary_merge import review_evidence_rows
        entries = payload.get('entries', [])
        return {**result, 'decisions': {
            'snapshot_sha256': payload.get('snapshot_sha256'), 'source_sha256': payload.get('source_hash'),
            'review_complete': False, 'review_summary': '', 'reviewed_refs': [e['ref'] for e in entries],
            'acknowledged_findings': [], 'edits': [], 'quarantine': [], 'additions': [], 'allow_no_match': [],
            'entry_reviews': [{'ref': e['ref'], 'evidence_rows': review_evidence_rows(e['row']),
                'source_excerpt': '', 'reason': '', 'checks': checks(('source', 'translation', 'category', 'consistency'))}
                for e in entries]}}
    records = []
    for row in payload.get('records', []):
        if scope is not None and row['id'] not in scope:
            continue
        item = {'id': row['id']}
        if stage == 'pilot_review' and not row.get('exceptions'):
            item.update(reviewed=False, verdict=None)
        else:
            item['translation'] = row.get('translation') or '' if stage in ('review', 'final_review', 'pilot_review', 'polish') else ''
            if stage in ('review', 'final_review', 'pilot_review'):
                item.update(reviewed=False, reason='', source_excerpt='', checks=checks(dimensions), changes=[], issues=[])
                if stage == 'pilot_review':
                    item['verdict'] = None
            if stage == 'polish':
                item['reason'] = ''
            if row.get('exceptions'):
                item['exceptions'] = copy.deepcopy(row['exceptions'])
        records.append(item)
    result['records'] = records
    if stage == 'pilot_review':
        result['review_summary'] = checks(dimensions)
    return result


def render_protocol_input(payload, result_file):
    from translation_workflow import prompt
    system, data = prompt(payload)
    return ('# 已准备的任务\n' + system + '\n'
            + '结果保存到：' + str(result_file) + '\n'
            + 'output_template 为输出骨架，任务范围已列好；不要把空证据或待定状态当作完成。\n'
            + '```json\n' + data + '\n```\n')
