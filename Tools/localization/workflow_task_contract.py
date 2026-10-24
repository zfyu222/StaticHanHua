"""Program-owned output envelopes. Empty evidence never means semantic approval."""
import copy
import json
from collections import Counter

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
        item['translation'] = row.get('translation') or '' if stage in ('review', 'final_review', 'pilot_review', 'polish') else ''
        if stage in ('review', 'final_review', 'pilot_review'):
            item.update(reviewed=False, reason='', source_excerpt='', checks=checks(dimensions), changes=[], issues=[])
        if stage == 'polish':
            item['reason'] = ''
        item['exceptions'] = copy.deepcopy(row.get('exceptions') or [])
        records.append(item)
    result['records'] = records
    return result


def api_output_template(payload, scope=None):
    """Schema examples only; the input, never this example, defines task scope.

    Persisted result templates and validators continue using output_template.
    """
    result = output_template(payload, scope)
    if payload['stage'] == 'glossary_review':
        decisions = result['decisions']
        for key in ('snapshot_sha256', 'source_sha256', 'reviewed_refs'):
            decisions.pop(key, None)
        decisions['entry_reviews'] = decisions['entry_reviews'][:1]
        for item in decisions['entry_reviews']:
            item['evidence_rows'] = []
            item['checks'] = {key: ['按实际核对选择状态', '填写本维度具体依据'] for key in item['checks']}
    elif 'records' in result:
        # Review needs both choices visible. Missing translation on corrected
        # items was repeatedly observed when the skeleton showed only keep.
        examples, shapes = [], set()
        for item in result['records']:
            reusable = payload['stage'] in ('review', 'final_review', 'polish')
            shape = ('revise' if not examples else 'keep') if reusable else ('detailed' if 'checks' in item else 'simple')
            if shape in shapes:
                continue
            shapes.add(shape)
            example = copy.deepcopy(item)
            if 'translation' in example:
                if reusable and shape == 'keep':
                    example.pop('translation')
                    example['keep'] = True
                    example.pop('reviewed', None)
                elif reusable:
                    example['translation'] = '填写本条完整新译文；不能只在reason或changes中描述修改'
                    example.pop('reviewed', None)
                else:
                    example['translation'] = ''
            # Make the keep-with-reason route visible in the response skeleton.
            # Actual inherited reasons remain in the input, not duplicated here.
            example['exceptions'] = []
            if 'checks' in example:
                example.pop('source_excerpt', None)
                example['source_ref'] = 'whole'
                example['checks'] = {key: ['pass 或 corrected' if key in ('accuracy', 'completeness', 'logic')
                    else 'pass、corrected 或有依据的 not_applicable', '填写本维度具体依据'] for key in example['checks']}
                example['evidence'] = {
                    'e1': '填写本条含义、覆盖及关系的实际核对依据；若依据不同，另列证据',
                    'e2': '填写本条术语适用情况的实际依据',
                    'e3': '填写本条口吻的实际依据',
                    'e4': '填写本条变量和标签的实际依据'}
                example['reason'] = {'ref': 'e1'}
                refs = {'accuracy': 'e1', 'completeness': 'e1', 'logic': 'e1',
                        'terminology': 'e2', 'tone': 'e3', 'placeholders': 'e4'}
                for key, check in example['checks'].items():
                    check[1] = {'ref': refs[key]}
                if reusable and shape == 'revise':
                    example['changes'] = [{'category': 'accuracy', 'severity': 'major',
                        'source_ref': 'whole', 'reason': {'ref': 'e1'}}]
            examples.append(example)
        result['records'] = examples
    return result


