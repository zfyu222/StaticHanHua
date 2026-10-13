"""Headless adapters for the pinned, locally patched AINiee checkout.

No global AINiee configuration is read or written. Run with its Python venv.
Imported AINiee code retains its own AGPL-3.0 license (Tools/AINiee/LICENSE).
"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
AINIEE = ROOT / 'Tools' / 'AINiee'
BASELINE = 'c50570c1dee7df7bd51316a253f0b42d10a8ca4c'
if str(AINIEE) not in sys.path:
    sys.path.insert(0, str(AINIEE))


def load_script(name):
    path = ROOT / '.agents/skills/game-translate/scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location('shh_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def helper():
    from ModuleFolders.Domain.PromptBuilder.GlossaryHelper import GlossaryHelper
    return GlossaryHelper


def provenance():
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=AINIEE,
                            capture_output=True, text=True, check=True)
    paths = ['Domain/PromptBuilder/GlossaryHelper.py', 'Domain/TextProcessor/TextProcessor.py',
             'Infrastructure/LLMRequester/LLMClientFactory.py', 'Infrastructure/LLMRequester/OpenaiRequester.py']
    return {'expected_commit': BASELINE, 'actual_commit': result.stdout.strip(),
            'modules': {p: hashlib.sha256((AINIEE / 'ModuleFolders' / p).read_bytes()).hexdigest()
                        for p in paths}, 'license': 'AGPL-3.0; Tools/AINiee/LICENSE'}


def glossary_rows(analysis):
    rows = []
    for table in ('characters', 'terms', 'non_translate'):
        for index, row in enumerate(analysis.get(table, [])):
            rows.append({**row, 'ref': f'{table}:{index}', 'src': row['source'],
                         'dst': row.get('recommended_translation', '保留原文'),
                         'src_state': row.get('src_state', 'valid')})
    return rows


def matched_terms(analysis, texts, options):
    h = helper()
    rows = glossary_rows(analysis)
    # Reuse the patched matcher: literal brackets, term families, quotes, overlap priority.
    matched = h.collect_matched_rows(rows, {str(i): t for i, t in enumerate(texts)},
                                    case_sensitive=options['case_sensitive'],
                                    whole_word=options['whole_word'])
    # Keep-original entries still convey useful instructions, but are not term-check errors.
    for row in rows:
        if h.is_keep_original_translation(row['dst']) and any(
                h.source_matches_text(row['src'], t, row['src_state'],
                                      options['case_sensitive'], options['whole_word']) for t in texts):
            matched.append(row)
    return matched


def patterns(named_curly=True, exclusions=(), family_overrides=None):
    module = load_script('analyze_placeholders')
    overrides = family_overrides or {}
    names = {name for name, _, _ in module.FAMILIES}
    if not isinstance(overrides, dict) or any(name not in names for name in overrides):
        raise ValueError('Unknown placeholder family override')
    result = []
    for name, pattern, _ in module.FAMILIES:
        if name == 'curly_named' and not named_curly:
            continue
        if name in overrides:
            expression = overrides[name]
            if expression is None:
                continue
            if not isinstance(expression, str) or not expression:
                raise ValueError('Placeholder family override must be a non-empty regex or null')
            pattern = re.compile(expression)
        result.append((name, pattern))
    for index, item in enumerate(exclusions):
        expression = item.get('regex') or re.escape(item['markers'])
        result.append((f'exclusion_{index}', re.compile(expression)))
    return result


def protect(text, record_id, rules):
    # The upstream protector has a batch-wide 50-marker cap and fixed [P<n>] names.
    # Reuse its patterns/restorer, with unbounded, non-colliding task-local identities.
    candidates = sorted({(m.start(), m.end()) for _, p in rules for m in p.finditer(text)
                         if m.start() != m.end()}, key=lambda s: (-(s[1] - s[0]), s[0]))
    selected = []
    for start, end in candidates:
        if not any(start < b and end > a for a, b in selected):
            selected.append((start, end))
    selected.sort()
    namespace = 'SHH_' + hashlib.sha256((record_id + text).encode()).hexdigest()[:12]
    while namespace in text:
        namespace += '_'
    parts, mapping, pos = [], [], 0
    for index, (start, end) in enumerate(selected):
        token = f'⟪{namespace}_{index}⟫'
        parts.extend([text[pos:start], token])
        mapping.append({'placeholder': token, 'original': text[start:end]})
        pos = end
    parts.append(text[pos:])
    return ''.join(parts), mapping


def restore(text, mapping):
    for item in mapping:
        if text.count(item['placeholder']) != 1:
            raise ValueError('Protected token missing or repeated: ' + item['placeholder'])
    from ModuleFolders.Domain.TextProcessor.TextProcessor import TextProcessor
    # This method is stateless; no config/resource initialization is needed.
    processor = TextProcessor.__new__(TextProcessor)
    restored = processor._restore_special_placeholders({'0': text}, {'0': mapping})['0']
    if re.search(r'⟪SHH_[^⟫]*⟫', restored):
        raise ValueError('Unrestored or fabricated protected token')
    return restored


def structural_issues(source, translation, rules):
    issues = []
    if source.strip() and not translation.strip():
        issues.append('empty_translation')
    if not source.strip() and translation.strip():
        issues.append('invented_translation_for_blank_source')
    for name, pattern in rules:
        # finditer.group(0), not findall: captures in format patterns are not the full tag.
        if Counter(m.group(0) for m in pattern.finditer(source)) != Counter(
                m.group(0) for m in pattern.finditer(translation)):
            issues.append(name)
    if re.findall(r'\r\n|\r|\n', source) != re.findall(r'\r\n|\r|\n', translation):
        issues.append('physical_newlines')
    return issues


class ApiFailure(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.retryable = retryable


class AINieeAPI:
    """Reuse patched client factory, provider parameters and SSE parsing.

    First supported transport: OpenAI-compatible Chat Completions (including
    DeepSeek/custom gateways). Native provider adapters can be added separately.
    SDK retries disabled: the workflow owns bounded retries and request accounting.
    """
    def __init__(self, settings, key):
        from ModuleFolders.Infrastructure.LLMRequester.LLMClientFactory import LLMClientFactory
        from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester
        self.settings = {**settings, 'api_key': key}
        self.factory = LLMClientFactory()
        self.client = self.factory.get_openai_client(self.settings).with_options(max_retries=0)
        self.parser = OpenaiRequester()

    def request(self, system, user):
        settings = self.settings
        params = {'model': settings['model_name'], 'messages': [
            {'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'stream': False, 'timeout': settings.get('request_timeout', 120),
            'extra_body': settings.get('extra_body', {})}
        if settings.get('temperature', 1) != 1:
            params['temperature'] = settings['temperature']
        if settings.get('max_completion_tokens'):
            params['max_completion_tokens'] = settings['max_completion_tokens']
        params = self.parser.apply_platform_thinking_params(params, settings)
        try:
            raw = self.client.chat.completions.with_raw_response.create(**params)
            try:
                response = raw.parse()
                content = response.choices[0].message.content
                usage = response.usage
                tokens = (usage.prompt_tokens, usage.completion_tokens) if usage else (0, 0)
            except (ValueError, AttributeError, IndexError, TypeError):
                _, content, prompt, completion = self.parser._parse_sse_response(raw.text)
                tokens = (prompt, completion)
            if not content:
                raise ApiFailure('empty_response', True)
            return content, {'prompt_tokens': tokens[0], 'completion_tokens': tokens[1]}
        except ApiFailure:
            raise
        except Exception as error:
            # Do not retain messages, URLs, headers or exception strings containing secrets.
            status = getattr(error, 'status_code', None)
            retryable = status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
            retryable = retryable or type(error).__name__ in ('APITimeoutError', 'APIConnectionError')
            raise ApiFailure(f'{type(error).__name__}:{status or "transport"}', retryable) from None

    def close(self):
        self.factory.close_all_clients()


def parse_response(content):
    # Strict ID-based JSON protocol; never infer IDs from numbered prose.
    value = content.strip()
    if value.startswith('```') and value.endswith('```'):
        value = '\n'.join(value.splitlines()[1:-1])
    return json.loads(value)
