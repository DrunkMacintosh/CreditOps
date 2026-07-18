"""Stage-8 contract package domain: deterministic rendering + material change.

Master design section 5 giai đoạn 8 ("Đàm phán và ký kết hồ sơ tín dụng").

SPEC CONTRACT faithfully encoded here (all pure, frozen value objects; NO I/O,
NO LLM, NO clock, NO randomness anywhere in this module):

- ``render_contract_content_vi`` is a DETERMINISTIC template renderer.  It builds
  the contract document text ONLY from the human credit decision and its frozen
  ``ApprovedTerms`` -- the model never invents a clause.  Identical inputs always
  produce byte-identical output, so a re-render can never drift.  Every rendered
  document embeds the canonical synthetic-data notice
  (``domain/synthetic_notice.SYNTHETIC_NOTICE_VI``) and the fixed
  ``MOCK_CONTRACT_LABEL_VI`` label -- this prototype has NO legal effect and its
  signing records are MOCK signature evidence only.
- Redlines are VERSIONED rows, never edits: every change appends a new
  ``package_version`` row (persistence lives in the migration/adapter).  The
  ``ContractPackageState`` closed set names the lifecycle of each appended row.
- ``detect_material_change`` compares the term-snapshot hash the package was
  rendered from against the CURRENT credit-decision snapshot hash.  ANY mismatch
  is a material change: it forces state ``MATERIAL_CHANGE_DETECTED`` which BLOCKS
  all three stage-8 gates.  The CONSEQUENCE (per spec) is that the case must go
  back to stage 6 for a new ``HumanCreditDecision`` version; that loop is a
  deferred lead decision and is deliberately NOT implemented here -- this module
  only records the blocking state.
- e-sign / real contract execution is OUT OF SCOPE.  ``SignatureEvidenceKind`` is
  the closed set ``{MOCK_SIGNATURE}`` and the signing record is labelled mock
  evidence only.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.credit_decisions import APPROVAL_DECISIONS, ApprovedTerms, CreditDecisionType
from creditops.domain.ids import CaseId
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI

type ContractPackageId = UUID
type ContractRedlineId = UUID
type ContractSignatureEvidenceId = UUID

_SHA256_HEX = r"^[0-9a-f]{64}$"

#: The fixed mock-contract label the deterministic renderer embeds on every
#: document.  This prototype produces NO legally effective contract.
MOCK_CONTRACT_LABEL_VI: Final = "Hợp đồng mô phỏng — không có hiệu lực pháp lý."

#: Rendered when an approved-term field was left unspecified by the decision.
_UNSPECIFIED_VI: Final = "(không xác định)"

#: Rendered for an approval that carried no conditions.
_NO_CONDITIONS_VI: Final = "(không có điều kiện)"


class ContractPackageState(StrEnum):
    """The closed lifecycle-state set of one appended contract-package row.

    Rows are append-only and a new ``package_version`` is written per change, so
    the state names the stage of THAT version, never a mutable column:

    - ``DRAFT``                    the first deterministically rendered package;
    - ``REDLINED``                 a version produced by a legal redline;
    - ``MATERIAL_CHANGE_DETECTED`` a version fenced because its term-snapshot hash
      no longer matches the current decision snapshot -- BLOCKS all three gates
      (the case must return to stage 6, a deferred loop, not implemented here);
    - ``READY_FOR_SIGNATURE``      the finalized version the sign flow appends and
      attaches its ``MOCK_SIGNATURE`` evidence to.

    There is deliberately NO ``SIGNED`` state: "signed" is represented by the
    presence of a 1:1 ``ContractSignatureEvidence`` row on a ``READY_FOR_SIGNATURE``
    package, never by a mutable state.
    """

    DRAFT = "DRAFT"
    REDLINED = "REDLINED"
    MATERIAL_CHANGE_DETECTED = "MATERIAL_CHANGE_DETECTED"
    READY_FOR_SIGNATURE = "READY_FOR_SIGNATURE"


class SignatureEvidenceKind(StrEnum):
    """Closed set of signing-evidence kinds.

    Only ``MOCK_SIGNATURE`` exists: real e-sign / contract execution is OUT OF
    SCOPE (master design section 5 giai đoạn 8).  A signing record is mock
    evidence only.
    """

    MOCK_SIGNATURE = "MOCK_SIGNATURE"


def compute_content_hash(content_vi: str) -> str:
    """sha256 hex over the exact rendered contract content (pure; no I/O).

    Mirrors ``domain/credit_decisions.compute_terms_hash`` and
    ``domain/gap_request_batches.compute_open_gap_snapshot_hash``: the hash binds
    a package/redline row to the exact content it stored, so stored text can
    never drift from its recorded hash unnoticed.
    """

    return hashlib.sha256(content_vi.encode("utf-8")).hexdigest()


def detect_material_change(
    package_term_snapshot_hash: str, current_decision_snapshot_hash: str
) -> bool:
    """Whether the package's terms materially diverge from the CURRENT decision.

    Pure hash comparison: ``True`` (material change) iff the term-snapshot hash
    the package was rendered from is not byte-identical to the current credit
    decision's approved-term snapshot hash.  A ``True`` result forces
    ``MATERIAL_CHANGE_DETECTED`` and BLOCKS all three stage-8 gates; the case
    must go back to stage 6 for a new decision (a deferred loop -- not
    implemented here, only the blocking state is recorded).
    """

    return package_term_snapshot_hash != current_decision_snapshot_hash


class ContractDecisionView(BaseModel):
    """The decision-side inputs the deterministic renderer reads.

    A thin, frozen projection of the ``HumanCreditDecision`` fields that appear
    in the rendered contract text -- kept separate so the renderer depends only
    on what it prints, never on the full decision aggregate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_type: CreditDecisionType
    rationale_vi: str = Field(min_length=1, max_length=4000)
    conditions: tuple[str, ...] = ()