def api_template_instruction(stage):
    if stage == 'extract':
        return ('output_template 是结构示例；阅读全部 records 后提取术语，无术语可返回空数组。'
                'source 必须逐字复制所引原文中的连续片段，保留其标点，不合并分散词语；'
                'evidence_rows 引用确实含该片段的 ordinal，歧义和候选差异保留必要出处。')
    if stage == 'glossary_review':
        return ('output_template 是结构示例，不是任务清单；entry_reviews 必须覆盖全部分配 entries.ref，'
                '不包含只读关联项；edits/quarantine 用 ref 指向 e编号，无需 table/index。'
                '现有条目的 allow_no_match 也使用 ref 和真实 reason。acknowledged_findings 明确确认本批 audit.findings 的 f编号；'
                'checks 每维用 [status, evidence] 两项数组，均由模型真实填写；程序恢复 status/evidence 对象。'
                '不得自动确认未核对项；哈希、任务标识和 reviewed_refs 由程序绑定恢复，无需返回。')
    instruction = ('records 仅示范结构，不表示示例中的ID应修改或保留；每条依实际核对选择路径。'
                   '必须返回输入的全部 ID 各一次，不限于示例。')
    if stage in ('review', 'final_review', 'polish'):
        instruction += ('未改译文返回 keep:true 并省略 translation；修改时仅返回一次完整 translation，可省略 keep。'
                        '两种路径的字段片段分别为 {"id":"1","keep":true} 和 '
                        '{"id":"1","translation":"本条完整新译文"}；两者都须填写本阶段其余实际理由和证据。'
                        'changes非空或checks出现corrected时必须有完整translation，且须有本条来源引用；'
                        'reason/changes里的替换短语不能代替完整译文。不以缺少字段表示通过；reason说明实际判断。')
        if stage != 'polish':
            instruction += ('审校 checks、source_excerpt、changes 必须真实填写。'
                            'checks 每维使用 [status,evidence] 两项数组；两个值都须由本次真实检查填写，'
                            '程序只恢复字段名，不代填状态或依据。'
                            '同条相同依据可只写一次：evidence={"e1":"本条具体核对依据"}，'
                            'reason、checks 各维第二项和 changes.reason 可用 {"ref":"e1"} 显式引用；'
                            '六个状态仍各自填写，依据必须足以支持所引用维度，不使用空泛全通过说明；'
                            'e编号仅限本条，不跨记录引用，不嵌套引用，不列未使用证据。'
                            'changes 的 before/after 由程序恢复，无需抄写；reviewed 标记由程序绑定。'
                            '引用本条完整原文时可用 source_ref:"whole" 代替 source_excerpt，changes 中也适用；'
                            '局部引用仍填写连续 source_excerpt。issues 只填写当前候选尚未解决的问题，'
                            '已解决的错误写入 changes 和 checks，不重复保留为 blocking 或 warning。')
    return (instruction +
            '术语例外用 exceptions=[{term_ref:"t1",reason:"具体原句语境依据"}]，'
            'term_ref 只能取本条 term_refs 中的编号，无需抄写术语原文；没有新例外用 [] 或省略。'
            '撤销已有例外用 exception_changes=[{exception_ref:"x1",action:"remove",reason:"原句反证"}]，'
            'exception_ref 只能引用本条 existing_exceptions 中的编号，即使对应术语当前未命中也可撤销。'
            '可选 issues 按规则填写。')


def api_protocol_data(payload):
    """Remove scheduling/default fields, never semantic evidence or unknown fields."""
    value = copy.deepcopy(payload)
    for key in ('protocol', 'revision', 'config_hash', 'worker', 'lease_token', 'attempt', 'extract_split_lineage'):
        value.pop(key, None)
    if payload['stage'] == 'extract':
        value.pop('source_hash', None)  # Extraction output has no source-hash field.
    for key in ('context', 'terms', 'glossary_version', 'related_entries'):
        if not value.get(key):
            value.pop(key, None)
    for row in value.get('records', []):
        if row.get('translation') is None:
            row.pop('translation', None)
        for key in ('metadata', 'protection', 'exceptions', 'locked'):
            if row.get(key) is None or row.get(key) in ('', [], {}, False):
                row.pop(key, None)
    value['output_template'] = api_output_template(payload)
    if payload['stage'] in ('extract', 'glossary_review'):
        from workflow_api_transport import compact_term_input
        value = compact_term_input(value, payload)
    compact_record_metadata(value)
    return value


