"""Audit-events read API: a cursor-paginated case audit timeline.

Mirrors the read-only maker-output APIs (``api/risk_review.py``,
``api/underwriting.py``, ``api/legal.py``): a case-participant role is
required, row access is the case-assignment check, and an unassigned actor
receives an indistinguishable 404. There is no write surface here -- audit
events are append-only and written exclusively by the writers named in
``application/ports/orchestration.py`` (the orchestrator engine and the
human-action audit repository); this router only ever reads.

``event_data`` passes through as-is: by construction the writers only ever
record metadata there, never a secret, credential, or prompt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.ports.orchestration import AuditEventRow, OrchestrationRepository
from creditops.application.unit_of_work import ActorContext

router = APIRouter(prefix="/api/v1/cases/{case_id}/audit-events", tags=["audit"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_version: int = Field(serialization_alias="caseVersion")
    event_type: str = Field(serialization_alias="eventType")
    actor_type: str = Field(serialization_alias="actorType")
    actor_id: UUID | None = Field(serialization_alias="actorId")
    artifact_type: str = Field(serialization_alias="artifactType")
    artifact_id: UUID = Field(serialization_alias="artifactId")
    event_data: dict[str, object] = Field(serialization_alias="eventData")
    created_at: datetime = Field(serialization_alias="createdAt")


class AuditEventListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    events: list[AuditEventResponse]
    next_cursor: UUID | None = Field(serialization_alias="nextCursor")


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


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


def _orchestration_repository(request: Request) -> OrchestrationRepository:
    repository = getattr(request.app.state, "orchestration_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="AUDIT_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ nhật ký hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(OrchestrationRepository, repository)


def _event_response(row: AuditEventRow) -> AuditEventResponse:
    return AuditEventResponse(
        id=row.id,
        case_version=row.case_version,
        event_type=row.event_type,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        artifact_type=row.artifact_type,
        artifact_id=row.artifact_id,
        event_data=dict(row.event_data),
        created_at=row.created_at,
    )


@router.get("", response_model=AuditEventListResponse)
async def list_audit_events(
    case_id: UUID,
    actor: Actor,
    request: Request,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
) -> AuditEventListResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _orchestration_repository(request)
    events, next_cursor = await repository.list_audit_events(
        case_id, cursor=cursor, limit=limit
    )
    return AuditEventListResponse(
        events=[_event_response(event) for event in events],
        next_cursor=next_cursor,
    )
