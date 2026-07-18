"""Assigned-intake completion + immutable handoff read API (master design
section 5 stage 3, section 9, section 15).

POST ``/intake-completion`` is the ONLY human surface that turns a
fully-dispositioned intake evidence state into the immutable ``IntakeHandoff``
that deterministically satisfies ``G1_INTAKE_COMPLETE``.  It is restricted to
the ``INTAKE_OFFICER`` human role AND the case assignment (an unassigned actor
gets the same indistinguishable 404 as a missing case).  Completeness is the
domain validator's verdict: when it fails the endpoint returns 409
``INTAKE_INCOMPLETE`` listing the unresolved reasons in ``details`` and nothing
is persisted or scheduled.  On success it returns 201 with the handoff, or 200
with ``created=false`` on an idempotent repeat.

GET ``/handoffs`` lets any assigned case participant read the current immutable
handoff, or a 404 ``HANDOFF_NOT_AVAILABLE`` before intake completes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    INTAKE_OFFICER_ROLE,
)
from creditops.application.ports.intake import IntakeRepository
from creditops.application.ports.orchestration import OrchestrationRepository
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.complete_intake import (
    CompleteIntake,
    IntakeIncompleteError,
)
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.observability import log_event

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["intake"])

_logger = logging.getLogger(__name__)


class IntakeCompletionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    handoff_id: UUID = Field(serialization_alias="handoffId")
    case_version: int = Field(serialization_alias="caseVersion")
    state: str
    created: bool


class HandoffResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    handoff_id: UUID = Field(serialization_alias="handoffId")
    state: str
    case_version: int = Field(serialization_alias="caseVersion")
    created_at: datetime = Field(serialization_alias="createdAt")


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


def _repository(request: Request) -> IntakeRepository:
    repository = getattr(request.app.state, "intake_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="INTAKE_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ tiếp nhận chưa sẵn sàng.",
            retryable=True,
        )
    return cast(IntakeRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository | None:
    repository = getattr(request.app.state, "orchestration_repository", None)
    return cast("OrchestrationRepository | None", repository)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> CaseRecord:
    """Return the assigned case record, or fail closed with an
    indistinguishable 404 for an unassigned actor (assignment membership is
    never disclosed)."""

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


@router.post(
    "/intake-completion", response_model=IntakeCompletionResponse, status_code=201
)
async def complete_intake(
    case_id: UUID, actor: Actor, request: Request, response: Response
) -> IntakeCompletionResponse:
    _require_intake_role(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    orchestration = _orchestration_repository(request)

    try:
        result = await CompleteIntake(repository, orchestration).execute(
            case_id, record.version, actor.actor_id
        )
    except IntakeIncompleteError as exc:
        raise ApiException(
            status_code=409,
            code="INTAKE_INCOMPLETE",
            message_vi="Hồ sơ tiếp nhận chưa hoàn tất; còn bằng chứng chưa được xử lý.",
            details={
                "reasons": list(exc.reasons),
                "unresolvedCount": len(exc.reasons),
            },
        ) from exc

    if result.created:
        # The use case created the plan task + outbox event atomically; publish
        # it here best-effort (the recovery sweep covers anything left behind).
        await _dispatch_outbox(request, orchestration)
    else:
        response.status_code = 200

    return IntakeCompletionResponse(
        handoff_id=result.handoff_id,
        case_version=result.case_version,
        state=result.state,
        created=result.created,
    )


@router.get("/handoffs", response_model=HandoffResponse)
async def get_handoff(case_id: UUID, actor: Actor, request: Request) -> HandoffResponse:
    _require_participant(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    current = await repository.load_current_handoff(case_id, record.version)
    if current is None:
        raise ApiException(
            status_code=404,
            code="HANDOFF_NOT_AVAILABLE",
            message_vi="Chưa có bản bàn giao tiếp nhận cho hồ sơ này.",
        )
    return HandoffResponse(
        handoff_id=current.id,
        state=current.state,
        case_version=current.case_version,
        created_at=current.created_at,
    )


async def _dispatch_outbox(
    request: Request, orchestration: OrchestrationRepository | None
) -> None:
    """Best-effort queue publish after an intake handoff re-tick.  A publish
    failure never fails the human's already-durable handoff; the scheduled
    recovery sweep will deliver anything left undispatched."""

    if orchestration is None:
        return
    queue = getattr(request.app.state, "agent_task_queue", None)
    if queue is None:
        return
    try:
        await DispatchOutbox(
            orchestration,
            queue,
            worker_dispatcher=getattr(request.app.state, "worker_dispatcher", None),
        ).run()
    except Exception:
        log_event(
            _logger,
            logging.ERROR,
            "Intake handoff outbox dispatch failed; the handoff is durable and "
            "the recovery sweep will deliver the tick",
            {"event": "intake_handoff_dispatch_failed"},
        )
