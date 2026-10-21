"""Small, event-driven API profiles. No payloads, keys or background polling."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

_CURRENT = ContextVar('localization_profile', default=None)


def usage_totals(usages):
    totals = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0,
                  cached_prompt_tokens=0, reasoning_tokens=0,
                  reported_requests=0, unknown_requests=0)
    def number(value):
        return value if type(value) is int and value >= 0 else None
    for usage in usages:
        usage = usage if isinstance(usage, dict) else {}
        prompt = number(usage.get('prompt_tokens', usage.get('input_tokens')))
        completion = number(usage.get('completion_tokens', usage.get('output_tokens')))
        total = number(usage.get('total_tokens'))
        known = usage.get('usage_reported') is not False and (total is not None or (prompt is not None and completion is not None))
        totals['reported_requests' if known else 'unknown_requests'] += 1
        totals['prompt_tokens'] += prompt or 0
        totals['completion_tokens'] += completion or 0
        totals['total_tokens'] += total if total is not None else (prompt or 0) + (completion or 0)
        details = usage.get('prompt_tokens_details') or {}
        output_details = usage.get('completion_tokens_details') or {}
        totals['cached_prompt_tokens'] += (number(details.get('cached_tokens')) or 0) if isinstance(details, dict) else 0
        totals['reasoning_tokens'] += (number(output_details.get('reasoning_tokens')) or 0) if isinstance(output_details, dict) else 0
    # Cached/reasoning tokens are subsets, never added again to total_tokens.
    return totals


def profiled(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            profile = _CURRENT.get()
            if profile is None:
                return function(*args, **kwargs)
            with profile.measure(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def progress(phase, completed, total, **fields):
    profile = _CURRENT.get()
    if profile is not None:
        profile.progress(phase, completed, total, **fields)


def increment(name):
    profile = _CURRENT.get()
    if profile is not None:
        with profile.lock:
            profile.counters[name] = profile.counters.get(name, 0) + 1


@contextmanager
def measure(name):
    profile = _CURRENT.get()
    if profile is None:
        yield
    else:
        with profile.measure(name):
            yield


class RunProfile:
    def __init__(self, root, stage):
        identity = os.environ.get('SHH_API_PROFILE_ID', '')
        self.run_id = identity if re.fullmatch(r'[0-9a-f]{32}', identity) else uuid.uuid4().hex
        self.stage = stage
        self.started = time.perf_counter()
        self.first_request_seconds = None
        self.timings, self.usages, self.checkpoints, self.counters = {}, [], {}, {}
        self.requests_started = 0
        self.phase_starts, self.phases = {}, {}
        self.lock = threading.RLock()
        root = Path(root)
        self.log_path = root / 'logs/api_runs' / (self.run_id + '.profile.jsonl')
        self.summary_path = root / 'reports/api_runs' / (self.run_id + '.profile.json')
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        self.emit('run_started')

    @contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    @contextmanager
    def measure(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.duration(name, time.perf_counter() - start)

    def duration(self, name, seconds):
        with self.lock:
            timing = self.timings.setdefault(name, dict(count=0, sum_seconds=0.0, max_seconds=0.0))
            timing['count'] += 1
            timing['sum_seconds'] += seconds
            timing['max_seconds'] = max(timing['max_seconds'], seconds)

    def emit(self, event, **fields):
        with self.lock:
            if event == 'phase_started':
                self.phase_starts[fields['phase']] = time.perf_counter()
            elif event == 'phase_finished' and fields['phase'] in self.phase_starts:
                seconds = round(time.perf_counter() - self.phase_starts.pop(fields['phase']), 4)
                self.phases[fields['phase']] = round(self.phases.get(fields['phase'], 0) + seconds, 4)
                fields['phase_seconds'] = seconds
            item = dict(time=datetime.now(timezone.utc).isoformat(), run_id=self.run_id,
                        stage=self.stage, event=event,
                        elapsed_seconds=round(time.perf_counter() - self.started, 4), **fields)
            with self.log_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(item, ensure_ascii=False) + '\n')
            if event in ('run_started', 'phase_started', 'phase_finished', 'progress', 'cycle_started', 'cycle_checked', 'cycle_stopped'):
                print('[api-profile] ' + json.dumps(item, ensure_ascii=False), file=sys.stderr, flush=True)

    def progress(self, phase, completed, total, force=False, **fields):
        with self.lock:
            now = time.perf_counter()
            previous, last = self.checkpoints.get(phase, (0, self.started))
            if not force and completed < total and completed - previous < 10 and now - last < 30:
                return
            if not force and completed == previous and phase in self.checkpoints and now - last < 30:
                return
            self.checkpoints[phase] = (completed, now)
            self.emit('progress', phase=phase, completed=completed, total=total,
                      percent=round(100 * completed / total, 2) if total else 100.0,
                      run_tokens=usage_totals(self.usages), **fields)

    def request_started(self, call_id):
        with self.lock:
            self.requests_started += 1
            if self.first_request_seconds is None:
                self.first_request_seconds = round(time.perf_counter() - self.started, 4)
            self.emit('request_started', call_id=call_id)

    def response(self, call_id, seconds, usage, status):
        with self.lock:
            self.usages.append(usage)
            self.emit('request_finished', call_id=call_id, request_seconds=round(seconds, 4),
                      status=status, tokens=usage_totals([usage]),
                      finish_reason=usage.get('finish_reason') if isinstance(usage, dict) else None,
                      response_model=usage.get('response_model') if isinstance(usage, dict) else None)

    def finish(self, status, summary=None, error_type=None):
        summary = summary or {}
        with self.lock:
            result = dict(run_id=self.run_id, stage=self.stage, status=status,
                          elapsed_seconds=round(time.perf_counter() - self.started, 4),
                          first_request_seconds=self.first_request_seconds,
                          requests_started=self.requests_started,
                          accepted_batches=summary.get('accepted', 0), failed_attempts=summary.get('failed', 0),
                          peak_in_flight=summary.get('peak_in_flight', 0),
                          total_batches=summary.get('total_batches'), completed_before_run=summary.get('completed_before_run'),
                          recovered_batches=summary.get('recovered_batches', 0),
                          records_saved_this_run=summary.get('records_saved_this_run', 0),
                          api_cycles=summary.get('api_rounds', []), stage_clean=summary.get('stage_clean'),
                          phase_seconds={**self.phases, **{name: round(self.phases.get(name, 0) + time.perf_counter() - start, 4)
                                                         for name, start in self.phase_starts.items()}},
                          unfinished_phases=list(self.phase_starts),
                          run_tokens=usage_totals(self.usages), project_tokens=summary.get('project_tokens'),
                          counters=dict(self.counters),
                          timings={name: {k: round(v, 6) if isinstance(v, float) else v for k, v in values.items()}
                                   for name, values in self.timings.items()},
                          timing_note='Nested spans overlap; concurrent request sums may exceed elapsed wall time.',
                          profile_log=str(self.log_path), profile_summary=str(self.summary_path))
            if error_type:
                result['error_type'] = error_type
            temporary = self.summary_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, self.summary_path)
            self.emit('run_finished', **{k: v for k, v in result.items() if k not in ('run_id', 'stage', 'elapsed_seconds')})
            tokens = result['run_tokens']
            first = 'none' if result['first_request_seconds'] is None else f"{result['first_request_seconds']:.2f}s"
            project_total = result['project_tokens']['total_tokens'] if result['project_tokens'] else 'unknown'
            print(f"[api-summary] status={status} elapsed={result['elapsed_seconds']:.2f}s "
                  f"first_request={first} accepted_batches={result['accepted_batches']} "
                  f"failed_attempts={result['failed_attempts']} requests={result['requests_started']} "
                  f"cycles={len(result['api_cycles'])} stage_clean={result['stage_clean']} "
                  f"input_tokens={tokens['prompt_tokens']} output_tokens={tokens['completion_tokens']} "
                  f"total_tokens={tokens['total_tokens']} unknown_usage_requests={tokens['unknown_requests']} "
                  f"project_total_tokens={project_total}",
                  file=sys.stderr, flush=True)
            return result
