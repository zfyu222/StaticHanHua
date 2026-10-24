"""Semantic term classes and enforcement, shared by both execution routes.

Legacy frozen snapshots remain strict. New extraction merges require an explicit
semantic decision before freezing; the program does not guess proper names.
"""
def validate_term_policy(row, table, required=False):
    level = row.get('enforcement')
    kind = row.get('term_kind')
    if level is None and kind is None and not required:
        return
    if table == 'non_translate':
        if level not in (None, 'required'):
            raise ValueError('Protected non_translate entries cannot be advisory')
        if required:
            from workflow_term_scope import scope_risk
            if scope_risk(row['source']) and row.get('applicability', {}).get('mode') not in ('whole_record', 'literal_context'):
                raise ValueError('Ordinary words cannot become global non_translate exclusions')
        return
    if level not in ('required', 'reference'):
        raise ValueError('Term enforcement must be required or reference')
    if kind not in ('proper_name', 'game_term'):
        raise ValueError('Ordinary words must be quarantined, not frozen as terms')
    if not isinstance(row.get('term_reason'), str) or not row['term_reason'].strip():
        raise ValueError('Term classification requires a source-based term_reason')
    from workflow_term_scope import scope_risk
    if scope_risk(row['source']):
        scope = row.get('applicability', {})
        if scope.get('mode') not in ('whole_record', 'literal_context'):
            raise ValueError('Ordinary word cannot be a global/reference term; quarantine or prove a scoped game meaning')
    if kind == 'proper_name' and level != 'required':
        raise ValueError('Confirmed proper names require consistent naming')


def term_required(term):
    level = term.get('enforcement', 'required')
    if level not in ('required', 'reference'):
        raise ValueError('Unknown frozen term enforcement')
    return level == 'required'


def hidden_marker_wrapper_issues(source, translation):
    """Catch added wrappers around known nonprinting gender metadata only.

    Visible values such as {price} must not be assumed invisible. Existing
    source wrappers may localize their punctuation, but cannot be multiplied.
    """
    import re
    for token in ('{Female}', '{Male}'):
        def count(text):
            return sum(len(re.findall(re.escape(a) + r'\s*' + re.escape(token)
                                      + r'\s*' + re.escape(b), text))
                       for a, b in (('(', ')'), ('（', '）'), ('[', ']'), ('【', '】')))
        if token in source and count(translation) > count(source):
            return ['hidden_marker_wrapper_added']
    return []
