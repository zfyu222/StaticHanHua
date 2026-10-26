"""Program-owned API presentation; semantic evidence stays model-owned."""
import copy


TERM_STAGES = ('extract', 'glossary_review')
REUSE_STAGES = ('review', 'final_review', 'polish')


def expand_check_pairs(checks):
    """Decode an explicit [status, evidence] pair without inventing either."""
    if not isinstance(checks, dict):
        return checks  # Shared contract validation reports missing dimensions.
    result = {}
    for dimension, value in checks.items():
        if isinstance(value, list):
            if len(value) != 2 or any(not isinstance(part, str) for part in value):
                raise ValueError('Review check pair requires exactly [status, evidence]: ' + dimension)
            value = {'status': value[0], 'evidence': value[1]}
        result[dimension] = value
    return result


def term_binding(payload):
    refs = []
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get('ref'), str):
                refs.append(value['ref'])
            if isinstance(value.get('refs'), list):
                refs.extend(value['refs'])
            for key, child in value.items():
                if key not in ('ref', 'refs', 'row', 'extraction_candidates'):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    for key in ('entries', 'related_entries', 'term_families', 'audit'):
        collect(payload.get(key))
    refs = list(dict.fromkeys(refs))
    return {'entry_refs': {f'e{i+1}': ref for i, ref in enumerate(refs)},
            'assigned_refs': [e['ref'] for e in payload.get('entries', [])],
            'finding_refs': {f'f{i+1}': f['id'] for i, f in enumerate(payload.get('audit', {}).get('findings', []))},
            'snapshot_sha256': payload.get('snapshot_sha256'),
            'source_sha256': payload.get('source_hash'),
            'entry_sources': {e['ref']: e['row'].get('source') for e in payload.get('entries', [])}}


