"""Consolidate extraction candidates before assigning semantic glossary review."""
import copy
import json
from collections import Counter

TABLES = ('characters', 'terms', 'non_translate')


def merge_extractions(extractions):
    """One case-insensitive literal source owns one review slot across tables.

    Frequency counts extraction proposals, not inferred corpus occurrences.
    Ties retain first-seen order; no semantic approval is made here.
    """
    groups = {}
    for task_id, analysis in extractions:
        for table in TABLES:
            for entry in analysis[table]:
                source = entry['source'].strip().casefold()
                candidates = groups.setdefault(source, {})
                row = {k: copy.deepcopy(v) for k, v in entry.items() if k != 'evidence_rows'}
                identity = (table, json.dumps(row, ensure_ascii=False, sort_keys=True))
                if identity not in candidates:
                    candidates[identity] = {'table': table, 'row': row, 'extraction_count': 0,
                                            'evidence_rows': [], 'extraction_tasks': []}
                candidate = candidates[identity]
                candidate['extraction_count'] += 1
                candidate['evidence_rows'] = sorted(set(candidate['evidence_rows']) | set(entry.get('evidence_rows', [])))
                if task_id not in candidate['extraction_tasks']:
                    candidate['extraction_tasks'].append(task_id)
    result = {table: [] for table in TABLES}
    for candidates_by_identity in groups.values():
        candidates = list(candidates_by_identity.values())
        votes = Counter()
        for candidate in candidates:
            translation = candidate['row'].get('recommended_translation') if candidate['table'] != 'non_translate' else None
            votes[translation] += candidate['extraction_count']
        selected_translation = max(votes, key=votes.get)
        matching = [c for c in candidates if
                    (c['row'].get('recommended_translation') if c['table'] != 'non_translate' else None) == selected_translation]
        selected = max(matching, key=lambda c: c['extraction_count'])
        row = copy.deepcopy(selected['row'])
        row['source'] = row['source'].strip()
        row['evidence_rows'] = sorted({n for c in candidates for n in c['evidence_rows']})
        row['extraction_candidates'] = candidates
        row['provisional_selection'] = 'most_extraction_proposals_then_first_seen'
        result[selected['table']].append(row)
    result['applicability_policy'] = 1
    result['terminology_policy'] = 1
    return result


def review_evidence_rows(row):
    """Include representative source evidence for every retained candidate."""
    evidence = set(row.get('evidence_rows', [])[:2])
    for candidate in row.get('extraction_candidates', []):
        evidence.update(candidate.get('evidence_rows', [])[:2])
    return sorted(evidence)
