"""Cross-case audit search API: GET /api/v1/audit-events.

The ``/nhat-ky-kiem-toan`` surface (master design section 17.3): the read-only
auditor timeline spanning EVERY case.  Unlike the per-case
``/cases/{id}/audit-events`` endpoint (row access = case assignment), this
estate-wide view is gated on the synthetic ``AUDITOR`` role alone (fail closed
403); there is no case-assignment check because an auditor is, by design, not a
case participant.  It shares the SAME keyset pagination contract as the per-case
endpoint and is strictly read-only -- audit events are append-only and written
only by the orchestration writers.

An optional ``eventType`` filter is validated against a conservative regex
before it reaches the repository.  This router is deliberately NOT wired into
``main.py`` here; it exports ``router`` for the lead to mount.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.ports.orchestration import AuditEventRow, OrchestrationRepository
from creditops.application.unit_of_work import ActorContext

router = APIRouter(prefix="/api/v1/audit-events", tags=["audit-search"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# PROPOSED synthetic JWT role for the read-only auditor surface (spec 17.3).
# ``AUDITOR`` IS a member of the closed case-assignment role set (migration
# 202607180008), but here it authorises the estate-wide read directly -- no case
# assignment is consulted.  The official SHB audit RBAC mapping is an OPEN
# QUESTION (design section 24); the surface fails closed until a token carries it.
AUDITOR_ROLE = "AUDITOR"

# Conservative event-type shape: an UPPER_SNAKE token (e.g. ``CASE_CREATED``,
# ``CASE_VERSION_BUMPED``).  A value outside this shape is rejected at the
# boundary (422) and never reaches SQL.
_EVENT_TYPE_PATTERN = r"^[A-Z][A-Z0-9_]{0,63}$"


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
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


def _require_auditor(actor: ActorContext) -> None:
    """Fail closed unless the actor holds the synthetic AUDITOR role."""
    if AUDITOR_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò kiểm toán được yêu cầu.",
        )


def _orchestration_repository(request: Request) -> OrchestrationRepository:
    repository = getattr(request.app.state, "orchestration_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="AUDIT_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ nhật ký kiểm toán chưa sẵn sàng.",
            retryable=True,
        )
    return cast(OrchestrationRepository, repository)


def _event_response(row: AuditEventRow) -> AuditEventResponse:
    return AuditEventResponse(
        id=row.id,
        case_id=row.case_id,
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
async def search_audit_events(
    actor: Actor,
    request: Request,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    event_type: Annotated[
        str | None,
        Query(alias="eventType", min_length=1, max_length=64, pattern=_EVENT_TYPE_PATTERN),
    ] = None,
) -> AuditEventListResponse:
    _require_auditor(actor)
    repository = _orchestration_repository(request)
    events, next_cursor = await repository.list_audit_events_all(
        cursor=cursor, limit=limit, event_type=event_type
    )
    return AuditEventListResponse(
        events=[_event_response(event) for event in events],
        next_cursor=next_cursor,
    )
