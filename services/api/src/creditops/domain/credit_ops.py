"""Structured output contract for the Credit Operations Agent.

Design invariants (docs/AGENT_ARCHITECTURE.md §Credit Operations Agent;
CONTEXT.md glossary: Proposed Action, Credit Memo, Disposition, Handoff):

- Credit Operations is the FIFTH and LAST specialist role.  It never approves
  or rejects credit, never sends a customer-facing request, never mutates a
  banking system, and never signs, disburses, or otherwise executes a
  sensitive action.  ``extra="forbid"`` plus TWO import-time schema guards
  (one over the whole package, one scoped to ``DraftCreditMemo`` only) make a
  decision/approval/execution-capable field a construction-time error, not a
  reviewable mistake.
- ``package_completeness`` is a deterministic checklist -- computed BEFORE
  inference, never by the LLM -- recording which upstream artifacts are
  present or missing for the case version.  A missing artifact is RECORDED,
  never hidden; the package still assembles (application/credit_ops/analysis.py).
- ``evidence_consolidation`` is the full, deduplicated provenance index over
  every upstream assessment/execution id this package rests on, so the
  package stands on its own for audit.
- ``document_requests`` are drafts only.  Every request starts
  ``PENDING_APPROVAL`` and can only reach ``APPROVED`` through a separate,
  append-only human approval record (the "G2 pattern" -- mirrors
  ``G2_GAP_REQUEST_APPROVAL``).  There is no send/dispatch field or method
  anywhere in this module; approval flips a read-only *view*, never this row.
- ``draft_memo`` is a structured Vietnamese narrative built EXCLUSIVELY over
  cited upstream findings (underwriting, legal/collateral, independent risk
  review).  Every ``MemoStatement`` requires >=1 citation
  (``Field(min_length=1)``): a section that omits citations is structurally
  impossible, not merely discouraged.  ``synthetic_disclaimer_vi`` is a
  mandatory, content-pinned header field.
- ``proposed_actions`` are typed drafts.  ``ProposedActionExecutionStatus``
  has exactly ONE member, ``DRAFT`` -- there is deliberately no ``EXECUTED``
  value anywhere in this schema, and no executor code path exists in this
  codebase (tests/unit/credit_ops/test_port_surface.py proves the port
  surface has no execute-shaped method).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId, EvidenceGapId, TaskId
from creditops.domain.underwriting import FORBIDDEN_DECISION_FIELD_NAMES
from creditops.domain.underwriting import GapBlockingLevel as GapBlockingLevel

type CreditOpsPackageId = UUID

CREDIT_OPS_AGENT_ROLE: Literal["CREDIT_OPERATIONS"] = "CREDIT_OPERATIONS"

#: The standard synthetic-data disclaimer (AGENTS.md "Non-negotiable
#: boundaries"), translated to Vietnamese and pinned as the ONLY value the
#: mandatory header field may hold.
SYNTHETIC_DISCLAIMER_VI: Final = (
    "Toan bo du lieu khach hang, chinh sach, tai lieu va phan hoi he thong "
    "ngan hang trong du an nay la du lieu tong hop (synthetic), chi phuc vu "
    "muc dich trinh dien; day KHONG PHAI la mot quyet dinh tin dung."
)

#: Package-wide forbidden field names: no field anywhere in the credit-ops
#: output may express a decision, approval, disbursement, signature, or
#: execution.  Deliberately does NOT include "approvalstatus" -- a document
#: request's own ``approval_status`` field is the explicit, required "G2
#: pattern" view field described in the module docstring; only the MEMO gets
#: the stricter check below.
FORBIDDEN_CREDIT_OPS_FIELD_NAMES = FORBIDDEN_DECISION_FIELD_NAMES | frozenset(
    {
        "memodecision",
        "signoff",
        "signeddocument",
        "execute",
        "executed",
        "executing",
        "dispatch",
        "dispatched",
        "send",
        "sent",
    }
)

#: Additional names forbidden ONLY inside ``DraftCreditMemo`` (deliverable:
#: "extend the forbidden-field guard: also memo_decision, approval_status on
#: the memo itself, disbursement, sign_off").  Scoped narrowly so a document
#: request elsewhere in the package may still legitimately carry
#: ``approval_status``.
_MEMO_ONLY_FORBIDDEN_FIELD_NAMES = frozenset({"approvalstatus"})


class UpstreamArtifactKind(StrEnum):
    """The finite set of upstream artifacts the completeness checklist tracks."""

    INTAKE_HANDOFF = "INTAKE_HANDOFF"
    UNDERWRITING_ASSESSMENT = "UNDERWRITING_ASSESSMENT"
    LEGAL_ASSESSMENT = "LEGAL_ASSESSMENT"
    RISK_REVIEW_ASSESSMENT = "RISK_REVIEW_ASSESSMENT"


class ChecklistItemStatus(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"


class PackageChecklistItem(BaseModel):
    """One deterministic completeness check for one upstream artifact.

    ``reference_id`` is the artifact's own durable id (handoff or assessment
    id) when ``PRESENT``, and ``None`` when ``MISSING`` -- the absence itself
    is the evidence reference for a missing item.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: UpstreamArtifactKind
    status: ChecklistItemStatus
    detail_vi: str = Field(min_length=1, max_length=1000)
    reference_id: UUID | None = None

    @model_validator(mode="after")
    def _reference_matches_status(self) -> Self:
        if self.status is ChecklistItemStatus.PRESENT and self.reference_id is None:
            raise ValueError("a PRESENT checklist item must carry its artifact reference id")
        if self.status is ChecklistItemStatus.MISSING and self.reference_id is not None:
            raise ValueError("a MISSING checklist item cannot carry a reference id")
        return self


