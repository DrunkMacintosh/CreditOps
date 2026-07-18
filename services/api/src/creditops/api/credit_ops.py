"""Credit Operations API: read-only package status + human authorization writes.

GET mirrors the read-only agent-output APIs (``api/underwriting.py``,
``api/legal.py``, ``api/risk_review.py``): a case-participant role is
required, row access is the case-assignment check, and an unassigned actor
receives an indistinguishable 404.  There is no way to write a package here
-- packages are append-only and written exclusively by the worker
(application/credit_ops/processor.py).

The two POSTs are the ONLY human write surfaces for a credit-ops package:

- ``POST .../actions/{action_id}/authorize`` records an append-only human
  authorization of ONE proposed action.  It records authority ONLY -- no
  code path anywhere in this codebase executes an action; the action's
  ``execution_status`` enum has no EXECUTED member at all.
- ``POST .../document-requests/{request_id}/approve`` records an append-only
  human approval of ONE drafted document request.  Approval flips only the
  derived ``approval_status`` view; it never mutates the package row and
  never sends anything (no send mechanism exists).

Both POSTs are restricted to the OPS_OFFICER human role.  After recording,
each handler re-derives its gate (``derive_g4_status`` /
``derive_g2_status``, application/orchestration/gates.py) and, only if the
pure derivation says SATISFIED, calls the orchestration repository to record
it -- the credit-ops worker never calls this; only a human record can
trigger it, and only through the deterministic derivation.  Authorizing a
nonexistent or foreign action/request 404s without a capability leak
(indistinguishable from a case the actor cannot access).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.gates import derive_g2_status, derive_g4_status
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.credit_ops import CreditOpsRepository
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.unit_of_work import ActorContext
from creditops.domain.credit_ops import DocumentRequestApprovalStatus
from creditops.domain.orchestration import GateStatus, GateType

router = APIRouter(prefix="/api/v1/cases/{case_id}/credit-ops", tags=["credit-ops"])


class HandoffStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    handoff_id: UUID = Field(serialization_alias="handoffId")
    state: str
    created_at: datetime = Field(serialization_alias="createdAt")


class AuthorizationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    action_id: UUID = Field(serialization_alias="actionId")
    actor_id: UUID = Field(serialization_alias="actorId")
    actor_role: str = Field(serialization_alias="actorRole")
    rationale_vi: str = Field(serialization_alias="rationale")
    created_at: datetime = Field(serialization_alias="createdAt")


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    request_id: UUID = Field(serialization_alias="requestId")
    actor_id: UUID = Field(serialization_alias="actorId")
    actor_role: str = Field(serialization_alias="actorRole")
    rationale_vi: str = Field(serialization_alias="rationale")
    created_at: datetime = Field(serialization_alias="createdAt")


class ProposedActionStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    action_type: str = Field(serialization_alias="actionType")
    description_vi: str = Field(serialization_alias="description")
    execution_status: str = Field(serialization_alias="executionStatus")
    required_authorization: dict[str, object] = Field(
        serialization_alias="requiredAuthorization"
    )
    authorized: bool
    authorizations: list[AuthorizationResponse]


class DocumentRequestStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    originating_gap_id: UUID = Field(serialization_alias="originatingGapId")
    request_text_vi: str = Field(serialization_alias="requestText")
    blocking_level: str = Field(serialization_alias="blockingLevel")
    approval_status: str = Field(serialization_alias="approvalStatus")
    approvals: list[ApprovalResponse]


class CreditOpsStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    package_id: UUID = Field(serialization_alias="packageId")
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    agent_role: str = Field(serialization_alias="agentRole")
    execution_id: UUID = Field(serialization_alias="executionId")
    prompt_version: str = Field(serialization_alias="promptVersion")
    created_at: datetime = Field(serialization_alias="createdAt")
    handoff: HandoffStatusResponse | None
    package_completeness: dict[str, object] = Field(serialization_alias="packageCompleteness")
    evidence_consolidation: dict[str, object] = Field(
        serialization_alias="evidenceConsolidation"
    )
    draft_memo: dict[str, object] = Field(serialization_alias="draftMemo")
    document_requests: list[DocumentRequestStatusResponse] = Field(
        serialization_alias="documentRequests"
    )
    proposed_actions: list[ProposedActionStatusResponse] = Field(
        serialization_alias="proposedActions"
    )
    g2_gate_status: str = Field(serialization_alias="g2GateStatus")
    g4_gate_status: str = Field(serialization_alias="g4GateStatus")


class RecordAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _require_ops_officer(actor: ActorContext) -> None:
    if OPS_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò vận hành tín dụng được yêu cầu.",
        )


def _repository(request: Request) -> CreditOpsRepository:
    repository = getattr(request.app.state, "credit_ops_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="CREDIT_OPS_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ vận hành tín dụng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(CreditOpsRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository | None:
    repository = getattr(request.app.state, "orchestration_repository", None)
    return cast("OrchestrationRepository | None", repository)


async def _assert_case_access(request: Request, actor: ActorContext, case_id: UUID) -> None:
    """Fail closed with an indistinguishable 404 for unassigned actors."""
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


def _package_not_available() -> ApiException:
    return ApiException(
        status_code=404,
        code="CREDIT_OPS_NOT_AVAILABLE",
        message_vi="Chưa có gói hồ sơ vận hành tín dụng cho hồ sơ này.",
    )


def _ids_in(package: Any, key: str) -> set[str]:
    raw = package.get(key, []) if isinstance(package, dict) else []
    if not isinstance(raw, list):
        return set()
    return {str(item["id"]) for item in raw if isinstance(item, dict) and "id" in item}


@router.get("", response_model=CreditOpsStatusResponse)
async def get_credit_ops(
    case_id: UUID, actor: Actor, request: Request
) -> CreditOpsStatusResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    record = await repository.load_latest_package(case_id)
    if record is None:
        raise _package_not_available()
    authorizations = await repository.load_action_authorizations(record.package_id)
    approvals = await repository.load_document_request_approvals(record.package_id)
    by_action: dict[UUID, list[Any]] = {}
    for authorization in authorizations:
        by_action.setdefault(authorization.action_id, []).append(authorization)
    by_request: dict[UUID, list[Any]] = {}
    for approval in approvals:
        by_request.setdefault(approval.request_id, []).append(approval)

    raw_actions = record.package.get("proposed_actions", [])
    actions: list[ProposedActionStatusResponse] = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            action_id = UUID(str(item["id"]))
            bound = by_action.get(action_id, [])
            actions.append(
                ProposedActionStatusResponse(
                    id=action_id,
                    action_type=str(item.get("action_type", "")),
                    description_vi=str(item.get("description_vi", "")),
                    execution_status=str(item.get("execution_status", "")),
                    required_authorization=dict(item.get("required_authorization", {})),
                    authorized=bool(bound),
                    authorizations=[_authorization_response(a) for a in bound],
                )
            )

    raw_requests = record.package.get("document_requests", [])
    requests: list[DocumentRequestStatusResponse] = []
    if isinstance(raw_requests, list):
        for item in raw_requests:
            if not isinstance(item, dict):
                continue
            request_id = UUID(str(item["id"]))
            bound = by_request.get(request_id, [])
            # The derived approval view: APPROVED iff a human approval record
            # exists; the stored package row itself always stays
            # PENDING_APPROVAL and is never mutated.
            derived_status = (
                DocumentRequestApprovalStatus.APPROVED
                if bound
                else DocumentRequestApprovalStatus.PENDING_APPROVAL
            )
            requests.append(
                DocumentRequestStatusResponse(
                    id=request_id,
                    originating_gap_id=UUID(str(item["originating_gap_id"])),
                    request_text_vi=str(item.get("request_text_vi", "")),
                    blocking_level=str(item.get("blocking_level", "")),
                    approval_status=derived_status.value,
                    approvals=[_approval_response(a) for a in bound],
                )
            )

    g2_status = derive_g2_status(
        package_exists=True,
        request_ids={r.id for r in requests},
        approved_request_ids=set(by_request.keys()),
    )
    g4_status = derive_g4_status(
        package_exists=True,
        action_ids={a.id for a in actions},
        authorized_action_ids=set(by_action.keys()),
    )

    return CreditOpsStatusResponse(
        package_id=record.package_id,
        case_id=record.case_id,
        case_version=record.case_version,
        agent_role=record.agent_role,
        execution_id=record.execution_id,
        prompt_version=record.prompt_version,
        created_at=record.created_at,
        handoff=(
            HandoffStatusResponse(
                handoff_id=record.handoff_id,
                state=record.handoff_state,
                created_at=record.handoff_created_at,
            )
            if record.handoff_id is not None
            and record.handoff_state is not None
            and record.handoff_created_at is not None
            else None
        ),
        package_completeness=dict(
            cast("dict[str, object]", record.package.get("package_completeness", {}))
        ),
        evidence_consolidation=dict(
            cast("dict[str, object]", record.package.get("evidence_consolidation", {}))
        ),
        draft_memo=dict(cast("dict[str, object]", record.package.get("draft_memo", {}))),
        document_requests=requests,
        proposed_actions=actions,
        g2_gate_status=g2_status.value,
        g4_gate_status=g4_status.value,
    )


@router.post(
    "/actions/{action_id}/authorize",
    response_model=AuthorizationResponse,
    status_code=201,
)
async def authorize_action(
    case_id: UUID,
    action_id: UUID,
    body: RecordAuthorizationRequest,
    actor: Actor,
    request: Request,
) -> AuthorizationResponse:
    """Record ONE append-only human authorization for ONE proposed action.

    Records authority only; nothing is executed here or anywhere else.  A
    nonexistent/foreign action id 404s without a capability leak.
    """

    _require_ops_officer(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    record = await repository.load_latest_package(case_id)
    if record is None:
        raise _package_not_available()
    if str(action_id) not in _ids_in(record.package, "proposed_actions"):
        raise ApiException(
            status_code=404,
            code="ACTION_NOT_FOUND",
            message_vi="Không tìm thấy hành động đề xuất trong gói hồ sơ này.",
        )

    authorization = await repository.record_action_authorization(
        authorization_id=uuid4(),
        package_id=record.package_id,
        action_id=action_id,
        actor_id=actor.actor_id,
        actor_role=OPS_OFFICER_ROLE,
        rationale_vi=body.rationale_vi,
    )
    await repository.append_audit(
        _human_audit_event(
            record,
            "CREDIT_OPS_ACTION_AUTHORIZED",
            {
                "authorizationId": str(authorization.id),
                "actionId": str(action_id),
                "actorId": str(actor.actor_id),
                "actorRole": OPS_OFFICER_ROLE,
            },
        )
    )
    await _maybe_satisfy_g4(request, repository, record, actor)
    return _authorization_response(authorization)


@router.post(
    "/document-requests/{request_id}/approve",
    response_model=ApprovalResponse,
    status_code=201,
)
async def approve_document_request(
    case_id: UUID,
    request_id: UUID,
    body: RecordAuthorizationRequest,
    actor: Actor,
    request: Request,
) -> ApprovalResponse:
    """Record ONE append-only human approval for ONE drafted document request.

    Flips only the derived approval view; never mutates the package row and
    never sends anything (no send mechanism exists in this codebase).
    """

    _require_ops_officer(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    record = await repository.load_latest_package(case_id)
    if record is None:
        raise _package_not_available()
    if str(request_id) not in _ids_in(record.package, "document_requests"):
        raise ApiException(
            status_code=404,
            code="DOCUMENT_REQUEST_NOT_FOUND",
            message_vi="Không tìm thấy yêu cầu bổ sung tài liệu trong gói hồ sơ này.",
        )

    approval = await repository.record_document_request_approval(
        approval_id=uuid4(),
        package_id=record.package_id,
        request_id=request_id,
        actor_id=actor.actor_id,
        actor_role=OPS_OFFICER_ROLE,
        rationale_vi=body.rationale_vi,
    )
    await repository.append_audit(
        _human_audit_event(
            record,
            "CREDIT_OPS_DOCUMENT_REQUEST_APPROVED",
            {
                "approvalId": str(approval.id),
                "requestId": str(request_id),
                "actorId": str(actor.actor_id),
                "actorRole": OPS_OFFICER_ROLE,
            },
        )
    )
    await _maybe_satisfy_g2(request, repository, record, actor)
    return _approval_response(approval)


async def _maybe_satisfy_g4(
    request: Request, repository: CreditOpsRepository, record: Any, actor: ActorContext
) -> None:
    """Re-derive G4 after an authorization and record it ONLY if now SATISFIED.

    This is the human-triggered write path described in
    ``application/orchestration/gates.py::derive_g4_status``; the credit-ops
    worker processor never calls this.
    """

    orchestration_repository = _orchestration_repository(request)
    if orchestration_repository is None:
        return
    authorizations = await repository.load_action_authorizations(record.package_id)
    status = derive_g4_status(
        package_exists=True,
        action_ids={UUID(value) for value in _ids_in(record.package, "proposed_actions")},
        authorized_action_ids={a.action_id for a in authorizations},
    )
    if status is not GateStatus.SATISFIED:
        return
    await orchestration_repository.ensure_gate(
        case_id=record.case_id,
        case_version=record.case_version,
        gate_type=GateType.G4_OPS_AUTHORIZATION,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=f"credit-ops-package:{record.package_id}",
    )


async def _maybe_satisfy_g2(
    request: Request, repository: CreditOpsRepository, record: Any, actor: ActorContext
) -> None:
    """Re-derive G2 after an approval and record it ONLY if now SATISFIED."""

    orchestration_repository = _orchestration_repository(request)
    if orchestration_repository is None:
        return
    approvals = await repository.load_document_request_approvals(record.package_id)
    status = derive_g2_status(
        package_exists=True,
        request_ids={UUID(value) for value in _ids_in(record.package, "document_requests")},
        approved_request_ids={a.request_id for a in approvals},
    )
    if status is not GateStatus.SATISFIED:
        return
    await orchestration_repository.ensure_gate(
        case_id=record.case_id,
        case_version=record.case_version,
        gate_type=GateType.G2_GAP_REQUEST_APPROVAL,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=f"credit-ops-package:{record.package_id}",
    )


def _human_audit_event(
    record: Any, event_type: str, event_data: dict[str, object]
) -> OrchestrationAuditEvent:
    return OrchestrationAuditEvent(
        case_id=record.case_id,
        case_version=record.case_version,
        event_type=event_type,
        execution_id=uuid4(),
        artifact_type="CREDIT_OPS_PACKAGE",
        artifact_id=record.package_id,
        event_data=event_data,
    )


def _authorization_response(authorization: Any) -> AuthorizationResponse:
    return AuthorizationResponse(
        id=authorization.id,
        action_id=authorization.action_id,
        actor_id=authorization.actor_id,
        actor_role=authorization.actor_role,
        rationale_vi=authorization.rationale_vi,
        created_at=authorization.created_at,
    )


def _approval_response(approval: Any) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        request_id=approval.request_id,
        actor_id=approval.actor_id,
        actor_role=approval.actor_role,
        rationale_vi=approval.rationale_vi,
        created_at=approval.created_at,
    )
