"""Bounded, local English family hints; never merge or rewrite translations."""
import re
from collections import defaultdict


def tokens(source):
    words = re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", source.casefold())
    if words and words[0] in {'the', 'a', 'an'}:
        words = words[1:]
    return tuple(w.removesuffix("'s") for w in words)


def singular(word):
    irregular = {'children': 'child', 'people': 'person', 'men': 'man',
                 'women': 'woman', 'mice': 'mouse', 'feet': 'foot', 'teeth': 'tooth'}
    if word in irregular:
        return irregular[word]
    if word.endswith('ies') and len(word) > 4:
        return word[:-3] + 'y'
    if word.endswith(('ches', 'shes', 'xes', 'zes', 'sses')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def stem(word):
    # Conservative candidate roots, not linguistic equivalence. E.g. alchem-y/ist/ical.
    for suffix in ('ical', 'ists', 'ist', 'ism', 'ing', 'ers', 'er', 'ed', 'ic', 'y'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 5:
            return word[:-len(suffix)]
    return word


def family_plan(entries, batch_size):
    """Return complete bounded batches and sparse relationship hints (no model calls)."""
    buckets = defaultdict(list)
    for i, entry in enumerate(entries):
        source = entry['row']['source']
        # Do not partially tokenize non-English names into spurious English families.
        if re.search(r'[^\x00-\x7f]', source):
            continue
        words = tokens(source)
        if not words:
            continue
        normalized = tuple(singular(w) for w in words)
        buckets[('variant', normalized)].append(i)
        buckets[('root', tuple(stem(w) for w in normalized))].append(i)
        # Whole-word shared components, never arbitrary spelling similarity.
        for word in set(normalized):
            if len(word) >= 5:
                buckets[('component', word)].append(i)
    edges, seen = [], set()
    for (kind, _), members in buckets.items():
        if len(members) < 2:
            continue
        anchor = members[0]
        for member in members[1:]:
            pair = (anchor, member)
            if pair not in seen:
                seen.add(pair)
                edges.append((anchor, member, kind))
    # Connected families stay together when they fit. Large families split at the cap.
    parents = list(range(len(entries)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for a, b, _ in edges:
        parents[find(b)] = find(a)
    families = defaultdict(list)
    for i in range(len(entries)):
        families[find(i)].append(entries[i])
    batches, current = [], []
    for family in families.values():
        if current and len(current) + len(family) > batch_size:
            batches.append(current)
            current = []
        for start in range(0, len(family), batch_size):
            part = family[start:start + batch_size]
            if len(part) == batch_size:
                batches.append(part)
            else:
                current.extend(part)
    if current or not batches:
        batches.append(current)
    hints = [{'refs': [entries[a]['ref'], entries[b]['ref']], 'kind': kind}
             for a, b, kind in edges]
    return batches, hints


def batch_hints(batch, entries, hints, extra_limit=12, hint_limit=120):
    assigned = {e['ref'] for e in batch}
    by_ref = {e['ref']: e for e in entries}
    selected, extras = [], {}
    for hint in hints:
        refs = set(hint['refs'])
        if not refs.intersection(assigned):
            continue
        outside = refs - assigned
        if len(set(extras) | outside) > extra_limit:
            continue
        selected.append(hint)
        for ref in outside:
            e = by_ref[ref]
            extras[ref] = {'ref': ref, 'source': e['row']['source'],
                           'translation': e['row'].get('recommended_translation', '保留原文')}
        if len(selected) >= hint_limit:
            break
    return {'candidates': selected, 'related_terms': list(extras.values()),
            'candidate_only': True}
