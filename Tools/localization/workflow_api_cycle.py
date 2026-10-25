"""Run a stage's ordinary API/check/repair cycles in one process and cache."""
from contextlib import redirect_stdout
import json
import sys



def run_cycles(flow, stage, run_round, profile, requester_factory=None, session_ids=None, resume=False):
    # draft_check is an execution boundary, not a new persisted task protocol.
    task_stage = 'translate' if stage == 'draft_check' else stage
    aggregate = dict(accepted=0, failed=0, paused=False, needs_attention=[], pending_handoffs=[],
                     peak_in_flight=0, records_saved_this_run=0, recovered_batches=0,
                     api_rounds=[], continuous=True, stage_clean=False)
    # The owning run_api ProviderPool caches one client per configured API and
    # preserves its active provider across ordinary rounds.

    def merge(result):
        for key in ('accepted', 'failed', 'records_saved_this_run', 'recovered_batches'):
            aggregate[key] += result.get(key, 0)
        aggregate['peak_in_flight'] = max(aggregate['peak_in_flight'], result.get('peak_in_flight', 0))
        for key in ('paused', 'needs_attention', 'pending_handoffs', 'concurrency_limit', 'project_tokens', 'total_batches',
                    'api_provider_report', 'api_provider_log', 'api_concurrency_limits'):
            if key in result:
                aggregate[key] = result[key]
        aggregate.setdefault('completed_before_run', result.get('completed_before_run', 0))
        profile.current_summary = aggregate

    def stop(reason, **fields):
        aggregate['needs_attention'].append(dict(reason=reason, **fields))
        aggregate['next_action'] = reason
        profile.emit('cycle_stopped', reason=reason)
        return aggregate

    def check(number, cycle):
        scan_name = 'draft_scan' if stage == 'draft_check' else 'review_scan'
        profile.emit('phase_started', phase=scan_name, cycle=number)
        with profile.measure(scan_name), redirect_stdout(sys.stderr):
            scan = (flow.draft_scan(prepare_repairs=True) if stage == 'draft_check'
                    else flow.review_scan(stage, prepare_repairs=True))
        profile.emit('phase_finished', phase=scan_name, cycle=number,
                     checked_records=scan.get('checked_records'), failed_records=scan.get('failed_records'))
        cycle['scan'] = {k: scan[k] for k in ('phase', 'status', 'clean', 'checked_records', 'failed_records', 'report_file') if k in scan}
        aggregate['last_scan'] = {key: scan[key] for key in ('stage', 'phase', 'status', 'clean',
            'checked_records', 'failed_records', 'report_file') if key in scan}
        profile.emit('cycle_checked', cycle=number, clean=scan.get('clean'),
                     failed_records=scan.get('failed_records'), repair_tasks=len(scan.get('repair_tasks', [])))
        if scan.get('clean'):
            aggregate.update(stage_clean=True, next_action='stage_complete')
            return False
        if scan.get('needs_attention_ids') or scan.get('status') == 'needs_attention':
            stop('quality_needs_attention', report_file=scan.get('report_file'),
                 ids=scan.get('needs_attention_ids', []))
            return False
        if scan.get('phase') == 'awaiting_round':
            return True  # Resume an already prepared repair round; never scan it early.
        dispatch = scan.get('repair_dispatch', [])
        if not dispatch:
            stop('no_progress', report_file=scan.get('report_file'))
            return False
        if not any(task['backend'] == 'api' for task in dispatch):
            stop('external_executor_required', repair_dispatch=dispatch)
            return False
        return True

    try:
        with flow.db() as db:
            config = flow.config(db)
        if stage == 'draft_check':
            from translation_workflow import resume_api_tasks, sync_api_batching
            from workflow_profile import usage_totals
            if resume and session_ids is not None:
                raise ValueError('Use resume or explicit sessions, not both')
            if resume:
                with profile.measure('resume'):
                    recovered = resume_api_tasks(flow, task_stage)
                aggregate.update(accepted=recovered, recovered_batches=recovered)
                flow.pause(False)
                resume = False
            with flow.db() as db:
                aggregate['project_tokens'] = usage_totals(json.loads(r[0]) for r in db.execute(
                    'SELECT usage FROM api_calls WHERE status!="cancelled"'))
                if flow.meta(db, 'paused'):
                    aggregate['paused'] = True
                    return stop('resolve_execution')
            sync_api_batching(flow)
            with flow.db() as db:
                config = flow.config(db)
            profile.current_summary = aggregate
            if not check(0, {}):
                return aggregate
        # Per-ID limits in the shared scanners remain authoritative. This guard
        # also prevents an accidental controller loop when no work advances.
        limit = config.get('body_repair_round_limit', 6) + 2
        for number in range(1, limit + 1):
            profile.emit('cycle_started', cycle=number)
            try:
                result = run_round(flow, task_stage, requester_factory, session_ids if number == 1 else None,
                                   resume if number == 1 else False, profile)
            except BaseException:
                partial = getattr(profile, 'current_summary', None)
                if partial is not None and partial is not aggregate:
                    merge(partial)
                raise
            merge(result)
            cycle = dict(cycle=number, accepted=result['accepted'], failed=result['failed'],
                         records_saved=result.get('records_saved_this_run', 0))
            aggregate['api_rounds'].append(cycle)
            if result['paused'] or result['needs_attention'] or result['pending_handoffs']:
                aggregate['next_action'] = 'resolve_execution'
                return aggregate
            if stage not in ('translate', 'draft_check', 'review', 'final_review'):
                if stage == 'pilot_translate':
                    with profile.measure('pilot_report'), redirect_stdout(sys.stderr):
                        report = flow.pilot_report()
                    aggregate['pilot_report'] = {k: report[k] for k in ('report_file', 'report_hash') if k in report}
                    aggregate['next_action'] = 'pilot-decide'
                else:
                    aggregate['next_action'] = 'main_workflow'
                return aggregate
            with flow.db() as db:
                revision = flow.meta(db, 'revision')
                unfinished = [dict(row) for row in db.execute(
                    'SELECT id,backend,status,payload FROM tasks WHERE stage=? AND revision=? '
                    'AND status IN ("pending","running","failed")', (task_stage, revision))]
                for row in unfinished:
                    payload = json.loads(row.pop('payload'))
                    row.update({key: payload[key] for key in ('required_model', 'repair_tier', 'automatic_escalation') if key in payload})
            external = [row for row in unfinished if row['backend'] != 'api']
            if external:
                return stop('external_executor_required', tasks=external)
            if unfinished:
                return stop('unfinished_round', tasks=unfinished)
            if stage == 'translate':
                aggregate.update(drafts_complete=True, next_action='draft_check')
                profile.emit('stage_boundary', next_action='draft_check', drafts_complete=True)
                return aggregate
            if not check(number, cycle):
                return aggregate
        return stop('automation_round_guard', limit=limit)
    finally:
        if getattr(profile, 'api_pool', None) is not None:
            profile.api_pool.audit.save()  # run_api owns final client cleanup.
