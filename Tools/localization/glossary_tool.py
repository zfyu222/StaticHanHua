"""Shared AINiee glossary audit/build/apply CLI. Uses only Python's standard library."""
import argparse
from collections import Counter, defaultdict
import copy
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

TABLES = ('characters', 'terms', 'non_translate')
TERM_CATEGORIES = {'身份', '物品', '组织', '地名', '技能', '种族', '其他'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_shape(analysis):
    if not isinstance(analysis, dict):
        raise ValueError('Expected an AINiee analysis_v1 object.')
    for table in TABLES:
        rows = analysis.get(table)
        if not isinstance(rows, list):
            raise ValueError(f'Missing or unsupported table: {table}')
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get('source'), str):
                raise ValueError(f'Invalid source at {table}:{index}')
            if table != 'non_translate' and not isinstance(row.get('recommended_translation'), str):
                raise ValueError(f'Invalid translation at {table}:{index}')
            from workflow_term_scope import validate_applicability
            validate_applicability(row)


def source_rows(path):
    # Formal pipeline CSV: no header, one logical field per record, UTF-8 BOM allowed.
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.reader(handle))
    if not rows or any(len(row) != 1 for row in rows):
        raise ValueError('Source must be a non-empty single-column CSV without a header.')
    return [row[0] for row in rows]


def findings(analysis, texts):
    validate_shape(analysis)
    corpus = '\n'.join(texts).casefold()
    result, names, translations = [], defaultdict(list), defaultdict(list)

    def flag(code, refs, message):
        result.append(dict(id=f'F{len(result):04d}', code=code, refs=refs, message=message))

    for table in ('characters', 'terms'):
        for index, row in enumerate(analysis[table]):
            ref = f'{table}:{index}'
            name, translation = row['source'], row['recommended_translation']
            names[name.casefold()].append(ref)
            translations[translation.strip()].append((ref, name))
            if not name.strip() or not translation.strip() or translation.strip() == '——':
                flag('empty', [ref], 'Empty source or unusable translation.')
            if name and name.casefold() not in corpus:
                flag('no_match', [ref], 'Source has no literal case-insensitive match; check variants/examples.')
            category = row.get('gender' if table == 'characters' else 'category_path')
            supported = {'男性', '女性', '其他'} if table == 'characters' else TERM_CATEGORIES
            if category not in supported:
                flag('category', [ref], f'Unsupported AINiee category: {category}')
            if ' / ' in name:
                flag('compound_source', [ref], 'Slash-separated alternatives may need separate source entries.')
    from workflow_term_scope import scope_risk
    for table in TABLES:
        for index, row in enumerate(analysis[table]):
            if scope_risk(row['source']) and not row.get('applicability'):
                flag('ambiguous_applicability', [f'{table}:{index}'],
                     'Ordinary word/single letter: quarantine unless a real game-specific meaning is evidenced; '
                     'otherwise supply explicit applicability, case sensitivity and reason.')
    for refs in names.values():
        if len(refs) > 1:
            flag('duplicate_source', refs, 'Exact source collision across project tables (case-insensitive).')
    for translation, rows in translations.items():
        if translation and len(rows) > 1:
            flag('shared_translation', [ref for ref, _ in rows], 'Different names share a translation; verify aliases/context.')
    return result


def refresh_stats(analysis):
    analysis['stats'] = dict(character_count=len(analysis['characters']), term_count=len(analysis['terms']),
                             non_translate_count=len(analysis['non_translate']))
    analysis['stats']['total_hits'] = sum(analysis['stats'].values())


def audit(cache, source, output):
    analysis = read_json(cache).get('extra', {}).get('analysis_v1')
    validate_shape(analysis)
    texts = source_rows(source)
    issues = findings(analysis, texts)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destinations = [output / name for name in ('glossary_original.json', 'glossary_audit.json', 'glossary_audit.md', 'glossary_decisions.json', 'glossary_contexts.json')]
    if any(path.exists() for path in destinations):
        raise ValueError('Audit artifacts already exist. Use a new review directory; keep the previous snapshot.')
    write_json(destinations[0], analysis)  # Never export the full cache: it may contain private runtime data.
    report = dict(format_version=1, source_sha256=digest(source), snapshot_sha256=digest(destinations[0]),
                  original_counts={table: len(analysis[table]) for table in TABLES}, findings=issues)
    write_json(destinations[1], report)
    lines = ['# 术语表自动检查', '', '这是机械检查结果，语义审校尚未完成。索引从 0 开始，原文记录号从 1 开始。',
             '无匹配不等于虚构；共用译名不等于错误。人物/物品误归类、译名准确性和专名统一须结合原文另行审校。', '',
             '| ID | 类型 | 条目 | 提示 |', '|---|---|---|---|']
    for item in issues:
        lines.append(f"| {item['id']} | {item['code']} | {', '.join(item['refs'])} | {item['message'].replace('|', '/')} |")
    destinations[2].write_text('\n'.join(lines) + '\n', encoding='utf-8')
    write_json(destinations[3], dict(review_complete=False, review_summary='', snapshot_sha256=report['snapshot_sha256'],
                                    source_sha256=report['source_sha256'], acknowledged_findings=[], edits=[], quarantine=[],
                                    additions=[], allow_no_match=[]))
    folded = [text.casefold() for text in texts]
    contexts = []
    for table in ('characters', 'terms'):
        for index, row in enumerate(analysis[table]):
            evidence = []
            if row['source'].strip():
                for record, text in enumerate(folded, 1):
                    offset = text.find(row['source'].casefold())
                    if offset >= 0:
                        start = max(0, offset - 120)
                        evidence.append(dict(row=record, excerpt=texts[record-1][start:start+500]))
                        if len(evidence) == 2:
                            break
            contexts.append(dict(ref=f'{table}:{index}', source=row['source'], evidence=evidence))
    write_json(destinations[4], contexts)
    return report


