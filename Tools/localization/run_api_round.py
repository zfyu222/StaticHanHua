"""Run one API stage; translate saves drafts, draft_check checks and repairs them.

The Codex Luna executor owns this process. The parent agent waits for that
executor's native completion notification, not for a polled status file.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


STAGES = ('extract', 'glossary_review', 'pilot_translate',
          'translate', 'draft_check', 'review', 'polish', 'final_review')


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return True  # Unknown ownership must not permit overlapping runners.
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID means stopped; access denied means unknown.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def execute(project, stage, command, resume=False, api=None):
    """Block on a single child process, with no timer or status polling loop."""
    started = time.perf_counter()
    project = Path(project).resolve()
    root = project / 'TranslateFiles'
    if not root.is_dir() or root.resolve() != root:
        raise ValueError('Use an initialized localization project with a local TranslateFiles directory')
    run_id = uuid.uuid4().hex
    reports, logs = root / 'reports/api_runs', root / 'logs/api_runs'
    reports.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    lock = root / 'state/api_round.lock'
    if resume and lock.exists():
        previous_text = lock.read_text(encoding='utf-8')
        previous = json.loads(previous_text)
        old_receipt = reports / (previous['run_id'] + '.json')
        old = json.loads(old_receipt.read_text(encoding='utf-8'))
        if pid_alive(previous.get('owner_pid')) or pid_alive(old.get('pid')):
            raise ValueError('Previous API executor is still alive; stop it before resume')
        if lock.read_text(encoding='utf-8') != previous_text:
            raise ValueError('API lock changed during recovery')
        lock.unlink()
    # Ordinary starts never reclaim locks. Explicit resume above requires both
    # old processes to have stopped before the usual exclusive acquisition.
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError('API round already locked; verify the old executor stopped before recovery') from None
    stdout, stderr = logs / (run_id + '.stdout.log'), logs / (run_id + '.stderr.log')
    receipt_file = reports / (run_id + '.json')
    receipt = {'run_id': run_id, 'stage': stage, 'status': 'running',
               'started_at': datetime.now(timezone.utc).isoformat(),
               'receipt_file': str(receipt_file), 'stdout_log': str(stdout), 'stderr_log': str(stderr)}
    receipt['profile_log'] = str(logs / (run_id + '.profile.jsonl'))
    receipt['profile_summary'] = str(reports / (run_id + '.profile.json'))
    if api:
        receipt['api'] = api
    child = None
    terminal = False
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump({'run_id': run_id, 'owner_pid': os.getpid(), 'stage': stage}, handle)
        save(receipt_file, receipt)
        environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'SHH_API_PROFILE_ID': run_id}
        with stdout.open('w', encoding='utf-8') as output, stderr.open('w', encoding='utf-8') as error:
            child = subprocess.Popen(command, cwd=project, stdout=output, stderr=error,
                                     env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            receipt['pid'] = child.pid
            save(receipt_file, receipt)
            receipt['exit_code'] = child.wait()
            terminal = True
        receipt['status'] = 'failed'
        if receipt['exit_code'] == 0:
            try:
                summary = json.loads(stdout.read_text(encoding='utf-8-sig'))
                required = ('accepted', 'failed', 'paused', 'needs_attention', 'pending_handoffs')
                if not isinstance(summary, dict) or any(k not in summary for k in required):
                    raise ValueError('Invalid round summary')
                if (any(type(summary[k]) is not int or summary[k] < 0 for k in ('accepted', 'failed'))
                        or type(summary['paused']) is not bool
                        or any(not isinstance(summary[k], list) for k in ('needs_attention', 'pending_handoffs'))):
                    raise ValueError('Invalid round summary types')
                receipt.update(accepted=summary['accepted'], failed=summary['failed'],
                               paused=summary['paused'], needs_attention_count=len(summary['needs_attention']),
                               pending_handoffs_count=len(summary['pending_handoffs']))
                for key in ('continuous', 'stage_clean', 'drafts_complete', 'next_action', 'api_rounds', 'last_scan',
                            'api_provider_report', 'api_provider_log', 'api_concurrency_limits'):
                    if key in summary:
                        receipt[key] = summary[key]
                external = [item for item in summary['needs_attention']
                            if isinstance(item, dict) and item.get('reason') == 'external_executor_required']
                if external:
                    receipt['repair_dispatch'] = [task for item in external
                        for task in item.get('repair_dispatch', item.get('tasks', []))]
                receipt['status'] = ('needs_attention' if summary['paused'] or summary['needs_attention']
                                     or summary['pending_handoffs'] else 'completed')
            except (ValueError, TypeError):
                receipt['reason'] = 'Invalid final summary; inspect logs, do not replay requests automatically'
        else:
            receipt['reason'] = 'API process failed; inspect logs and recover existing handoffs before retrying'
    except KeyboardInterrupt:
        if child is not None:
            child.terminate()
            child.wait()
        terminal = True
        receipt.update(status='interrupted', reason='Executor interrupted; verify existing handoffs before recovery')
    except OSError:
        # Keep OS error details in local logs, rather than copying command or
        # environment contents into the parent agent's context.
        terminal = child is None or child.poll() is not None
        receipt.update(status='failed', reason='Unable to execute or record API round; inspect local state')
    finally:
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        receipt['elapsed_seconds'] = round(time.perf_counter() - started, 4)
        profile_file = Path(receipt['profile_summary'])
        if profile_file.exists():
            try:
                receipt['performance'] = json.loads(profile_file.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                receipt['profile_error'] = 'Unable to read profile summary; inspect profile log'
        save(receipt_file, receipt)
        if terminal:
            if json.loads(lock.read_text(encoding='utf-8')).get('run_id') == run_id:
                lock.unlink()
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--sessions', nargs='+', help='Explicitly recovered, unissued sessions only')
    parser.add_argument('--resume', action='store_true', help='Resume unfinished batches after the old executor stopped')
    parser.add_argument('--single-round', action='store_true', help='Debug only: stop before stage checks and ordinary repair cycles')
    parser.add_argument('--api', help='Named API from api.local.jsonc for this run; default_api is used otherwise')
    args = parser.parse_args()
    command = [sys.executable, str(Path(__file__).with_name('translation_workflow.py')),
               '--project', str(Path(args.project).resolve()), 'run-api', '--stage', args.stage]
    if args.sessions:
        command.extend(['--sessions', *args.sessions])
    if args.resume:
        command.append('--resume')
    if args.single_round:
        command.append('--single-round')
    if args.api:
        command.extend(['--api', args.api])
    try:
        receipt = execute(args.project, args.stage, command, resume=args.resume, api=args.api)
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'needs_attention', 'reason': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt['status'] == 'completed' else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
