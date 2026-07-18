"""Stage 1 prospect API: prospects, descriptive screening snapshots, and the
human-recorded contact decision (master design section 5 stage 1, section 13,
section 15).

Stage 1 is human-owned and pre-case, so every surface here is gated on the
``INTAKE_OFFICER`` human role (the RM analog in the synthetic role set) and
scoped to the actor's OWN prospects -- ``created_by = actor``.  There is no
cross-officer listing and no case-assignment check (no case exists yet); a
prospect the actor does not own is an indistinguishable 404, so ownership is
never disclosed.

The system never auto-contacts and never scores secretly:

* screening snapshots are DESCRIPTIVE only -- the body carries a labelled
  synthetic ``screeningConfigVersion`` and descriptive fields; a verdict-shaped
  ``details`` key is rejected (422) before anything is written;
* recording a contact decision has NO side effect -- it is a durable record of
  the human's decision plus a required rationale, nothing more.

Provenance: ``public.audit_events`` is case-scoped and no case exists at Stage
1, so provenance is the ``created_by`` / ``decided_by`` columns on the rows
themselves rather than an audit event.

The lead wires this ``router`` into ``main.py``; the module never touches the
composition root.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import INTAKE_OFFICER_ROLE
from creditops.application.ports.prospects import (
    ProspectDetail,
    ProspectNotFound,
    ProspectRepository,
)
from creditops.application.unit_of_work import ActorContext
from creditops.domain.prospects import (
    ContactDecision,
    ContactDecisionRecord,
    Prospect,
    ScreeningSnapshot,
    assert_details_descriptive,
)

router = APIRouter(prefix="/api/v1/prospects", tags=["prospects"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


# --- request bodies -------------------------------------------------------


class CreateProspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name_vi: str = Field(alias="nameVi", min_length=1, max_length=500)
    industry_vi: str | None = Field(default=None, alias="industryVi", max_length=500)
    years_operating: int | None = Field(default=None, alias="yearsOperating", ge=0)
    revenue_band_vi: str | None = Field(default=None, alias="revenueBandVi", max_length=500)
    legal_status_vi: str | None = Field(default=None, alias="legalStatusVi", max_length=500)
    notes_vi: str | None = Field(default=None, alias="notesVi", max_length=4000)


class CreateScreeningSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    screening_config_version: str = Field(
        alias="screeningConfigVersion", min_length=1, max_length=200
    )
    industry_vi: str | None = Field(default=None, alias="industryVi", max_length=500)
    years_operating: int | None = Field(default=None, alias="yearsOperating", ge=0)
    revenue_band_vi: str | None = Field(default=None, alias="revenueBandVi", max_length=500)
    legal_status_vi: str | None = Field(default=None, alias="legalStatusVi", max_length=500)
    credit_history_vi: str | None = Field(
        default=None, alias="creditHistoryVi", max_length=2000
    )
    risk_appetite_note_vi: str | None = Field(
        default=None, alias="riskAppetiteNoteVi", max_length=2000
    )
    details: dict[str, object] = Field(default_factory=dict)


class RecordContactDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: A closed set -- an unknown value is a 422, never a silent default.
    decision: ContactDecision
    rationale_vi: str = Field(alias="rationaleVi", min_length=1, max_length=4000)


# --- responses ------------------------------------------------------------


class ProspectResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    name_vi: str = Field(serialization_alias="nameVi")
    industry_vi: str | None = Field(serialization_alias="industryVi")
    years_operating: int | None = Field(serialization_alias="yearsOperating")
    revenue_band_vi: str | None = Field(serialization_alias="revenueBandVi")
    legal_status_vi: str | None = Field(serialization_alias="legalStatusVi")
    notes_vi: str | None = Field(serialization_alias="notesVi")
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class ProspectListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    prospects: list[ProspectResponse]
    next_cursor: UUID | None = Field(serialization_alias="nextCursor")


class ScreeningSnapshotResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    prospect_id: UUID = Field(serialization_alias="prospectId")
    version: int
    screening_config_version: str = Field(serialization_alias="screeningConfigVersion")
    industry_vi: str | None = Field(serialization_alias="industryVi")
    years_operating: int | None = Field(serialization_alias="yearsOperating")
    revenue_band_vi: str | None = Field(serialization_alias="revenueBandVi")
    legal_status_vi: str | None = Field(serialization_alias="legalStatusVi")
    credit_history_vi: str | None = Field(serialization_alias="creditHistoryVi")
    risk_appetite_note_vi: str | None = Field(serialization_alias="riskAppetiteNoteVi")
    details: dict[str, object]
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class ContactDecisionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    prospect_id: UUID = Field(serialization_alias="prospectId")
    decision: ContactDecision
    rationale_vi: str = Field(serialization_alias="rationaleVi")
    decided_by: UUID = Field(serialization_alias="decidedBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class ProspectDetailResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    prospect: ProspectResponse
    latest_snapshot: ScreeningSnapshotResponse | None = Field(
        serialization_alias="latestSnapshot"
    )
    decisions: list[ContactDecisionResponse]


Actor = Annotated[ActorContext, Depends(require_actor)]


# --- helpers --------------------------------------------------------------


def _require_intake_role(actor: ActorContext) -> None:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tiếp nhận được yêu cầu.",
        )


def _repository(request: Request) -> ProspectRepository:
    repository = getattr(request.app.state, "prospect_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="PROSPECT_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ khách hàng tiềm năng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(ProspectRepository, repository)


def _not_found() -> ApiException:
    return ApiException(
        status_code=404,
        code="PROSPECT_NOT_ACCESSIBLE",
        message_vi="Không tìm thấy khách hàng tiềm năng hoặc bạn không có quyền truy cập.",
    )


def _prospect_response(prospect: Prospect) -> ProspectResponse:
    return ProspectResponse(
        id=prospect.id,
        name_vi=prospect.name_vi,
        industry_vi=prospect.industry_vi,
        years_operating=prospect.years_operating,
        revenue_band_vi=prospect.revenue_band_vi,
        legal_status_vi=prospect.legal_status_vi,
        notes_vi=prospect.notes_vi,
        created_by=prospect.created_by,
        created_at=prospect.created_at,
    )


def _snapshot_response(snapshot: ScreeningSnapshot) -> ScreeningSnapshotResponse:
    return ScreeningSnapshotResponse(
        id=snapshot.id,
        prospect_id=snapshot.prospect_id,
        version=snapshot.version,
        screening_config_version=snapshot.screening_config_version,
        industry_vi=snapshot.industry_vi,
        years_operating=snapshot.years_operating,
        revenue_band_vi=snapshot.revenue_band_vi,
        legal_status_vi=snapshot.legal_status_vi,
        credit_history_vi=snapshot.credit_history_vi,
        risk_appetite_note_vi=snapshot.risk_appetite_note_vi,
        details=dict(snapshot.details),
        created_by=snapshot.created_by,
        created_at=snapshot.created_at,
    )


def _decision_response(decision: ContactDecisionRecord) -> ContactDecisionResponse:
    return ContactDecisionResponse(
        id=decision.id,
        prospect_id=decision.prospect_id,
        decision=decision.decision,
        rationale_vi=decision.rationale_vi,
        decided_by=decision.decided_by,
        created_at=decision.created_at,
    )


def _detail_response(detail: ProspectDetail) -> ProspectDetailResponse:
    return ProspectDetailResponse(
        prospect=_prospect_response(detail.prospect),
        latest_snapshot=(
            _snapshot_response(detail.latest_snapshot)
            if detail.latest_snapshot is not None
            else None
        ),
        decisions=[_decision_response(decision) for decision in detail.decisions],
    )


# --- endpoints ------------------------------------------------------------


@router.post("", response_model=ProspectResponse, status_code=201)
async def create_prospect(
    body: CreateProspectRequest, actor: Actor, request: Request
) -> ProspectResponse:
    _require_intake_role(actor)
    repository = _repository(request)
    prospect = await repository.create_prospect(
        name_vi=body.name_vi,
        industry_vi=body.industry_vi,
        years_operating=body.years_operating,
        revenue_band_vi=body.revenue_band_vi,
        legal_status_vi=body.legal_status_vi,
        notes_vi=body.notes_vi,
        created_by=actor.actor_id,
    )
    return _prospect_response(prospect)


@router.get("", response_model=ProspectListResponse)
async def list_prospects(
    actor: Actor,
    request: Request,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
) -> ProspectListResponse:
    _require_intake_role(actor)
    repository = _repository(request)
    # Own prospects only -- created_by is the actor; there is no cross-officer
    # listing.
    prospects, next_cursor = await repository.list_prospects(
        created_by=actor.actor_id, cursor=cursor, limit=limit
    )
    return ProspectListResponse(
        prospects=[_prospect_response(prospect) for prospect in prospects],
        next_cursor=next_cursor,
    )


@router.get("/{prospect_id}", response_model=ProspectDetailResponse)
async def get_prospect(
    prospect_id: UUID, actor: Actor, request: Request
) -> ProspectDetailResponse:
    _require_intake_role(actor)
    repository = _repository(request)
    detail = await repository.load_prospect(
        prospect_id=prospect_id, created_by=actor.actor_id
    )
    if detail is None:
        raise _not_found()
    return _detail_response(detail)


@router.post(
    "/{prospect_id}/screening-snapshots",
    response_model=ScreeningSnapshotResponse,
    status_code=201,
)
async def append_screening_snapshot(
    prospect_id: UUID,
    body: CreateScreeningSnapshotRequest,
    actor: Actor,
    request: Request,
) -> ScreeningSnapshotResponse:
    _require_intake_role(actor)
    repository = _repository(request)
    # Fail closed BEFORE any write: a screening snapshot is descriptive only,
    # so a verdict-shaped details key is rejected rather than persisted.
    try:
        assert_details_descriptive(body.details)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="SCREENING_NOT_DESCRIPTIVE",
            message_vi="Thông tin sàng lọc chỉ được mô tả, không được chứa kết luận.",
            details={"reason": str(exc)},
        ) from exc
    try:
        snapshot = await repository.append_screening_snapshot(
            prospect_id=prospect_id,
            created_by=actor.actor_id,
            screening_config_version=body.screening_config_version,
            industry_vi=body.industry_vi,
            years_operating=body.years_operating,
            revenue_band_vi=body.revenue_band_vi,
            legal_status_vi=body.legal_status_vi,
            credit_history_vi=body.credit_history_vi,
            risk_appetite_note_vi=body.risk_appetite_note_vi,
            details=body.details,
        )
    except ProspectNotFound as exc:
        raise _not_found() from exc
    return _snapshot_response(snapshot)


@router.post(
    "/{prospect_id}/contact-decisions",
    response_model=ContactDecisionResponse,
    status_code=201,
)
async def record_contact_decision(
    prospect_id: UUID,
    body: RecordContactDecisionRequest,
    actor: Actor,
    request: Request,
) -> ContactDecisionResponse:
    _require_intake_role(actor)
    repository = _repository(request)
    # Recording only -- NO side effect: the decision is a durable record made
    # BY the human, and never triggers any contact.
    try:
        decision = await repository.record_contact_decision(
            prospect_id=prospect_id,
            created_by=actor.actor_id,
            decision=body.decision,
            rationale_vi=body.rationale_vi,
            decided_by=actor.actor_id,
        )
    except ProspectNotFound as exc:
        raise _not_found() from exc
    return _decision_response(decision)
