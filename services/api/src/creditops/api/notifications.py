"""Credit notification API: deterministic draft, human approval gate, mock delivery.

Master design section 5 giai đoạn 7 ("Thông báo tín dụng cho khách hàng").  This
is the ONLY human surface for the stage-7 notification lifecycle; it exports
``router`` only (mounting in ``main.py`` is a deferred lead decision).

Three POSTs and one GET, all case-participant scoped with an indistinguishable
404 for unassigned actors:

- ``POST ""`` idempotently creates the ``CreditNotificationDraft`` for the
  current case version.  The draft derives ONLY from a recorded
  ``HumanCreditDecision`` whose decision PERMITS a notification; if none permits,
  409 ``DECISION_DOES_NOT_PERMIT_NOTIFICATION``.  No agent path exists; the
  content is deterministic template output (never an LLM generation).  Restricted
  to the ``OPS_OFFICER`` human role.
- ``POST "/approve"`` is a human gate write: it satisfies
  ``HG_CREDIT_NOTIFICATION_APPROVED`` through the orchestration repository (never
  the engine), audits, and reticks -- copying the ``api/underwriting.py`` gate
  pattern.
- ``POST "/deliver"`` records the LABELLED MOCK ``CommunicationReceipt``.  It
  enforces separation of actor (Credit Operations checker vs. maker): the
  deliverer MUST be a DIFFERENT actor than the draft creator, else 409
  ``SAME_ACTOR_FORBIDDEN``.  It requires ``HG_CREDIT_NOTIFICATION_APPROVED``
  SATISFIED for the version, else 409 ``GATE_NOT_SATISFIED``.  Nothing is ever
  sent; ``delivered_via`` is always the mock channel and the receipt pins the
  exact content sha256.
- ``GET ""`` returns the current draft, its receipt (if any), and the gate status
  for any case participant.

ASSUMPTION: no official SHB role mapping exists (docs/AGENT_ARCHITECTURE.md); the
draft/approve/deliver authority is modelled on the synthetic ``OPS_OFFICER`` human
role plus a case assignment, with duty separation enforced by ACTOR identity on
delivery.  All data is synthetic and created solely for demonstration.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.notifications import (
    DecisionDoesNotPermitNotificationError,
    NotificationRepository,
    RecordedCommunicationReceipt,
    RecordedNotificationDraft,
)
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/notifications", tags=["notifications"]
)

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic disposition-reference prefix for the approval gate, bound
#: to the exact draft approved (no official SHB mapping).
_APPROVAL_DISPOSITION_REF_PREFIX = "notification-draft"

#: Default labelled receipt note for a mock delivery (data is synthetic).
_DEFAULT_RECEIPT_NOTE_VI = "Đã ghi nhận giao thông báo qua kênh mock (dữ liệu mô phỏng)."


class NotificationDraftResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    decision_id: UUID = Field(serialization_alias="decisionId")
    content_vi: str = Field(serialization_alias="content")
    content_hash: str = Field(serialization_alias="contentHash")
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class CommunicationReceiptResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    draft_id: UUID = Field(serialization_alias="draftId")
    delivered_via: str = Field(serialization_alias="deliveredVia")
    content_hash: str = Field(serialization_alias="contentHash")
    receipt_note_vi: str | None = Field(serialization_alias="receiptNote")
    recorded_by: UUID = Field(serialization_alias="recordedBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class NotificationStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    draft: NotificationDraftResponse | None
    receipt: CommunicationReceiptResponse | None
    approval_gate_status: str = Field(serialization_alias="approvalGateStatus")


class GateWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    draft_id: UUID = Field(serialization_alias="draftId")
    disposition_ref: str = Field(serialization_alias="dispositionRef")


class ApproveNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    draft_id: UUID = Field(alias="draftId")
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


class DeliverNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_note_vi: str | None = Field(
        alias="receiptNote", default=None, min_length=1, max_length=4000
    )


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_ops_officer(actor: ActorContext) -> None:
    if OPS_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò vận hành tín dụng được yêu cầu.",
        )


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> NotificationRepository:
    repository = getattr(request.app.state, "notification_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="NOTIFICATION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ thông báo tín dụng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(NotificationRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository:
    repository = getattr(request.app.state, "orchestration_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="ORCHESTRATION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ điều phối chưa sẵn sàng.",
            retryable=True,
        )
    return cast(OrchestrationRepository, repository)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> None:
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


def _draft_not_available() -> ApiException:
    return ApiException(
        status_code=404,
        code="NOTIFICATION_DRAFT_NOT_AVAILABLE",
        message_vi="Chưa có bản nháp thông báo tín dụng cho hồ sơ này.",
    )


@router.post("", response_model=NotificationDraftResponse, status_code=201)
async def create_notification_draft(
    case_id: UUID,
    actor: Actor,
    request: Request,
    response: Response,
) -> NotificationDraftResponse:
    """Idempotently create the notification draft for the current case version.

    Derives ONLY from a permitting ``HumanCreditDecision`` (else 409); the content
    is deterministic and no agent sends anything.  A repeat returns the existing
    draft with 200.
    """

    _require_ops_officer(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    try:
        draft = await repository.create_draft(
            draft_id=uuid4(), case_id=case_id, created_by=actor.actor_id
        )
    except DecisionDoesNotPermitNotificationError as exc:
        raise ApiException(
            status_code=409,
            code="DECISION_DOES_NOT_PERMIT_NOTIFICATION",
            message_vi=(
                "Chưa thể tạo thông báo: chưa có quyết định phê duyệt cho phép "
                "phát hành thông báo tín dụng."
            ),
        ) from exc
    if not draft.created:
        response.status_code = 200
    return _draft_response(draft)


@router.post("/approve", response_model=GateWriteResponse, status_code=200)
async def approve_notification(
    case_id: UUID,
    body: ApproveNotificationRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> GateWriteResponse:
    """Satisfy ``HG_CREDIT_NOTIFICATION_APPROVED`` for the current draft.

    A human gate write (never the engine): the referenced ``draftId`` must be the
    current draft, else 409 ``STALE_NOTIFICATION_DRAFT``.  Audits and reticks.
    """

    _require_ops_officer(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    draft = await repository.load_draft(case_id)
    if draft is None:
        raise _draft_not_available()
    if draft.id != body.draft_id:
        raise ApiException(
            status_code=409,
            code="STALE_NOTIFICATION_DRAFT",
            message_vi=(
                "Bản nháp thông báo đã thay đổi; vui lòng xem lại bản mới nhất "
                "trước khi phê duyệt."
            ),
            details={"currentDraftId": str(draft.id)},
        )
    orchestration = _orchestration_repository(request)
    disposition_ref = f"{_APPROVAL_DISPOSITION_REF_PREFIX}:{draft.id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=draft.case_version,
        gate_type=GateType.HG_CREDIT_NOTIFICATION_APPROVED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await orchestration.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=draft.case_version,
            event_type="CREDIT_NOTIFICATION_APPROVED",
            execution_id=uuid4(),
            artifact_type="CREDIT_NOTIFICATION_DRAFT",
            artifact_id=draft.id,
            event_data={
                "actorId": str(actor.actor_id),
                "draftId": str(draft.id),
                "rationale": body.rationale_vi,
            },
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_NOTIF:{draft.id}"
    )
    response.status_code = 200
    return GateWriteResponse(
        gate_type=GateType.HG_CREDIT_NOTIFICATION_APPROVED.value,
        status=GateStatus.SATISFIED.value,
        draft_id=draft.id,
        disposition_ref=disposition_ref,
    )


@router.post("/deliver", response_model=CommunicationReceiptResponse, status_code=201)
async def deliver_notification(
    case_id: UUID,
    body: DeliverNotificationRequest,
    actor: Actor,
    request: Request,
) -> CommunicationReceiptResponse:
    """Record the LABELLED MOCK delivery receipt for the approved draft.

    Enforces separation of actor (deliverer != draft creator, else 409
    ``SAME_ACTOR_FORBIDDEN``) and requires the approval gate SATISFIED (else 409
    ``GATE_NOT_SATISFIED``).  Nothing is sent; the receipt pins the exact hash.
    """

    _require_ops_officer(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    draft = await repository.load_draft(case_id)
    if draft is None:
        raise _draft_not_available()

    # Separation of duties: the maker who drafted may not also deliver.
    if draft.created_by == actor.actor_id:
        raise ApiException(
            status_code=409,
            code="SAME_ACTOR_FORBIDDEN",
            message_vi=(
                "Người tạo bản nháp không được đồng thời thực hiện giao thông báo; "
                "cần một người khác (tách biệt nhiệm vụ)."
            ),
        )

    orchestration = _orchestration_repository(request)
    gate_satisfied = await _approval_gate_satisfied(
        orchestration, case_id, draft.case_version
    )
    if not gate_satisfied:
        raise ApiException(
            status_code=409,
            code="GATE_NOT_SATISFIED",
            message_vi=(
                "Chưa thể giao thông báo: cổng phê duyệt "
                "HG_CREDIT_NOTIFICATION_APPROVED chưa được thỏa mãn."
            ),
        )

    receipt = await repository.record_mock_delivery(
        receipt_id=uuid4(),
        draft_id=draft.id,
        content_hash=draft.content_hash,
        receipt_note_vi=body.receipt_note_vi or _DEFAULT_RECEIPT_NOTE_VI,
        recorded_by=actor.actor_id,
        gate_satisfied=gate_satisfied,
    )
    return _receipt_response(receipt)


@router.get("", response_model=NotificationStatusResponse)
async def get_notification(
    case_id: UUID, actor: Actor, request: Request
) -> NotificationStatusResponse:
    """Read the current draft, its mock receipt (if any), and the gate status."""

    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    draft = await repository.load_draft(case_id)
    receipt = (
        await repository.load_receipt(draft.id) if draft is not None else None
    )
    gate_status = GateStatus.OPEN
    orchestration = getattr(request.app.state, "orchestration_repository", None)
    if orchestration is not None and draft is not None:
        satisfied = await _approval_gate_satisfied(
            cast(OrchestrationRepository, orchestration), case_id, draft.case_version
        )
        gate_status = GateStatus.SATISFIED if satisfied else GateStatus.OPEN
    return NotificationStatusResponse(
        draft=_draft_response(draft) if draft is not None else None,
        receipt=_receipt_response(receipt) if receipt is not None else None,
        approval_gate_status=gate_status.value,
    )


async def _approval_gate_satisfied(
    orchestration: OrchestrationRepository, case_id: UUID, case_version: int
) -> bool:
    """Whether HG_CREDIT_NOTIFICATION_APPROVED is SATISFIED for the version.

    Reads the stored gate directly (the engine never satisfies this gate); a
    missing snapshot or gate reads as not-yet-satisfied, fail closed.
    """

    snapshot = await orchestration.load_snapshot(case_id)
    if snapshot is None:
        return False
    return any(
        gate.gate_type is GateType.HG_CREDIT_NOTIFICATION_APPROVED
        and gate.case_version == case_version
        and gate.status is GateStatus.SATISFIED
        for gate in snapshot.gates
    )


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after a gate satisfaction.

    Mirrors ``api/underwriting.py``: the plan task + outbox event commit durably,
    the queue publish is best-effort, and a tick failure never fails the human's
    already-recorded approval -- but it is logged, never silent.
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
                worker_dispatcher=getattr(request.app.state, "worker_dispatcher", None),
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
            "Orchestration retick failed; the approval is durable and the case "
            "can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )


def _draft_response(draft: RecordedNotificationDraft) -> NotificationDraftResponse:
    return NotificationDraftResponse(
        id=draft.id,
        case_id=draft.case_id,
        case_version=draft.case_version,
        decision_id=draft.decision_id,
        content_vi=draft.content_vi,
        content_hash=draft.content_hash,
        created_by=draft.created_by,
        created_at=draft.created_at,
    )


def _receipt_response(
    receipt: RecordedCommunicationReceipt,
) -> CommunicationReceiptResponse:
    return CommunicationReceiptResponse(
        id=receipt.id,
        draft_id=receipt.draft_id,
        delivered_via=receipt.delivered_via,
        content_hash=receipt.content_hash,
        receipt_note_vi=receipt.receipt_note_vi,
        recorded_by=receipt.recorded_by,
        created_at=receipt.created_at,
    )
