"""Stage-8 contract package API: deterministic rendering, versioned redlines,
material-change detection, and the three signing gates.

Master design section 5 giai đoạn 8 ("Đàm phán và ký kết hồ sơ tín dụng").  Every
surface is case-scoped and fail-closed (an unassigned actor gets the same
indistinguishable 404 as a missing case).  There is NO agent path anywhere and
NO LLM in this module -- the contract text is built by the DETERMINISTIC domain
template renderer from the credit decision's ApprovedTermSnapshot; the model
never invents a clause.

Surfaces:

- POST ``""``                -- OPS_OFFICER drafts the package deterministically
  from the current APPROVED_* credit decision + its frozen approved terms.  409
  when no permitting decision/snapshot exists.  Idempotent first draft.
- POST ``/redlines``         -- LEGAL_REVIEWER appends a versioned redline (a new
  REDLINED package version + the redline row, one transaction) -- never an edit.
- POST ``/approve``          -- OPS_CHECKER approves.  The server re-runs
  material-change detection against the CURRENT decision snapshot; ANY mismatch
  fences the package in MATERIAL_CHANGE_DETECTED and returns 409 (the case must
  return to stage 6 for a new decision -- a deferred loop, recorded not run).
  Otherwise it satisfies ``HG_CONTRACT_PACKAGE_APPROVED`` and reticks.
- POST ``/signature-authority`` -- confirms signing authority.  Requires the
  package-approval gate SATISFIED first (else 409); satisfies
  ``HG_SIGNATURE_AUTHORITY_CONFIRMED`` and reticks.
- POST ``/sign``             -- records MOCK signature evidence (real e-sign is
  OUT OF SCOPE).  Requires BOTH prior gates SATISFIED; appends the
  READY_FOR_SIGNATURE version + its 1:1 mock evidence, satisfies
  ``HG_CONTRACTS_SIGNED`` and reticks.
- GET ``""``                 -- any case participant reads the current package,
  its versioned redline history, and its signature evidence.

AUTHORITY MODEL (PROPOSED synthetic; no official SHB matrix -- master design
sections 4 & 24): the three gate-writing surfaces are gated by the ``OPS_OFFICER``
/ ``LEGAL_REVIEWER`` / ``OPS_CHECKER`` JWT roles plus a case assignment, BOTH
required and BOTH fail closed.  The signature-authority surface is an
ACTION_AUTHORIZER-analog: no dedicated authorizer JWT role has been supplied, so
it reuses ``OPS_CHECKER`` and records a documented PROPOSED note.  This module
exports ``router`` only; mounting it in ``main.py`` is a separate change.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.contract_packages import (
    ContractPackageAlreadySignedError,
    ContractPackageRepository,
    ContractPackageView,
    MaterialChangeBlockedError,
    NoContractPackageError,
    PermittingDecisionSnapshot,
    RecordedContractPackage,
    RecordedContractRedline,
    RecordedSignatureEvidence,
)
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.contract_packages import (
    ContractDecisionView,
    assert_renderable_decision,
    compute_content_hash,
    detect_material_change,
    render_contract_content_vi,
)
from creditops.domain.credit_decisions import ApprovedTerms, CreditDecisionType
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/contract-packages", tags=["contract-packages"]
)

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic human role that reviews/redlines the contract (mirrors
#: ``api/legal.py``); no official SHB mapping.
LEGAL_REVIEWER_ROLE = "LEGAL_REVIEWER"

#: PROPOSED synthetic human role that approves the package and, as an
#: ACTION_AUTHORIZER-analog, confirms signing authority and records the MOCK
#: signing.  No official SHB mapping (docs/AGENT_ARCHITECTURE.md).
OPS_CHECKER_ROLE = "OPS_CHECKER"


# -- request / response models ------------------------------------------------


class ContractPackageResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    decision_id: UUID = Field(serialization_alias="decisionId")
    term_snapshot_hash: str = Field(serialization_alias="termSnapshotHash")
    content_vi: str = Field(serialization_alias="content")
    content_hash: str = Field(serialization_alias="contentHash")
    package_version: int = Field(serialization_alias="packageVersion")
    state: str
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class RedlineResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    package_id: UUID = Field(serialization_alias="packageId")
    redline_version: int = Field(serialization_alias="redlineVersion")
    change_note_vi: str = Field(serialization_alias="changeNote")
    changed_content_vi: str = Field(serialization_alias="changedContent")
    changed_content_hash: str = Field(serialization_alias="changedContentHash")
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class SignatureEvidenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    package_id: UUID = Field(serialization_alias="packageId")
    kind: str
    signer_names: list[str] = Field(serialization_alias="signerNames")
    evidence_note_vi: str | None = Field(serialization_alias="evidenceNote")
    recorded_by: UUID = Field(serialization_alias="recordedBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class AddRedlineResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    redline: RedlineResponse
    package: ContractPackageResponse


class GateWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    package_id: UUID = Field(serialization_alias="packageId")
    disposition_ref: str = Field(serialization_alias="dispositionRef")


class SignResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    package: ContractPackageResponse
    signature_evidence: SignatureEvidenceResponse = Field(
        serialization_alias="signatureEvidence"
    )
    disposition_ref: str = Field(serialization_alias="dispositionRef")


class PackageViewResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    package: ContractPackageResponse
    redlines: list[RedlineResponse]
    signature_evidence: SignatureEvidenceResponse | None = Field(
        serialization_alias="signatureEvidence"
    )


class AddRedlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    change_note_vi: str = Field(alias="changeNote", min_length=1, max_length=4000)
    changed_content_vi: str = Field(alias="changedContent", min_length=1, max_length=200000)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


class SignatureAuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


class SignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    signer_names: tuple[str, ...] = Field(alias="signerNames", min_length=1)
    evidence_note_vi: str | None = Field(
        default=None, alias="evidenceNote", min_length=1, max_length=4000
    )


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- role + dependency helpers ------------------------------------------------


def _require_role(actor: ActorContext, role: str, message_vi: str) -> None:
    if role not in actor.roles:
        raise ApiException(
            status_code=403, code="INSUFFICIENT_ROLE", message_vi=message_vi
        )


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> ContractPackageRepository:
    repository = getattr(request.app.state, "contract_package_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="CONTRACT_PACKAGE_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hồ sơ hợp đồng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(ContractPackageRepository, repository)


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


async def _require_permitting_decision(
    repository: ContractPackageRepository, case_id: UUID, case_version: int
) -> PermittingDecisionSnapshot:
    permitting = await repository.load_permitting_decision(case_id, case_version)
    if permitting is None:
        raise ApiException(
            status_code=409,
            code="NO_PERMITTING_DECISION",
            message_vi=(
                "Chưa có quyết định phê duyệt kèm điều khoản để lập hồ sơ hợp đồng."
            ),
        )
    return permitting


async def _gate_satisfied(
    orchestration: OrchestrationRepository,
    case_id: UUID,
    case_version: int,
    gate_type: GateType,
) -> bool:
    snapshot = await orchestration.load_snapshot(case_id)
    if snapshot is None:
        return False
    return any(
        gate.gate_type == gate_type
        and gate.case_version == case_version
        and gate.status is GateStatus.SATISFIED
        for gate in snapshot.gates
    )


# -- response mappers ---------------------------------------------------------


def _package_response(package: RecordedContractPackage) -> ContractPackageResponse:
    return ContractPackageResponse(
        id=package.id,
        case_id=package.case_id,
        case_version=package.case_version,
        decision_id=package.decision_id,
        term_snapshot_hash=package.term_snapshot_hash,
        content_vi=package.content_vi,
        content_hash=package.content_hash,
        package_version=package.package_version,
        state=package.state,
        created_by=package.created_by,
        created_at=package.created_at,
    )


def _redline_response(redline: RecordedContractRedline) -> RedlineResponse:
    return RedlineResponse(
        id=redline.id,
        package_id=redline.package_id,
        redline_version=redline.redline_version,
        change_note_vi=redline.change_note_vi,
        changed_content_vi=redline.changed_content_vi,
        changed_content_hash=redline.changed_content_hash,
        created_by=redline.created_by,
        created_at=redline.created_at,
    )


def _evidence_response(
    evidence: RecordedSignatureEvidence,
) -> SignatureEvidenceResponse:
    return SignatureEvidenceResponse(
        id=evidence.id,
        package_id=evidence.package_id,
        kind=evidence.kind,
        signer_names=list(evidence.signer_names),
        evidence_note_vi=evidence.evidence_note_vi,
        recorded_by=evidence.recorded_by,
        created_at=evidence.created_at,
    )


# -- endpoints ----------------------------------------------------------------


@router.post("", response_model=ContractPackageResponse, status_code=201)
async def create_contract_package(
    case_id: UUID, actor: Actor, request: Request, response: Response
) -> ContractPackageResponse:
    """Deterministically render + persist the first contract-package draft.

    OPS_OFFICER + assignment.  Requires an APPROVED_* credit decision with a
    frozen approved-term snapshot (else 409).  Idempotent: a repeat returns the
    current package with 200.
    """

    _require_role(
        actor,
        OPS_OFFICER_ROLE,
        "Bạn không có vai trò tác nghiệp tín dụng được yêu cầu.",
    )
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    permitting = await _require_permitting_decision(repository, case_id, record.version)
    decision_type = CreditDecisionType(permitting.decision_type)
    assert_renderable_decision(decision_type)
    decision_view = ContractDecisionView(
        decision_type=decision_type,
        rationale_vi=permitting.rationale_vi,
        conditions=permitting.conditions,
    )
    terms = ApprovedTerms.model_validate(dict(permitting.terms))
    content_vi = render_contract_content_vi(decision_view, terms)

    created = await repository.create_package(
        case_id=case_id,
        case_version=record.version,
        decision_id=permitting.decision_id,
        term_snapshot_hash=permitting.snapshot_hash,
        content_vi=content_vi,
        content_hash=compute_content_hash(content_vi),
        actor_id=actor.actor_id,
    )
    if not created.created:
        response.status_code = 200
    return _package_response(created.package)


@router.post("/redlines", response_model=AddRedlineResponse, status_code=201)
async def add_contract_redline(
    case_id: UUID, body: AddRedlineRequest, actor: Actor, request: Request
) -> AddRedlineResponse:
    """Append a versioned redline: a new REDLINED package version + the redline
    row in one transaction.  LEGAL_REVIEWER + assignment.

    Deterministic-by-construction: ANY redline sets state REDLINED; whether the
    change is material is re-verified at approve time against the decision
    snapshot, never guessed here.
    """

    _require_role(
        actor,
        LEGAL_REVIEWER_ROLE,
        "Bạn không có vai trò rà soát pháp lý được yêu cầu.",
    )
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    try:
        added = await repository.add_redline(
            case_id=case_id,
            case_version=record.version,
            change_note_vi=body.change_note_vi,
            changed_content_vi=body.changed_content_vi,
            changed_content_hash=compute_content_hash(body.changed_content_vi),
            actor_id=actor.actor_id,
        )
    except NoContractPackageError as exc:
        raise _no_package_exception() from exc
    return AddRedlineResponse(
        redline=_redline_response(added.redline),
        package=_package_response(added.package),
    )


@router.post("/approve", response_model=GateWriteResponse)
async def approve_contract_package(
    case_id: UUID, body: ApproveRequest, actor: Actor, request: Request
) -> GateWriteResponse:
    """Approve the current package; satisfy ``HG_CONTRACT_PACKAGE_APPROVED``.

    OPS_CHECKER + assignment.  Blocked 409 ``MATERIAL_CHANGE_DETECTED`` when the
    package's term-snapshot hash no longer matches the CURRENT decision snapshot
    (the package is fenced; the case must return to stage 6 -- a deferred loop).
    """

    _require_role(
        actor,
        OPS_CHECKER_ROLE,
        "Bạn không có vai trò kiểm soát tác nghiệp được yêu cầu.",
    )
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    orchestration = _orchestration_repository(request)

    package = await repository.load_current_package(case_id, record.version)
    if package is None:
        raise _no_package_exception()
    permitting = await _require_permitting_decision(repository, case_id, record.version)

    if detect_material_change(package.term_snapshot_hash, permitting.snapshot_hash):
        fenced = await repository.mark_material_change(
            case_id=case_id, case_version=record.version, actor_id=actor.actor_id
        )
        raise ApiException(
            status_code=409,
            code="MATERIAL_CHANGE_DETECTED",
            message_vi=(
                "Điều khoản hợp đồng không còn khớp quyết định tín dụng hiện tại; "
                "hồ sơ phải quay lại giai đoạn quyết định (stage 6) để tạo quyết "
                "định mới."
            ),
            details={"packageVersion": fenced.package_version, "state": fenced.state},
        )

    disposition_ref = f"contract-package:{package.id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_CONTRACT_PACKAGE_APPROVED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await _audit_gate(
        orchestration,
        case_id=case_id,
        case_version=record.version,
        event_type="CONTRACT_PACKAGE_APPROVED",
        artifact_id=package.id,
        event_data={
            "actorId": str(actor.actor_id),
            "actorRole": OPS_CHECKER_ROLE,
            "rationale": body.rationale_vi,
            "packageVersion": package.package_version,
        },
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_CONTR:{package.id}"
    )
    return GateWriteResponse(
        gate_type=GateType.HG_CONTRACT_PACKAGE_APPROVED.value,
        status=GateStatus.SATISFIED.value,
        package_id=package.id,
        disposition_ref=disposition_ref,
    )


@router.post("/signature-authority", response_model=GateWriteResponse)
async def confirm_signature_authority(
    case_id: UUID, body: SignatureAuthorityRequest, actor: Actor, request: Request
) -> GateWriteResponse:
    """Confirm signing authority; satisfy ``HG_SIGNATURE_AUTHORITY_CONFIRMED``.

    ACTION_AUTHORIZER-analog (PROPOSED: reuses OPS_CHECKER; no dedicated
    authorizer role supplied).  Requires ``HG_CONTRACT_PACKAGE_APPROVED``
    SATISFIED first, else 409 -- gates are strictly ordered.
    """

    _require_role(
        actor,
        OPS_CHECKER_ROLE,
        "Bạn không có vai trò kiểm soát tác nghiệp được yêu cầu.",
    )
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    orchestration = _orchestration_repository(request)

    package = await repository.load_current_package(case_id, record.version)
    if package is None:
        raise _no_package_exception()
    if not await _gate_satisfied(
        orchestration, case_id, record.version, GateType.HG_CONTRACT_PACKAGE_APPROVED
    ):
        raise _gate_order_exception(
            "Chưa phê duyệt hồ sơ hợp đồng trước khi xác nhận thẩm quyền ký."
        )

    disposition_ref = f"signature-authority:{package.id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_SIGNATURE_AUTHORITY_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await _audit_gate(
        orchestration,
        case_id=case_id,
        case_version=record.version,
        event_type="SIGNATURE_AUTHORITY_CONFIRMED",
        artifact_id=package.id,
        event_data={
            "actorId": str(actor.actor_id),
            # PROPOSED: OPS_CHECKER stands in for an ACTION_AUTHORIZER (no
            # dedicated signing-authority role has been supplied).
            "actorRole": OPS_CHECKER_ROLE,
            "authorityNote": "PROPOSED ACTION_AUTHORIZER-analog (OPS_CHECKER)",
            "rationale": body.rationale_vi,
        },
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_SIGAUTH:{package.id}"
    )
    return GateWriteResponse(
        gate_type=GateType.HG_SIGNATURE_AUTHORITY_CONFIRMED.value,
        status=GateStatus.SATISFIED.value,
        package_id=package.id,
        disposition_ref=disposition_ref,
    )


@router.post("/sign", response_model=SignResponse)
async def sign_contract_package(
    case_id: UUID, body: SignRequest, actor: Actor, request: Request
) -> SignResponse:
    """Record MOCK signature evidence; satisfy ``HG_CONTRACTS_SIGNED``.

    OPS_CHECKER + assignment.  Requires BOTH prior gates SATISFIED (else 409).
    Real e-sign / execution is OUT OF SCOPE: the record is mock evidence only.
    A defensive material-change re-check fences the package (409) if the terms
    drifted since approval.
    """

    _require_role(
        actor,
        OPS_CHECKER_ROLE,
        "Bạn không có vai trò kiểm soát tác nghiệp được yêu cầu.",
    )
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    orchestration = _orchestration_repository(request)

    package = await repository.load_current_package(case_id, record.version)
    if package is None:
        raise _no_package_exception()
    approved = await _gate_satisfied(
        orchestration, case_id, record.version, GateType.HG_CONTRACT_PACKAGE_APPROVED
    )
    authorized = await _gate_satisfied(
        orchestration, case_id, record.version, GateType.HG_SIGNATURE_AUTHORITY_CONFIRMED
    )
    if not (approved and authorized):
        raise _gate_order_exception(
            "Phải phê duyệt hồ sơ và xác nhận thẩm quyền ký trước khi ký."
        )

    # Defensive: re-verify material change against the CURRENT decision snapshot
    # even after both gates -- a decision may have changed since approval.
    permitting = await _require_permitting_decision(repository, case_id, record.version)
    if detect_material_change(package.term_snapshot_hash, permitting.snapshot_hash):
        fenced = await repository.mark_material_change(
            case_id=case_id, case_version=record.version, actor_id=actor.actor_id
        )
        raise ApiException(
            status_code=409,
            code="MATERIAL_CHANGE_DETECTED",
            message_vi=(
                "Điều khoản hợp đồng không còn khớp quyết định tín dụng hiện tại; "
                "không thể ký."
            ),
            details={"packageVersion": fenced.package_version, "state": fenced.state},
        )

    try:
        signed = await repository.record_signature_evidence(
            case_id=case_id,
            case_version=record.version,
            signer_names=body.signer_names,
            evidence_note_vi=body.evidence_note_vi,
            actor_id=actor.actor_id,
        )
    except ContractPackageAlreadySignedError as exc:
        raise ApiException(
            status_code=409,
            code="CONTRACT_ALREADY_SIGNED",
            message_vi="Hồ sơ hợp đồng đã được ký (bằng chứng mô phỏng).",
        ) from exc
    except MaterialChangeBlockedError as exc:
        raise ApiException(
            status_code=409,
            code="MATERIAL_CHANGE_DETECTED",
            message_vi="Hồ sơ đang bị chặn do thay đổi trọng yếu; không thể ký.",
        ) from exc
    except NoContractPackageError as exc:
        raise _no_package_exception() from exc

    disposition_ref = f"contracts-signed:{signed.evidence.id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_CONTRACTS_SIGNED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await _audit_gate(
        orchestration,
        case_id=case_id,
        case_version=record.version,
        event_type="CONTRACTS_SIGNED_MOCK",
        artifact_id=signed.evidence.id,
        event_data={
            "actorId": str(actor.actor_id),
            "actorRole": OPS_CHECKER_ROLE,
            "kind": signed.evidence.kind,
            "packageVersion": signed.package.package_version,
            "note": "MOCK signature evidence only; real execution is out of scope",
        },
    )
    await _retick_orchestration(
        request,
        orchestration,
        case_id=case_id,
        trigger_ref=f"HG_SIGNED:{signed.package.id}",
    )
    return SignResponse(
        gate_type=GateType.HG_CONTRACTS_SIGNED.value,
        status=GateStatus.SATISFIED.value,
        package=_package_response(signed.package),
        signature_evidence=_evidence_response(signed.evidence),
        disposition_ref=disposition_ref,
    )


@router.get("", response_model=PackageViewResponse)
async def get_contract_package(
    case_id: UUID, actor: Actor, request: Request
) -> PackageViewResponse:
    """Read the current package, its versioned redlines, and signature evidence.

    Any case participant + assignment.
    """

    _require_participant(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    view = await repository.load_package_view(case_id, record.version)
    if view is None:
        raise _no_package_exception(status_code=404)
    return _view_response(view)


def _view_response(view: ContractPackageView) -> PackageViewResponse:
    return PackageViewResponse(
        package=_package_response(view.package),
        redlines=[_redline_response(redline) for redline in view.redlines],
        signature_evidence=(
            _evidence_response(view.signature_evidence)
            if view.signature_evidence is not None
            else None
        ),
    )


# -- shared exceptions + orchestration retick ---------------------------------


def _no_package_exception(*, status_code: int = 409) -> ApiException:
    return ApiException(
        status_code=status_code,
        code="NO_CONTRACT_PACKAGE",
        message_vi="Chưa có hồ sơ hợp đồng cho phiên bản hồ sơ hiện tại.",
    )


def _gate_order_exception(message_vi: str) -> ApiException:
    return ApiException(
        status_code=409, code="GATE_ORDER_VIOLATION", message_vi=message_vi
    )


async def _audit_gate(
    orchestration: OrchestrationRepository,
    *,
    case_id: UUID,
    case_version: int,
    event_type: str,
    artifact_id: UUID,
    event_data: dict[str, object],
) -> None:
    await orchestration.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=case_version,
            event_type=event_type,
            execution_id=artifact_id,
            artifact_type="CONTRACT_PACKAGE",
            artifact_id=artifact_id,
            event_data=event_data,
        )
    )


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after a gate satisfaction.

    A tick failure must never fail the human's already-recorded action, but it
    is logged, never silent (mirrors ``api/financing.py``).
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
            "Orchestration retick failed; the action is durable and the case can "
            "be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )
