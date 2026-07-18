"""Domain schema tests for the Credit Operations package (domain/credit_ops.py).

Requirements exercised: (b) every memo statement cited -- a section omitting
citations fails validation; (d) the proposed-action enum has no EXECUTED
state; the memo cannot express a decision/approval; the synthetic
disclaimer header is mandatory and content-pinned.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from creditops.domain.credit_ops import (
    FORBIDDEN_CREDIT_OPS_FIELD_NAMES,
    SYNTHETIC_DISCLAIMER_VI,
    ChallengeStatusSection,
    ChecklistItemStatus,
    CreditOpsPackage,
    CreditOpsProvenance,
    DocumentRequest,
    DocumentRequestApprovalStatus,
    DraftCreditMemo,
    EvidenceConsolidation,
    MemoFindingCitation,
    MemoFindingRef,
    MemoSection,
    MemoSource,
    MemoStatement,
    PackageChecklistItem,
    PackageCompleteness,
    ProposedAction,
    ProposedActionExecutionStatus,
    ProposedActionType,
    ProvenanceIndexEntry,
    RequiredAuthorization,
    UpstreamArtifactKind,
)
from creditops.domain.underwriting import GapBlockingLevel

NOW = datetime(2026, 7, 18, 11, 0, tzinfo=UTC)
CASE_ID = uuid4()
UW_ID = uuid4()
LEGAL_ID = uuid4()
RR_ID = uuid4()


def _provenance(**overrides: Any) -> CreditOpsProvenance:
    base: dict[str, Any] = {
        "case_id": CASE_ID,
        "case_version": 1,
        "execution_id": uuid4(),
        "task_id": uuid4(),
        "prompt_version": "credit-ops-prompt-v1",
        "model_id": "synthetic-model",
        "endpoint_id": "synthetic-endpoint",
        "evidence_view_built_at": NOW,
        "created_at": NOW,
        "intake_handoff_id": uuid4(),
        "underwriting_assessment_id": UW_ID,
        "underwriting_execution_id": uuid4(),
        "legal_assessment_id": LEGAL_ID,
        "legal_execution_id": uuid4(),
        "risk_review_assessment_id": RR_ID,
        "risk_review_execution_id": uuid4(),
    }
    base.update(overrides)
    return CreditOpsProvenance(**base)


def _completeness() -> PackageCompleteness:
    return PackageCompleteness(
        artifacts=(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.UNDERWRITING_ASSESSMENT,
                status=ChecklistItemStatus.PRESENT,
                detail_vi="Da co (mo phong).",
                reference_id=UW_ID,
            ),
        ),
        dispositions_state_vi="G3_RISK_DISPOSITION: DA XU LY (mo phong).",
        unresolved_challenge_count=0,
        open_blocking_gap_count=0,
        all_required_present=True,
    )


def _consolidation() -> EvidenceConsolidation:
    return EvidenceConsolidation(
        entries=(
            ProvenanceIndexEntry(
                artifact=UpstreamArtifactKind.UNDERWRITING_ASSESSMENT,
                assessment_id=UW_ID,
                execution_id=uuid4(),
                citation_count=3,
            ),
        ),
        distinct_citation_count=3,
    )


def _citation(source: MemoSource = MemoSource.CREDIT_UNDERWRITING) -> MemoFindingCitation:
    assessment_id = {
        MemoSource.CREDIT_UNDERWRITING: UW_ID,
        MemoSource.LEGAL_COMPLIANCE_COLLATERAL: LEGAL_ID,
        MemoSource.INDEPENDENT_RISK_REVIEW: RR_ID,
    }[source]
    return MemoFindingCitation(
        ref=MemoFindingRef(
            source=source,
            source_assessment_id=assessment_id,
            section_path="business.findings[0]",
        )
    )


def _statement(citation: MemoFindingCitation | None = None) -> MemoStatement:
    return MemoStatement(
        statement_vi="Nhan dinh mo phong, co trich dan.",
        citations=(citation or _citation(),),
    )


def _memo() -> DraftCreditMemo:
    section = MemoSection(statements=(_statement(),))
    return DraftCreditMemo(
        tom_tat_nhu_cau=section,
        phan_tich_maker=section,
        ra_soat_phap_ly_tsbd=MemoSection(
            statements=(_statement(_citation(MemoSource.LEGAL_COMPLIANCE_COLLATERAL)),)
        ),
        thach_thuc_checker=ChallengeStatusSection(
            statements=(_statement(_citation(MemoSource.INDEPENDENT_RISK_REVIEW)),),
            disposition_status_vi="G3 da xu ly (mo phong).",
        ),
        dieu_kien_de_xuat=section,
        phu_luc_bang_chung=section,
    )


def _package(**overrides: Any) -> CreditOpsPackage:
    base: dict[str, Any] = {
        "id": uuid4(),
        "provenance": _provenance(),
        "package_completeness": _completeness(),
        "evidence_consolidation": _consolidation(),
        "document_requests": (),
        "draft_memo": _memo(),
        "proposed_actions": (),
    }
    base.update(overrides)
    return CreditOpsPackage(**base)


# -- (b) citation enforcement -------------------------------------------------


def test_memo_statement_without_citations_is_structurally_impossible() -> None:
    with pytest.raises(ValidationError):
        MemoStatement(statement_vi="khong co trich dan", citations=())


def test_memo_section_without_statements_is_structurally_impossible() -> None:
    with pytest.raises(ValidationError):
        MemoSection(statements=())


def test_memo_citation_must_resolve_to_a_provenance_assessment() -> None:
    foreign = MemoFindingCitation(
        ref=MemoFindingRef(
            source=MemoSource.CREDIT_UNDERWRITING,
            source_assessment_id=uuid4(),  # never recorded in provenance
            section_path="risks[0]",
        )
    )
    memo = _memo()
    bad_memo = memo.model_copy(
        update={"tom_tat_nhu_cau": MemoSection(statements=(_statement(foreign),))}
    )
    with pytest.raises(ValidationError, match="not recorded in provenance"):
        _package(draft_memo=bad_memo)


def test_memo_citation_fails_when_provenance_has_no_such_upstream() -> None:
    # Provenance recorded no risk-review assessment at all: a memo citing one
    # cannot exist.
    with pytest.raises(ValidationError, match="not recorded in provenance"):
        _package(
            provenance=_provenance(
                risk_review_assessment_id=None, risk_review_execution_id=None
            )
        )


# -- synthetic disclaimer header ----------------------------------------------


def test_synthetic_disclaimer_is_mandatory_and_content_pinned() -> None:
    memo = _memo()
    assert memo.synthetic_disclaimer_vi == SYNTHETIC_DISCLAIMER_VI
    with pytest.raises(ValidationError):
        DraftCreditMemo.model_validate(
            {**memo.model_dump(mode="json"), "synthetic_disclaimer_vi": "van ban khac"}
        )


# -- (d) no EXECUTED state; DRAFT-only actions --------------------------------


def test_execution_status_enum_has_no_executed_value() -> None:
    members = {member.value for member in ProposedActionExecutionStatus}
    assert members == {"DRAFT"}
    assert "EXECUTED" not in members


def test_proposed_action_defaults_to_draft_and_g4_authorization() -> None:
    action = ProposedAction(
        id=uuid4(),
        action_type=ProposedActionType.PREPARE_HANDOFF_PACKAGE,
        description_vi="Chuan bi goi ban giao (mo phong).",
    )
    assert action.execution_status is ProposedActionExecutionStatus.DRAFT
    assert action.required_authorization == RequiredAuthorization()
    assert action.required_authorization.gate == "G4_OPS_AUTHORIZATION"
    assert action.required_authorization.role == "OPS_OFFICER"


def test_proposed_action_cannot_claim_an_executed_status() -> None:
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(
            {
                "id": str(uuid4()),
                "action_type": "PREPARE_HANDOFF_PACKAGE",
                "description_vi": "x",
                "execution_status": "EXECUTED",
            }
        )


def test_required_authorization_gate_and_role_are_const_pinned() -> None:
    with pytest.raises(ValidationError):
        RequiredAuthorization.model_validate({"gate": "G1_INTAKE_COMPLETE", "role": "OPS_OFFICER"})
    with pytest.raises(ValidationError):
        RequiredAuthorization.model_validate(
            {"gate": "G4_OPS_AUTHORIZATION", "role": "INTAKE_OFFICER"}
        )


# -- document requests: G2 pattern --------------------------------------------


def test_document_request_starts_pending_approval() -> None:
    request = DocumentRequest(
        id=uuid4(),
        originating_gap_id=uuid4(),
        request_text_vi="De nghi bo sung bao cao tai chinh (mo phong).",
        blocking_level=GapBlockingLevel.BLOCKING,
    )
    assert request.approval_status is DocumentRequestApprovalStatus.PENDING_APPROVAL


def test_document_request_approval_states_are_a_closed_two_value_set() -> None:
    assert {member.value for member in DocumentRequestApprovalStatus} == {
        "PENDING_APPROVAL",
        "APPROVED",
    }


def test_proposed_action_must_reference_a_known_document_request() -> None:
    action = ProposedAction(
        id=uuid4(),
        action_type=ProposedActionType.PREPARE_DOCUMENT_REQUEST,
        description_vi="x",
        related_document_request_id=uuid4(),
    )
    with pytest.raises(ValidationError, match="unknown document request"):
        _package(proposed_actions=(action,))


# -- checklist consistency ----------------------------------------------------


def test_present_checklist_item_requires_a_reference_id() -> None:
    with pytest.raises(ValidationError):
        PackageChecklistItem(
            artifact=UpstreamArtifactKind.INTAKE_HANDOFF,
            status=ChecklistItemStatus.PRESENT,
            detail_vi="thieu tham chieu",
        )


def test_missing_checklist_item_cannot_carry_a_reference_id() -> None:
    with pytest.raises(ValidationError):
        PackageChecklistItem(
            artifact=UpstreamArtifactKind.INTAKE_HANDOFF,
            status=ChecklistItemStatus.MISSING,
            detail_vi="vang mat",
            reference_id=uuid4(),
        )


def test_all_required_present_flag_must_match_the_items() -> None:
    with pytest.raises(ValidationError, match="all_required_present"):
        PackageCompleteness(
            artifacts=(
                PackageChecklistItem(
                    artifact=UpstreamArtifactKind.INTAKE_HANDOFF,
                    status=ChecklistItemStatus.MISSING,
                    detail_vi="vang mat",
                ),
            ),
            dispositions_state_vi="x",
            unresolved_challenge_count=0,
            open_blocking_gap_count=0,
            all_required_present=True,
        )


# -- forbidden-field import-time guard ----------------------------------------


def test_no_model_in_the_package_tree_has_a_decision_capable_field() -> None:
    seen: set[str] = set()

    def _walk(model: type[BaseModel]) -> None:
        if model.__name__ in seen:
            return
        seen.add(model.__name__)
        for field_name, field_info in model.model_fields.items():
            normalized = "".join(c for c in field_name.casefold() if c.isalnum())
            assert normalized not in FORBIDDEN_CREDIT_OPS_FIELD_NAMES, (
                f"{model.__name__}.{field_name} is decision/execution-capable"
            )
            stack = [field_info.annotation]
            while stack:
                candidate = stack.pop()
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    _walk(candidate)
                else:
                    stack.extend(getattr(candidate, "__args__", ()))

    _walk(CreditOpsPackage)
    assert len(seen) > 5  # the guard actually walked the tree


def test_memo_guard_additionally_forbids_approval_status_on_the_memo() -> None:
    # The package-wide guard allows a document request's approval_status
    # (the G2 pattern view field); the memo-scoped guard must NOT -- no memo
    # model may carry an approval_status-shaped field.
    seen: set[str] = set()

    def _walk(model: type[BaseModel]) -> None:
        if model.__name__ in seen:
            return
        seen.add(model.__name__)
        for field_name, field_info in model.model_fields.items():
            normalized = "".join(c for c in field_name.casefold() if c.isalnum())
            assert normalized not in (
                FORBIDDEN_CREDIT_OPS_FIELD_NAMES | {"approvalstatus"}
            ), f"DraftCreditMemo tree: {model.__name__}.{field_name}"
            stack = [field_info.annotation]
            while stack:
                candidate = stack.pop()
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    _walk(candidate)
                else:
                    stack.extend(getattr(candidate, "__args__", ()))

    _walk(DraftCreditMemo)


def test_forbidden_names_cover_the_deliverable_vocabulary() -> None:
    required = {
        "approve",
        "approved",
        "approval",
        "decision",
        "memodecision",
        "disbursement",
        "signoff",
        "execute",
        "executed",
        "dispatch",
        "send",
    }
    assert required <= FORBIDDEN_CREDIT_OPS_FIELD_NAMES


def test_extra_fields_are_forbidden_everywhere() -> None:
    payload = _package().model_dump(mode="json")
    payload["memo_decision"] = "APPROVED"
    with pytest.raises(ValidationError):
        CreditOpsPackage.model_validate(payload)


def test_package_round_trips_through_json() -> None:
    package = _package()
    restored = CreditOpsPackage.model_validate(package.model_dump(mode="json"))
    assert restored == package