def build(snapshot, source, decisions_path, output):
    original, decisions = read_json(snapshot), read_json(decisions_path)
    validate_shape(original)
    texts = source_rows(source)
    if decisions.get('snapshot_sha256') != digest(snapshot) or decisions.get('source_sha256') != digest(source):
        raise ValueError('Review decisions refer to a different snapshot or source.')
    if decisions.get('review_complete') is not True or not str(decisions.get('review_summary', '')).strip():
        raise ValueError('Complete the contextual review and record review_summary before building.')
    issues = findings(original, texts)
    pending = {x['id'] for x in issues} - set(decisions.get('acknowledged_findings', []))
    if pending:
        raise ValueError('Unacknowledged audit findings: ' + ', '.join(sorted(pending)))
    revised = copy.deepcopy(original)
    mutations, removals, seen = [], set(), set()
    for action in ('edits', 'quarantine'):
        for item in decisions.get(action, []):
            table, index = item['table'], item['index']
            ref = (table, index)
            if table not in TABLES or not isinstance(index, int) or index < 0 or index >= len(original[table]) or ref in seen:
                raise ValueError(f'Invalid or repeated decision: {ref}')
            if not str(item.get('reason', '')).strip():
                raise ValueError(f'Decision lacks reason: {ref}')
            for record in item.get('evidence_rows', []):
                if not isinstance(record, int) or not 1 <= record <= len(texts):
                    raise ValueError(f'Invalid evidence record: {record}')
            seen.add(ref)
            before = copy.deepcopy(original[table][index])
            if action == 'quarantine':
                removals.add(ref)
                after = None
            else:
                patch = item['patch']
                if not isinstance(patch, dict):
                    raise ValueError('patch must be an object.')
                if any(key in patch for key in ('source', 'recommended_translation')) and not item.get('evidence_rows'):
                    raise ValueError('Source/translation edits need evidence_rows: ' + str(ref))
                if 'applicability' in patch and not item.get('evidence_rows'):
                    raise ValueError('Applicability edits need evidence_rows: ' + str(ref))
                revised[table][index].update(patch)
                after = copy.deepcopy(revised[table][index])
            mutations.append(dict(action=action, table=table, index=index, before=before, after=after,
                                  reason=item['reason'], evidence=[dict(row=n, text=texts[n-1]) for n in item.get('evidence_rows', [])]))
    for table in TABLES:
        revised[table] = [row for index, row in enumerate(revised[table]) if (table, index) not in removals]
    for item in decisions.get('additions', []):
        if item['table'] not in TABLES or not str(item.get('reason', '')).strip():
            raise ValueError('Addition needs a supported table and a reason.')
        if not item.get('evidence_rows') or any(not isinstance(n, int) or not 1 <= n <= len(texts) for n in item['evidence_rows']):
            raise ValueError('Addition needs valid evidence_rows.')
        revised[item['table']].append(copy.deepcopy(item['row']))
        mutations.append(dict(action='add', after=item['row'], table=item['table'], reason=item['reason'],
                              evidence=[dict(row=n, text=texts[n-1]) for n in item['evidence_rows']]))
    # Moving/splitting uses quarantine + additions so the original entry remains in the log.
    validate_shape(revised)
    if revised.get('applicability_policy') == 1:
        from workflow_term_scope import validate_applicability
        for table in TABLES:
            for row in revised[table]:
                validate_applicability(row, required=True)
    final = findings(revised, texts)
    exceptions = {item['source'].casefold(): item['reason'] for item in decisions.get('allow_no_match', []) if str(item.get('reason', '')).strip()}
    for item in final:
        if item['code'] in {'empty', 'duplicate_source', 'category'}:
            raise ValueError('Unresolved structural error: ' + item['message'] + ' ' + ', '.join(item['refs']))
        if item['code'] == 'no_match':
            table, index = item['refs'][0].split(':')
            if revised[table][int(index)]['source'].casefold() not in exceptions:
                raise ValueError('Unresolved no-match source; fix/quarantine or explain allow_no_match: ' + item['refs'][0])
    refresh_stats(revised)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths = [output / name for name in ('glossary_reviewed.json', 'glossary_review_changes.json', 'glossary_manifest.json')]
    if any(path.exists() for path in paths):
        raise ValueError('Build artifacts exist. Use a new directory to retain review history.')
    write_json(paths[0], revised)
    write_json(paths[1], dict(review_summary=decisions['review_summary'], changes=mutations, remaining_findings=final,
                             no_match_exceptions=exceptions))
    write_json(paths[2], dict(format_version=1, snapshot_sha256=digest(snapshot), source_sha256=digest(source),
                             reviewed_sha256=digest(paths[0]), decisions_sha256=digest(decisions_path)))
    return revised['stats']


