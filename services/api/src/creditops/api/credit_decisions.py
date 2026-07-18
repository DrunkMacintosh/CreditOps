"""Human Credit Decision API: human-only decision write + participant read.

Master design section 5 stage 6; P0 #9 remainder.  This is the ONLY surface
that records a ``HumanCreditDecision``; there is deliberately NO agent-callable
path anywhere to it.

AUTHORITY MODEL (PROPOSED synthetic; documented because no official SHB
authority matrix exists -- master design sections 4 and 5 stage 6):

- No official SHB decision authority matrix, JWT role, or ``case_role`` for a
  credit approver has been supplied.  The closed synthetic ``case_role`` set
  (supabase/migrations/202607180008_case_assignment_roles.sql) has no credit-
  approver member, and ``application/orchestration/roles.py`` carries no such
  role either.  So the credit-decision authority is modelled as a NEW dedicated
  JWT role ``CREDIT_APPROVER`` checked here at the API layer, PLUS any case
  assignment for row access.  Both are required and BOTH fail closed: a missing
  ``CREDIT_APPROVER`` role is a 403 with a Vietnamese message; a caller not
  assigned to the case gets an indistinguishable 404.

BINDING VALIDATIONS (fail closed): the server verifies ``caseVersion`` equals
the case's current version (else 409 ``STALE_CASE_VERSION`` with the expected
version), and that any referenced memo/risk/underwriting artifact id is the
current-version artifact for the case (else 422).  The decision then binds that
exact case version and those exact artifact versions.

NO gate write and NO orchestration side effect happen here (unlike
``api/risk_review.py`` / ``api/credit_ops.py``): wiring
``HG_CREDIT_DECISION_RECORDED`` and any downstream tick is a later lead
decision.  This module also exports ``router`` only; mounting it in
``main.py`` (and wiring ``app.state.credit_decision_repository``) is likewise
deferred to that later decision.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.ports.credit_decisions import (
    CreditDecisionRepository,
    DecisionBinding,
    RecordedDecision,
)
from creditops.application.unit_of_work import ActorContext
from creditops.domain.credit_decisions import (
    ApprovedTerms,
    CreditDecisionType,
    HumanCreditDecision,
    assert_snapshot_matches_decision,
    build_term_snapshot,
)

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/human-credit-decisions",
    tags=["human-credit-decisions"],
)

#: PROPOSED synthetic JWT authority role for recording a credit decision (see
#: module docstring): no official SHB role exists, so this dedicated role is the
#: API-layer authority check, alongside a case assignment for row access.
CREDIT_APPROVER_ROLE = "CREDIT_APPROVER"

#: Roles allowed to READ a recorded decision: any case participant, plus the
#: credit approver themselves.  Row access is still the case-assignment check.
_READ_ROLES = CASE_PARTICIPANT_ROLES | {CREDIT_APPROVER_ROLE}


class TermsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    term: str | None = Field(default=None, min_length=1, max_length=100)
    rate: Decimal | None = None


class RecordDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: str = Field(min_length=1, max_length=64)
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)
    case_version: int = Field(alias="caseVersion", ge=1)
    memo_artifact_id: UUID | None = Field(alias="memoArtifactId", default=None)
    risk_assessment_id: UUID | None = Field(alias="riskAssessmentId", default=None)
    underwriting_assessment_id: UUID | None = Field(
        alias="underwritingAssessmentId", default=None
    )
    conditions: tuple[str, ...] = ()
    terms: TermsRequest | None = None


class TermSnapshotResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    snapshot_hash: str = Field(serialization_alias="snapshotHash")
    terms: dict[str, object]
    created_at: datetime = Field(serialization_alias="createdAt")


class CreditDecisionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    decision: str
    rationale_vi: str = Field(serialization_alias="rationale")
    decided_by: UUID = Field(serialization_alias="decidedBy")
    decided_by_role: str = Field(serialization_alias="decidedByRole")
    memo_artifact_id: UUID | None = Field(serialization_alias="memoArtifactId")
    risk_assessment_id: UUID | None = Field(serialization_alias="riskAssessmentId")
    underwriting_assessment_id: UUID | None = Field(
        serialization_alias="underwritingAssessmentId"
    )
    conditions: list[str]
    created_at: datetime = Field(serialization_alias="createdAt")
    approved_terms: TermSnapshotResponse | None = Field(
        serialization_alias="approvedTerms"
    )


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_credit_approver(actor: ActorContext) -> None:
    if CREDIT_APPROVER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có thẩm quyền phê duyệt tín dụng được yêu cầu.",
        )


def _require_reader(actor: ActorContext) -> None:
    if not (_READ_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> CreditDecisionRepository:
    repository = getattr(request.app.state, "credit_decision_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="CREDIT_DECISION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ quyết định tín dụng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(CreditDecisionRepository, repository)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> None:
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


def _load_binding(binding: DecisionBinding | None) -> DecisionBinding:
    if binding is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
        )
    return binding


def _assert_artifact_binding(
    *,
    field: str,
    referenced: UUID | None,
    current: UUID | None,
) -> None:
    """A referenced artifact id must be the case's current-version artifact."""
    if referenced is None:
        return
    if referenced != current:
        raise ApiException(
            status_code=422,
            code="UNKNOWN_ARTIFACT_BINDING",
            message_vi=(
                "Mã tạo tác tham chiếu không khớp phiên bản hiện tại của hồ sơ."
            ),
            details={"field": field},
        )


