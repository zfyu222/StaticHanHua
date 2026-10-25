"""Coordinated provider failover. One execute() is exactly one accounted wire call."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

import ainiee_bridge as bridge
from workflow_api_budget import service_failure

DEFAULT_POLICY = {'enabled': False, 'priority': [], 'failure_threshold': 5, 'cooldown_seconds': 60}


def validate_policy(value, names):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_POLICY):
        raise ValueError('api_failover fields: enabled, priority, failure_threshold, cooldown_seconds')
    result = {**DEFAULT_POLICY, **value}
    priority = result['priority']
    if type(result['enabled']) is not bool:
        raise ValueError('api_failover.enabled must be boolean')
    if (not isinstance(priority, list) or any(not isinstance(n, str) or n not in names for n in priority)
            or len(set(priority)) != len(priority) or (result['enabled'] and not priority)):
        raise ValueError('api_failover.priority must contain unique configured API names')
    if type(result['failure_threshold']) is not int or not 1 <= result['failure_threshold'] <= 20:
        raise ValueError('api_failover.failure_threshold must be an integer in 1..20')
    if type(result['cooldown_seconds']) not in (int, float) or not 0 < result['cooldown_seconds'] <= 86400:
        raise ValueError('api_failover.cooldown_seconds must be positive and at most 86400')
    return result


def fault_kind(code):
    if code.endswith(':transport') or code in ('stream_interrupted', 'incomplete_stream'):
        return 'connection'
    if code == 'APIStatusError:transport_initialization_failed': return 'invalid_endpoint'
    if code in ('empty_response', 'unexpected_html_response'):
        return 'empty_response' if code == 'empty_response' else 'invalid_endpoint'
    match = re.search(r':(\d{3})$', code)
    status = int(match[1]) if match else None
    if status in (401, 403): return 'authorization'
    if status == 404: return 'invalid_endpoint'
    if status == 429: return 'rate_limit_or_quota'
    if status in (408, 409) or status is not None and status >= 500: return 'transient_http'
    if status is not None and 400 <= status < 500: return 'request_http'
    return None  # Model/contract failures and local budget/control errors are not API faults.


def _number(value):
    return value if type(value) is int and value >= 0 else None


class ProviderAudit:
    """Append-only attempts/transitions plus a regenerable, provider-level summary."""
    def __init__(self, root, stage):
        self.run_id = uuid.uuid4().hex
        self.stage = stage
        self.log = Path(root) / 'logs/api_failover' / (self.run_id + '.jsonl')
        self.report = Path(root) / 'reports/api_failover' / (self.run_id + '.json')
        self.lock = threading.RLock(); self.attempts = {}; self.transitions = []
        self.state_changes = []; self.provider_states = {}
        self.in_flight = {}; self.peaks = {}
        self.started_at = time.time()

    def event(self, event, **fields):
        row = {'event': event, 'run_id': self.run_id, 'stage': self.stage,
               'time_utc': datetime.now(timezone.utc).isoformat(), **fields}
        with self.lock:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open('a', encoding='utf8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row

    def start(self, call_id, spec, **context):
        settings = spec['settings']
        with self.lock:
            name = spec['name']; self.in_flight[name] = self.in_flight.get(name, 0) + 1
            self.peaks[name] = max(self.peaks.get(name, 0), self.in_flight[name])
            row = self.event('request_started', call_id=str(call_id), api=name,
                requested_model=settings['model_name'], endpoint_host=urlsplit(settings['api_url']).hostname,
                concurrency_limit=settings['concurrency'], thinking=settings.get('think_switch'),
                provider_in_flight=self.in_flight[name], **context)
            self.attempts[str(call_id)] = row

    def finish(self, call_id, status, usage, elapsed):
        row = self.event('request_finished', call_id=str(call_id), status=status,
            fault_kind=fault_kind(status), elapsed_seconds=round(elapsed, 6), usage=usage)
        with self.lock:
            self.attempts[str(call_id)].update(row)
            name = self.attempts[str(call_id)]['api']
            self.in_flight[name] = max(0, self.in_flight.get(name, 1) - 1)

    def outcome(self, call_id, status):
        self.event('collection_outcome', call_id=str(call_id), status=status)
        with self.lock:
            if str(call_id) in self.attempts: self.attempts[str(call_id)]['collection_status'] = status

    def transition(self, **fields):
        row = self.event('provider_transition', **fields)
        with self.lock: self.transitions.append(row)
        self.save()

    def state_changed(self, name, before, after, **fields):
        row = self.event('provider_state_changed', api=name, from_state=before, to_state=after, **fields)
        with self.lock:
            self.state_changes.append(row); self.provider_states[name] = after
        self.save()

    def summary(self):
        with self.lock:
            providers = {}
            cancelled_without_wire = 0
            for row in self.attempts.values():
                if (row.get('usage') or {}).get('request_issued') is False:
                    cancelled_without_wire += 1; continue
                p = providers.setdefault(row['api'], {'requests': 0, 'responses_received': 0,
                    'requests_completed': 0, 'unfinished_requests': 0, 'peak_in_flight': 0,
                    'service_failures': 0, 'other_transport_failures': 0, 'model_or_contract_rejections': 0,
                    'results_collected': 0, 'partial_results': 0,
                    'usage_reported_requests': 0, 'usage_unknown_requests': 0,
                    'latency_seconds_sum': 0.0, 'latency_seconds_max': 0.0,
                    'reported_tokens': {k: None for k in ('input', 'output', 'total', 'cached_input', 'reasoning')},
                    'token_field_coverage': {k: 0 for k in ('input', 'output', 'total', 'cached_input', 'reasoning')},
                    'errors': {}, 'requested_models': [], 'response_models': []})
                p['requests'] += 1
                status = row.get('status')
                p['requests_completed' if status is not None else 'unfinished_requests'] += 1
                p['peak_in_flight'] = max(p['peak_in_flight'], row.get('provider_in_flight', 0))
                if status == 'received': p['responses_received'] += 1
                elif status is not None:
                    p['service_failures' if service_failure(status) else 'other_transport_failures'] += 1
                    p['errors'][status] = p['errors'].get(status, 0) + 1
                if row.get('collection_status') in ('invalid_model_result', 'rejected', 'rejected_length_terminated_response'):
                    p['model_or_contract_rejections'] += 1
                if row.get('collection_status') in ('submitted', 'saved'): p['results_collected'] += 1
                if row.get('collection_status') in ('repair_required', 'repair_part_saved', 'partial_result'): p['partial_results'] += 1
                elapsed = row.get('elapsed_seconds', 0)
                p['latency_seconds_sum'] += elapsed
                p['latency_seconds_max'] = max(p['latency_seconds_max'], elapsed)
                usage = row.get('usage') or {}
                known = usage.get('usage_reported') is not False and any(_number(usage.get(k)) is not None
                    for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens'))
                p['usage_reported_requests' if known else 'usage_unknown_requests'] += 1
                details = usage.get('prompt_tokens_details') or {}; output = usage.get('completion_tokens_details') or {}
                values = {'input': usage.get('prompt_tokens', usage.get('input_tokens')),
                    'output': usage.get('completion_tokens', usage.get('output_tokens')), 'total': usage.get('total_tokens'),
                    'cached_input': details.get('cached_tokens') if isinstance(details, dict) else None,
                    'reasoning': output.get('reasoning_tokens') if isinstance(output, dict) else None}
                for k, value in values.items():
                    if known and _number(value) is not None:
                        p['reported_tokens'][k] = (p['reported_tokens'][k] or 0) + value
                        p['token_field_coverage'][k] += 1
                for field, model in (('requested_models', row.get('requested_model')), ('response_models', usage.get('response_model'))):
                    if model and model not in p[field]: p[field].append(model)
            for p in providers.values():
                p['response_success_rate'] = p['responses_received'] / p['requests_completed'] if p['requests_completed'] else None
                p['latency_seconds_mean'] = p['latency_seconds_sum'] / p['requests_completed'] if p['requests_completed'] else None
            return {'run_id': self.run_id, 'stage': self.stage, 'providers': providers,
                'configuration': getattr(self, 'configuration', None),
                'cancelled_without_wire': cancelled_without_wire,
                'transitions': list(self.transitions), 'log_file': str(self.log),
                'state_changes': list(getattr(self, 'state_changes', [])),
                'provider_states': dict(getattr(self, 'provider_states', {})),
                'elapsed_seconds': round(time.time() - self.started_at, 3),
                'stability_note': 'Response success measures transport availability, not translation/semantic accuracy; collection failures are separate.',
                'token_note': 'Only server-reported fields summed. Missing remains null/unknown. Cached/reasoning are subsets, not extra totals.'}

    def save(self):
        with self.lock:
            self.report.parent.mkdir(parents=True, exist_ok=True)
            temp = self.report.with_suffix('.tmp')
            temp.write_text(json.dumps(self.summary(), ensure_ascii=False, indent=2), encoding='utf8')
            temp.replace(self.report)


@dataclass
class Lease:
    index: int
    generation: int
    probe: bool
    spec: dict
    released: bool = False


@dataclass
class ProviderState:
    state: str = 'normal'
    generation: int = 0
    consecutive_failures: int = 0
    retry_at: float = 0


class ProviderPool:
    def __init__(self, specs, policy, root, stage, requester_factory=None):
        self.specs = specs; self.policy = validate_policy(policy, {s['name'] for s in specs})
        self.managed = self.policy['enabled']
        self.factory = requester_factory or bridge.AINieeAPI
        self.audit = ProviderAudit(root, stage)
        self.cv = threading.Condition(threading.RLock()); self.client_lock = threading.Lock()
        self.clients = {}; self.states = [ProviderState() for _ in specs]
        self.last_selected = None; self.closed = False; self.clock = time.monotonic
        self.active = [0] * len(specs); self.times = [deque() for _ in specs]
        self.max_workers = max(s['settings']['concurrency'] for s in specs)
        self.audit.configuration = {'enabled': self.managed, 'priority': [s['name'] for s in specs],
            'failure_threshold': self.policy['failure_threshold'], 'cooldown_seconds': self.policy['cooldown_seconds'],
            'providers': [{'api': s['name'], 'endpoint_host': urlsplit(s['settings']['api_url']).hostname,
                **{k: s['settings'].get(k) for k in ('model_name','concurrency','requests_per_minute',
                    'think_switch','request_timeout','max_completion_tokens','max_requests')}} for s in specs]}
        self.audit.event('routing_configured', configuration=self.audit.configuration)
        self.audit.provider_states = {s['name']: 'normal' for s in specs}

    @property
    def exhausted(self):
        # All temporarily disabled providers are recoverable, not exhausted.
        with self.cv: return self.closed

    def change_state(self, index, state, reason):
        record = self.states[index]; before = record.state; record.state = state
        self.audit.state_changed(self.specs[index]['name'], before, state, reason=reason,
            consecutive_failures=record.consecutive_failures,
            cooldown_seconds=self.policy['cooldown_seconds'] if state == 'disabled' else None)

    def refresh(self, now):
        for i, record in enumerate(self.states):
            if record.state == 'disabled' and now >= record.retry_at:
                self.change_state(i, 'retry_ready', 'cooldown_elapsed')

    def state_snapshot(self):
        with self.cv:
            self.refresh(self.clock())
            return {spec['name']: record.state for spec, record in zip(self.specs, self.states)}

    def acquire(self, cancelled=lambda: False):
        with self.cv:
            while True:
                if cancelled(): raise bridge.ApiFailure('APIStatusError:dispatch_stopped', False, {'usage_reported': False, 'request_issued': False})
                if self.closed:
                    raise bridge.ApiFailure('APIStatusError:dispatch_stopped', False, {'usage_reported': False, 'request_issued': False})
                now = self.clock(); self.refresh(now)
                for i, record in enumerate(self.states):
                    if record.state not in ('normal', 'retry_ready'): continue
                    s = self.specs[i]['settings']; q = self.times[i]
                    while q and q[0] <= now - 60: q.popleft()
                    rate = s.get('requests_per_minute', 0)
                    # A healthy cheap provider being busy/rate-limited does not
                    # by itself authorize raising the API price. Wait for it.
                    if self.active[i] >= s['concurrency'] or rate and len(q) >= rate: break
                    probe = record.state == 'retry_ready'
                    if probe:
                        record.generation += 1
                        self.change_state(i, 'awaiting_retry', 'single_probe_dispatched')
                    self.active[i] += 1
                    if rate: q.append(now)
                    if self.last_selected is not None and self.last_selected != i:
                        self.audit.transition(from_api=self.specs[self.last_selected]['name'],
                            to_api=self.specs[i]['name'], reason='recovery_probe' if probe else 'priority_selection')
                    self.last_selected = i
                    return Lease(i, record.generation, probe, self.specs[i])
                self.cv.wait(.1)

    def release(self, lease, error=None, success=False):
        with self.cv:
            if lease.released: return
            lease.released = True; self.active[lease.index] -= 1
            record = self.states[lease.index]
            current = lease.generation == record.generation
            kind = fault_kind(error or '')
            if current and self.managed and kind:
                record.consecutive_failures += 1
                if lease.probe or record.consecutive_failures >= self.policy['failure_threshold']:
                    record.generation += 1
                    record.retry_at = self.clock() + self.policy['cooldown_seconds']
                    self.change_state(lease.index, 'disabled', error)
                else:
                    self.audit.event('consecutive_failure', api=lease.spec['name'],
                        consecutive_failures=record.consecutive_failures, threshold=self.policy['failure_threshold'], reason=error)
            elif current and lease.probe:
                record.generation += 1
                if success:
                    record.consecutive_failures = 0
                    self.change_state(lease.index, 'normal', 'probe_succeeded')
                else:
                    self.change_state(lease.index, 'retry_ready', 'probe_not_issued_or_request_error')
            elif current and success:
                record.consecutive_failures = 0
            self.cv.notify_all()

    def should_retry(self, code):
        return self.managed and fault_kind(code) is not None and not self.exhausted

    def execute(self, lease, system, user, call_id, **context):
        spec = lease.spec; started = time.monotonic()
        def metadata(usage):
            return {**(usage or {'usage_reported': False}), 'api_name': spec['name'],
                'requested_model': spec['settings']['model_name'],
                'request_issued': (usage or {}).get('request_issued', True),
                'endpoint_host': urlsplit(spec['settings']['api_url']).hostname,
                'request_elapsed_seconds': round(time.monotonic()-started, 6),
                'routing_run_id': self.audit.run_id}
        try:
            with self.client_lock:
                if lease.index not in self.clients:
                    try:
                        self.clients[lease.index] = self.factory(spec['settings'], spec['key'])
                    except Exception as error:
                        code = 'APIStatusError:transport_initialization_failed'
                        self.audit.event('transport_initialization_failed', api=spec['name'], error_type=type(error).__name__)
                        self.release(lease, code)
                        raise bridge.ApiFailure(code, False, metadata({'usage_reported': False, 'request_issued': False})) from None
                client = self.clients[lease.index]
            self.audit.start(call_id, spec, generation=lease.generation, recovery_probe=lease.probe, **context)
            try:
                content, usage = client.request(system, user)
            except bridge.ApiFailure as error:
                error.usage = metadata(error.usage)
                self.audit.finish(call_id, str(error), error.usage, time.monotonic()-started)
                self.release(lease, str(error)); raise
            usage = metadata(usage)
            self.audit.finish(call_id, 'received', usage, time.monotonic()-started)
            self.release(lease, success=True)
            return content, usage
        finally:
            self.release(lease)

    def close(self):
        with self.cv:
            self.closed = True; self.cv.notify_all()
        for client in self.clients.values(): client.close()
        self.clients.clear()
        self.audit.save()
