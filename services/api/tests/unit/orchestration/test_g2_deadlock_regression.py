"""Regression: the G2 deadlock (P0 #5) is gone.

The old cycle was: INDEPENDENT_RISK_REVIEW gated on G2 -> G2 was only satisfied
by a Credit Operations document-request approval -> Credit Operations required
Risk COMPLETE.  The fix moves G2 entirely BEFORE Risk, satisfied from a
gap-request batch + human disposition, with NO credit-ops package involved.

This test proves both halves:

1. readiness: with G2 recorded as SATISFIED (as the gap-request workflow does)
   and BOTH makers COMPLETE -- and ZERO credit-ops packages/tasks anywhere --
   INDEPENDENT_RISK_REVIEW evaluates READY; drop G2 to OPEN and it goes BLOCKED,
   proving G2 (not the credit-ops package) is the only thing gating Risk.
2. source: neither the gate-derivation module nor the credit-ops API references
   the deleted package-based G2 path any longer.
"""

from __future__ import annotations

import inspect
from uuid import UUID, uuid4

from creditops.application.orchestration import gates as gates_module
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.readiness import evaluate_readiness
from creditops.application.ports.orchestration import (
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskReadiness, TaskType

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
TEMPLATE = DependencyTemplate.canonical()


def _gate(gate_type: GateType, status: GateStatus) -> GateRecord:
    return GateRecord(gate_type=gate_type, case_version=1, status=status)


def _succeeded(task_type: TaskType) -> OrchestrationTaskRow:
    return OrchestrationTaskRow(
        task_id=uuid4(), task_type=task_type, case_version=1, status=TaskStatus.SUCCEEDED
    )


def _snapshot(*, g2: GateStatus) -> OrchestrationSnapshot:
    # ZERO credit-ops packages/tasks anywhere: only the two makers plus the
    # G1/G2 human gates.  G2 SATISFIED stands in for "a gap-request batch was
    # dispositioned", recorded by api/gap_requests.py, never by credit-ops.
    return OrchestrationSnapshot(
        case_id=CASE_ID,
        case_version=1,
        has_intake_handoff=True,
        tasks=(
            _succeeded(TaskType.CREDIT_UNDERWRITING),
            _succeeded(TaskType.LEGAL_COMPLIANCE_COLLATERAL),
        ),
        gates=(
            _gate(GateType.G1_INTAKE_COMPLETE, GateStatus.SATISFIED),
            _gate(GateType.G2_GAP_REQUEST_APPROVAL, g2),
        ),
    )


def test_risk_review_is_ready_from_a_satisfied_g2_with_no_credit_ops_package() -> None:
    report = evaluate_readiness(_snapshot(g2=GateStatus.SATISFIED), template=TEMPLATE)
    assert report.by_type(TaskType.INDEPENDENT_RISK_REVIEW).readiness is TaskReadiness.READY
    # Credit Operations is still downstream of Risk (it needs G3 + Risk COMPLETE);
    # the point is Risk no longer waits on it.
    assert report.by_type(TaskType.CREDIT_OPERATIONS).readiness is TaskReadiness.BLOCKED


def test_risk_review_blocks_again_when_g2_is_open() -> None:
    report = evaluate_readiness(_snapshot(g2=GateStatus.OPEN), template=TEMPLATE)
    assessment = report.by_type(TaskType.INDEPENDENT_RISK_REVIEW)
    assert assessment.readiness is TaskReadiness.BLOCKED
    assert "G2_GAP_REQUEST_APPROVAL" in assessment.reason


def test_gates_module_no_longer_defines_the_package_based_g2_derivation() -> None:
    source = inspect.getsource(gates_module)
    assert "derive_g2_status" not in source
    # G4 legitimately still references the credit-ops package; G2 must not.
    assert not hasattr(gates_module, "derive_g2_status")


def test_credit_ops_api_no_longer_writes_g2_from_the_package() -> None:
    from creditops.api import credit_ops as credit_ops_module

    source = inspect.getsource(credit_ops_module)
    assert "derive_g2_status" not in source
    assert "_maybe_satisfy_g2" not in source
    # The credit-ops API never touches the G2 gate at all now.
    assert "G2_GAP_REQUEST_APPROVAL" not in source
