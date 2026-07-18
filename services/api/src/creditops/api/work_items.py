"""Work-queue read API: GET /api/v1/work-items -- the ``/cong-viec`` surface.

The spec's default entry surface (master design section 17.1).  Items are
computed ONLY from the authenticated actor's own case assignments intersected
with their JWT roles; the query grants no authority and probes no case outside
those assignments (no capability leak).

Any case-participant role authenticates the request.  The set actually passed
to the repository is ``actor.roles & CASE_PARTICIPANT_ROLES`` -- the JWT/role
AND is done HERE, at the API layer, not in SQL: a role held as a server-side
case assignment but absent from the token can never surface an item.

This router deliberately is NOT wired into ``main.py`` here; it exports
``router`` for the lead to mount.
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
from creditops.application.ports.work_items import WorkItem, WorkItemRepository
from creditops.application.unit_of_work import ActorContext

router = APIRouter(prefix="/api/v1/work-items", tags=["work-items"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class WorkItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    kind: str
    title_vi: str = Field(serialization_alias="titleVi")
    reason_vi: str = Field(serialization_alias="reasonVi")
    severity: str
    primary_route: str = Field(serialization_alias="primaryRoute")
    created_at: datetime = Field(serialization_alias="createdAt")


class WorkItemListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    items: list[WorkItemResponse]


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> frozenset[str]:
    """Fail closed unless the actor holds at least one case-participant role.

    Returns the intersection of the actor's JWT roles with the case-participant
    vocabulary -- the exact set of roles the repository is allowed to act on.
    """
    effective_roles = frozenset(actor.roles) & CASE_PARTICIPANT_ROLES
    if not effective_roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )
    return effective_roles


def _work_item_repository(request: Request) -> WorkItemRepository:
    repository = getattr(request.app.state, "work_item_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="WORK_ITEMS_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hàng việc chưa sẵn sàng.",
            retryable=True,
        )
    return cast(WorkItemRepository, repository)


def _item_response(item: WorkItem) -> WorkItemResponse:
    return WorkItemResponse(
        case_id=item.case_id,
        case_version=item.case_version,
        kind=item.kind,
        title_vi=item.title_vi,
        reason_vi=item.reason_vi,
        severity=item.severity,
        primary_route=item.primary_route,
        created_at=item.created_at,
    )


@router.get("", response_model=WorkItemListResponse)
async def list_work_items(
    actor: Actor,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
) -> WorkItemListResponse:
    effective_roles = _require_participant(actor)
    repository = _work_item_repository(request)
    items = await repository.list_for_actor(actor.actor_id, effective_roles, limit=limit)
    return WorkItemListResponse(items=[_item_response(item) for item in items])
