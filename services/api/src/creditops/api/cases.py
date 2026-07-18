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


class CaseCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_roles: list[str] = Field(serialization_alias="caseRoles")
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


class CaseCollectionCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    can_create_case: bool = Field(serialization_alias="canCreateCase")


class CaseListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[CaseResponse]
    next_cursor: UUID | None = Field(serialization_alias="nextCursor")
    capabilities: CaseCollectionCapabilities


def _derive_capabilities(
    case_roles: frozenset[str],
    jwt_roles: frozenset[str],
) -> CaseCapabilities:
    """Derive intake mutation capabilities from server-side case roles.

    Fail closed: a capability is granted only when the actor holds the
    ``INTAKE_OFFICER`` role BOTH as a server-side case assignment AND as a JWT
    claim (their intersection). No assignment role means no mutation capability;
    assignment/delegation is an audited server command, never inferred from the
    client. Non-intake case roles carry no intake mutation capability here.
    """

    can_mutate_intake = INTAKE_OFFICER_ROLE in (case_roles & jwt_roles)
    return CaseCapabilities(
        can_upload=can_mutate_intake,
        can_confirm=can_mutate_intake,
        can_complete_intake=can_mutate_intake,
    )


def _case_response(record: CaseRecord, capabilities: CaseCapabilities) -> CaseResponse:
    return CaseResponse(
        id=record.id,
        version=record.version,
        assigned_officer_id=record.assigned_officer_id,
        requested_amount=record.requested_amount,
        purpose_vi=record.purpose_vi,
        created_at=record.created_at,
        capabilities=capabilities,
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
    # Create self-assigns the actor as INTAKE_OFFICER on the new case, so the
    # server-derived case role is exactly {INTAKE_OFFICER}; capabilities still
    # fail closed through the JWT intersection performed by ``_derive_capabilities``.
    capabilities = _derive_capabilities(frozenset({INTAKE_OFFICER_ROLE}), actor.roles)
    return _case_response(record, capabilities)


@router.get("", response_model=CaseListResponse)
async def list_cases(
    request: Request,
    actor: Actor,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CaseListResponse:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        return CaseListResponse(
            items=[],
            next_cursor=None,
            capabilities=CaseCollectionCapabilities(can_create_case=False),
        )

    uow_factory = _uow_factory(request)
    async with uow_factory(actor) as uow:
        records, next_cursor = await uow.cases.list_assigned(
            actor.actor_id,
            cursor=cursor,
            limit=limit,
        )
        items = [
            _case_response(
                record,
                _derive_capabilities(
                    await uow.cases.list_assignment_roles(record.id, actor.actor_id),
                    actor.roles,
                ),
            )
            for record in records
        ]
    return CaseListResponse(
        items=items,
        next_cursor=next_cursor,
        capabilities=CaseCollectionCapabilities(can_create_case=True),
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
        case_roles: frozenset[str] = frozenset()
        if record is not None:
            case_roles = await uow.cases.list_assignment_roles(case_id, actor.actor_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
            retryable=False,
        )
    return _case_response(record, _derive_capabilities(case_roles, actor.roles))


@router.get("/{case_id}/capabilities", response_model=CaseCapabilitiesResponse)
async def get_case_capabilities(
    case_id: UUID,
    actor: Actor,
    uow_factory: UowFactory,
) -> CaseCapabilitiesResponse:
    # No JWT-role gate here: any non-revoked assignee (intake or otherwise) may read
    # their own capability map. Unassigned actors get the same 404 as a missing case
    # so assignment membership is not disclosed (fail-closed 404-indistinguishability).
    async with uow_factory(actor) as uow:
        record = await uow.cases.get_assigned(case_id, actor.actor_id)
        case_roles: frozenset[str] = frozenset()
        if record is not None:
            case_roles = await uow.cases.list_assignment_roles(case_id, actor.actor_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
            retryable=False,
        )
    capabilities = _derive_capabilities(case_roles, actor.roles)
    return CaseCapabilitiesResponse(
        case_roles=sorted(case_roles),
        can_upload=capabilities.can_upload,
        can_confirm=capabilities.can_confirm,
        can_complete_intake=capabilities.can_complete_intake,
    )
