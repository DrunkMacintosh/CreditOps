from __future__ import annotations

from uuid import UUID, uuid4

from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.readiness import evaluate_readiness
from creditops.application.ports.orchestration import (
    BlockingGap,
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import (
    GateStatus,
    GateType,
    TaskReadiness,
    TaskType,
)

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
TEMPLATE = DependencyTemplate.canonical()


def gate(gate_type: GateType, status: GateStatus, case_version: int = 1) -> GateRecord:
    return GateRecord(gate_type=gate_type, case_version=case_version, status=status)


def satisfied(*gate_types: GateType) -> tuple[GateRecord, ...]:
    return tuple(gate(gate_type, GateStatus.SATISFIED) for gate_type in gate_types)


def task(
    task_type: TaskType,
    status: TaskStatus,
    *,
    case_version: int = 1,
    task_id: UUID | None = None,
) -> OrchestrationTaskRow:
    return OrchestrationTaskRow(
        task_id=task_id or uuid4(),
        task_type=task_type,
        case_version=case_version,
        status=status,
    )


def snapshot(
    *,
    case_version: int = 1,
    has_intake_handoff: bool = True,
    tasks: tuple[OrchestrationTaskRow, ...] = (),
    gates: tuple[GateRecord, ...] = (),
    blocking_gaps: tuple[BlockingGap, ...] = (),
) -> OrchestrationSnapshot:
    return OrchestrationSnapshot(
        case_id=CASE_ID,
        case_version=case_version,
        has_intake_handoff=has_intake_handoff,
        tasks=tasks,
        gates=gates,
        blocking_gaps=blocking_gaps,
    )


def test_specialists_wait_for_the_intake_gate_then_run_in_parallel() -> None:
    before = evaluate_readiness(snapshot(gates=()), template=TEMPLATE)
    after = evaluate_readiness(
        snapshot(gates=satisfied(GateType.G1_INTAKE_COMPLETE)),
        template=TEMPLATE,
    )

    for report, expected in ((before, TaskReadiness.BLOCKED), (after, TaskReadiness.READY)):
        assert report.by_type(TaskType.CREDIT_UNDERWRITING).readiness is expected
        assert report.by_type(TaskType.LEGAL_COMPLIANCE_COLLATERAL).readiness is expected
    # Downstream stages never start with the makers merely ready.
    assert after.by_type(TaskType.INDEPENDENT_RISK_REVIEW).readiness is TaskReadiness.BLOCKED
    assert after.by_type(TaskType.CREDIT_OPERATIONS).readiness is TaskReadiness.BLOCKED


def test_risk_review_requires_both_makers_and_ops_requires_risk_review() -> None:
    gates = satisfied(
        GateType.G1_INTAKE_COMPLETE,
        GateType.G2_GAP_REQUEST_APPROVAL,
        GateType.G3_RISK_DISPOSITION,
    )
    one_maker = evaluate_readiness(
        snapshot(
            gates=gates,
            tasks=(task(TaskType.CREDIT_UNDERWRITING, TaskStatus.SUCCEEDED),),
        ),
        template=TEMPLATE,
    )
    both_makers = evaluate_readiness(
        snapshot(
            gates=gates,
            tasks=(
                task(TaskType.CREDIT_UNDERWRITING, TaskStatus.SUCCEEDED),
                task(TaskType.LEGAL_COMPLIANCE_COLLATERAL, TaskStatus.SUCCEEDED),
            ),
        ),
        template=TEMPLATE,
    )
    risk_done = evaluate_readiness(
        snapshot(
            gates=gates,
            tasks=(
                task(TaskType.CREDIT_UNDERWRITING, TaskStatus.SUCCEEDED),
                task(TaskType.LEGAL_COMPLIANCE_COLLATERAL, TaskStatus.SUCCEEDED),
                task(TaskType.INDEPENDENT_RISK_REVIEW, TaskStatus.SUCCEEDED),
            ),
        ),
        template=TEMPLATE,
    )

    assert one_maker.by_type(TaskType.INDEPENDENT_RISK_REVIEW).readiness is TaskReadiness.BLOCKED
    assert "LEGAL_COMPLIANCE_COLLATERAL" in (
        one_maker.by_type(TaskType.INDEPENDENT_RISK_REVIEW).reason
    )
    assert both_makers.by_type(TaskType.INDEPENDENT_RISK_REVIEW).readiness is TaskReadiness.READY
    assert both_makers.by_type(TaskType.CREDIT_OPERATIONS).readiness is TaskReadiness.BLOCKED
    assert risk_done.by_type(TaskType.CREDIT_OPERATIONS).readiness is TaskReadiness.READY


def test_a_blocking_evidence_gap_prevents_the_affected_task_from_starting() -> None:
    underwriting_id = uuid4()
    tasks = (
        task(
            TaskType.CREDIT_UNDERWRITING,
            TaskStatus.SUCCEEDED,
            task_id=underwriting_id,
        ),
        task(TaskType.LEGAL_COMPLIANCE_COLLATERAL, TaskStatus.SUCCEEDED),
    )
    gates = satisfied(GateType.G1_INTAKE_COMPLETE, GateType.G2_GAP_REQUEST_APPROVAL)
    # An unresolved BLOCKING gap re-attaches to the underwriting task type, so a
    # fresh run of that node is fenced; CLARIFICATION-level gaps do not block.
    blocking = evaluate_readiness(
        snapshot(
            gates=gates,
            tasks=(tasks[1], task(TaskType.CREDIT_UNDERWRITING, TaskStatus.SUPERSEDED,
                                  task_id=underwriting_id)),
            blocking_gaps=(
                BlockingGap(uuid4(), affected_task_id=underwriting_id,
                            blocking_level="BLOCKING"),
            ),
        ),
        template=TEMPLATE,
    )
    clarification = evaluate_readiness(
        snapshot(
            gates=gates,
            tasks=(tasks[1], task(TaskType.CREDIT_UNDERWRITING, TaskStatus.SUPERSEDED,
                                  task_id=underwriting_id)),
            blocking_gaps=(
                BlockingGap(uuid4(), affected_task_id=underwriting_id,
                            blocking_level="CLARIFICATION"),
            ),
        ),
        template=TEMPLATE,
    )

    assert blocking.by_type(TaskType.CREDIT_UNDERWRITING).readiness is TaskReadiness.BLOCKED
    assert "blocking evidence gap" in blocking.by_type(TaskType.CREDIT_UNDERWRITING).reason
    assert clarification.by_type(TaskType.CREDIT_UNDERWRITING).readiness is TaskReadiness.READY


def test_stale_case_version_tasks_are_fenced_and_never_counted_as_progress() -> None:
    stale_task_id = uuid4()
    report = evaluate_readiness(
        snapshot(
            case_version=2,
            gates=(gate(GateType.G1_INTAKE_COMPLETE, GateStatus.SATISFIED, case_version=2),),
            tasks=(
                task(
                    TaskType.CREDIT_UNDERWRITING,
                    TaskStatus.SUCCEEDED,
                    case_version=1,
                    task_id=stale_task_id,
                ),
            ),
        ),
        template=TEMPLATE,
    )

    # The old-version success is superseded, not credited: underwriting must be
    # redone at version 2, and risk review stays blocked on it.
    assert str(stale_task_id) in report.superseded_task_ids
    assert report.by_type(TaskType.CREDIT_UNDERWRITING).readiness is TaskReadiness.READY
    assert report.by_type(TaskType.INDEPENDENT_RISK_REVIEW).readiness is TaskReadiness.BLOCKED


def test_human_gates_hold_downstream_even_when_all_dependencies_are_complete() -> None:
    tasks = (
        task(TaskType.CREDIT_UNDERWRITING, TaskStatus.SUCCEEDED),
        task(TaskType.LEGAL_COMPLIANCE_COLLATERAL, TaskStatus.SUCCEEDED),
    )
    open_gate = evaluate_readiness(
        snapshot(
            gates=satisfied(GateType.G1_INTAKE_COMPLETE)
            + (gate(GateType.G2_GAP_REQUEST_APPROVAL, GateStatus.OPEN),),
            tasks=tasks,
        ),
        template=TEMPLATE,
    )

    assessment = open_gate.by_type(TaskType.INDEPENDENT_RISK_REVIEW)
    assert assessment.readiness is TaskReadiness.BLOCKED
    assert "G2_GAP_REQUEST_APPROVAL" in assessment.reason


def test_failed_manual_review_is_surfaced_not_silently_rescheduled() -> None:
    report = evaluate_readiness(
        snapshot(
            gates=satisfied(GateType.G1_INTAKE_COMPLETE),
            tasks=(task(TaskType.CREDIT_UNDERWRITING, TaskStatus.FAILED_MANUAL_REVIEW),),
        ),
        template=TEMPLATE,
    )

    assert report.by_type(TaskType.CREDIT_UNDERWRITING).readiness is TaskReadiness.FAILED
    assert TaskType.CREDIT_UNDERWRITING not in report.ready_types()
