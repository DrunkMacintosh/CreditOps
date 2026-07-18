from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.planner import (
    OrchestrationPlanner,
    OrchestratorPrompt,
    build_default_plan,
)
from creditops.application.orchestration.readiness import evaluate_readiness
from creditops.application.ports.model_gateway import (
    InferenceResult,
    InferenceUnavailableError,
    ReasonRequest,
)
from creditops.application.ports.orchestration import (
    GateRecord,
    OrchestrationSnapshot,
)
from creditops.domain.orchestration import GateStatus, GateType, TaskType

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
TEMPLATE = DependencyTemplate.canonical()


def makers_ready_report() -> Any:
    snapshot = OrchestrationSnapshot(
        case_id=CASE_ID,
        case_version=1,
        has_intake_handoff=True,
        gates=(
            GateRecord(GateType.G1_INTAKE_COMPLETE, 1, GateStatus.SATISFIED),
        ),
    )
    return evaluate_readiness(snapshot, template=TEMPLATE)


class FakeReasonGateway:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.requests: list[ReasonRequest] = []

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        self.requests.append(request)
        if isinstance(self.payload, Exception):
            raise self.payload
        return InferenceResult(
            capability="reasoning",
            provider="FPT",
            case_id=request.case_id,
            endpoint_id="ep-test",
            model_id="model-test",
            payload=self.payload,
            prompt_version="orchestrator-prompt-v1",
            schema_version="orchestrator-proposal-v1",
            route_version="fpt-route-v1",
            correlation_id=request.correlation_id,
            started_at=datetime(2026, 7, 18, 6, 0, tzinfo=UTC),
            latency_ms=5,
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
async def test_without_a_configured_gateway_the_default_plan_is_used() -> None:
    report = makers_ready_report()
    planner = OrchestrationPlanner(TEMPLATE, gateway=None)

    outcome = await planner.plan(case_id=CASE_ID, correlation_id="corr-1", report=report)

    assert outcome.status == "SKIPPED"
    assert outcome.plan.source == "DEFAULT"
    assert outcome.plan.task_types() == (
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    )
    assert outcome.plan == build_default_plan(report, TEMPLATE)


@pytest.mark.asyncio
async def test_a_valid_proposal_is_accepted_and_reprioritises_ready_work() -> None:
    report = makers_ready_report()
    gateway = FakeReasonGateway(
        {
            "steps": [
                {"task_type": "LEGAL_COMPLIANCE_COLLATERAL", "priority": 0},
                {"task_type": "CREDIT_UNDERWRITING", "priority": 1},
            ]
        }
    )
    planner = OrchestrationPlanner(TEMPLATE, gateway=gateway)

    outcome = await planner.plan(case_id=CASE_ID, correlation_id="corr-2", report=report)

    assert outcome.status == "ACCEPTED"
    assert outcome.plan.source == "LLM_PROPOSED"
    assert outcome.plan.task_types() == (
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
        TaskType.CREDIT_UNDERWRITING,
    )
    assert outcome.model_version == "model-test"
    # The prompt keeps the untrusted-data boundary and case identifiers only.
    request = gateway.requests[0]
    assert "dữ liệu không tin cậy" in request.system_context
    assert str(CASE_ID) in request.content


@pytest.mark.asyncio
async def test_a_gate_bypassing_proposal_is_rejected_and_falls_back() -> None:
    report = makers_ready_report()
    # Risk review is behind the open G2 gate and unmet dependencies: proposing
    # it is a gate/dependency violation, so the deterministic validator rejects.
    gateway = FakeReasonGateway(
        {"steps": [{"task_type": "INDEPENDENT_RISK_REVIEW", "priority": 0}]}
    )
    planner = OrchestrationPlanner(TEMPLATE, gateway=gateway)

    outcome = await planner.plan(case_id=CASE_ID, correlation_id="corr-3", report=report)

    assert outcome.status == "REJECTED"
    assert any("INDEPENDENT_RISK_REVIEW" in error for error in outcome.validation_errors)
    assert outcome.plan == build_default_plan(report, TEMPLATE)
    assert outcome.plan.source == "DEFAULT"


@pytest.mark.asyncio
async def test_an_unknown_task_type_or_extra_field_is_rejected_by_schema() -> None:
    report = makers_ready_report()
    unknown_type = FakeReasonGateway(
        {"steps": [{"task_type": "APPROVE_CREDIT", "priority": 0}]}
    )
    gate_flag = FakeReasonGateway(
        {
            "steps": [{"task_type": "CREDIT_UNDERWRITING", "priority": 0}],
            "satisfied_gates": ["G2_GAP_REQUEST_APPROVAL"],
        }
    )

    for gateway in (unknown_type, gate_flag):
        outcome = await OrchestrationPlanner(TEMPLATE, gateway=gateway).plan(
            case_id=CASE_ID, correlation_id="corr-4", report=report
        )
        assert outcome.status == "REJECTED"
        assert outcome.plan.source == "DEFAULT"


@pytest.mark.asyncio
async def test_provider_unavailability_is_a_rule_based_fallback_not_a_model_fallback() -> None:
    report = makers_ready_report()
    gateway = FakeReasonGateway(InferenceUnavailableError("endpoint down"))
    planner = OrchestrationPlanner(
        TEMPLATE, gateway=gateway, prompt=OrchestratorPrompt("tin cậy")
    )

    outcome = await planner.plan(case_id=CASE_ID, correlation_id="corr-5", report=report)

    assert outcome.status == "REJECTED"
    assert outcome.plan == build_default_plan(report, TEMPLATE)
    # Exactly one provider attempt: no silent second model call.
    assert len(gateway.requests) == 1
