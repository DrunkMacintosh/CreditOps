"""Officer intake evidence review API (master design P0 #3; spec sections 5
stage 3 and 15) -- the backend for the four contract-pending endpoints the
officer workspace already calls.

``GET /documents/{documentId}/review`` and the two case reads
(``/cases/{caseId}/evidence``, ``/cases/{caseId}/conflicts``) are read-only:
any assigned case participant may read them, an unassigned/foreign resource
yields the same indistinguishable 404 as a missing one, and the case reads are
scoped to the assignee's current case version.

``POST /documents/{documentId}/confirmations`` is the ONLY human write here:
restricted to the ``INTAKE_OFFICER`` JWT role AND the case assignment (the case
is derived from the document row; an unassigned/foreign document gets the same
404).  The batch must carry exactly one disposition for every candidate on the
version (partial confirmation is not a thing -> 422); a ``CORRECTED`` disposition
requires ``correctedValue`` + ``rationale`` (the domain invariant) or it is 422;
and a stale/superseded target version is 409 ``STALE_DOCUMENT_VERSION``.  The
adapter appends ``fact_confirmations`` in one transaction (the DB trigger derives
``confirmed_facts``), idempotent per candidate, auditing each fresh write.
Nothing here can complete intake, satisfy a gate, or resolve a conflict/gap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    INTAKE_OFFICER_ROLE,
)
from creditops.application.ports.evidence_review import (
    ConfirmationInput,
    ConflictView,
    DocumentReviewView,
    EvidenceFactView,
    EvidenceReviewRepository,
    StaleDocumentVersionError,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.domain.enums import DocumentStage, FactDisposition
from creditops.domain.evidence import FactValue, PageRegion

router = APIRouter(prefix="/api/v1", tags=["evidence-review"])

#: Confirmation is only accepted while the document is in its reviewable stage.
_REVIEWABLE_STAGE = DocumentStage.READY_FOR_OFFICER_REVIEW


# --- wire models (camelCase via serialization_alias) -----------------------


class PageRegionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    page: int
    x: float
    y: float
    width: float
    height: float


class CandidateFactResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    document_version_id: UUID = Field(serialization_alias="documentVersionId")
    field_key: str = Field(serialization_alias="fieldKey")
    proposed_value: FactValue = Field(serialization_alias="proposedValue")
    confidence: float
    source: PageRegionResponse


class DocumentReviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    document_id: UUID = Field(serialization_alias="documentId")
    case_id: UUID = Field(serialization_alias="caseId")
    document_version_id: UUID = Field(serialization_alias="documentVersionId")
    document_version: int = Field(serialization_alias="documentVersion")
    stage: str
    file_name: str | None = Field(serialization_alias="fileName")
    page_count: int | None = Field(serialization_alias="pageCount")
    candidates: list[CandidateFactResponse]


class ConfirmedFactResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    candidate_id: UUID = Field(serialization_alias="candidateId")
    confirmation_id: UUID = Field(serialization_alias="confirmationId")
    document_version_id: UUID = Field(serialization_alias="documentVersionId")
    field_key: str = Field(serialization_alias="fieldKey")
    value: FactValue
    candidate_value: FactValue = Field(serialization_alias="candidateValue")
    source: PageRegionResponse
    confirmed_at: datetime = Field(serialization_alias="confirmedAt")
    stale: bool


class EvidenceListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    items: list[ConfirmedFactResponse]


class ConflictSourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    document_version_id: UUID = Field(serialization_alias="documentVersionId")
    value: FactValue
    source: PageRegionResponse | None


class ConflictResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    field_key: str = Field(serialization_alias="fieldKey")
    sources: list[ConflictSourceResponse]
    detected_at: datetime | None = Field(serialization_alias="detectedAt")
    stale: bool


class ConflictListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    items: list[ConflictResponse]


class ConfirmationResultResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    document_version_id: UUID = Field(serialization_alias="documentVersionId")
    confirmed_count: int = Field(serialization_alias="confirmedCount")
    created: bool


class CandidateDispositionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )

    candidate_id: UUID = Field(alias="candidateId")
    disposition: FactDisposition
    corrected_value: str | None = Field(default=None, alias="correctedValue", max_length=4000)
    rationale: str | None = Field(default=None, alias="rationale", max_length=4000)


class ConfirmDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_document_version: int = Field(alias="expectedDocumentVersion", ge=1)
    dispositions: list[CandidateDispositionRequest]


Actor = Annotated[ActorContext, Depends(require_actor)]


# --- guards + helpers (mirroring api/intake.py, api/risk_review.py) ---------


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


def _repository(request: Request) -> EvidenceReviewRepository:
    repository = getattr(request.app.state, "evidence_review_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="EVIDENCE_REVIEW_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ rà soát chứng cứ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(EvidenceReviewRepository, repository)


def _not_accessible() -> ApiException:
    """The single indistinguishable 404 for a missing/foreign/unassigned
    resource -- membership and existence are never disclosed."""

    return ApiException(
        status_code=404,
        code="CASE_NOT_ACCESSIBLE",
        message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
    )


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> CaseRecord:
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
        raise _not_accessible()
    return cast(CaseRecord, record)


async def _load_accessible_review(
    request: Request, actor: ActorContext, document_id: UUID
) -> DocumentReviewView:
    review = await _repository(request).load_document_review(document_id)
    if review is None:
        raise _not_accessible()
    await _assert_case_access(request, actor, review.case_id)
    return review


def _page_region(region: PageRegion) -> PageRegionResponse:
    return PageRegionResponse(
        page=region.page,
        x=region.x,
        y=region.y,
        width=region.width,
        height=region.height,
    )


# --- routes ----------------------------------------------------------------


@router.get("/documents/{document_id}/review", response_model=DocumentReviewResponse)
async def get_document_review(
    document_id: UUID, actor: Actor, request: Request
) -> DocumentReviewResponse:
    _require_participant(actor)
    review = await _load_accessible_review(request, actor, document_id)
    return DocumentReviewResponse(
        document_id=review.document_id,
        case_id=review.case_id,
        document_version_id=review.document_version_id,
        document_version=review.document_version,
        stage=review.stage.value,
        file_name=review.file_name,
        page_count=review.page_count,
        candidates=[
            CandidateFactResponse(
                id=candidate.candidate_id,
                case_id=review.case_id,
                case_version=review.case_version,
                document_version_id=review.document_version_id,
                field_key=candidate.field_key,
                proposed_value=candidate.proposed_value,
                confidence=candidate.confidence,
                source=_page_region(candidate.source),
            )
            for candidate in review.candidates
        ],
    )


@router.post(
    "/documents/{document_id}/confirmations",
    response_model=ConfirmationResultResponse,
    status_code=201,
)
async def confirm_document(
    document_id: UUID,
    body: ConfirmDocumentRequest,
    actor: Actor,
    request: Request,
) -> ConfirmationResultResponse:
    _require_intake_role(actor)
    review = await _load_accessible_review(request, actor, document_id)

    # Fail closed on a superseded snapshot before validating a batch the officer
    # built against a version they can no longer confirm.
    if body.expected_document_version != review.document_version:
        raise ApiException(
            status_code=409,
            code="STALE_DOCUMENT_VERSION",
            message_vi="Phiên bản tài liệu đã thay đổi; vui lòng tải lại trước khi xác nhận.",
            details={
                "expectedDocumentVersion": body.expected_document_version,
                "currentDocumentVersion": review.document_version,
            },
        )

    confirmations = _validate_batch(body, review)

    try:
        result = await _repository(request).record_confirmations(
            document_version_id=review.document_version_id,
            confirmations=confirmations,
            actor_id=actor.actor_id,
            expected_document_stage=_REVIEWABLE_STAGE,
        )
    except StaleDocumentVersionError as exc:
        raise ApiException(
            status_code=409,
            code="STALE_DOCUMENT_VERSION",
            message_vi="Phiên bản tài liệu đã thay đổi; vui lòng tải lại trước khi xác nhận.",
        ) from exc

    return ConfirmationResultResponse(
        document_version_id=review.document_version_id,
        confirmed_count=len(result.confirmation_ids),
        created=result.created,
    )


def _validate_batch(
    body: ConfirmDocumentRequest, review: DocumentReviewView
) -> tuple[ConfirmationInput, ...]:
    """Enforce the domain rules the frontend payload must satisfy: every
    candidate dispositioned exactly once, and ``CORRECTED`` carries both a
    corrected value and a rationale (nothing else may)."""

    review_ids = {candidate.candidate_id for candidate in review.candidates}
    seen: set[UUID] = set()
    for item in body.dispositions:
        if item.candidate_id in seen:
            raise ApiException(
                status_code=422,
                code="DUPLICATE_DISPOSITION",
                message_vi="Mỗi chứng cứ chỉ được xác nhận một lần trong một lượt.",
            )
        seen.add(item.candidate_id)
        if item.candidate_id not in review_ids:
            raise ApiException(
                status_code=422,
                code="UNKNOWN_CANDIDATE",
                message_vi="Yêu cầu tham chiếu chứng cứ không thuộc phiên bản tài liệu này.",
            )
        if item.disposition is FactDisposition.CORRECTED:
            if not item.corrected_value or not item.rationale:
                raise ApiException(
                    status_code=422,
                    code="CORRECTION_REQUIRES_VALUE_AND_RATIONALE",
                    message_vi="Điều chỉnh phải kèm giá trị đã sửa và lý do.",
                )
        elif item.corrected_value is not None or item.rationale is not None:
            raise ApiException(
                status_code=422,
                code="DISPOSITION_FORBIDS_CORRECTION",
                message_vi="Chỉ điều chỉnh mới được kèm giá trị đã sửa và lý do.",
            )

    if seen != review_ids:
        raise ApiException(
            status_code=422,
            code="INCOMPLETE_CONFIRMATION",
            message_vi="Phải xác nhận đúng một quyết định cho mỗi chứng cứ.",
            details={"expectedCount": len(review_ids), "submittedCount": len(seen)},
        )

    return tuple(
        ConfirmationInput(
            candidate_id=item.candidate_id,
            disposition=item.disposition,
            corrected_value=item.corrected_value
            if item.disposition is FactDisposition.CORRECTED
            else None,
            rationale=item.rationale
            if item.disposition is FactDisposition.CORRECTED
            else None,
        )
        for item in body.dispositions
    )


@router.get("/cases/{case_id}/evidence", response_model=EvidenceListResponse)
async def list_case_evidence(
    case_id: UUID, actor: Actor, request: Request
) -> EvidenceListResponse:
    _require_participant(actor)
    record = await _assert_case_access(request, actor, case_id)
    facts = await _repository(request).load_case_evidence(case_id, record.version)
    return EvidenceListResponse(items=[_confirmed_fact_response(fact) for fact in facts])


@router.get("/cases/{case_id}/conflicts", response_model=ConflictListResponse)
async def list_case_conflicts(
    case_id: UUID, actor: Actor, request: Request
) -> ConflictListResponse:
    _require_participant(actor)
    record = await _assert_case_access(request, actor, case_id)
    conflicts = await _repository(request).load_case_conflicts(case_id, record.version)
    return ConflictListResponse(items=[_conflict_response(conflict) for conflict in conflicts])


def _confirmed_fact_response(fact: EvidenceFactView) -> ConfirmedFactResponse:
    return ConfirmedFactResponse(
        id=fact.id,
        case_id=fact.case_id,
        case_version=fact.case_version,
        candidate_id=fact.candidate_id,
        confirmation_id=fact.confirmation_id,
        document_version_id=fact.document_version_id,
        field_key=fact.field_key,
        value=fact.value,
        candidate_value=fact.candidate_value,
        source=_page_region(fact.source),
        confirmed_at=fact.confirmed_at,
        stale=fact.stale,
    )


def _conflict_response(conflict: ConflictView) -> ConflictResponse:
    return ConflictResponse(
        id=conflict.id,
        case_id=conflict.case_id,
        case_version=conflict.case_version,
        field_key=conflict.field_key,
        sources=[
            ConflictSourceResponse(
                document_version_id=source.document_version_id,
                value=source.value,
                source=_page_region(source.source) if source.source is not None else None,
            )
            for source in conflict.sources
        ],
        detected_at=conflict.detected_at,
        stale=conflict.stale,
    )