def _render_amount(value: Decimal | None) -> str:
    return _UNSPECIFIED_VI if value is None else format(value, "f")


def _render_text(value: str | None) -> str:
    return _UNSPECIFIED_VI if value is None else value


def render_contract_content_vi(decision: ContractDecisionView, terms: ApprovedTerms) -> str:
    """Render the deterministic mock contract text from the decision + terms.

    Pure and total: the SAME ``(decision, terms)`` always yields byte-identical
    output.  No clock, no randomness, no LLM, no external corpus -- only the
    fixed template below plus the decision/terms fields.  The document always
    opens and closes with ``MOCK_CONTRACT_LABEL_VI`` and embeds the canonical
    ``SYNTHETIC_NOTICE_VI``, so every rendered package is unmistakably a
    non-effective demonstration artifact.
    """

    if decision.conditions:
        conditions_block = "\n".join(
            f"- {condition}" for condition in decision.conditions
        )
    else:
        conditions_block = f"- {_NO_CONDITIONS_VI}"

    lines = [
        MOCK_CONTRACT_LABEL_VI,
        "",
        "HỢP ĐỒNG TÍN DỤNG (BẢN MÔ PHỎNG)",
        "",
        SYNTHETIC_NOTICE_VI,
        "",
        "I. QUYẾT ĐỊNH TÍN DỤNG",
        f"- Loại quyết định: {decision.decision_type.value}",
        f"- Căn cứ: {decision.rationale_vi}",
        "",
        "II. ĐIỀU KHOẢN ĐƯỢC PHÊ DUYỆT",
        f"- Số tiền: {_render_amount(terms.amount)}",
        f"- Loại tiền: {_render_text(terms.currency)}",
        f"- Thời hạn: {_render_text(terms.term)}",
        f"- Lãi suất: {_render_amount(terms.rate)}",
        "",
        "III. ĐIỀU KIỆN",
        conditions_block,
        "",
        MOCK_CONTRACT_LABEL_VI,
    ]
    return "\n".join(lines)


