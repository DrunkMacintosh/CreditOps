from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.ports.repositories import (
    CaseRecord,
    InsufficientRoleError,
)
from creditops.application.unit_of_work import ActorContext, UnitOfWorkFactory
from creditops.application.use_cases.create_case import (
    INTAKE_OFFICER_ROLE,
    CreateCase,
    CreateCaseCommand,
)

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requested_amount: str = Field(
        alias="requestedAmount",
        min_length=1,
        max_length=30,
        pattern=r"^[1-9][0-9]*$",
    )
    purpose_vi: str = Field(alias="purpose", min_length=1, max_length=500)


class CaseCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    can_upload: bool = Field(serialization_alias="canUpload")
    can_confirm: bool = Field(serialization_alias="canConfirm")
    can_complete_intake: bool = Field(serialization_alias="canCompleteIntake")


class CaseResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int
    assigned_officer_id: UUID = Field(serialization_alias="assignedOfficerId")
    requested_amount: str = Field(serialization_alias="requestedAmount")
    purpose_vi: str = Field(serialization_alias="purpose")
    created_at: datetime = Field(serialization_alias="createdAt")
    capabilities: CaseCapabilities


class CaseListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[CaseResponse]
    next_cursor: UUID | None = Field(serialization_alias="nextCursor")


def _case_response(record: CaseRecord) -> CaseResponse:
    return CaseResponse(
        id=record.id,
        version=record.version,
        assigned_officer_id=record.assigned_officer_id,
        requested_amount=record.requested_amount,
        purpose_vi=record.purpose_vi,
        created_at=record.created_at,
        capabilities=CaseCapabilities(
            can_upload=True,
            can_confirm=True,
            can_complete_intake=True,
        ),
    )


def _uow_factory(request: Request) -> UnitOfWorkFactory:
    factory = getattr(request.app.state, "uow_factory", None)
    if factory is None:
        raise ApiException(
            status_code=503,
            code="CASE_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(UnitOfWorkFactory, factory)


def _require_intake_role(actor: ActorContext) -> None:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tiếp nhận được yêu cầu.",
            retryable=False,
        )


Actor = Annotated[ActorContext, Depends(require_actor)]
UowFactory = Annotated[UnitOfWorkFactory, Depends(_uow_factory)]


@router.post("", response_model=CaseResponse, status_code=201)
async def create_case(
    body: CreateCaseRequest,
    response: Response,
    actor: Actor,
    uow_factory: UowFactory,
) -> CaseResponse:
    _require_intake_role(actor)
    try:
        record = await CreateCase(uow_factory).execute(
            actor,
            CreateCaseCommand(
                requested_amount=body.requested_amount,
                purpose_vi=body.purpose_vi,
            ),
        )
    except InsufficientRoleError as exc:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tiếp nhận được yêu cầu.",
        ) from exc
    response.headers["Location"] = f"/api/v1/cases/{record.id}"
    return _case_response(record)


@router.get("", response_model=CaseListResponse)
async def list_cases(
    actor: Actor,
    uow_factory: UowFactory,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CaseListResponse:
    _require_intake_role(actor)
    async with uow_factory(actor) as uow:
        records, next_cursor = await uow.cases.list_assigned(
            actor.actor_id,
            cursor=cursor,
            limit=limit,
        )
    return CaseListResponse(
        items=[_case_response(record) for record in records],
        next_cursor=next_cursor,
    )


@router.get("/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: UUID,
    actor: Actor,
    uow_factory: UowFactory,
) -> CaseResponse:
    _require_intake_role(actor)
    async with uow_factory(actor) as uow:
        record = await uow.cases.get_assigned(case_id, actor.actor_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
            retryable=False,
        )
    return _case_response(record)
