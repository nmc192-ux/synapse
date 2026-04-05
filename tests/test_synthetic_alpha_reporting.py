import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SWARM_COMMON_PATH = REPO_ROOT / 'examples' / 'synthetic_alpha_swarm' / 'common.py'
COMMON_SPEC = importlib.util.spec_from_file_location('synthetic_alpha_swarm_common_reporting', SWARM_COMMON_PATH)
common = importlib.util.module_from_spec(COMMON_SPEC)
assert COMMON_SPEC is not None and COMMON_SPEC.loader is not None
sys.modules[COMMON_SPEC.name] = common
sys.modules["common"] = common
COMMON_SPEC.loader.exec_module(common)

REPORTER_PATH = REPO_ROOT / 'examples' / 'synthetic_alpha_swarm' / 'reporter.py'
REPORTER_SPEC = importlib.util.spec_from_file_location('synthetic_alpha_swarm_reporter_reporting', REPORTER_PATH)
reporter = importlib.util.module_from_spec(REPORTER_SPEC)
assert REPORTER_SPEC is not None and REPORTER_SPEC.loader is not None
sys.modules[REPORTER_SPEC.name] = reporter
REPORTER_SPEC.loader.exec_module(reporter)


def test_compute_project_metrics_includes_telemetry_and_agent_rates() -> None:
    run = common.RunState(
        run_id='run-1',
        task_id='task-1',
        agent_id='synthetic-alpha-browser-runner-1',
        project_id='project-1',
        status='failed',
        started_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
        current_phase='browser crashed',
        metadata={'error': 'captcha challenge after browser crash'},
    )
    intervention = common.OperatorInterventionRecord(
        run_id='run-1',
        agent_id='synthetic-alpha-browser-runner-1',
        reason='Captcha challenge encountered',
        payload={'category': 'challenge/captcha'},
    )
    audit_logs = [
        {
            'timestamp': '2026-03-28T00:01:00+00:00',
            'action': 'plugin.execution',
            'metadata': {'policy_violations': ['network'], 'status': 'failed'},
        },
        {
            'timestamp': '2026-03-28T00:02:00+00:00',
            'action': 'run.recovered',
            'metadata': {},
        },
    ]
    telemetry_events = [
        {'event_type': 'a2a.sent', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:30+00:00', 'details': {}},
        {'event_type': 'a2a.succeeded', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:31+00:00', 'details': {}},
        {'event_type': 'browser.error', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:32+00:00', 'details': {'error': 'captcha challenge'}},
        {'event_type': 'scheduler.recovered', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:33+00:00', 'details': {}},
        {'event_type': 'scheduler.request_recovered', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:34+00:00', 'details': {'request_signal_key': 'run-1:req-1:recovered:t-1'}},
        {'event_type': 'scheduler.result_replayed', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:35+00:00', 'details': {}},
        {'event_type': 'browser.request_slow', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:36+00:00', 'details': {'request_signal_key': 'run-1:req-1:slow:t-1'}},
        {'event_type': 'browser.request_stuck', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:37+00:00', 'details': {'request_signal_key': 'run-1:req-1:stuck:t-1'}},
        {'event_type': 'browser.request_health.completed_after_slow', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:38+00:00', 'details': {'dedupe_key': 'run-1:req-1:completed:t-2'}},
    ]

    metrics = common.compute_project_metrics('steady', [run], [intervention], audit_logs, telemetry_events)

    assert metrics['runs_failed'] == 1
    assert metrics['a2a_messages_sent'] == 1
    assert metrics['a2a_messages_succeeded'] == 1
    assert metrics['a2a_messages_failed'] == 0
    assert metrics['scheduler_recovery_events'] == 3
    assert metrics['duplicate_result_recoveries'] == 1
    assert metrics['plugin_denials'] == 1
    assert metrics['browser_errors_by_category']['challenge/captcha'] >= 1
    assert metrics['browser_errors_by_category']['browser issue'] >= 1
    assert metrics['browser_errors_by_category']['scheduler issue'] >= 1
    assert metrics['request_health_summary']['completed_after_slow'] == 1
    assert metrics['intervention_count_by_reason']['challenge/captcha'] == 1
    assert metrics['per_agent_outcomes']['synthetic-alpha-browser-runner-1']['failure_rate'] == 1.0
    assert metrics['alpha_gate']['recommendation'] == 'hold'
    assert metrics['alpha_gate']['unresolved_degradation'] >= 1
    assert metrics['alpha_gate']['release_blockers']


def test_compute_project_metrics_counts_only_explicit_stale_ownership_signals() -> None:
    run = common.RunState(
        run_id='run-2',
        task_id='task-2',
        agent_id='synthetic-alpha-browser-runner-1',
        project_id='project-1',
        status='completed',
        started_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
        current_phase='completed',
        metadata={'ownership': 'project-owner-user'},
    )
    audit_logs = [
        {
            'timestamp': '2026-03-28T00:01:00+00:00',
            'action': 'worker.audit',
            'metadata': {'owner_user_id': 'user-1', 'note': 'ownership metadata updated'},
        }
    ]
    telemetry_events = [
        {'event_type': 'scheduler.stale_ownership', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:33+00:00', 'details': {}},
    ]

    metrics = common.compute_project_metrics('steady', [run], [], audit_logs, telemetry_events)

    assert metrics['stale_ownership_incidents'] == 1


def test_record_and_load_telemetry_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_OUTPUT_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_TELEMETRY_DIR', str(tmp_path / 'logs' / 'telemetry'))

    common.record_telemetry_event(
        'a2a.sent',
        project_alias='steady',
        project_id='project-1',
        role='director',
        agent_id='synthetic-alpha-director',
        status='pending',
        timestamp=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
    )

    events = common.load_telemetry_events_since(datetime(2026, 3, 27, 23, 59, tzinfo=timezone.utc))
    assert len(events) == 1
    assert events[0]['event_type'] == 'a2a.sent'
    assert events[0]['project_alias'] == 'steady'

def test_normalize_runtime_event_to_telemetry_maps_worker_lifecycle_events() -> None:
    telemetry = common.normalize_runtime_event_to_telemetry(
        {
            'event_id': 'event-1',
            'event_type': 'worker.request.stuck',
            'timestamp': '2026-03-28T00:00:00+00:00',
            'project_id': 'project-1',
            'run_id': 'run-1',
            'severity': 'warning',
            'payload': {
                'worker_id': 'controller-1:browser-worker-1',
                'request_id': 'request-1',
                'action_id': 'action-1',
                'action': 'open',
                'age_seconds': 12.5,
            },
        },
        project_alias='steady',
    )

    assert telemetry is not None
    assert telemetry['event_type'] == 'browser.request_stuck'
    assert telemetry['project_alias'] == 'steady'
    assert telemetry['project_id'] == 'project-1'
    assert telemetry['run_id'] == 'run-1'
    assert telemetry['status'] == 'warning'
    assert telemetry['details']['event_id'] == 'event-1'
    assert telemetry['details']['source_event_type'] == 'worker.request.stuck'
    assert telemetry['details']['worker_id'] == 'controller-1:browser-worker-1'
    assert telemetry['details']['age_seconds'] == 12.5
    assert telemetry['details']['request_signal_key'].startswith('run-1:action-1:stuck:')

def test_compute_project_metrics_dedupes_runtime_feed_events_by_event_id() -> None:
    telemetry_events = [
        {
            'event_type': 'scheduler.request_recovered',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:34+00:00',
            'details': {'event_id': 'evt-1'},
        },
        {
            'event_type': 'scheduler.request_recovered',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:35+00:00',
            'details': {'event_id': 'evt-1'},
        },
        {
            'event_type': 'scheduler.result_replayed',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:36+00:00',
            'details': {'event_id': 'evt-2'},
        },
        {
            'event_type': 'scheduler.result_replayed',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:37+00:00',
            'details': {'event_id': 'evt-2'},
        },
        {
            'event_type': 'scheduler.stale_ownership',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:38+00:00',
            'details': {'event_id': 'evt-3'},
        },
        {
            'event_type': 'scheduler.stale_ownership',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:39+00:00',
            'details': {'event_id': 'evt-3'},
        },
    ]

    metrics = common.compute_project_metrics('steady', [], [], [], telemetry_events)

    assert metrics['scheduler_recovery_events'] == 1
    assert metrics['duplicate_result_recoveries'] == 1
    assert metrics['stale_ownership_incidents'] == 1


def test_normalize_worker_request_health_to_telemetry_maps_request_health_states() -> None:
    events = common.normalize_worker_request_health_to_telemetry(
        {
            'request': {
                'request_id': 'request-1',
                'action_id': 'action-1',
                'action': 'open',
                'worker_id': 'worker-1',
                'session_id': 'session-1',
                'status_reason': 'request exceeded 12s without a durable result',
                'updated_at': '2026-03-28T00:00:45+00:00',
                'completed_at': '2026-03-28T00:00:50+00:00',
            },
            'result': {
                'completed_at': '2026-03-28T00:00:50+00:00',
                'worker_id': 'worker-1',
            },
            'health_state': 'slow',
            'has_result': True,
            'is_active': False,
            'total_age_seconds': 15.0,
            'execution_age_seconds': 10.0,
            'progress_age_seconds': 9.0,
        },
        project_alias='steady',
        project_id='project-1',
        run_id='run-1',
    )

    assert [event['event_type'] for event in events] == [
        'browser.request_health.slow',
        'browser.request_health.completed_after_slow',
    ]
    assert events[0]['details']['request_id'] == 'request-1'
    assert events[0]['details']['health_state'] == 'slow'
    assert events[1]['details']['dedupe_key'].endswith(':completed-after-slow')


def test_normalize_worker_request_health_to_telemetry_maps_abandoned_and_operator_required() -> None:
    abandoned_events = common.normalize_worker_request_health_to_telemetry(
        {
            'request': {
                'request_id': 'request-2',
                'action_id': 'action-2',
                'action': 'click',
                'worker_id': 'worker-2',
                'updated_at': '2026-03-28T00:01:00+00:00',
                'status_reason': 'late worker result rejected after lease ownership changed',
            },
            'result': None,
            'health_state': 'abandoned',
            'has_result': False,
            'is_active': False,
        },
        project_alias='steady',
        project_id='project-1',
        run_id='run-2',
    )
    operator_events = common.normalize_worker_request_health_to_telemetry(
        {
            'request': {
                'request_id': 'request-3',
                'action_id': 'action-3',
                'action': 'type',
                'worker_id': 'worker-3',
                'updated_at': '2026-03-28T00:02:00+00:00',
                'status_reason': 'request requires operator review after repeated degraded progress',
            },
            'result': None,
            'health_state': 'operator_required',
            'has_result': False,
            'is_active': True,
        },
        project_alias='steady',
        project_id='project-1',
        run_id='run-3',
    )

    assert [event['event_type'] for event in abandoned_events] == ['browser.request_health.abandoned']
    assert [event['event_type'] for event in operator_events] == ['browser.request_health.operator_required']


def test_sync_project_request_health_records_deduped_request_health_events(monkeypatch) -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    class _API:
        project_id = 'project-1'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_runs(self):
            return [
                common.RunState(
                    run_id='run-1',
                    task_id='task-1',
                    agent_id='agent-1',
                    project_id='project-1',
                    status='running',
                    started_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
                )
            ]

        def get_run_worker_requests(self, run_id: str):
            assert run_id == 'run-1'
            return [
                {
                    'request': {
                        'request_id': 'request-1',
                        'action_id': 'action-1',
                        'action': 'open',
                        'updated_at': '2026-03-28T00:00:45+00:00',
                    },
                    'result': None,
                    'health_state': 'slow',
                    'has_result': False,
                    'is_active': True,
                    'total_age_seconds': 15.0,
                    'execution_age_seconds': 10.0,
                    'progress_age_seconds': 9.0,
                }
            ]

    monkeypatch.setattr(common, 'build_project_api', lambda alias: _API())
    monkeypatch.setattr(
        common,
        'load_telemetry_events_since',
        lambda since: [
            {
                'event_type': 'browser.request_health.slow',
                'timestamp': '2026-03-28T00:00:45+00:00',
                'details': {'dedupe_key': 'run-1:action-1:slow:2026-03-28T00:00:45+00:00'},
            }
        ],
    )
    monkeypatch.setattr(
        common,
        'record_telemetry_event',
        lambda event_type, **kwargs: recorded.append((event_type, kwargs)) or {},
    )

    count = common.sync_project_request_health('steady', datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc))

    assert count == 0
    assert recorded == []


def test_request_health_summary_dedupes_runtime_and_durable_signals() -> None:
    telemetry_events = [
        {
            'event_type': 'browser.request_slow',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:36+00:00',
            'run_id': 'run-1',
            'details': {'request_signal_key': 'run-1:action-1:slow:t-1'},
        },
        {
            'event_type': 'browser.request_health.slow',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:37+00:00',
            'run_id': 'run-1',
            'details': {'dedupe_key': 'run-1:action-1:slow:t-1'},
        },
        {
            'event_type': 'scheduler.request_recovered',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:38+00:00',
            'run_id': 'run-1',
            'details': {'request_signal_key': 'run-1:action-1:recovered:t-2'},
        },
        {
            'event_type': 'browser.request_health.recovered',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:39+00:00',
            'run_id': 'run-1',
            'details': {'dedupe_key': 'run-1:action-1:recovered:t-2'},
        },
    ]

    metrics = common.compute_project_metrics('steady', [], [], [], telemetry_events)

    assert metrics['browser_errors_by_category']['browser issue'] == 1
    assert metrics['scheduler_recovery_events'] == 1


def test_request_health_summary_counts_abandoned_and_operator_required() -> None:
    telemetry_events = [
        {
            'event_type': 'browser.request_health.abandoned',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:40+00:00',
            'run_id': 'run-1',
            'details': {'dedupe_key': 'run-1:action-1:abandoned:t-1'},
        },
        {
            'event_type': 'browser.request_health.operator_required',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:41+00:00',
            'run_id': 'run-1',
            'details': {'dedupe_key': 'run-1:action-2:operator_required:t-2', 'is_active': True, 'has_result': False},
        },
    ]

    summary = common.request_health_summary(telemetry_events)

    assert summary['abandoned'] == 1
    assert summary['operator_required'] == 1
    assert summary['unresolved'] == 1


def test_request_backlog_subtype_summary_classifies_operator_review_and_unresolved() -> None:
    telemetry_events = [
        {
            'event_type': 'browser.request_health.operator_required',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:41+00:00',
            'run_id': 'run-1',
            'details': {
                'request_signal_key': 'run-1:action-1:operator_required:t-1',
                'action': 'create_session',
                'status_reason': 'session bootstrap stalled after degraded progress and requires operator intervention',
                'started_at': None,
                'last_progress_at': '2026-03-28T00:00:40+00:00',
            },
        },
        {
            'event_type': 'browser.request_health.stuck',
            'project_alias': 'steady',
            'timestamp': '2026-03-28T00:00:42+00:00',
            'run_id': 'run-1',
            'details': {
                'request_signal_key': 'run-1:action-2:stuck:t-2',
                'action': 'click',
                'status_reason': 'request started on a worker but reported no durable progress within 60.00s',
                'started_at': '2026-03-28T00:00:10+00:00',
                'last_progress_at': '2026-03-28T00:00:12+00:00',
                'is_active': True,
                'has_result': False,
            },
        },
    ]

    summary = common.request_backlog_subtype_summary(telemetry_events)

    assert summary['operator_required:bootstrap_stalled'] == 1
    assert summary['unresolved:started_no_durable_progress'] == 1


def test_stale_ownership_metrics_only_count_explicit_stale_signals() -> None:
    run = common.RunState(
        run_id='run-stale',
        task_id='task-stale',
        agent_id='synthetic-alpha-browser-runner-1',
        project_id='project-1',
        status='completed',
        started_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
        current_phase='completed',
        metadata={'note': 'ownership metadata present but healthy'},
    )
    audit_logs = [
        {'timestamp': '2026-03-28T00:01:00+00:00', 'action': 'worker.status', 'metadata': {'message': 'healthy ownership handoff'}},
        {'timestamp': '2026-03-28T00:02:00+00:00', 'action': 'worker.status', 'metadata': {'message': 'worker lease expired during recovery'}},
    ]
    telemetry_events = [
        {'event_type': 'scheduler.loop_error', 'timestamp': '2026-03-28T00:00:30+00:00', 'details': {'error': 'worker lease expired'}},
        {'event_type': 'scheduler.stale_ownership', 'timestamp': '2026-03-28T00:00:31+00:00', 'details': {}},
    ]

    metrics = common.compute_project_metrics('steady', [run], [], audit_logs, telemetry_events)

    assert metrics['stale_ownership_incidents'] == 3


def test_reporter_outputs_include_review_sections(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_OUTPUT_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_REPORTS_DIR', str(tmp_path / 'reports'))
    paths = reporter.write_example_reports()

    daily_report = Path(paths['daily']['log']).read_text()
    weekly_report = Path(paths['weekly']['log']).read_text()
    dashboard = Path(paths['dashboard']['log']).read_text()

    assert 'Alpha Gate Recommendation' in daily_report
    assert 'Release Blockers' in daily_report
    assert 'Browser Errors By Category' in daily_report
    assert 'Alpha gate recommendation' in weekly_report
    assert 'Release Blockers' in weekly_report
    assert 'Agents Requiring Intervention' in daily_report
    assert 'Top Regressions' in weekly_report
    assert 'Alpha Gate' in dashboard
    assert 'Synthetic Alpha Review' in dashboard


def test_reporter_run_once_starts_project_runtime_listeners(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_OUTPUT_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_REPORTS_DIR', str(tmp_path / 'reports'))

    listener_calls: list[str] = []
    monkeypatch.setattr(reporter, 'register_role_agent', lambda *args, **kwargs: None)
    monkeypatch.setattr(reporter, 'ensure_a2a_listener', lambda: None)
    monkeypatch.setattr(reporter, 'ensure_project_runtime_listener', lambda alias: listener_calls.append(alias))
    monkeypatch.setattr(reporter, 'collect_metrics', lambda window: ([], {'runs_started': 0, 'runs_completed': 0, 'runs_failed': 0, 'intervention_count': 0, 'browser_crash_count': 0, 'captcha_challenge_count': 0, 'session_restore_failures': 0, 'duplicate_result_recoveries': 0, 'stale_ownership_incidents': 0, 'a2a_messages_sent': 0, 'a2a_messages_succeeded': 0, 'a2a_messages_failed': 0, 'scheduler_recovery_events': 0, 'plugin_denials': 0, 'average_run_latency_seconds': 0.0, 'request_health_summary': {'slow': 0, 'stuck': 0, 'recovered': 0, 'abandoned': 0, 'operator_required': 0, 'operator_review_overdue': 0, 'operator_review_timed_out': 0, 'completed_after_slow': 0, 'unresolved': 0}, 'browser_errors_by_category': {bucket: 0 for bucket in reporter.FAILURE_BUCKETS}, 'intervention_count_by_reason': {}, 'per_project_failure_rate': {}, 'failure_classification': {bucket: 0 for bucket in reporter.FAILURE_BUCKETS}, 'per_agent_outcomes': {}, 'agents_requiring_intervention': {}, 'request_backlog_subtypes': {}, 'stale_waiting_for_operator_runs': 0, 'timed_out_operator_review_runs': 0, 'pending_operator_review_interventions': 0, 'overdue_operator_review_interventions': 0, 'timed_out_operator_review_interventions': 0, 'alpha_gate': {'recommendation': 'continue', 'safe_degraded_recoveries': 0, 'unresolved_degradation': 0, 'unsafe_failures': 0, 'manual_interventions': 0, 'release_blockers': [], 'reasons': ['restricted alpha reliability remains within continue thresholds'], 'projects': {}}}))

    reporter.run_once()

    assert listener_calls == ['steady', 'chaos']


def test_sync_project_runtime_events_records_mapped_worker_events(monkeypatch) -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    class _API:
        project_id = "project-1"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_runs(self):
            return [
                common.RunState(
                    run_id='run-1',
                    task_id='task-1',
                    agent_id='agent-1',
                    project_id='project-1',
                    status='running',
                    started_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 3, 28, 0, 1, tzinfo=timezone.utc),
                )
            ]

        def get_run_events(self, run_id: str):
            assert run_id == 'run-1'
            return [
                {
                    'event_id': 'evt-1',
                    'event_type': 'worker.request.slow',
                    'timestamp': '2026-03-28T00:00:30+00:00',
                    'project_id': 'project-1',
                    'run_id': 'run-1',
                    'severity': 'warning',
                    'payload': {'worker_id': 'worker-1', 'request_id': 'request-1', 'age_seconds': 6.0},
                },
                {
                    'event_id': 'evt-2',
                    'event_type': 'task.updated',
                    'timestamp': '2026-03-28T00:00:31+00:00',
                    'project_id': 'project-1',
                    'run_id': 'run-1',
                    'payload': {},
                },
            ]

    monkeypatch.setattr(common, 'build_project_api', lambda alias: _API())
    monkeypatch.setattr(
        common,
        'record_telemetry_event',
        lambda event_type, **kwargs: recorded.append((event_type, kwargs)) or {},
    )

    count = common.sync_project_runtime_events('steady', datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc))

    assert count == 1
    assert recorded[0][0] == 'browser.request_slow'
    assert recorded[0][1]['project_alias'] == 'steady'
    assert recorded[0][1]['run_id'] == 'run-1'
    assert recorded[0][1]['details']['event_id'] == 'evt-1'


def test_assess_project_alpha_gate_recommends_expand_for_clean_project() -> None:
    snapshot = reporter.fixture_project_summary("steady", 50, 49, 1, 1)
    snapshot["browser_crash_count"] = 0
    snapshot["stale_ownership_incidents"] = 0
    snapshot["plugin_denials"] = 0
    snapshot["average_run_latency_seconds"] = 60.0
    snapshot["per_project_failure_rate"] = 0.02
    snapshot["scheduler_recovery_events"] = 1
    snapshot["request_health_summary"] = {
        "slow": 1,
        "stuck": 0,
        "recovered": 1,
        "abandoned": 0,
        "operator_required": 0,
        "operator_review_overdue": 0,
        "operator_review_timed_out": 0,
        "completed_after_slow": 1,
        "unresolved": 0,
    }

    assessment = common.assess_project_alpha_gate(snapshot)

    assert assessment["recommendation"] == "expand"
    assert assessment["unsafe_failures"] == 1
    assert assessment["unresolved_degradation"] == 0


def test_overall_metrics_rolls_up_alpha_gate_recommendation() -> None:
    steady = reporter.fixture_project_summary("steady", 40, 38, 2, 1)
    chaos = reporter.fixture_project_summary("chaos", 20, 10, 10, 6)
    chaos["plugin_denials"] = 2
    chaos["request_health_summary"]["unresolved"] = 2
    chaos["per_project_failure_rate"] = 0.5
    chaos["alpha_gate"] = common.assess_project_alpha_gate(chaos)

    summary = common.overall_metrics([steady, chaos])

    assert summary["alpha_gate"]["recommendation"] == "hold"
    assert summary["alpha_gate"]["unresolved_degradation"] >= 1
    assert any("chaos:" in reason for reason in summary["alpha_gate"]["reasons"])


def test_assess_project_alpha_gate_counts_operator_required_as_manual_intervention() -> None:
    snapshot = reporter.fixture_project_summary("chaos", 20, 10, 1, 2)
    snapshot["request_health_summary"]["operator_required"] = 2

    assessment = common.assess_project_alpha_gate(snapshot)

    assert assessment["recommendation"] == "hold"
    assert assessment["manual_interventions"] == 4
    assert "operator review required for degraded browser requests" in assessment["reasons"]


def test_compute_project_metrics_uses_operator_review_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    base_time = datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc)
    run = common.RunState(
        run_id='run-operator',
        task_id='task-operator',
        agent_id='synthetic-alpha-browser-runner-1',
        project_id='project-1',
        status='waiting_for_operator',
        started_at=base_time,
        updated_at=base_time + timedelta(minutes=2),
        current_phase='session bootstrap stalled',
        metadata={},
    )
    intervention = common.OperatorInterventionRecord(
        run_id='run-operator',
        agent_id='synthetic-alpha-browser-runner-1',
        reason='Browser human intervention required',
        payload={'ui': {'operator_required': True}},
    )
    monkeypatch.setattr(common, 'utc_now', lambda: base_time + timedelta(minutes=5))

    metrics = common.compute_project_metrics('steady', [run], [intervention], [], [])

    assert metrics['waiting_for_operator_runs'] == 1
    assert metrics['pending_operator_review_interventions'] == 1
    assert metrics['stale_waiting_for_operator_runs'] == 0
    assert metrics['overdue_operator_review_interventions'] == 0
    assert metrics['timed_out_operator_review_runs'] == 0
    assert metrics['timed_out_operator_review_interventions'] == 0
    assert metrics['request_health_summary']['operator_required'] == 1
    assert metrics['request_health_summary']['operator_review_overdue'] == 0
    assert metrics['request_health_summary']['operator_review_timed_out'] == 0
    assert metrics['request_health_summary']['unresolved'] == 1
    assert metrics['request_backlog_subtypes'] == {}


def test_compute_project_metrics_bounds_stale_operator_review_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    base_time = datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc)
    run = common.RunState(
        run_id='run-operator-stale',
        task_id='task-operator-stale',
        agent_id='synthetic-alpha-browser-runner-1',
        project_id='project-1',
        status='waiting_for_operator',
        started_at=base_time,
        updated_at=base_time,
        current_phase='operator review pending',
        metadata={},
    )
    intervention = common.OperatorInterventionRecord(
        run_id='run-operator-stale',
        agent_id='synthetic-alpha-browser-runner-1',
        reason='Browser human intervention required',
        payload={'ui': {'operator_required': True}},
        created_at=base_time,
    )
    monkeypatch.setattr(common, 'utc_now', lambda: base_time + timedelta(hours=1))

    metrics = common.compute_project_metrics('steady', [run], [intervention], [], [])

    assert metrics['waiting_for_operator_runs'] == 1
    assert metrics['stale_waiting_for_operator_runs'] == 1
    assert metrics['pending_operator_review_interventions'] == 1
    assert metrics['overdue_operator_review_interventions'] == 1
    assert metrics['timed_out_operator_review_runs'] == 1
    assert metrics['timed_out_operator_review_interventions'] == 1
    assert metrics['request_health_summary']['operator_required'] == 1
    assert metrics['request_health_summary']['operator_review_overdue'] == 1
    assert metrics['request_health_summary']['operator_review_timed_out'] == 1
    assert metrics['request_health_summary']['unresolved'] == 0


def test_build_daily_report_includes_backlog_subtypes() -> None:
    steady = reporter.fixture_project_summary("steady", 48, 42, 6, 3)
    chaos = reporter.fixture_project_summary("chaos", 28, 22, 6, 5)
    summary = common.overall_metrics([steady, chaos])

    report = reporter.build_daily_report([steady, chaos], summary)

    assert "## Backlog Subtypes" in report
    assert "operator_required:bootstrap_stalled" in report
    assert "Operator-review timed out in harness" in report
