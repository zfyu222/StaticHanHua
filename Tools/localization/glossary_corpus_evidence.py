"""Retrieve bounded *candidate* definitions from the source corpus, not CSV neighbours.

No meaning/translation is inferred here. Complete selected records are attached
by the normal evidence bundler and remain subject to the existing token budget.
English definition cues currently supplement (never replace) extraction evidence.
"""
import re
from collections import defaultdict

_CUE = re.compile(r'\b(?:descriptor|ability|abilities|skill|spell|trait|class|'
                  r'bonuses|bonus|called|known as|refers to|means)\b', re.I)
_ROLE = re.compile(r'^\s*(descriptor|ability|skill|spell|trait|class)\b', re.I)
_WORD = re.compile(r"[\w]+(?:['’][\w]+)?", re.UNICODE)


def attach_corpus_evidence(analysis, records, limit=2):
    """Indexed cue definitions plus full-corpus non-translate contrasts.

    An unrelated use of a homonym may be retrieved: reviewers must distinguish
    it rather than letting retrieval manufacture a global sense or exemption.
    """
    if type(limit) is not int or not 0 <= limit <= 2:
        raise ValueError('Corpus evidence limit must be between zero and two')
    records = list(records)
    from workflow_nontranslate import lexical_literal
    lexical_rows = [row for row in analysis.get('non_translate', []) if lexical_literal(row['source'])]
    full_index = defaultdict(list)
    index = defaultdict(list)
    by_ordinal = {}
    for record in records:
        text = record['source']
        if lexical_rows:
            for word in set(_WORD.findall(text.casefold())):
                full_index[word].append(record['ordinal'])
        if not _CUE.search(text):
            continue
        ordinal = record['ordinal']
        by_ordinal[ordinal] = text
        for word in set(_WORD.findall(text.casefold())):
            index[word].append(ordinal)
    attached = 0
    for table in ('characters', 'terms'):
        for row in analysis.get(table, []):
            row.pop('corpus_evidence_rows', None)
            row.pop('corpus_evidence_note', None)
            words = set(_WORD.findall(row['source'].casefold()))
            if not words or any(word not in index for word in words):
                continue
            candidates = index[min(words, key=lambda word: len(index[word]))]
            pattern = re.compile(r'(?<!\w)' + re.escape(row['source']) + r'(?!\w)', re.I)
            # Only the representative extraction citations are already bundled.
            # A useful definition elsewhere in the full evidence list must not
            # be lost just because its ordinal was recorded but never shown.
            existing = set(row.get('evidence_rows', [])[:2])
            for candidate in row.get('extraction_candidates', []):
                existing.update(candidate.get('evidence_rows', [])[:2])
            ranked = []
            roles = {}
            for ordinal in candidates:
                text = by_ordinal[ordinal]
                matches = list(pattern.finditer(text))
                if not matches:
                    continue
                found = [match.group(1).casefold() for m in matches
                         if (match := _ROLE.match(text[m.end():]))]
                direct = bool(found)
                if found:
                    roles[ordinal] = found[0]
                ranked.append((not direct, len(text), ordinal))
            ordered = sorted(ranked)
            selected = [ordinal for _, _, ordinal in ordered if ordinal not in existing][:limit]
            # One hop only: "X descriptor" -> a definition of "descriptor".
            # This is a literal relation, not an inferred character/item label.
            # Reserve a slot inside the same cap; never grow the bundle recursively.
            anchor = next((ordinal for _, _, ordinal in ordered if ordinal in roles), None)
            if limit >= 2 and anchor is not None:
                role = roles[anchor]
                explains = re.compile(r'\b' + re.escape(role)
                    + r'\s+(?:is|are|provides?|grants?|determines?|defines?|represents?|affects?)\b', re.I)
                owner = re.compile(r'\b(?:character|player|item|weapon)\s+' + re.escape(role) + r'\b', re.I)
                related = []
                for ordinal in index[role]:
                    text = by_ordinal[ordinal]
                    if ordinal in existing or ordinal == anchor or pattern.search(text):
                        continue
                    definition = bool(explains.search(text))
                    if definition or owner.search(text):
                        related.append((not definition, len(text), ordinal))
                if related:
                    related_ordinal = min(related)[2]
                    selected = ([anchor, related_ordinal] if anchor not in existing else
                                [related_ordinal] + [n for n in selected if n != related_ordinal][:limit-1])
                    row['corpus_evidence_note'] = f'Literal role link: row {anchor} -> "{role}" -> row {related_ordinal}; verify meaning.'
            if selected:
                row['corpus_evidence_rows'] = selected
                attached += len(selected)
    from workflow_nontranslate import lexical_literal, usage_witnesses
    nontranslate_attached = 0
    full_records = list(records)
    full_texts = {r['ordinal']: r['source'] for r in full_records}
    for row in analysis.get('non_translate', []):
        row.pop('corpus_evidence_rows', None);row.pop('corpus_evidence_note', None)
        if not lexical_literal(row['source']):
            continue
        words = set(_WORD.findall(row['source'].casefold()))
        candidate_ids = (full_index[min(words, key=lambda word: len(full_index[word]))]
                         if words and all(word in full_index for word in words) else [])
        standalone, embedded = usage_witnesses(row, [dict(ordinal=n, source=full_texts[n]) for n in candidate_ids])
        existing = set(row.get('evidence_rows', [])[:2])
        for candidate in row.get('extraction_candidates', []):
            existing.update(candidate.get('evidence_rows', [])[:2])
        selected = []
        # If one kind is already shown, spend the bounded evidence budget on
        # the contrasting use first. Complete records only, never snippets.
        pools = sorted([standalone, embedded], key=lambda pool: bool(existing.intersection(pool)))
        for pool in pools:
            candidates = sorted((n for n in pool if n not in existing), key=lambda n: (len(full_texts[n]), n))
            if candidates and len(selected) < limit:
                selected.append(candidates[0])
        if selected:
            row['corpus_evidence_rows'] = selected
            nontranslate_attached += len(selected)
        row['corpus_evidence_note'] = (f'Literal uses: standalone={len(standalone)}, embedded={len(embedded)}. '
            'Structural contrast only, not proof of command meaning. Check whether protection applies in each context; '
            'justify global text scope or narrow to whole_record/literal_context.')
    analysis['nontranslate_scope_policy'] = 1
    analysis['corpus_evidence_policy'] = 1
    return {'attached_record_links': attached+nontranslate_attached, 'cue_records': len(by_ordinal),
            'nontranslate_attached_links': nontranslate_attached}
