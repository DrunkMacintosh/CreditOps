from __future__ import annotations

from creditops.application.orchestration.graph import (
    DependencyTemplate,
    detect_cycle,
    detect_deadlock,
)
from creditops.domain.orchestration import TaskReadiness, TaskType


def test_canonical_template_is_acyclic_and_orders_specialists() -> None:
    template = DependencyTemplate.canonical()

    assert detect_cycle(template.edges()) is None
    assert template.ordered_types() == (
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
        TaskType.INDEPENDENT_RISK_REVIEW,
        TaskType.CREDIT_OPERATIONS,
    )
    risk = template.by_type[TaskType.INDEPENDENT_RISK_REVIEW]
    assert set(risk.predecessors) == {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    }
    ops = template.by_type[TaskType.CREDIT_OPERATIONS]
    assert ops.predecessors == (TaskType.INDEPENDENT_RISK_REVIEW,)


def test_cycle_detection_reports_a_dependency_loop() -> None:
    edges = {
        TaskType.CREDIT_UNDERWRITING: (TaskType.CREDIT_OPERATIONS,),
        TaskType.INDEPENDENT_RISK_REVIEW: (TaskType.CREDIT_UNDERWRITING,),
        TaskType.CREDIT_OPERATIONS: (TaskType.INDEPENDENT_RISK_REVIEW,),
    }

    cycle = detect_cycle(edges)

    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.INDEPENDENT_RISK_REVIEW,
        TaskType.CREDIT_OPERATIONS,
    }


def test_deadlock_is_surfaced_when_everything_is_blocked() -> None:
    assessments = {
        TaskType.CREDIT_UNDERWRITING: TaskReadiness.COMPLETE,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL: TaskReadiness.COMPLETE,
        TaskType.INDEPENDENT_RISK_REVIEW: TaskReadiness.BLOCKED,
        TaskType.CREDIT_OPERATIONS: TaskReadiness.BLOCKED,
    }
    reasons = {
        TaskType.INDEPENDENT_RISK_REVIEW: "human gate G2_GAP_REQUEST_APPROVAL is not satisfied",
        TaskType.CREDIT_OPERATIONS: "waiting for predecessor: INDEPENDENT_RISK_REVIEW",
    }

    deadlock = detect_deadlock(assessments, reasons)

    assert deadlock is not None
    assert (
        TaskType.INDEPENDENT_RISK_REVIEW,
        "human gate G2_GAP_REQUEST_APPROVAL is not satisfied",
    ) in deadlock.blocked
    assert "waiting for predecessor: INDEPENDENT_RISK_REVIEW" in deadlock.reasons


def test_no_deadlock_while_work_is_ready_or_running_or_finished() -> None:
    running = {
        TaskType.CREDIT_UNDERWRITING: TaskReadiness.IN_PROGRESS,
        TaskType.INDEPENDENT_RISK_REVIEW: TaskReadiness.BLOCKED,
    }
    finished = {
        TaskType.CREDIT_UNDERWRITING: TaskReadiness.COMPLETE,
        TaskType.INDEPENDENT_RISK_REVIEW: TaskReadiness.COMPLETE,
    }

    assert detect_deadlock(running, {}) is None
    assert detect_deadlock(finished, {}) is None
