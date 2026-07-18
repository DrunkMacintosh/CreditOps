"""Stage-9 security-perfection ledger API: per-asset interests + the confirm gate.

Master design section 5 giai đoạn 9.  Five case-scoped, fail-closed surfaces (an
unassigned actor gets the same indistinguishable 404 as a missing case, so
assignment membership is never disclosed):

- POST ``/security-interests`` -- a ``LEGAL_REVIEWER`` or ``OPS_OFFICER`` appends
  ONE per-asset ``SecurityInterest`` bound to the current case version.
- POST ``/security-interests/{interest_id}/items`` -- the same roles append ONE
  perfection requirement to an interest.
- POST ``/security-interests/items/{item_id}/transition`` -- the same roles
  advance a requirement through the closed status graph.
- GET ``/security-interests`` -- any assigned case participant reads the ledger.
- POST ``/security-interests/confirm`` -- an INDEPENDENT ``OPS_CHECKER`` confirms
  perfection.  Only when ``derive_perfection_confirmable`` holds (every interest
  has >=1 requirement and every requirement is in a terminal-satisfied state with
  evidence) does it satisfy ``HG_SECURITY_PERFECTION_CONFIRMED`` through the
  orchestration repository (exactly as ``api/financing.py`` records its gate),
  re-tick the orchestrator, and audit; otherwise it rejects 409 with the blocking
  ids.

AUTHORITY MODEL (PROPOSED synthetic; documented because no official SHB authority
matrix exists -- master design sections 4 and 5 giai đoạn 9):

- The WRITE roles ``LEGAL_REVIEWER`` (mirrors ``api/legal.py``) and ``OPS_OFFICER``
  reflect the stage-9 owners (legal/collateral officer + credit operations); the
  CONFIRM role ``OPS_CHECKER`` is a NEW dedicated synthetic JWT role modelling the
  independent checker who confirms perfection -- kept distinct from the maker
  roles so a writer cannot self-confirm.  All are checked at the API layer and
  ALSO require a case assignment for row access; both fail closed (a missing role
  is 403, an unassigned actor is an indistinguishable 404).

NO agent path reaches any surface here: agents never call a real registration
authority and the system never declares a final priority ranking.  This module
exports ``router`` only; mounting it in ``main.py`` is a separate change.

All data is synthetic and created solely for demonstration.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.ports.security_interests import (
    ForbiddenTransitionError,
    InterestNotAccessibleError,
    InvalidTransitionInputError,
    ItemNotAccessibleError,
    RecordedInterest,
    RecordedInterestWithItems,
    RecordedItem,
    SecurityInterestRepository,
)
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.orchestration import GateStatus, GateType
from creditops.domain.security_interests import (
    PerfectionStatus,
    SecurityAssetKind,
    SecurityInterest,
    SecurityInterestWithItems,
    SecurityPerfectionItem,
    derive_perfection_blockers,
)
from creditops.observability import log_event

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/security-interests",
    tags=["security-interests"],
)

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic JWT authority roles (see module docstring; no official SHB
#: mapping).  ``LEGAL_REVIEWER`` mirrors the constant in ``api/legal.py``.
LEGAL_REVIEWER_ROLE = "LEGAL_REVIEWER"

#: NEW PROPOSED synthetic role for the independent perfection checker.
OPS_CHECKER_ROLE = "OPS_CHECKER"

#: Roles that may WRITE the ledger (append interests/items, transition items).
_WRITER_ROLES = frozenset({LEGAL_REVIEWER_ROLE, OPS_OFFICER_ROLE})

#: Roles that may READ the ledger: any case participant, plus the two stage-9
#: authority roles.
_READ_ROLES = CASE_PARTICIPANT_ROLES | {LEGAL_REVIEWER_ROLE, OPS_CHECKER_ROLE}

#: PROPOSED synthetic disposition-reference prefix bound to the confirmed case
#: version (no official SHB mapping).
_DISPOSITION_REF_PREFIX = "security-perfection"


# -- request / response models ------------------------------------------------


class CreateInterestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_description_vi: str = Field(
        alias="assetDescription", min_length=1, max_length=2000
    )
    asset_kind: str = Field(alias="assetKind", min_length=1, max_length=32)
    owner_name_vi: str | None = Field(
        default=None, alias="ownerName", min_length=1, max_length=500
    )
    valuation_reference: str | None = Field(
        default=None, alias="valuationReference", min_length=1, max_length=500
    )
    notes_vi: str | None = Field(
        default=None, alias="notes", min_length=1, max_length=4000
    )


class AddItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_vi: str = Field(alias="requirement", min_length=1, max_length=2000)
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    filing_reference: str | None = Field(
        default=None, alias="filingReference", min_length=1, max_length=500
    )
    effective_date: date | None = Field(default=None, alias="effectiveDate")
    expiry_date: date | None = Field(default=None, alias="expiryDate")


class TransitionItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to_status: str = Field(alias="toStatus", min_length=1, max_length=32)
    rationale_vi: str | None = Field(
        default=None, alias="rationale", min_length=1, max_length=4000
    )
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    filing_reference: str | None = Field(
        default=None, alias="filingReference", min_length=1, max_length=500
    )
    effective_date: date | None = Field(default=None, alias="effectiveDate")
    expiry_date: date | None = Field(default=None, alias="expiryDate")


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


class ItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    interest_id: UUID = Field(serialization_alias="interestId")
    requirement_vi: str = Field(serialization_alias="requirement")
    status: str
    evidence_refs: list[str] = Field(serialization_alias="evidenceRefs")
    filing_reference: str | None = Field(serialization_alias="filingReference")
    effective_date: date | None = Field(serialization_alias="effectiveDate")
    expiry_date: date | None = Field(serialization_alias="expiryDate")
    completed_by: UUID | None = Field(serialization_alias="completedBy")
    completed_at: datetime | None = Field(serialization_alias="completedAt")
    created_at: datetime = Field(serialization_alias="createdAt")


class InterestResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    asset_description_vi: str = Field(serialization_alias="assetDescription")
    asset_kind: str = Field(serialization_alias="assetKind")
    owner_name_vi: str | None = Field(serialization_alias="ownerName")
    valuation_reference: str | None = Field(serialization_alias="valuationReference")
    notes_vi: str | None = Field(serialization_alias="notes")
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class InterestWithItemsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    interest: InterestResponse
    items: list[ItemResponse]


class LedgerResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    interests: list[InterestWithItemsResponse]


class ConfirmationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    disposition_ref: str = Field(serialization_alias="dispositionRef")


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- authority + wiring helpers -----------------------------------------------


def _require_writer(actor: ActorContext) -> None:
    if not (_WRITER_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có thẩm quyền cập nhật biện pháp bảo đảm.",
        )


def _require_checker(actor: ActorContext) -> None:
    if OPS_CHECKER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có thẩm quyền xác nhận hoàn thiện bảo đảm.",
        )


def _require_reader(actor: ActorContext) -> None:
    if not (_READ_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> SecurityInterestRepository:
    repository = getattr(request.app.state, "security_interest_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="SECURITY_INTEREST_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hoàn thiện bảo đảm chưa sẵn sàng.",
            retryable=True,
        )
    return cast(SecurityInterestRepository, repository)


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


def _parse_asset_kind(value: str) -> SecurityAssetKind:
    try:
        return SecurityAssetKind(value)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_ASSET_KIND",
            message_vi="Loại tài sản bảo đảm không hợp lệ.",
        ) from exc


def _parse_status(value: str) -> PerfectionStatus:
    try:
        return PerfectionStatus(value)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_PERFECTION_STATUS",
            message_vi="Trạng thái hoàn thiện bảo đảm không hợp lệ.",
        ) from exc


# -- write surfaces -----------------------------------------------------------


@router.post("", response_model=InterestResponse, status_code=201)
async def create_security_interest(
    case_id: UUID,
    body: CreateInterestRequest,
    actor: Actor,
    request: Request,
) -> InterestResponse:
    """Append ONE per-asset security interest bound to the current case version."""

    _require_writer(actor)
    record = await _assert_case_access(request, actor, case_id)
    asset_kind = _parse_asset_kind(body.asset_kind)
    repository = _repository(request)

    try:
        interest = SecurityInterest(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            asset_description_vi=body.asset_description_vi,
            asset_kind=asset_kind,
            owner_name_vi=body.owner_name_vi,
            valuation_reference=body.valuation_reference,
            notes_vi=body.notes_vi,
            created_by=actor.actor_id,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_SECURITY_INTEREST",
            message_vi="Biện pháp bảo đảm không hợp lệ.",
        ) from exc

    recorded = await repository.create_interest(
        interest=interest, actor_role=_writer_role(actor)
    )
    return _interest_response(recorded)


@router.post("/{interest_id}/items", response_model=ItemResponse, status_code=201)
async def add_perfection_item(
    case_id: UUID,
    interest_id: UUID,
    body: AddItemRequest,
    actor: Actor,
    request: Request,
) -> ItemResponse:
    """Append ONE perfection requirement (starts ``PENDING``) to an interest."""

    _require_writer(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    try:
        item = SecurityPerfectionItem(
            id=uuid4(),
            interest_id=interest_id,
            requirement_vi=body.requirement_vi,
            status=PerfectionStatus.PENDING,
            evidence_refs=body.evidence_refs,
            filing_reference=body.filing_reference,
            effective_date=body.effective_date,
            expiry_date=body.expiry_date,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_PERFECTION_ITEM",
            message_vi="Yêu cầu hoàn thiện bảo đảm không hợp lệ.",
        ) from exc

    try:
        recorded = await repository.add_item(
            case_id=case_id,
            item=item,
            actor_id=actor.actor_id,
            actor_role=_writer_role(actor),
        )
    except InterestNotAccessibleError as exc:
        raise ApiException(
            status_code=404,
            code="INTEREST_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy biện pháp bảo đảm cho hồ sơ này.",
        ) from exc
    return _item_response(recorded)


@router.post("/items/{item_id}/transition", response_model=ItemResponse)
async def transition_perfection_item(
    case_id: UUID,
    item_id: UUID,
    body: TransitionItemRequest,
    actor: Actor,
    request: Request,
) -> ItemResponse:
    """Advance a perfection requirement through the closed status graph."""

    _require_writer(actor)
    await _assert_case_access(request, actor, case_id)
    to_status = _parse_status(body.to_status)
    repository = _repository(request)

    try:
        recorded = await repository.transition_item(
            case_id=case_id,
            item_id=item_id,
            to_status=to_status,
            actor_id=actor.actor_id,
            actor_role=_writer_role(actor),
            rationale=body.rationale_vi,
            evidence_refs=body.evidence_refs,
            filing_reference=body.filing_reference,
            effective_date=body.effective_date,
            expiry_date=body.expiry_date,
        )
    except ItemNotAccessibleError as exc:
        raise ApiException(
            status_code=404,
            code="ITEM_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy yêu cầu hoàn thiện bảo đảm cho hồ sơ này.",
        ) from exc
    except ForbiddenTransitionError as exc:
        raise ApiException(
            status_code=409,
            code="FORBIDDEN_PERFECTION_TRANSITION",
            message_vi="Chuyển trạng thái hoàn thiện bảo đảm không được phép.",
            details={"fromStatus": exc.current.value, "toStatus": exc.target.value},
        ) from exc
    except InvalidTransitionInputError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_TRANSITION_INPUT",
            message_vi="Dữ liệu chuyển trạng thái chưa đủ điều kiện.",
            details={"reason": exc.reason},
        ) from exc
    return _item_response(recorded)


# -- read surface -------------------------------------------------------------


@router.get("", response_model=LedgerResponse)
async def list_security_interests(
    case_id: UUID, actor: Actor, request: Request
) -> LedgerResponse:
    """Read the whole per-asset security-perfection ledger for the current version."""

    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    ledger = await repository.list_interests(case_id, record.version)
    return LedgerResponse(
        interests=[_interest_with_items_response(entry) for entry in ledger]
    )


# -- confirmation gate --------------------------------------------------------


@router.post("/confirm", response_model=ConfirmationResponse)
async def confirm_security_perfection(
    case_id: UUID,
    body: ConfirmRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> ConfirmationResponse:
    """Independent checker confirms perfection and satisfies the stage-9 gate.

    Fail closed: only when every interest has >=1 requirement and every
    requirement is in a terminal-satisfied state (``derive_perfection_confirmable``)
    is the gate satisfied; otherwise 409 with the blocking ids.
    """

    _require_checker(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    ledger = await repository.list_interests(case_id, record.version)
    blockers = derive_perfection_blockers([_to_domain(entry) for entry in ledger])
    if not blockers.confirmable:
        raise ApiException(
            status_code=409,
            code="PERFECTION_NOT_SATISFIED",
            message_vi=(
                "Chưa thể xác nhận: còn yêu cầu hoàn thiện bảo đảm chưa hoàn tất."
            ),
            details={
                "hasInterests": blockers.has_interests,
                "interestsWithoutItems": [
                    str(interest_id) for interest_id in blockers.interests_without_items
                ],
                "blockingItemIds": [
                    str(item_id) for item_id in blockers.blocking_item_ids
                ],
            },
        )

    orchestration = _orchestration_repository(request)
    disposition_ref = f"{_DISPOSITION_REF_PREFIX}:{record.version}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_SECURITY_PERFECTION_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=record.version,
            event_type="SECURITY_PERFECTION_CONFIRMED",
            execution_id=uuid4(),
            artifact_type="SECURITY_PERFECTION_LEDGER",
            artifact_id=case_id,
            event_data={
                "actorId": str(actor.actor_id),
                "rationale": body.rationale_vi,
                "interestCount": len(ledger),
            },
        ),
        actor_id=actor.actor_id,
        actor_role=OPS_CHECKER_ROLE,
    )
    await _retick_orchestration(
        request,
        orchestration,
        case_id=case_id,
        trigger_ref=f"HG_SEC:{record.version}",
    )
    response.status_code = 200
    return ConfirmationResponse(
        gate_type=GateType.HG_SECURITY_PERFECTION_CONFIRMED.value,
        status=GateStatus.SATISFIED.value,
        disposition_ref=disposition_ref,
    )


# -- mapping helpers ----------------------------------------------------------


def _writer_role(actor: ActorContext) -> str:
    """The specific writer authority the actor exercised (for audit provenance)."""

    return LEGAL_REVIEWER_ROLE if LEGAL_REVIEWER_ROLE in actor.roles else OPS_OFFICER_ROLE


def _interest_response(record: RecordedInterest) -> InterestResponse:
    return InterestResponse(
        id=record.id,
        case_id=record.case_id,
        case_version=record.case_version,
        asset_description_vi=record.asset_description_vi,
        asset_kind=record.asset_kind,
        owner_name_vi=record.owner_name_vi,
        valuation_reference=record.valuation_reference,
        notes_vi=record.notes_vi,
        created_by=record.created_by,
        created_at=record.created_at,
    )


def _item_response(record: RecordedItem) -> ItemResponse:
    return ItemResponse(
        id=record.id,
        interest_id=record.interest_id,
        requirement_vi=record.requirement_vi,
        status=record.status,
        evidence_refs=list(record.evidence_refs),
        filing_reference=record.filing_reference,
        effective_date=record.effective_date,
        expiry_date=record.expiry_date,
        completed_by=record.completed_by,
        completed_at=record.completed_at,
        created_at=record.created_at,
    )


def _interest_with_items_response(
    entry: RecordedInterestWithItems,
) -> InterestWithItemsResponse:
    return InterestWithItemsResponse(
        interest=_interest_response(entry.interest),
        items=[_item_response(item) for item in entry.items],
    )


def _to_domain(entry: RecordedInterestWithItems) -> SecurityInterestWithItems:
    """Rebuild the pure domain read model from persisted rows for the derivation.

    The DB CHECK/trigger guarantee every persisted row already satisfies the
    domain invariants, so this reconstruction never fails.
    """

    interest = SecurityInterest(
        id=entry.interest.id,
        case_id=entry.interest.case_id,
        case_version=entry.interest.case_version,
        asset_description_vi=entry.interest.asset_description_vi,
        asset_kind=SecurityAssetKind(entry.interest.asset_kind),
        owner_name_vi=entry.interest.owner_name_vi,
        valuation_reference=entry.interest.valuation_reference,
        notes_vi=entry.interest.notes_vi,
        created_by=entry.interest.created_by,
    )
    items = tuple(
        SecurityPerfectionItem(
            id=item.id,
            interest_id=item.interest_id,
            requirement_vi=item.requirement_vi,
            status=PerfectionStatus(item.status),
            evidence_refs=item.evidence_refs,
            filing_reference=item.filing_reference,
            effective_date=item.effective_date,
            expiry_date=item.expiry_date,
            completed_by=item.completed_by,
            completed_at=item.completed_at,
        )
        for item in entry.items
    )
    return SecurityInterestWithItems(interest=interest, items=items)


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after the gate satisfaction.

    A tick failure must never fail the human's already-recorded confirmation, but
    it is logged, never silent (mirrors ``api/financing.py``).
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
            "Orchestration retick failed; the confirmation is durable and the "
            "case can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )
