from __future__ import annotations

from tests.test_run_state import (
    test_operator_intervention_api_endpoints,
    test_operator_intervention_transitions_run_and_resume,
)


def test_intervention_queue_and_approve_resume_path() -> None:
    test_operator_intervention_transitions_run_and_resume()


def test_intervention_api_reject_and_input_path() -> None:
    test_operator_intervention_api_endpoints()