def assert_renderable_decision(decision_type: CreditDecisionType) -> None:
    """A contract package may be rendered ONLY from an approving decision.

    Raises ``ValueError`` for a ``DECLINED_BY_HUMAN`` / ``RETURNED_FOR_REVISION``
    / ``MORE_INFORMATION_REQUIRED`` decision -- those carry no approved terms to
    render into a contract.  The permitting decision must be an approval.
    """

    if decision_type not in APPROVAL_DECISIONS:
        raise ValueError(
            f"{decision_type.value} does not permit a contract package "
            "(no approved terms to render)"
        )


class ContractPackage(BaseModel):
    """One append-only, deterministically rendered contract-package version.

    ``content_hash`` must equal ``compute_content_hash(content_vi)`` so stored
    text can never diverge from its recorded hash.  ``term_snapshot_hash`` binds
    the package to the exact approved-term snapshot it was rendered from -- the
    input to ``detect_material_change`` at approve/sign time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ContractPackageId
    case_id: CaseId
    case_version: int = Field(ge=1)
    decision_id: UUID
    term_snapshot_hash: str = Field(pattern=_SHA256_HEX)
    content_vi: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_HEX)
    package_version: int = Field(ge=1)
    state: ContractPackageState
    created_by: UUID

    @model_validator(mode="after")
    def _hash_matches_content(self) -> Self:
        if self.content_hash != compute_content_hash(self.content_vi):
            raise ValueError("content_hash must equal the canonical sha256 of content_vi")
        return self


class ContractRedline(BaseModel):
    """One append-only versioned redline against a contract package.

    A redline is NEVER an edit: it records the human's change note plus the
    replacement content, and its persistence appends a NEW ``REDLINED``
    package_version in the same transaction (adapter).  ``changed_content_hash``
    binds the redline to the exact replacement content it proposed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ContractRedlineId
    package_id: ContractPackageId
    redline_version: int = Field(ge=1)
    change_note_vi: str = Field(min_length=1, max_length=4000)
    changed_content_vi: str = Field(min_length=1)
    changed_content_hash: str = Field(pattern=_SHA256_HEX)
    created_by: UUID

    @model_validator(mode="after")
    def _hash_matches_content(self) -> Self:
        if self.changed_content_hash != compute_content_hash(self.changed_content_vi):
            raise ValueError(
                "changed_content_hash must equal the canonical sha256 of changed_content_vi"
            )
        return self


class ContractSignatureEvidence(BaseModel):
    """One append-only MOCK signing record, 1:1 with a signable package.

    OUT OF SCOPE reminder encoded in the type: ``kind`` is the closed
    ``{MOCK_SIGNATURE}`` set, so this is never real execution -- only mock
    evidence.  ``signer_names`` must be a non-empty tuple of non-empty names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ContractSignatureEvidenceId
    package_id: ContractPackageId
    kind: SignatureEvidenceKind = SignatureEvidenceKind.MOCK_SIGNATURE
    signer_names: tuple[str, ...] = Field(min_length=1)
    evidence_note_vi: str | None = Field(default=None, max_length=4000)
    recorded_by: UUID

    @model_validator(mode="after")
    def _signer_names_are_non_empty(self) -> Self:
        if any(not name.strip() for name in self.signer_names):
            raise ValueError("every signer name must be non-empty")
        return self


__all__ = [
    "MOCK_CONTRACT_LABEL_VI",
    "ContractDecisionView",
    "ContractPackage",
    "ContractPackageId",
    "ContractPackageState",
    "ContractRedline",
    "ContractRedlineId",
    "ContractSignatureEvidence",
    "ContractSignatureEvidenceId",
    "SignatureEvidenceKind",
    "assert_renderable_decision",
    "compute_content_hash",
    "detect_material_change",
    "render_contract_content_vi",
]
