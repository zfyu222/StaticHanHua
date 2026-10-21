"""AINiee-compatible o200k_base counting and stage-specific batch weights."""
import json
import sys
from contextlib import redirect_stdout
from functools import lru_cache
import ainiee_bridge  # Adds the pinned AINiee checkout to sys.path.
from workflow_profile import profiled


@lru_cache(maxsize=1)
def _cache_item():
    from ModuleFolders.Infrastructure.Tokener.TiktokenLoader import initialize_tiktoken
    with redirect_stdout(sys.stderr):
        initialize_tiktoken()  # Seed bundled data before tiktoken can fetch it.
    from ModuleFolders.Service.Cache.CacheItem import CacheItem
    return CacheItem


@profiled('token_count')
def count_tokens(text):
    return _cache_item().get_token_count(str(text or ''))


def record_limit(config, stage):
    stage = {'pilot_translate': 'translate', 'pilot_review': 'review'}.get(stage, stage)
    field = {'extract': 'extract_batch_records', 'glossary_review': 'glossary_review_batch_records',
             'review': 'review_batch_records', 'final_review': 'review_batch_records'}.get(stage)
    limits = [config.get('batch_records')]
    if field:
        limits.append(config.get(field))
    values = [value for value in limits if value is not None]
    return min(values) if values else None


def batch_limit(config, stage):
    field = ('extract_batch_tokens' if stage == 'extract' else
             'glossary_review_batch_tokens' if stage == 'glossary_review' else
             'review_batch_tokens' if stage in ('review', 'pilot_review', 'final_review') else 'batch_tokens')
    return config.get(field, 10000 if stage in ('extract', 'glossary_review') else 1024)


def record_tokens(row, stage):
    if stage == 'glossary_review':
        return count_tokens(json.dumps(row, ensure_ascii=False))
    if stage in ('review', 'pilot_review', 'final_review'):
        return max(1, count_tokens(json.dumps({'problem': row.get('diagnostic', ''),
            'source': row.get('source', ''), 'current_translation': row.get('translation', '')}, ensure_ascii=False)))
    # AINiee translation/polish/extraction all split on source-text tokens.
    return count_tokens(row.get('source', ''))