def compact_record_metadata(value):
    """Factor repeated metadata values without dropping context or changing tasks.

    Only the new wire presentation changes; canonical payloads and existing
    handoff files retain their full metadata. Unknown metadata keys stay intact.
    """
    rows = value.get('records', [])
    if ('shared_metadata' in value or 'shared_metadata_text' in value
            or any('metadata_refs' in row for row in rows)):
        return  # Never overwrite an existing/future presentation namespace.
    counts = Counter()
    for row in rows:
        metadata = row.get('metadata')
        if isinstance(metadata, dict):
            counts.update((key, json.dumps(item, ensure_ascii=False, sort_keys=True))
                          for key, item in metadata.items())
    shared, refs = {}, {}
    for row in rows:
        metadata = row.get('metadata')
        if not isinstance(metadata, dict):
            continue
        for key, item in list(metadata.items()):
            token = (key, json.dumps(item, ensure_ascii=False, sort_keys=True))
            # Very short values cost less inline than a table plus references.
            if counts[token] < 2 or len(token[1]) < 24:
                continue
            if token not in refs:
                ref = 'm' + str(len(refs) + 1)
                refs[token] = ref
                shared[ref] = {key: item}
            row.setdefault('metadata_refs', []).append(refs[token])
            del metadata[key]
        if not metadata:
            row.pop('metadata', None)
    if shared:
        value['shared_metadata'] = shared
    # Resource addresses commonly differ only in #ID=...; retain the entire
    # address through a shared prefix rather than repeating a long file path.
    resources = [row['metadata'].get('resource') for row in rows if isinstance(row.get('metadata'), dict)]
    prefixes = Counter(item.partition('#')[0] for item in resources
                       if isinstance(item, str) and '#' in item)
    text_refs, texts = {}, {}
    for row in rows:
        metadata = row.get('metadata', {})
        if not isinstance(metadata, dict):
            continue
        resource = metadata.get('resource')
        if not isinstance(resource, str) or '#' not in resource:
            continue
        prefix, marker, suffix = resource.partition('#')
        if prefixes[prefix] < 2 or len(prefix) < 24:
            continue
        if prefix not in text_refs:
            ref = 'p' + str(len(text_refs) + 1)
            text_refs[prefix] = ref; texts[ref] = prefix
        metadata['resource'] = {'prefix_ref': text_refs[prefix], 'suffix': marker + suffix}
    if texts:
        value['shared_metadata_text'] = texts


def render_protocol_input(payload, result_file, *, api=False):
    from translation_workflow import prompt
    system, data = prompt(payload, api=api)
    if api:
        return (system + '\n' + api_template_instruction(payload['stage']) +
                '\nshared_metadata 是共用上下文表；每条 metadata_refs 引用其字段，与本条 metadata 合并理解。'
                'resource 的 prefix_ref 引用 shared_metadata_text，完整资源位置为该前缀与 suffix 原样拼接。\n```json\n' + data + '\n```\n')
    return ('# 已准备的任务\n' + system + '\n'
            + '按模板产出 JSON，由执行器保存到：' + str(result_file) + '\n'
            + 'output_template 为输出骨架，任务范围已列好；不要把空证据或待定状态当作完成。\n'
            + '```json\n' + data + '\n```\n')


API_SHORT_ID_STAGES = ('extract', 'glossary_review', 'translate', 'pilot_translate', 'review', 'final_review', 'polish')


def api_id_transport(payload, scope, session_id):
    """A session-local transport map; canonical task data is never changed."""
    mapping = {}
    for row in payload['records']:
        canonical = row['id']
        if canonical not in scope:
            continue
        short = str(len(mapping) + 1)
        if short in mapping or canonical in mapping.values():
            raise ValueError('Duplicate API transport ID')
        mapping[short] = canonical
    if set(mapping.values()) != set(scope):
        raise ValueError('API transport map does not cover session scope')
    result = {'version': 2, 'session_id': session_id, 'task_id': payload['task_id'],
              'input_hash': payload['input_hash'], 'short_to_canonical': mapping, 'stage': payload.get('stage')}
    if payload.get('stage') == 'glossary_review':
        from workflow_api_transport import term_binding
        result['glossary'] = term_binding(payload)
    if payload.get('stage') in ('review', 'final_review', 'polish'):
        result['original_translations'] = {row['id']: row['translation'] for row in payload['records'] if row['id'] in scope}
    if payload.get('stage') in ('review', 'final_review', 'translate', 'pilot_translate', 'polish'):
        import ainiee_bridge as bridge
        result['original_sources'] = {row['id']: bridge.restore(row['source'], row.get('protection', []), allow_original=True)
                                     for row in payload['records'] if row['id'] in scope}
    return result


def transport_id_fields(value, mapping):
    """Change identifier fields/keys only, preserving all semantic text."""
    if isinstance(value, list):
        return [transport_id_fields(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): (mapping.get(item, item) if key == 'id' and isinstance(item, str)
                else transport_id_fields(item, mapping)) for key, item in value.items()}
    return value


