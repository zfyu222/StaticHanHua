"""Expand model-written record-local evidence without creating judgments."""
import copy
import re


def expand_evidence(item):
    if 'evidence' not in item:
        return copy.deepcopy(item)
    value = copy.deepcopy(item)
    evidence = value.pop('evidence')
    if (not isinstance(evidence, dict) or not evidence or len(evidence) > 32
            or any(not isinstance(k, str) or not re.fullmatch(r'e[1-9][0-9]*', k)
                   or not isinstance(v, str) or not v.strip() for k, v in evidence.items())):
        raise ValueError('Evidence table requires record-local eN keys and actual nonempty text')
    used = set()
    def resolve(field):
        if isinstance(field, dict):
            if (set(field) != {'ref'} or not isinstance(field['ref'], str)
                    or field['ref'] not in evidence):
                raise ValueError('Unknown or malformed record-local evidence reference')
            used.add(field['ref'])
            return evidence[field['ref']]
        return field
    if 'reason' in value:
        value['reason'] = resolve(value['reason'])
    checks = value.get('checks')
    if isinstance(checks, dict):
        for key, check in checks.items():
            if isinstance(check, list) and len(check) == 2:
                checks[key] = [check[0], resolve(check[1])]
            elif isinstance(check, dict) and 'evidence' in check:
                check['evidence'] = resolve(check['evidence'])
    changes = value.get('changes')
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict) and 'reason' in change:
                change['reason'] = resolve(change['reason'])
    if set(evidence) != used:
        raise ValueError('Evidence table must contain only explicitly used evidence')
    return value
