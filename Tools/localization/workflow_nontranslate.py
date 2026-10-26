"""Structural non-translate scope checks; never infer command or prose meaning."""
import re


def lexical_literal(source):
    # Whole words / identifier-like labels need a deliberate scope. Format
    # placeholders, bracket markers and punctuation are handled by their rules.
    return (isinstance(source, str) and any(c.isalpha() for c in source)
            and re.fullmatch(r"\w+(?:[ '\-]\w+)*", source, re.UNICODE) is not None)


def literal_pattern(source, case_sensitive=False):
    return re.compile((r'(?<!\w)' if source and (source[0].isalnum() or source[0] == '_') else '')
                      + re.escape(source)
                      + (r'(?!\w)' if source and (source[-1].isalnum() or source[-1] == '_') else ''),
                      0 if case_sensitive else re.I)


def validate_nontranslate_scope(row):
    from workflow_term_scope import validate_applicability
    validate_applicability(row)
    if lexical_literal(row['source']) and not row.get('applicability'):
        raise ValueError('Lexical non_translate requires explicit applicability (whole_record, '
                         'literal_context, or justified global text): '+repr(row['source'])
                         + '. Internal-looking spelling is not proof of a global exclusion.')


def usage_witnesses(row, records):
    pattern = literal_pattern(row['source'], row.get('applicability', {}).get('case_sensitive', False))
    standalone, embedded = [], []
    for record in records:
        text = record['source']
        for match in pattern.finditer(text):
            dest = standalone if not text[:match.start()].strip() and not text[match.end():].strip() else embedded
            dest.append(record['ordinal']);break
    return standalone, embedded


def mixed_latin_candidates(records, glossary):
    """Candidates only: even a matching keep-original rule is not an exemption."""
    from workflow_term_scope import applicable
    plans = [(row, literal_pattern(row['source'], row.get('applicability', {}).get('case_sensitive', False)))
             for row in glossary.get('non_translate', []) if row['source']]
    result = []
    for record in records:
        translated = record.get('translation') or ''
        if not re.search(r'[\u3400-\u9fff]', translated):
            continue
        fragments = sorted(set(re.findall(r'[A-Za-z]{2,}', translated)))
        if not fragments:
            continue
        rules = [dict(source=row['source'], applicability=row.get('applicability'))
                 for row, pattern in plans
                 if any(applicable(row, record['source'], m.start(), m.end())
                        for m in pattern.finditer(record['source']))]
        result.append(dict(id=record.get('id'), ordinal=record['ordinal'], source=record['source'],
                           translation=translated, latin_fragments=fragments,
                           matched_nontranslate_rules=rules, status='needs_semantic_review'))
    return result


def retirement_sources(exceptions, old_matches, new_matches):
    """Only retire a previously applicable exact source absent from new matches."""
    old = {m['src'] for m in old_matches};new = {m['src'] for m in new_matches}
    return sorted({e.get('source', e.get('src')) for e in exceptions
                   if isinstance(e, dict) and e.get('source', e.get('src')) in old-new})


def filter_retired_candidate(item, proof):
    # Caller verifies glossary + source/translation signatures. Raw replies and
    # attempt journals remain untouched; this is a derived validation view.
    retired = set(proof.get('sources', []))
    if not retired or not isinstance(item.get('exceptions', []), list):
        return item
    result = dict(item)
    result['exceptions'] = [e for e in item.get('exceptions', [])
                            if not isinstance(e, dict) or e.get('source', e.get('src')) not in retired]
    return result
