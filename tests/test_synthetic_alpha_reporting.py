import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

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
        {'event_type': 'scheduler.request_recovered', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:34+00:00', 'details': {}},
        {'event_type': 'scheduler.result_replayed', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:35+00:00', 'details': {}},
        {'event_type': 'browser.request_slow', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:36+00:00', 'details': {}},
        {'event_type': 'browser.request_stuck', 'project_alias': 'steady', 'timestamp': '2026-03-28T00:00:37+00:00', 'details': {}},
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
    assert metrics['intervention_count_by_reason']['challenge/captcha'] == 1
    assert metrics['per_agent_outcomes']['synthetic-alpha-browser-runner-1']['failure_rate'] == 1.0


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
    assert telemetry['details']['source_event_type'] == 'worker.request.stuck'
    assert telemetry['details']['worker_id'] == 'controller-1:browser-worker-1'
    assert telemetry['details']['age_seconds'] == 12.5


def test_reporter_outputs_include_review_sections(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_OUTPUT_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('SYNTHETIC_ALPHA_SWARM_REPORTS_DIR', str(tmp_path / 'reports'))
    paths = reporter.write_example_reports()

    daily_report = Path(paths['daily']['log']).read_text()
    weekly_report = Path(paths['weekly']['log']).read_text()
    dashboard = Path(paths['dashboard']['log']).read_text()

    assert 'Browser Errors By Category' in daily_report
    assert 'Agents Requiring Intervention' in daily_report
    assert 'Top Regressions' in weekly_report
    assert 'Synthetic Alpha Review' in dashboard