def ainiee_running():
    if os.name != 'nt':
        raise ValueError('Live application requires Windows process detection; auditing/building are portable.')
    command = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python(w)?|AiNiee)\\.exe$' -and ($_.Name -match '^AiNiee' -or $_.CommandLine -match 'AiNiee\\.py') }).Count"
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True, text=True, check=True)
    return int(result.stdout.strip()) > 0


def apply_analysis(cache_path, original, reviewed, apply=False):
    """Shared guarded update. Does not require a game's filenames or translation rules."""
    validate_shape(original)
    validate_shape(reviewed)
    # Shape and duplicate checks work without the source; provenance is checked by the CLI manifest.
    for item in findings(reviewed, ['\n'.join(row['source'] for table in TABLES for row in reviewed[table])]):
        if item['code'] in {'empty', 'duplicate_source', 'category'}:
            raise ValueError('Reviewed glossary has structural errors: ' + item['message'])
    path = Path(cache_path)
    raw = path.read_bytes()
    cache = json.loads(raw.decode('utf-8-sig'))
    current = cache.get('extra', {}).get('analysis_v1')
    if current == reviewed:
        return dict(status='already_installed')
    if current != original:
        raise ValueError('Live glossary changed since review; do not overwrite it. Re-review the new table.')
    if not apply:
        return dict(status='checked', entries=len(reviewed['characters']) + len(reviewed['terms']))
    if ainiee_running():
        raise ValueError('Close AINiee normally before applying; its in-memory cache can overwrite edits.')
    backup_dir = path.parent / 'glossary_review_backups'
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f'{path.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}.json'
    backup.write_bytes(raw)
    revised_cache = copy.deepcopy(cache)
    revised_cache['extra']['analysis_v1'] = reviewed
    temporary = path.with_name(path.name + '.glossary.tmp')
    with temporary.open('x', encoding='utf-8') as handle:
        json.dump(revised_cache, handle, ensure_ascii=False, indent=2)
    if path.read_bytes() != raw:
        temporary.unlink()
        raise ValueError('Cache changed during preparation; aborted. Backup retained.')
    os.replace(temporary, path)
    verified = read_json(path)
    if verified != revised_cache:
        raise ValueError('Post-write validation failed; use backup: ' + str(backup))
    return dict(status='applied', backup=str(backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    audit_parser = sub.add_parser('audit')
    for name in ('cache', 'source', 'output'):
        audit_parser.add_argument('--' + name, required=True, type=Path)
    build_parser = sub.add_parser('build')
    for name in ('snapshot', 'source', 'decisions', 'output'):
        build_parser.add_argument('--' + name, required=True, type=Path)
    apply_parser = sub.add_parser('apply')
    for name in ('cache', 'snapshot', 'reviewed', 'manifest', 'source', 'decisions'):
        apply_parser.add_argument('--' + name, required=True, type=Path)
    apply_parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'audit':
            result = audit(args.cache, args.source, args.output)
            print('Audit findings:', dict(Counter(x['code'] for x in result['findings'])))
        elif args.command == 'build':
            print(build(args.snapshot, args.source, args.decisions, args.output))
        else:
            manifest = read_json(args.manifest)
            if manifest.get('format_version') != 1:
                raise ValueError('Unsupported review manifest version.')
            for name in ('snapshot', 'source', 'reviewed', 'decisions'):
                if manifest.get(name + '_sha256') != digest(getattr(args, name)):
                    raise ValueError('Artifact changed after review: ' + name)
            print(apply_analysis(args.cache, read_json(args.snapshot), read_json(args.reviewed), args.apply))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