def restore_source_references(item, source):
    """Restore an explicitly chosen whole-source citation, never a semantic claim."""
    item = copy.deepcopy(item)
    def resolve(value):
        if 'source_ref' not in value:
            return
        if value.pop('source_ref') != 'whole' or not isinstance(source, str):
            raise ValueError('source_ref must explicitly name the bound whole source')
        if 'source_excerpt' in value and value['source_excerpt'] not in ('', source):
            raise ValueError('Whole-source reference conflicts with explicit source_excerpt')
        value['source_excerpt'] = source
    resolve(item)
    for change in item.get('changes', []) if isinstance(item.get('changes', []), list) else []:
        if isinstance(change, dict):
            resolve(change)
    return item


def canonical_api_result(result, transport, session_id, task_id, input_hash, scope):
    if (not isinstance(transport, dict) or transport.get('version') != 2 or transport.get('session_id') != session_id
            or transport.get('task_id') != task_id or transport.get('input_hash') != input_hash):
        raise ValueError('API transport binding mismatch')
    mapping = transport.get('short_to_canonical')
    if (not isinstance(mapping, dict) or (not mapping and not (transport.get('stage') == 'glossary_review' and not scope))
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())
            or len(set(mapping.values())) != len(mapping)
            or set(mapping.values()) != set(scope)):
        raise ValueError('Invalid API transport map scope')
    if set(mapping) != {str(i) for i in range(1, len(mapping) + 1)}:
        raise ValueError('Invalid API local ID mapping')
    if not isinstance(result, dict):
        raise ValueError('API response requires an object')
    from workflow_api_reliability import normalize_local_ids
    result = normalize_local_ids(result, mapping)
    for field, expected in (('task_id', task_id), ('input_hash', input_hash)):
        if field in result and result[field] != expected:
            raise ValueError('API response ' + field + ' mismatch')
    if transport.get('stage') in ('extract', 'glossary_review'):
        from workflow_api_transport import restore_term_result
        canonical = restore_term_result(result, transport)
        canonical.update(task_id=task_id, input_hash=input_hash)
        return canonical
    if not isinstance(result.get('records'), list):
        raise ValueError('API response requires records')
    ids = [row.get('id') if isinstance(row, dict) else None for row in result['records']]
    if any(not isinstance(i, str) or i not in mapping for i in ids):
        raise ValueError('API record id must name an assigned local ID')
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate API short ID: ' + ', '.join(str(i) for i in dict.fromkeys(ids) if ids.count(i) > 1))
    summaries = result.get('review_summaries', {})
    if not isinstance(summaries, dict) or any(key not in mapping for key in summaries):
        raise ValueError('Unknown API review summary ID')
    canonical = transport_id_fields(result, mapping)
    from workflow_review_evidence import expand_evidence
    canonical['records'] = [expand_evidence(row) for row in canonical['records']]
    from workflow_api_transport import expand_check_pairs
    for row in canonical['records']:
        if 'checks' in row:
            row['checks'] = expand_check_pairs(row['checks'])
    sources = transport.get('original_sources', {})
    if (not isinstance(sources, dict) or sources and (set(sources) != set(scope)
            or any(not isinstance(source, str) for source in sources.values()))):
        raise ValueError('Invalid bound original source map')
    existing = transport.get('record_exceptions')
    if existing is not None and (not isinstance(existing, dict) or set(existing) != set(scope)
            or any(not isinstance(refs, dict) or any(not isinstance(ref, str)
                or not ref.startswith('x') or not ref[1:].isdigit()
                or ref != 'x' + str(int(ref[1:])) or int(ref[1:]) < 1
                or not isinstance(source, str) or not source.strip() for ref, source in refs.items())
                   for refs in existing.values())):
        raise ValueError('Invalid bound existing exception map')
    canonical['records'] = [restore_source_references(row, sources.get(row['id'])) for row in canonical['records']]
    from workflow_api_transport import restore_body_result
    restore_body_result(canonical, transport)
    record_terms = transport.get('record_terms')
    if record_terms is not None:
        if (not isinstance(record_terms, dict) or set(record_terms) != set(scope)
                or any(not isinstance(terms, dict) or any(
                    not isinstance(ref, str) or not ref.startswith('t') or not ref[1:].isdigit()
                    or ref != 't' + str(int(ref[1:])) or int(ref[1:]) < 1
                    or not isinstance(source, str) or not source.strip()
                    for ref, source in terms.items()) for terms in record_terms.values())):
            raise ValueError('Invalid API term reference map')
    for row in canonical['records']:
        for field in ('exceptions', 'exception_changes'):
            entries = row.get(field)
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                continue  # Existing field validation reports malformed legacy values.
            converted, seen = [], set()
            for entry in entries:
                if isinstance(entry, dict) and 'exception_ref' in entry:
                    allowed_exceptions = transport.get('record_exceptions', {}).get(row['id'], {})
                    from workflow_api_reliability import normalize_bound_ref
                    ref = normalize_bound_ref(entry['exception_ref'], allowed_exceptions, 'x')
                    if (field != 'exception_changes' or not isinstance(ref, str) or ref not in allowed_exceptions
                            or entry.get('action') != 'remove'
                            or any(key in entry for key in ('term_ref', 'source', 'src'))):
                        raise ValueError(f'Record {row["id"]}: exception_ref must remove an assigned existing exception')
                    if ref in seen:
                        raise ValueError('Duplicate existing exception reference: ' + ref)
                    reason = entry.get('reason')
                    if not isinstance(reason, str) or not reason.strip():
                        raise ValueError('Existing exception removal requires specific nonempty reason')
                    seen.add(ref)
                    converted.append({'source': allowed_exceptions[ref], 'action': 'remove', 'reason': reason.strip()})
                    continue
                if not isinstance(entry, dict) or 'term_ref' not in entry:
                    converted.append(entry)
                    continue
                ref = entry['term_ref']
                allowed = (record_terms or {}).get(row['id'], {})
                from workflow_api_reliability import normalize_bound_ref
                ref = normalize_bound_ref(ref, allowed, 't')
                if not isinstance(ref, str) or ref not in allowed:
                    raise ValueError(f'Record {row["id"]}: term reference (term_ref) {ref!r} is not assigned to this record; allowed: {list(allowed)}')
                if ref in seen:
                    raise ValueError(f'Record {row["id"]}: Duplicate API term reference {ref}')
                if 'source' in entry or 'src' in entry:
                    raise ValueError('API term reference must not also supply source')
                reason = entry.get('reason')
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError(f'Record {row["id"]}: API term exception {ref} requires a nonempty string reason')
                seen.add(ref)
                converted.append({**{k: v for k, v in entry.items() if k != 'term_ref'},
                                  'source': allowed[ref], 'reason': reason.strip()})
            if field in row:
                row[field] = converted
    canonical.update(task_id=task_id, input_hash=input_hash)
    return canonical


