"""Deterministic layout restoration; never insert/reorder paragraphs or verdicts."""
import copy
import hashlib
import re


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def normalize_body_item(item, original, source_row, config, policy):
    value = copy.deepcopy(item)
    text = value.get('translation')
    source = source_row['source']
    if (policy != 'exact_v1' or not isinstance(text, str)
            or not source.strip() or not text.strip() or source_row['locked']):
        return value, None
    checks = value.get('checks', {})
    marker_check = checks.get('placeholders', {}) if isinstance(checks, dict) else {}
    marker_status = marker_check[0] if isinstance(marker_check, list) and marker_check else (
        marker_check.get('status') if isinstance(marker_check, dict) else None)
    changes = value.get('changes', [])
    if (marker_status == 'corrected' or not isinstance(changes, list)
            or any(isinstance(c, dict) and c.get('category') == 'placeholders' for c in changes)):
        return value, None  # Do not contradict the model's explicit format judgment.
    constraint = config.get('record_constraints', {}).get(source_row['id'], {})
    if constraint and constraint.get('source_sha256') != digest(source):
        raise ValueError('Layout constraint source hash mismatch')
    expected = constraint.get('physical_newlines', re.findall(r'\r\n|\r|\n', source))
    actual = re.findall(r'\r\n|\r|\n', text)
    if len(actual) != len(expected):
        return value, None  # A missing/added paragraph needs an actual review.
    separators = iter(expected)
    fixed = re.sub(r'\r\n|\r|\n', lambda _: next(separators), text)
    leading = source[:len(source) - len(source.lstrip(' \t'))]
    trailing = source[len(source.rstrip(' \t')):]
    fixed = leading + fixed.strip(' \t') + trailing
    if fixed == text:
        return value, None
    before = original.get('translation')
    if changes and fixed == before:
        return value, None  # Never erase a model's explicit claimed correction.
    for change in changes:
        if not isinstance(change, dict) or change.get('after') != text:
            return value, None
        change['after'] = fixed
    value['translation'] = fixed
    audit = {'policy': policy, 'source_sha256': digest(source),
             'before_sha256': digest(text), 'after_sha256': digest(fixed),
             'newline_count': len(expected), 'paragraphs_reordered': False,
             'semantic_checks_created': False}
    return value, audit