class PackageCompleteness(BaseModel):
    """Deterministic checklist result computed BEFORE inference.

    ``dispositions_state_vi`` summarizes the checker's G3_RISK_DISPOSITION
    status; ``unresolved_challenge_count`` and ``open_blocking_gap_count``
    are exact counts from the case's live state.  Every field here is
    produced by ``application/credit_ops/analysis.py`` alone; the LLM never
    populates or edits this section.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts: tuple[PackageChecklistItem, ...] = Field(min_length=1)
    dispositions_state_vi: str = Field(min_length=1, max_length=2000)
    unresolved_challenge_count: int = Field(ge=0)
    open_blocking_gap_count: int = Field(ge=0)
    all_required_present: bool

    @model_validator(mode="after")
    def _all_required_present_is_consistent(self) -> Self:
        computed = all(item.status is ChecklistItemStatus.PRESENT for item in self.artifacts)
        if computed != self.all_required_present:
            raise ValueError(
                "all_required_present must equal whether every checklist item is PRESENT"
            )
        kinds = {item.artifact for item in self.artifacts}
        if len(kinds) != len(self.artifacts):
            raise ValueError("package_completeness must list each artifact kind at most once")
        return self


class ProvenanceIndexEntry(BaseModel):
    """One deduplicated provenance-index row for one upstream artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: UpstreamArtifactKind
    assessment_id: UUID | None = None
    execution_id: UUID | None = None
    handoff_id: UUID | None = None
    citation_count: int = Field(ge=0)


class EvidenceConsolidation(BaseModel):
    """The full provenance index: every upstream assessment/execution id,
    deduplicated, so the package is audit-ready standing alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[ProvenanceIndexEntry, ...] = ()
    distinct_citation_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _entries_are_deduplicated(self) -> Self:
        kinds = [entry.artifact for entry in self.entries]
        if len(kinds) != len(set(kinds)):
            raise ValueError("evidence_consolidation entries must be deduplicated by artifact")
        return self


class DocumentRequestApprovalStatus(StrEnum):
    """A document request's derived approval view.  See module docstring
    "G2 pattern": starts PENDING_APPROVAL, can only reach APPROVED through a
    separate append-only human approval record."""

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"


class DocumentRequest(BaseModel):
    """A consolidated additional-document request, drafted only.

    No send/dispatch field or method exists anywhere in this module; a human
    must explicitly approve (application/credit_ops's document-request
    approval API) before any customer-facing communication could ever be
    considered, and even then this codebase contains no send mechanism at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    originating_gap_id: EvidenceGapId
    request_text_vi: str = Field(min_length=1, max_length=2000)
    blocking_level: GapBlockingLevel
    approval_status: DocumentRequestApprovalStatus = DocumentRequestApprovalStatus.PENDING_APPROVAL


class MemoSource(StrEnum):
    """Which upstream specialist assessment a memo citation points into."""

    CREDIT_UNDERWRITING = "CREDIT_UNDERWRITING"
    LEGAL_COMPLIANCE_COLLATERAL = "LEGAL_COMPLIANCE_COLLATERAL"
    INDEPENDENT_RISK_REVIEW = "INDEPENDENT_RISK_REVIEW"