def prune_unreadable_evidence(value):
    """List only citations the bundle can quote back.

    Canonical payloads keep the full evidence arrays; the model only ever sees
    rows whose text is included in `records`, because a citation it cannot read
    cannot be verified and only produces rejected batches. Assigned entries keep
    their original list only when nothing in it is readable, so no entry loses
    every option; read-only related entries and candidate lists are pruned even
    when that leaves them empty, because they are never valid citations.
    """
    visible = {row.get('ordinal') for row in value.get('records', []) if isinstance(row, dict)}

    def strip(item):
        if isinstance(item, list):
            return [strip(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key in ('evidence_rows', 'corpus_evidence_rows') and isinstance(child, list):
                result[key] = [number for number in child if number in visible]
            else:
                result[key] = strip(child)
        return result

    entries = value.get('entries')
    if isinstance(entries, list):
        pruned = []
        for entry in entries:
            item = strip(entry)
            row = item.get('row') if isinstance(item, dict) else None
            original = entry.get('row') if isinstance(entry, dict) else None
            if (isinstance(row, dict) and isinstance(original, dict)
                    and not row.get('evidence_rows') and isinstance(original.get('evidence_rows'), list)):
                row['evidence_rows'] = list(original['evidence_rows'])
            pruned.append(item)
        value['entries'] = pruned
    if isinstance(value.get('related_entries'), list):
        value['related_entries'] = [strip(entry) for entry in value['related_entries']]
    return value


def compact_term_input(value, payload):
    """Drop tracking fields; keep sources, evidence, candidates and conflicts."""
    binding = term_binding(payload)
    forward = {v: k for k, v in binding['entry_refs'].items()}
    def walk(item):
        if isinstance(item, list):
            return [walk(v) for v in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key in ('extraction_tasks', 'reviewers', 'reviewer_id', 'batch_id',
                       'input_hash', 'snapshot_sha256', 'source_sha256'):
                continue
            if key == 'ref' and isinstance(child, str):
                child = forward.get(child, child)
            elif key == 'refs' and isinstance(child, list):
                child = [forward.get(v, v) for v in child]
            result[key] = walk(child)
        return result
    value = walk(value)
    for key in ('task_id', 'input_hash', 'source_hash', 'snapshot_sha256', 'glossary_version'):
        value.pop(key, None)
    for row in value.get('records', []):
        row.pop('id', None)  # Evidence uses the unchanged original ordinal.
    for key in ('entries', 'related_entries'):
        for entry in value.get(key, []):
            entry.pop('index', None)
    value = prune_unreadable_evidence(value)
    findings = value.get('audit', {}).get('findings', [])
    finding_forward = {v: k for k, v in binding['finding_refs'].items()}
    for finding in findings:
        finding['id'] = finding_forward[finding['id']]
    template = value['output_template']
    template.pop('task_id', None)
    template.pop('input_hash', None)
    if payload['stage'] == 'glossary_review':
        for key in ('snapshot_sha256', 'source_sha256', 'reviewed_refs'):
            template['decisions'].pop(key, None)
    return value


def restore_term_result(result, transport):
    value = copy.deepcopy(result)
    if transport['stage'] == 'extract':
        if not isinstance(value.get('analysis'), dict):
            raise ValueError('API extraction requires analysis')
        return value
    binding = transport.get('glossary')
    if not isinstance(binding, dict):
        raise ValueError('Missing bound glossary snapshot')
    decisions = value.get('decisions')
    # Some models emit the decisions object itself. This is an envelope-only
    # equivalent: preserve every explicit field and still run all bound checks.
    decision_fields = {'review_complete', 'review_summary', 'acknowledged_findings',
                       'edits', 'quarantine', 'additions', 'allow_no_match', 'entry_reviews',
                       'snapshot_sha256', 'source_sha256', 'reviewed_refs'}
    if ('decisions' not in value and 'review_complete' in value and 'entry_reviews' in value
            and set(value).issubset(decision_fields)):
        value = {'decisions': value}
        decisions = value['decisions']
    if not isinstance(decisions, dict):
        raise ValueError('API glossary review requires decisions')
    assigned = set(binding['assigned_refs'])
    def restore_ref(ref):
        from workflow_api_reliability import normalize_bound_ref
        ref = normalize_bound_ref(ref, binding['entry_refs'], 'e')
        if not isinstance(ref, str):
            raise ValueError(f'Glossary entry reference {ref!r} must name a bound e reference')
        canonical = binding['entry_refs'].get(ref)
        if canonical not in assigned:
            allowed = [short for short, full in binding['entry_refs'].items() if full in assigned]
            raise ValueError(f'Unknown or read-only glossary entry reference {ref!r}; assigned: {allowed}')
        return canonical
    reviews = decisions.get('entry_reviews')
    if not isinstance(reviews, list) or any(not isinstance(r, dict) for r in reviews):
        raise ValueError('API glossary review requires entry_reviews')
    for review in reviews:
        review['ref'] = restore_ref(review.get('ref'))
        if 'checks' in review:
            review['checks'] = expand_check_pairs(review['checks'])
    reviewed = [r['ref'] for r in reviews]
    if len(reviewed) != len(assigned) or set(reviewed) != assigned:
        inverse = {full:short for short,full in binding['entry_refs'].items()}
        missing = [inverse[ref] for ref in sorted(assigned-set(reviewed))]
        duplicates = [inverse[ref] for ref in dict.fromkeys(reviewed) if reviewed.count(ref)>1]
        raise ValueError(f'API glossary reviews must cover assigned entries exactly once; missing: {missing}; duplicate: {duplicates}')
    if 'reviewed_refs' in decisions:
        if not isinstance(decisions['reviewed_refs'], list):
            raise ValueError('API reviewed_refs must be a list')
        supplied = [restore_ref(ref) for ref in decisions['reviewed_refs']]
        if len(supplied) != len(reviewed) or set(supplied) != assigned:
            raise ValueError('API reviewed_refs mismatch')
    decisions['reviewed_refs'] = reviewed
    for field in ('snapshot_sha256', 'source_sha256'):
        if field in decisions and decisions[field] != binding[field]:
            raise ValueError('API glossary hash mismatch')
        decisions[field] = binding[field]
    findings = decisions.get('acknowledged_findings', [])
    if isinstance(findings, list):
        from workflow_api_reliability import normalize_bound_ref
        findings = [normalize_bound_ref(ref, binding['finding_refs'], 'f') for ref in findings]
    if (not isinstance(findings, list) or any(not isinstance(ref, str) or ref not in binding['finding_refs'] for ref in findings)
            or len(set(findings)) != len(findings)):
        raise ValueError('Unknown or duplicate glossary finding reference')
    # Acknowledgment remains explicit; never mark every finding by default.
    decisions['acknowledged_findings'] = [binding['finding_refs'][ref] for ref in findings]
    sources = dict(binding['entry_sources'])
    for key in ('edits', 'quarantine', 'allow_no_match'):
        actions = decisions.get(key, [])
        if not isinstance(actions, list):
            raise ValueError('Glossary actions must be lists')
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError('Glossary actions must be objects')
            if key == 'allow_no_match' and 'ref' not in action:
                additions = decisions.get('additions', [])
                if (not isinstance(additions, list) or not isinstance(action.get('source'), str)
                        or not any(isinstance(a, dict) and isinstance(a.get('row'), dict)
                                   and a['row'].get('source') == action['source'] for a in additions)):
                    raise ValueError('A source-only exception must name an explicit addition')
                continue
            ref = restore_ref(action.pop('ref', None))
            if key == 'allow_no_match':
                if 'source' in action:
                    raise ValueError('Use glossary ref or addition source, not both')
                action['source'] = sources[ref]
            else:
                table, index = ref.split(':')
                if 'table' in action or 'index' in action:
                    raise ValueError('Use glossary ref instead of table/index')
                action.update(table=table, index=int(index))
                if key == 'edits' and isinstance(action.get('patch'), dict) and 'source' in action['patch']:
                    sources[ref] = action['patch']['source']
    return value


def restore_body_result(value, transport):
    """Reconstruct known text only; never create reasons, quotes or checks."""
    stage = transport.get('stage')
    if stage not in REUSE_STAGES:
        return
    originals = transport.get('original_translations', {})
    for item in value['records']:
        if item['id'] not in originals or not isinstance(originals[item['id']], str):
            raise ValueError('Missing bound original translation')
        before = originals[item['id']]
        if 'keep' in item and type(item['keep']) is not bool:
            raise ValueError(f'Record {item["id"]}: keep must be a boolean')
        if item['id'] in transport.get('require_explicit_translation', []) and ('translation' not in item or item.get('keep') is True):
            raise ValueError('Original and unaccepted candidate differ; return explicit translation without keep:true')
        if item.pop('keep', False):
            if 'translation' in item and item['translation'] != before:
                raise ValueError('API keep conflicts with translation')
            item['translation'] = before
        if not isinstance(item.get('translation'), str):
            raise ValueError(f'Record {item["id"]}: return keep:true or an explicit translation')
        if stage in ('review', 'final_review'):
            item.setdefault('reviewed', True)  # A metadata flag; evidence is still validated.
            changes = item.get('changes', [])
            if not isinstance(changes, list):
                raise ValueError('Review changes must be a list')
            for change in changes:
                if not isinstance(change, dict):
                    raise ValueError('Review change must be an object')
                change.setdefault('before', before)
                change.setdefault('after', item['translation'])
