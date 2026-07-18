from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from test_advance import CASE_ID, FakeOrchestrationRepository, RecordingQueue

from creditops.application.orchestration.advance import AdvanceCase
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.planner import OrchestrationPlanner
from creditops.application.ports.model_gateway import InferenceResult, ReasonRequest
from creditops.domain.orchestration import GateStatus, GateType, TaskType

TEMPLATE = DependencyTemplate.canonical()
NOW = datetime(2026, 7, 18, 6, 30, tzinfo=UTC)


class HostilePlannerGateway:
    """A planner that always proposes gate-bypassing downstream work."""

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        return InferenceResult(
            capability="reasoning",
            provider="FPT",
            case_id=request.case_id,
            endpoint_id="ep-test",
            model_id="model-hostile",
            payload={
                "steps": [
                    {"task_type": "CREDIT_OPERATIONS", "priority": 0},
                    {"task_type": "INDEPENDENT_RISK_REVIEW", "priority": 1},
                ]
            },
            prompt_version="orchestrator-prompt-v1",
            schema_version="orchestrator-proposal-v1",
            route_version="fpt-route-v1",
            correlation_id=request.correlation_id,
            started_at=NOW,
            latency_ms=1,
        )

    async def extract_kie(self, request: Any) -> InferenceResult:
        raise NotImplementedError

    async def extract_table(self, request: Any) -> InferenceResult:
        raise NotImplementedError

    async def inspect_vision(self, request: Any) -> InferenceResult:
        raise NotImplementedError

    async def embed(self, request: Any) -> InferenceResult:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_an_open_gate_holds_even_against_a_planner_proposal() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    # The makers are legitimately ready behind the satisfied intake gate, but G2
    # (gap approval) is OPEN: risk review and operations must stay unscheduled
    # no matter what the planner proposes.
    use_case = AdvanceCase(
        repository,
        queue,
        OrchestrationPlanner(TEMPLATE, gateway=HostilePlannerGateway()),
        template=TEMPLATE,
        clock=lambda: NOW,
    )

    result = await use_case.execute(CASE_ID)

    assert result.proposal_status == "REJECTED"
    assert result.plan.source == "DEFAULT"
    # Only the deterministic default plan ran: the two ready makers.
    assert {envelope.task_type for envelope in queue.sent} == {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    }
    assert not any(
        row.task_type in (TaskType.INDEPENDENT_RISK_REVIEW, TaskType.CREDIT_OPERATIONS)
        for row in repository.tasks_by_key.values()
    )
    # The rejection itself is auditable history, not a silent drop.
    rejected = [p for p in repository.proposals if p["status"] == "REJECTED"]
    assert len(rejected) == 1
    assert rejected[0]["validation_errors"]
    # No gate changed as a side effect of the hostile proposal.
    assert repository.gates[(1, GateType.G2_GAP_REQUEST_APPROVAL)].status is GateStatus.OPEN


@pytest.mark.asyncio
async def test_the_engine_never_satisfies_human_only_gates() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    use_case = AdvanceCase(
        repository,
        queue,
        OrchestrationPlanner(TEMPLATE, gateway=None),
        template=TEMPLATE,
        clock=lambda: NOW,
    )

    await use_case.execute(CASE_ID)
    await use_case.execute(CASE_ID)  # a retry must not creep gates forward

    for human_gate in (
        GateType.G2_GAP_REQUEST_APPROVAL,
        GateType.G3_RISK_DISPOSITION,
        GateType.G4_OPS_AUTHORIZATION,
    ):
        record = repository.gates[(1, human_gate)]
        assert record.status is GateStatus.OPEN
        assert record.satisfied_by_actor_id is None