class MemoFindingRef(BaseModel):
    """A pointer into one upstream assessment: which one, and what path.

    Mirrors ``creditops.domain.risk_review.MakerFindingRef``; the closed set
    of valid ``section_path`` values for one execution is enumerated by
    ``application/credit_ops/evidence.py``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: MemoSource
    source_assessment_id: UUID
    section_path: str = Field(min_length=1, max_length=200)


class MemoFindingCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["MEMO_FINDING"] = "MEMO_FINDING"
    ref: MemoFindingRef


class MemoStatement(BaseModel):
    """One material memo statement.  ``citations`` has ``min_length=1``: a
    statement -- and therefore a section built only from statements -- cannot
    omit evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[MemoFindingCitation, ...] = Field(min_length=1)


class MemoSection(BaseModel):
    """A narrative memo section built exclusively from cited statements."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statements: tuple[MemoStatement, ...] = Field(min_length=1)


class ChallengeStatusSection(BaseModel):
    """The "thach thuc checker + disposition trang thai" memo section.

    ``disposition_status_vi`` is a deterministic summary of
    G3_RISK_DISPOSITION (never an LLM-authored decision); the narrative
    ``statements`` discuss individual challenges, each cited back to the
    independent risk review assessment.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statements: tuple[MemoStatement, ...] = Field(min_length=1)
    disposition_status_vi: str = Field(min_length=1, max_length=2000)


class DraftCreditMemo(BaseModel):
    """The draft Vietnamese credit-memo narrative.  A draft for human
    decision-makers; NEVER a decision (CONTEXT.md: "Credit Memo ... A draft
    for human decision-makers, never a decision").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    synthetic_disclaimer_vi: Literal[
        "Toan bo du lieu khach hang, chinh sach, tai lieu va phan hoi he thong "
        "ngan hang trong du an nay la du lieu tong hop (synthetic), chi phuc vu "
        "muc dich trinh dien; day KHONG PHAI la mot quyet dinh tin dung."
    ] = SYNTHETIC_DISCLAIMER_VI
    tom_tat_nhu_cau: MemoSection
    phan_tich_maker: MemoSection
    ra_soat_phap_ly_tsbd: MemoSection
    thach_thuc_checker: ChallengeStatusSection
    dieu_kien_de_xuat: MemoSection
    phu_luc_bang_chung: MemoSection

    def _iter_citations(self) -> tuple[MemoFindingCitation, ...]:
        citations: list[MemoFindingCitation] = []
        for section in (
            self.tom_tat_nhu_cau,
            self.phan_tich_maker,
            self.ra_soat_phap_ly_tsbd,
            self.dieu_kien_de_xuat,
            self.phu_luc_bang_chung,
        ):
            for statement in section.statements:
                citations.extend(statement.citations)
        for statement in self.thach_thuc_checker.statements:
            citations.extend(statement.citations)
        return tuple(citations)


class ProposedActionType(StrEnum):
    PREPARE_DOCUMENT_REQUEST = "PREPARE_DOCUMENT_REQUEST"
    SCHEDULE_MOCK_LOS_ENTRY = "SCHEDULE_MOCK_LOS_ENTRY"
    PREPARE_HANDOFF_PACKAGE = "PREPARE_HANDOFF_PACKAGE"


class ProposedActionExecutionStatus(StrEnum):
    """Deliberately a single-member enum.  There is NO ``EXECUTED`` value:
    a proposed action can never mark itself executed, and no code path in
    this codebase ever writes one (tests/unit/credit_ops/test_port_surface.py)."""

    DRAFT = "DRAFT"


class RequiredAuthorization(BaseModel):
    """Names the gate and human role that must authorize this action.  A
    constant pair, not an LLM-chosen value: every proposed action requires
    exactly the same controlled-action gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: Literal["G4_OPS_AUTHORIZATION"] = "G4_OPS_AUTHORIZATION"
    role: Literal["OPS_OFFICER"] = "OPS_OFFICER"


class ProposedAction(BaseModel):
    """A controlled action drafted for later authorization.  Authorization
    (application/credit_ops's action-authorize API) only RECORDS authority;
    it never executes anything -- there is no executor code path anywhere in
    this codebase."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    action_type: ProposedActionType
    description_vi: str = Field(min_length=1, max_length=2000)
    related_document_request_id: UUID | None = None
    required_authorization: RequiredAuthorization = RequiredAuthorization()
    execution_status: ProposedActionExecutionStatus = ProposedActionExecutionStatus.DRAFT
    citations: tuple[MemoFindingCitation, ...] = ()


class CreditOpsProvenance(BaseModel):
    """Immutable provenance envelope recorded on every credit-ops output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    case_version: int = Field(ge=1)
    agent_role: Literal["CREDIT_OPERATIONS"] = CREDIT_OPS_AGENT_ROLE
    execution_id: UUID
    task_id: TaskId
    prompt_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    evidence_view_built_at: datetime
    created_at: datetime
    intake_handoff_id: UUID | None = None
    underwriting_assessment_id: UUID | None = None
    underwriting_execution_id: UUID | None = None
    legal_assessment_id: UUID | None = None
    legal_execution_id: UUID | None = None
    risk_review_assessment_id: UUID | None = None
    risk_review_execution_id: UUID | None = None


class CreditOpsPackage(BaseModel):
    """The Credit Operations Agent's complete package for one case version.

    Append-only once persisted.  Contains the deterministic completeness
    checklist, the consolidated provenance index, drafted (never sent)
    document requests, the draft memo, and drafted (never executable)
    proposed actions -- and NO credit decision, approval, send, or execution
    of any kind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: CreditOpsPackageId
    provenance: CreditOpsProvenance
    package_completeness: PackageCompleteness
    evidence_consolidation: EvidenceConsolidation
    document_requests: tuple[DocumentRequest, ...] = ()
    draft_memo: DraftCreditMemo
    proposed_actions: tuple[ProposedAction, ...] = ()

    @model_validator(mode="after")
    def _document_request_ids_are_unique(self) -> Self:
        ids = [request.id for request in self.document_requests]
        if len(ids) != len(set(ids)):
            raise ValueError("document_requests ids must be unique")
        return self

    @model_validator(mode="after")
    def _proposed_action_ids_are_unique_and_reference_known_requests(self) -> Self:
        ids = [action.id for action in self.proposed_actions]
        if len(ids) != len(set(ids)):
            raise ValueError("proposed_actions ids must be unique")
        known_request_ids = {request.id for request in self.document_requests}
        for action in self.proposed_actions:
            if (
                action.related_document_request_id is not None
                and action.related_document_request_id not in known_request_ids
            ):
                raise ValueError(
                    "proposed action references an unknown document request: "
                    f"{action.related_document_request_id}"
                )
        return self

    @model_validator(mode="after")
    def _memo_and_action_citations_resolve_to_provenance(self) -> Self:
        expected_assessment_id: dict[MemoSource, UUID | None] = {
            MemoSource.CREDIT_UNDERWRITING: self.provenance.underwriting_assessment_id,
            MemoSource.LEGAL_COMPLIANCE_COLLATERAL: self.provenance.legal_assessment_id,
            MemoSource.INDEPENDENT_RISK_REVIEW: self.provenance.risk_review_assessment_id,
        }

        def _check(citation: MemoFindingCitation, where: str) -> None:
            ref = citation.ref
            expected = expected_assessment_id.get(ref.source)
            if expected is None or ref.source_assessment_id != expected:
                raise ValueError(
                    f"{where} cites a source assessment not recorded in provenance: "
                    f"{ref.source}/{ref.source_assessment_id}"
                )

        for citation in self.draft_memo._iter_citations():
            _check(citation, "draft_memo statement")
        for action in self.proposed_actions:
            for citation in action.citations:
                _check(citation, f"proposed_action {action.id}")
        return self


def _assert_no_forbidden_fields(
    model: type[BaseModel], forbidden: frozenset[str], seen: set[str]
) -> None:
    name = model.__name__
    if name in seen:
        return
    seen.add(name)
    for field_name, field_info in model.model_fields.items():
        normalized = "".join(char for char in field_name.casefold() if char.isalnum())
        if normalized in forbidden:
            raise AssertionError(
                f"{name}.{field_name} would express a decision, approval, or execution"
            )
        annotation = field_info.annotation
        stack = [annotation]
        while stack:
            candidate = stack.pop()
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _assert_no_forbidden_fields(candidate, forbidden, seen)
            else:
                stack.extend(getattr(candidate, "__args__", ()))


# Import-time structural guards.  (1) Package-wide: no field anywhere in the
# credit-ops output can express a decision, approval, disbursement,
# signature, send/dispatch, or execution.  (2) Memo-only, STRICTER: the memo
# additionally can never carry an ``approval_status``-shaped field, even
# though a document request elsewhere in the SAME package legitimately does.
_assert_no_forbidden_fields(CreditOpsPackage, FORBIDDEN_CREDIT_OPS_FIELD_NAMES, set())
_assert_no_forbidden_fields(
    DraftCreditMemo,
    FORBIDDEN_CREDIT_OPS_FIELD_NAMES | _MEMO_ONLY_FORBIDDEN_FIELD_NAMES,
    set(),
)
