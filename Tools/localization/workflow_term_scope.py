"""Explicit glossary applicability. No semantic exclusions are inferred here."""
import re

AMBIGUOUS_WORDS = frozenset(('i', 'like', 'back', 'take', 'might', 'will', 'may',
                           'can', 'small', 'first', 'last', 'end', 'hold', 'word',
                           'whatever', 'voice', 'sound', 'image', 'dead', 'light',
                           'eye', 'heavy', 'the first', 'the one', 'he', 'she', 'it',
                           'you', 'we', 'they', 'me', 'us', 'him', 'her', 'them',
                           'my', 'our', 'your', 'his', 'its', 'their'))


def scope_risk(source):
    return (source.casefold() in AMBIGUOUS_WORDS
            or re.fullmatch(r'[A-Za-z]', source) is not None)


def validate_applicability(row, required=False):
    scope = row.get('applicability')
    if scope is None:
        if required and scope_risk(row['source']):
            raise ValueError('Ambiguous ordinary word needs explicit applicability or quarantine: ' + row['source'])
        return
    if (not isinstance(scope, dict)
            or set(scope) - {'mode', 'case_sensitive', 'prefix', 'suffix', 'reason'}
            or scope.get('mode') not in ('text', 'whole_record', 'literal_context')
            or type(scope.get('case_sensitive')) is not bool
            or not isinstance(scope.get('reason'), str) or not scope['reason'].strip()):
        raise ValueError('Applicability requires mode, case_sensitive and a specific reason')
    prefix, suffix = scope.get('prefix', ''), scope.get('suffix', '')
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise ValueError('Applicability context must be literal strings')
    if scope['mode'] == 'literal_context':
        if not prefix and not suffix:
            raise ValueError('Literal applicability context must not be empty')
    elif prefix or suffix:
        raise ValueError('Only literal_context may specify prefix/suffix')


def applicable(row, text, start, end):
    scope = row.get('applicability')
    if not scope:
        return True  # Existing frozen snapshots retain their original meaning.
    mode = scope['mode']
    if mode == 'whole_record':
        return not text[:start].strip() and not text[end:].strip()
    if mode == 'literal_context':
        return text[:start].endswith(scope.get('prefix', '')) and text[end:].startswith(scope.get('suffix', ''))
    return True