@router.post("", response_model=CreditDecisionResponse, status_code=201)
async def record_credit_decision(
    case_id: UUID,
    body: RecordDecisionRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> CreditDecisionResponse:
    """Record ONE append-only human credit decision for the current case version.

    Human-only: requires the ``CREDIT_APPROVER`` JWT role and a case assignment.
    Idempotent on (case, version): a repeat returns the existing decision with
    a 200.  No gate is satisfied and no orchestration runs here.
    """

    _require_credit_approver(actor)
    await _assert_case_access(request, actor, case_id)

    try:
        decision_type = CreditDecisionType(body.decision)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_DECISION_TYPE",
            message_vi="Loại quyết định tín dụng không hợp lệ.",
        ) from exc

    repository = _repository(request)
    binding = _load_binding(await repository.load_decision_binding(case_id))

    if body.case_version != binding.current_case_version:
        raise ApiException(
            status_code=409,
            code="STALE_CASE_VERSION",
            message_vi=(
                "Phiên bản hồ sơ đã thay đổi; hãy xem lại hồ sơ trước khi quyết định."
            ),
            details={"expectedVersion": binding.current_case_version},
        )

    _assert_artifact_binding(
        field="memoArtifactId",
        referenced=body.memo_artifact_id,
        current=binding.latest_memo_artifact_id,
    )
    _assert_artifact_binding(
        field="riskAssessmentId",
        referenced=body.risk_assessment_id,
        current=binding.latest_risk_assessment_id,
    )
    _assert_artifact_binding(
        field="underwritingAssessmentId",
        referenced=body.underwriting_assessment_id,
        current=binding.latest_underwriting_assessment_id,
    )

    try:
        decision = HumanCreditDecision(
            id=uuid4(),
            case_id=case_id,
            case_version=body.case_version,
            decision=decision_type,
            rationale_vi=body.rationale_vi,
            decided_by=actor.actor_id,
            decided_by_role=CREDIT_APPROVER_ROLE,
            memo_artifact_id=body.memo_artifact_id,
            risk_assessment_id=body.risk_assessment_id,
            underwriting_assessment_id=body.underwriting_assessment_id,
            conditions=body.conditions,
        )
        snapshot = None
        if body.terms is not None:
            terms = ApprovedTerms(
                amount=body.terms.amount,
                currency=body.terms.currency,
                term=body.terms.term,
                rate=body.terms.rate,
            )
            snapshot = build_term_snapshot(
                snapshot_id=uuid4(), decision=decision, terms=terms
            )
        assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_CREDIT_DECISION",
            message_vi="Quyết định tín dụng không hợp lệ so với ràng buộc nghiệp vụ.",
        ) from exc

    recorded = await repository.record_decision(decision=decision, snapshot=snapshot)
    if not recorded.created:
        response.status_code = 200
    return _decision_response(recorded)


@router.get("", response_model=CreditDecisionResponse)
async def get_credit_decision(
    case_id: UUID, actor: Actor, request: Request
) -> CreditDecisionResponse:
    """Read the credit decision recorded for the case's current version."""

    _require_reader(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    binding = _load_binding(await repository.load_decision_binding(case_id))
    recorded = await repository.load_decision(case_id, binding.current_case_version)
    if recorded is None:
        raise ApiException(
            status_code=404,
            code="CREDIT_DECISION_NOT_AVAILABLE",
            message_vi="Chưa có quyết định tín dụng cho phiên bản hồ sơ hiện tại.",
        )
    return _decision_response(recorded)


def _decision_response(record: RecordedDecision) -> CreditDecisionResponse:
    return CreditDecisionResponse(
        id=record.id,
        case_id=record.case_id,
        case_version=record.case_version,
        decision=record.decision,
        rationale_vi=record.rationale_vi,
        decided_by=record.decided_by,
        decided_by_role=record.decided_by_role,
        memo_artifact_id=record.memo_artifact_id,
        risk_assessment_id=record.risk_assessment_id,
        underwriting_assessment_id=record.underwriting_assessment_id,
        conditions=list(record.conditions),
        created_at=record.created_at,
        approved_terms=(
            TermSnapshotResponse(
                id=record.snapshot.id,
                snapshot_hash=record.snapshot.snapshot_hash,
                terms=dict(record.snapshot.terms),
                created_at=record.snapshot.created_at,
            )
            if record.snapshot is not None
            else None
        ),
    )
