"""Rebuild cross-run API statistics from attempt journals, including interrupted runs."""
import argparse
import json
from pathlib import Path
import threading
import time
from workflow_api_failover import ProviderAudit


def build(root):
    audit = object.__new__(ProviderAudit)
    audit.run_id = 'all_recorded_runs'; audit.stage = 'all'; audit.log = root / 'logs/api_failover'
    audit.lock = threading.RLock(); audit.attempts = {}; audit.transitions = []; audit.started_at = time.time()
    paths = sorted(root.glob('**/logs/api_failover/*.jsonl')); malformed = []; stages = {}; runs = set(); configurations = {}
    for path in paths:
        for number, raw_line in enumerate(path.read_bytes().splitlines(), 1):
            try: row = json.loads(raw_line.decode('utf8'))
            except (ValueError, UnicodeError):
                malformed.append({'file': str(path), 'line': number}); continue
            if not isinstance(row, dict) or not isinstance(row.get('run_id'), str) or 'event' not in row:
                malformed.append({'file': str(path), 'line': number}); continue
            run = row.get('run_id'); runs.add(run)
            key = (run, str(row.get('call_id'))); event = row.get('event')
            if event == 'request_started':
                audit.attempts.setdefault(key, {}).update(row)
                stages[row.get('stage')] = stages.get(row.get('stage'), set()) | {run}
            elif event == 'request_finished': audit.attempts.setdefault(key, {}).update(row)
            elif event == 'collection_outcome': audit.attempts.setdefault(key, {})['collection_status'] = row['status']
            elif event == 'provider_transition' and row not in audit.transitions: audit.transitions.append(row)
            elif event == 'routing_configured': configurations[run] = row['configuration']
    orphan = sum('api' not in row for row in audit.attempts.values())
    audit.attempts = {key: row for key, row in audit.attempts.items() if 'api' in row}
    report = audit.summary()
    report.pop('elapsed_seconds', None)  # Independent/overlapping runs have no additive wall time.
    report.update(log_files=len(paths), runs=len(runs), run_configurations=configurations,
        runs_by_stage={k:len(v) for k,v in stages.items()},
        malformed_log_lines=malformed, orphan_events_without_start=orphan,
        coverage='Only routed attempt journals; legacy requests without these journals are not assigned to the current API.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='Project TranslateFiles directory, or one isolated workflow root')
    parser.add_argument('--output', type=Path)
    args=parser.parse_args(); report=build(args.root)
    output=args.output or args.root/'reports/api_stability_summary.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'report':str(output),'providers':report['providers']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