def canonical_api_partial_result(result, transport, session_id, task_id, input_hash, scope):
    """Keep independently decodable body rows; reject an ambiguous envelope.

    A failed-row marker is program-owned diagnostic data, never an approval or
    a substituted translation. Common batch collection creates the repair scope.
    """
    if not isinstance(transport, dict):
        raise ValueError('API transport binding mismatch')
    if transport.get('stage') in ('extract', 'glossary_review'):
        return canonical_api_result(result, transport, session_id, task_id, input_hash, scope)
    from workflow_api_reliability import normalize_local_ids
    if not isinstance(result, dict) or not isinstance(result.get('records'), list):
        raise ValueError('API response requires records')
    if '_format_failures' in result:
        raise ValueError('Reserved program diagnostic field in model reply')
    canonical = canonical_api_result(dict(result, records=[]), transport, session_id, task_id, input_hash, scope)
    mapping = transport.get('short_to_canonical', {})
    value = normalize_local_ids(result, mapping)
    rows = value['records']
    ids = [row.get('id') if isinstance(row, dict) else None for row in rows]
    if (any(not isinstance(ref, str) or ref not in mapping for ref in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('API output IDs must be known, unique local IDs')
    envelope = dict(value, records=[])
    failures = []
    for row in rows:
        try:
            if any(key in row for key in ('_format_error', '_raw_api_item')):
                raise ValueError('Reserved program diagnostic field in model reply')
            decoded = canonical_api_result(dict(envelope, records=[row]), transport,
                                           session_id, task_id, input_hash, scope)
            canonical['records'].extend(decoded['records'])
        except ValueError as exc:
            identifier = mapping[row['id']]
            failures.append({'id': identifier, 'error': str(exc)})
            canonical['records'].append({'id': identifier, '_format_error': str(exc),
                                         '_raw_api_item': copy.deepcopy(row)})
    if failures:
        canonical['_format_failures'] = failures
    return canonical
