"""Pre-Risk Evidence-Gap request-batch API: assemble, read, and dispose (G2).

This router OWNS ``G2_GAP_REQUEST_APPROVAL`` (the ``HG_OUTBOUND_REQUEST_APPROVED``
capability, master design section 9).  It replaces the deleted credit-ops
package-derived G2 path, breaking the Risk-waits-on-Credit-Operations cycle.

- ``POST ''`` (assemble-or-get): the assigned INTAKE_OFFICER snapshots the
  CURRENT open evidence gaps and the deterministic assembler builds a versioned
  ``GapRequestBatch`` (one drafted request per open gap; may be EMPTY).  Persist
  is idempotent on ``(case, version, open-gap snapshot hash)`` -- re-assembling
  the same open-gap set returns the existing batch (200), a fresh set creates a
  new one (201).
- ``GET ''``: the current batch, its human dispositions, a staleness flag (the
  current open-gap hash vs the batch's), and the derived gate status.
- ``POST '/{batch_id}/disposition'``: the assigned INTAKE_OFFICER records one
  append-only human disposition, validated by the domain model
  (``domain/gap_request_batches.py``).  The gate is then RE-DERIVED against the
  CURRENT open gaps and case version (``derive_g2_from_batch``); only if that
  pure derivation says SATISFIED is ``G2_GAP_REQUEST_APPROVAL`` written through
  the orchestration repository and orchestration re-ticked -- exactly the
  human-triggered pattern in ``api/risk_review.py``.  A REJECTED disposition, a
  stale batch (open gaps changed), or a stale case version never satisfies.

Row access is the case-assignment check; an unassigned actor receives an
indistinguishable 404 (``api/risk_review.py`` pattern).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.gaps.assembler import assemble_gap_request_batch
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    INTAKE_OFFICER_ROLE,
)
from creditops.application.ports.gap_requests import (
    GapRequestBatchDispositionRecord,
    GapRequestRepository,
)
from creditops.application.ports.orchestration import OrchestrationRepository
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.gap_request_batches import (
    BatchDispositionType,
    GapRequestBatch,
    GapRequestBatchDisposition,
    assert_disposition_matches_batch,
    compute_open_gap_snapshot_hash,
    derive_g2_from_batch,
)
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/gap-request-batches", tags=["gap-request-batches"]
)


# -- response / request models ------------------------------------------------


class GapRequestItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    gap_id: UUID = Field(serialization_alias="gapId")
    request_text_vi: str = Field(serialization_alias="requestText")
    blocking_level: str = Field(serialization_alias="blockingLevel")


class GapRequestBatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    batch_id: UUID = Field(serialization_alias="batchId")
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    open_gap_snapshot_hash: str = Field(serialization_alias="openGapSnapshotHash")
    items: list[GapRequestItemResponse]


class BatchDispositionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    batch_id: UUID = Field(serialization_alias="batchId")
    disposition_type: str = Field(serialization_alias="dispositionType")
    item_dispositions: dict[str, str] = Field(serialization_alias="itemDispositions")
    edited_texts: dict[str, str] = Field(serialization_alias="editedTexts")
    actor_id: UUID = Field(serialization_alias="actorId")
    actor_role: str = Field(serialization_alias="actorRole")
    rationale_vi: str = Field(serialization_alias="rationale")
    created_at: datetime = Field(serialization_alias="createdAt")


class GapRequestBatchStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    batch: GapRequestBatchResponse
    stale: bool
    current_open_gap_hash: str = Field(serialization_alias="currentOpenGapHash")
    dispositions: list[BatchDispositionResponse]
    gate_status: str = Field(serialization_alias="gateStatus")


class RecordDispositionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    disposition: BatchDispositionResponse
    stale: bool
    gate_status: str = Field(serialization_alias="gateStatus")


class RecordBatchDispositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    disposition_type: str = Field(alias="dispositionType", min_length=1, max_length=50)
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)
    item_dispositions: dict[UUID, str] = Field(
        alias="itemDispositions", default_factory=dict
    )
    edited_texts: dict[UUID, str] = Field(alias="editedTexts", default_factory=dict)


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- auth / dependency helpers ------------------------------------------------


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _require_intake_officer(actor: ActorContext) -> None:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò cán bộ tiếp nhận được yêu cầu.",
        )


def _repository(request: Request) -> GapRequestRepository:
    repository = getattr(request.app.state, "gap_request_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="GAP_REQUEST_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ yêu cầu bổ sung bằng chứng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(GapRequestRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository | None:
    repository = getattr(request.app.state, "orchestration_repository", None)
    return cast("OrchestrationRepository | None", repository)


async def _load_assigned_case(
    request: Request, actor: ActorContext, case_id: UUID
) -> CaseRecord:
    """Return the assigned case (with its current version) or fail closed 404."""
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


# -- endpoints ----------------------------------------------------------------


@router.post("", response_model=GapRequestBatchResponse, status_code=200)
async def assemble_batch(
    case_id: UUID, actor: Actor, request: Request, response: Response
) -> GapRequestBatchResponse:
    """Assemble-or-get the current gap-request batch (idempotent).

    Snapshots the CURRENT open gaps and deterministically assembles a batch;
    persistence is idempotent on the open-gap snapshot hash.  A newly created
    batch returns 201; an existing identical batch returns 200.
    """

    _require_intake_officer(actor)
    case = await _load_assigned_case(request, actor, case_id)
    repository = _repository(request)
    open_gaps = await repository.load_open_gaps(case_id, case.version)
    assembled = assemble_gap_request_batch(
        open_gaps, case_id=case_id, case_version=case.version
    )
    persisted = await repository.persist_batch(assembled)
    if persisted.created:
        response.status_code = 201
    return _batch_response(persisted.batch)


@router.get("", response_model=GapRequestBatchStatusResponse)
async def get_batch(
    case_id: UUID, actor: Actor, request: Request
) -> GapRequestBatchStatusResponse:
    _require_participant(actor)
    case = await _load_assigned_case(request, actor, case_id)
    repository = _repository(request)
    batch = await repository.load_current_batch(case_id, case.version)
    if batch is None:
        raise ApiException(
            status_code=404,
            code="GAP_REQUEST_BATCH_NOT_AVAILABLE",
            message_vi="Chưa có đợt yêu cầu bổ sung bằng chứng cho phiên bản hồ sơ này.",
        )
    current_hash = await _current_open_gap_hash(repository, case_id, case.version)
    records = await repository.load_dispositions(batch.id)
    latest = _domain_disposition(records[-1]) if records else None
    gate_status = derive_g2_from_batch(
        batch=batch,
        disposition=latest,
        current_case_version=case.version,
        current_open_gap_hash=current_hash,
    )
    return GapRequestBatchStatusResponse(
        batch=_batch_response(batch),
        stale=_is_stale(batch, current_hash, case.version),
        current_open_gap_hash=current_hash,
        dispositions=[_disposition_response(record) for record in records],
        gate_status=gate_status.value,
    )


@router.post(
    "/{batch_id}/disposition",
    response_model=RecordDispositionResponse,
    status_code=201,
)
async def record_batch_disposition(
    case_id: UUID,
    batch_id: UUID,
    body: RecordBatchDispositionRequest,
    actor: Actor,
    request: Request,
) -> RecordDispositionResponse:
    """Record one append-only human disposition, then re-derive G2.

    The gate is written (and orchestration re-ticked) ONLY when the pure
    ``derive_g2_from_batch`` derivation says SATISFIED against the CURRENT open
    gaps and case version.
    """

    _require_intake_officer(actor)
    case = await _load_assigned_case(request, actor, case_id)
    repository = _repository(request)
    batch = await repository.load_current_batch(case_id, case.version)
    if batch is None or batch.id != batch_id:
        raise ApiException(
            status_code=404,
            code="GAP_REQUEST_BATCH_NOT_FOUND",
            message_vi="Không tìm thấy đợt yêu cầu bổ sung bằng chứng hiện hành.",
        )
    disposition = _validated_disposition(batch, body, actor)

    record = await repository.record_disposition(
        disposition_id=disposition.id,
        batch_id=batch.id,
        case_id=case_id,
        case_version=case.version,
        disposition_type=disposition.disposition_type,
        item_dispositions=disposition.item_dispositions,
        edited_texts=disposition.edited_texts,
        actor_id=actor.actor_id,
        actor_role=INTAKE_OFFICER_ROLE,
        rationale_vi=disposition.rationale_vi,
    )

    current_hash = await _current_open_gap_hash(repository, case_id, case.version)
    gate_status = derive_g2_from_batch(
        batch=batch,
        disposition=disposition,
        current_case_version=case.version,
        current_open_gap_hash=current_hash,
    )
    if gate_status is GateStatus.SATISFIED:
        await _satisfy_g2(request, case_id, case.version, batch.id, actor)
    return RecordDispositionResponse(
        disposition=_disposition_response(record),
        stale=_is_stale(batch, current_hash, case.version),
        gate_status=gate_status.value,
    )


# -- internals ----------------------------------------------------------------


def _validated_disposition(
    batch: GapRequestBatch,
    body: RecordBatchDispositionRequest,
    actor: ActorContext,
) -> GapRequestBatchDisposition:
    try:
        disposition_type = BatchDispositionType(body.disposition_type)
    except ValueError as error:
        raise ApiException(
            status_code=422,
            code="INVALID_DISPOSITION_TYPE",
            message_vi="Loại quyết định cho đợt yêu cầu không hợp lệ.",
            details={"dispositionType": body.disposition_type},
        ) from error
    try:
        disposition = GapRequestBatchDisposition(
            id=uuid4(),
            batch_id=batch.id,
            disposition_type=disposition_type,
            item_dispositions=cast("dict[UUID, Any]", dict(body.item_dispositions)),
            edited_texts=dict(body.edited_texts),
            actor_id=actor.actor_id,
            actor_role=INTAKE_OFFICER_ROLE,
            rationale_vi=body.rationale_vi,
        )
        assert_disposition_matches_batch(batch=batch, disposition=disposition)
    except (ValidationError, ValueError) as error:
        raise ApiException(
            status_code=422,
            code="INVALID_DISPOSITION",
            message_vi="Quyết định cho đợt yêu cầu không hợp lệ với đợt hiện hành.",
            details={"reason": str(error)},
        ) from error
    return disposition


async def _current_open_gap_hash(
    repository: GapRequestRepository, case_id: UUID, case_version: int
) -> str:
    open_gaps = await repository.load_open_gaps(case_id, case_version)
    return compute_open_gap_snapshot_hash(open_gaps)


def _is_stale(batch: GapRequestBatch, current_hash: str, current_version: int) -> bool:
    return (
        batch.open_gap_snapshot_hash != current_hash
        or batch.case_version != current_version
    )


async def _satisfy_g2(
    request: Request,
    case_id: UUID,
    case_version: int,
    batch_id: UUID,
    actor: ActorContext,
) -> None:
    orchestration_repository = _orchestration_repository(request)
    if orchestration_repository is None:
        return
    await orchestration_repository.ensure_gate(
        case_id=case_id,
        case_version=case_version,
        gate_type=GateType.G2_GAP_REQUEST_APPROVAL,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=f"gap-request-batch:{batch_id}",
    )
    await _retick_orchestration(
        request,
        orchestration_repository,
        case_id=case_id,
        trigger_ref=f"G2:{batch_id}",
    )


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after a gate satisfaction.

    Copied verbatim from ``api/risk_review.py``: the plan task + outbox event
    commit durably, the queue publish is best-effort, and a tick failure never
    fails the human's already-recorded disposition -- but it is logged, never
    silent.
    """

    try:
        result = await KickoffOrchestration(orchestration_repository).execute(
            case_id, trigger_ref=trigger_ref
        )
        queue = getattr(request.app.state, "agent_task_queue", None)
        if queue is not None:
            await DispatchOutbox(orchestration_repository, queue).run()
        log_event(
            logging.getLogger(__name__),
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
            logging.getLogger(__name__),
            logging.ERROR,
            "Orchestration retick failed; the disposition is durable and the "
            "case can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )


def _batch_response(batch: GapRequestBatch) -> GapRequestBatchResponse:
    return GapRequestBatchResponse(
        batch_id=batch.id,
        case_id=batch.case_id,
        case_version=batch.case_version,
        open_gap_snapshot_hash=batch.open_gap_snapshot_hash,
        items=[
            GapRequestItemResponse(
                id=item.id,
                gap_id=item.gap_id,
                request_text_vi=item.request_text_vi,
                blocking_level=item.blocking_level.value,
            )
            for item in batch.items
        ],
    )


def _disposition_response(
    record: GapRequestBatchDispositionRecord,
) -> BatchDispositionResponse:
    return BatchDispositionResponse(
        id=record.id,
        batch_id=record.batch_id,
        disposition_type=record.disposition_type.value,
        item_dispositions={str(k): v for k, v in record.item_dispositions.items()},
        edited_texts={str(k): v for k, v in record.edited_texts.items()},
        actor_id=record.actor_id,
        actor_role=record.actor_role,
        rationale_vi=record.rationale_vi,
        created_at=record.created_at,
    )


def _domain_disposition(
    record: GapRequestBatchDispositionRecord,
) -> GapRequestBatchDisposition:
    """Reconstruct the domain disposition from a stored record for derivation.

    The record was validated at write time, so reconstruction re-validates the
    same well-formed shape; ``derive_g2_from_batch`` only reads its batch id and
    type.
    """

    return GapRequestBatchDisposition(
        id=record.id,
        batch_id=record.batch_id,
        disposition_type=record.disposition_type,
        item_dispositions=cast("dict[UUID, Any]", dict(record.item_dispositions)),
        edited_texts=dict(record.edited_texts),
        actor_id=record.actor_id,
        actor_role=record.actor_role,
        rationale_vi=record.rationale_vi,
    )
