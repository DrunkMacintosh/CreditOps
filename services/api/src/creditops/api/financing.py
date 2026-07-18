"""Stage-2 FinancingRequest API: versioned read/append + the human confirm gate.

Master design section 5 stage 2, section 15.  Three surfaces, all case-scoped
and fail-closed (an unassigned actor gets the same indistinguishable 404 as a
missing case, so assignment membership is never disclosed):

- GET ``/financing-request/versions`` -- any assigned case participant reads the
  append-only version history.
- POST ``/financing-request/versions`` -- the ``INTAKE_OFFICER`` appends a NEW
  version (never edits a prior one); returns 201 and audits.
- POST ``/financing-request/confirm`` -- the ``INTAKE_OFFICER`` confirms the
  financing need.  The confirmed ``version`` MUST be the LATEST version or the
  request is rejected 409 ``STALE_FINANCING_VERSION`` (a new edit landed first);
  on success it satisfies ``HG_FINANCING_NEED_CONFIRMED`` through the
  orchestration repository (exactly as ``api/risk_review.py`` records G3 -- the
  gate-writing authority stays out of the financing port), re-ticks the
  orchestrator, and audits.

PROPOSED: ``HG_FINANCING_NEED_CONFIRMED`` is recorded human state surfaced to the
intake surface.  Whether intake-completion should later REQUIRE this gate is a
deferred decision and is intentionally NOT wired here.

This module is exported as ``router`` and is NOT registered in ``main.py`` here;
production wiring is a separate change (tests include the router directly).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    INTAKE_OFFICER_ROLE,
)
from creditops.application.ports.financing import FinancingRepository
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.financing_requests import (
    FinancingRequestDraft,
    FinancingRequestVersion,
)
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/financing-request", tags=["financing-request"]
)

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic disposition-reference prefix bound to the confirmed
#: financing-request version (no official SHB mapping).
_DISPOSITION_REF_PREFIX = "financing-request"


class FinancingVersionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    request_version: int = Field(serialization_alias="requestVersion")
    requested_amount: str = Field(serialization_alias="requestedAmount")
    purpose_vi: str = Field(serialization_alias="purpose")
    currency: str | None
    product_vi: str | None = Field(serialization_alias="product")
    term_months: int | None = Field(serialization_alias="termMonths")
    expected_use_date: date | None = Field(serialization_alias="expectedUseDate")
    repayment_source_vi: str | None = Field(serialization_alias="repaymentSource")
    repayment_plan_vi: str | None = Field(serialization_alias="repaymentPlan")
    proposed_security_vi: str | None = Field(serialization_alias="proposedSecurity")
    customer_own_funds: str | None = Field(serialization_alias="customerOwnFunds")
    connected_trade_products_vi: str | None = Field(
        serialization_alias="connectedTradeProducts"
    )
    working_capital_cycle_vi: str | None = Field(
        serialization_alias="workingCapitalCycle"
    )
    key_suppliers_customers_vi: str | None = Field(
        serialization_alias="keySuppliersCustomers"
    )
    proposed_cash_flow_controls_vi: str | None = Field(
        serialization_alias="proposedCashFlowControls"
    )
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class FinancingVersionsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    versions: list[FinancingVersionResponse]
    latest_version: int | None = Field(serialization_alias="latestVersion")


class ConfirmationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    request_version: int = Field(serialization_alias="requestVersion")
    disposition_ref: str = Field(serialization_alias="dispositionRef")


class AppendFinancingVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requested_amount: str = Field(
        alias="requestedAmount", min_length=1, max_length=30, pattern=r"^[1-9][0-9]*$"
    )
    purpose_vi: str = Field(alias="purpose", min_length=1, max_length=500)
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    product_vi: str | None = Field(default=None, alias="product", min_length=1, max_length=200)
    term_months: int | None = Field(default=None, alias="termMonths", gt=0, le=600)
    expected_use_date: date | None = Field(default=None, alias="expectedUseDate")
    repayment_source_vi: str | None = Field(
        default=None, alias="repaymentSource", min_length=1, max_length=2000
    )
    repayment_plan_vi: str | None = Field(
        default=None, alias="repaymentPlan", min_length=1, max_length=2000
    )
    proposed_security_vi: str | None = Field(
        default=None, alias="proposedSecurity", min_length=1, max_length=2000
    )
    customer_own_funds: str | None = Field(
        default=None,
        alias="customerOwnFunds",
        min_length=1,
        max_length=30,
        pattern=r"^(0|[1-9][0-9]*)$",
    )
    connected_trade_products_vi: str | None = Field(
        default=None, alias="connectedTradeProducts", min_length=1, max_length=2000
    )
    working_capital_cycle_vi: str | None = Field(
        default=None, alias="workingCapitalCycle", min_length=1, max_length=2000
    )
    key_suppliers_customers_vi: str | None = Field(
        default=None, alias="keySuppliersCustomers", min_length=1, max_length=2000
    )
    proposed_cash_flow_controls_vi: str | None = Field(
        default=None, alias="proposedCashFlowControls", min_length=1, max_length=2000
    )

    def to_draft(self) -> FinancingRequestDraft:
        return FinancingRequestDraft(
            requested_amount=self.requested_amount,
            purpose_vi=self.purpose_vi,
            currency=self.currency,
            product_vi=self.product_vi,
            term_months=self.term_months,
            expected_use_date=self.expected_use_date,
            repayment_source_vi=self.repayment_source_vi,
            repayment_plan_vi=self.repayment_plan_vi,
            proposed_security_vi=self.proposed_security_vi,
            customer_own_funds=self.customer_own_funds,
            connected_trade_products_vi=self.connected_trade_products_vi,
            working_capital_cycle_vi=self.working_capital_cycle_vi,
            key_suppliers_customers_vi=self.key_suppliers_customers_vi,
            proposed_cash_flow_controls_vi=self.proposed_cash_flow_controls_vi,
        )


class ConfirmFinancingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = Field(ge=1)
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_intake_role(actor: ActorContext) -> None:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tiếp nhận được yêu cầu.",
        )


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> FinancingRepository:
    repository = getattr(request.app.state, "financing_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="FINANCING_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ nhu cầu tài trợ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(FinancingRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository | None:
    repository = getattr(request.app.state, "orchestration_repository", None)
    return cast("OrchestrationRepository | None", repository)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> CaseRecord:
    """Return the assigned case record, or fail closed with an indistinguishable
    404 for an unassigned actor (assignment membership is never disclosed)."""

    uow_factory = getattr(request.app.state, "uow_factory", None)
    if uow_factory is None:
        raise ApiException(
            status_code=503,
            code="CASE_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    async with uow_factory(actor) as uow:
        record = await uow.cases.get_assigned(case_id, actor.actor_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
        )
    return cast(CaseRecord, record)


def _version_response(version: FinancingRequestVersion) -> FinancingVersionResponse:
    return FinancingVersionResponse(
        id=version.id,
        case_id=version.case_id,
        case_version=version.case_version,
        request_version=version.request_version,
        requested_amount=version.requested_amount,
        purpose_vi=version.purpose_vi,
        currency=version.currency,
        product_vi=version.product_vi,
        term_months=version.term_months,
        expected_use_date=version.expected_use_date,
        repayment_source_vi=version.repayment_source_vi,
        repayment_plan_vi=version.repayment_plan_vi,
        proposed_security_vi=version.proposed_security_vi,
        customer_own_funds=version.customer_own_funds,
        connected_trade_products_vi=version.connected_trade_products_vi,
        working_capital_cycle_vi=version.working_capital_cycle_vi,
        key_suppliers_customers_vi=version.key_suppliers_customers_vi,
        proposed_cash_flow_controls_vi=version.proposed_cash_flow_controls_vi,
        created_by=version.created_by,
        created_at=version.created_at,
    )


@router.get("/versions", response_model=FinancingVersionsResponse)
async def list_financing_versions(
    case_id: UUID, actor: Actor, request: Request
) -> FinancingVersionsResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    versions = await repository.list_versions(case_id)
    latest = max((v.request_version for v in versions), default=None)
    return FinancingVersionsResponse(
        versions=[_version_response(v) for v in versions],
        latest_version=latest,
    )


@router.post("/versions", response_model=FinancingVersionResponse, status_code=201)
async def append_financing_version(
    case_id: UUID,
    body: AppendFinancingVersionRequest,
    actor: Actor,
    request: Request,
) -> FinancingVersionResponse:
    _require_intake_role(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    version = await repository.append_version(
        case_id=case_id,
        case_version=record.version,
        fields=body.to_draft(),
        actor_id=actor.actor_id,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=record.version,
            event_type="FINANCING_REQUEST_VERSION_APPENDED",
            execution_id=uuid4(),
            artifact_type="FINANCING_REQUEST_VERSION",
            artifact_id=version.id,
            event_data={
                "actorId": str(actor.actor_id),
                "requestVersion": version.request_version,
            },
        )
    )
    return _version_response(version)


@router.post("/confirm", response_model=ConfirmationResponse)
async def confirm_financing_need(
    case_id: UUID,
    body: ConfirmFinancingRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> ConfirmationResponse:
    _require_intake_role(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    latest = await repository.latest_version(case_id)
    if latest is None:
        raise ApiException(
            status_code=404,
            code="FINANCING_REQUEST_NOT_AVAILABLE",
            message_vi="Chưa có phiên bản nhu cầu tài trợ để xác nhận.",
        )
    if body.version != latest.request_version:
        # A newer edit landed first: the human must re-confirm the current
        # version, never a stale one.  No gate is written.
        raise ApiException(
            status_code=409,
            code="STALE_FINANCING_VERSION",
            message_vi=(
                "Phiên bản nhu cầu tài trợ đã thay đổi; vui lòng xác nhận phiên "
                "bản mới nhất."
            ),
            details={"expectedVersion": latest.request_version},
        )

    orchestration = _orchestration_repository(request)
    if orchestration is None:
        raise ApiException(
            status_code=503,
            code="ORCHESTRATION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ điều phối chưa sẵn sàng.",
            retryable=True,
        )

    disposition_ref = f"{_DISPOSITION_REF_PREFIX}:{body.version}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_FINANCING_NEED_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=record.version,
            event_type="FINANCING_NEED_CONFIRMED",
            execution_id=uuid4(),
            artifact_type="FINANCING_REQUEST_VERSION",
            artifact_id=latest.id,
            event_data={
                "actorId": str(actor.actor_id),
                "requestVersion": body.version,
                "rationale": body.rationale_vi,
            },
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_FIN:{body.version}"
    )
    response.status_code = 200
    return ConfirmationResponse(
        gate_type=GateType.HG_FINANCING_NEED_CONFIRMED.value,
        status=GateStatus.SATISFIED.value,
        request_version=body.version,
        disposition_ref=disposition_ref,
    )


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after a gate satisfaction.

    The plan task + outbox event commit durably; the queue publish is
    best-effort here (the recovery dispatch picks up anything left).  A tick
    failure must never fail the human's already-recorded confirmation, but it
    is logged, never silent.
    """

    try:
        result = await KickoffOrchestration(orchestration_repository).execute(
            case_id, trigger_ref=trigger_ref
        )
        queue = getattr(request.app.state, "agent_task_queue", None)
        if queue is not None:
            await DispatchOutbox(
                orchestration_repository,
                queue,
                worker_dispatcher=getattr(
                    request.app.state, "worker_dispatcher", None
                ),
            ).run()
        log_event(
            _logger,
            logging.INFO,
            "Orchestration retick after gate satisfaction",
            {
                "event": "orchestration_retick",
                "trigger": trigger_ref,
                "created": result.created,
            },
        )
    except Exception:
        log_event(
            _logger,
            logging.ERROR,
            "Orchestration retick failed; the confirmation is durable and the "
            "case can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )
