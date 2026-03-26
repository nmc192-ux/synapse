from __future__ import annotations

from tests.test_browser_workers import (
    test_browser_worker_pool_recovers_persisted_result_after_restart,
    test_browser_worker_pool_rejects_stale_fencing_result,
)
from tests.test_scheduler import (
    test_scheduler_prevents_double_assignment_race,
    test_scheduler_requeues_expired_leases,
    test_scheduler_rejects_stale_token_renewal,
)


def test_double_assignment_race_is_blocked() -> None:
    test_scheduler_prevents_double_assignment_race()


def test_stale_fencing_token_is_rejected() -> None:
    test_scheduler_rejects_stale_token_renewal()
    test_browser_worker_pool_rejects_stale_fencing_result()


def test_worker_crash_reassigns_run() -> None:
    test_scheduler_requeues_expired_leases()


def test_durable_result_is_recovered_after_restart() -> None:
    test_browser_worker_pool_recovers_persisted_result_after_restart()
