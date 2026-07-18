"""Durable-state contract for the stage-8 contract package workflow.

Master design section 5 giai đoạn 8.  The port exposes only what the human-only
contract API needs and NOTHING an agent could use to invent a clause, satisfy a
gate, or drive orchestration:

- a read of the current APPROVED_* credit decision + its frozen approved-term
  snapshot (``load_permitting_decision``) so the API can render deterministically
  and re-run material-change detection;
- an idempotent first-draft ``create_package`` (409-able at the API when no
  permitting decision/snapshot exists);
- ``add_redline`` -- an append-only versioned redline that writes the redline row
  AND a new ``REDLINED`` package version in ONE transaction (never an edit);
- ``mark_material_change`` -- appends a ``MATERIAL_CHANGE_DETECTED`` version that
  blocks all three gates (the return-to-stage-6 loop is a deferred decision, not
  implemented here -- only the blocking state is recorded);
- ``record_signature_evidence`` -- the sign flow's atomic append of the
  ``READY_FOR_SIGNATURE`` version plus its 1:1 ``MOCK_SIGNATURE`` evidence
  (mock evidence only; real execution is OUT OF SCOPE);
- read models for the participant read surface.

Every write also appends an audit row (adapter).  The gate writes themselves are
NOT here: they go through the orchestration repository from the API, exactly as
``api/financing.py`` / ``api/credit_ops.py`` keep gate authority out of the
feature port.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class NoContractPackageError(RuntimeError):
    """No contract package exists yet for the case's current version.

    Raised by ``add_redline`` / ``mark_material_change`` /
    ``record_signature_evidence`` when there is no package to act on.  The API
    maps it to a fail-closed 409.
    """


class ContractPackageAlreadySignedError(RuntimeError):
    """The current package already carries MOCK signature evidence.

    Signing is once-only: a second sign attempt against an already-signed
    (``READY_FOR_SIGNATURE`` + evidence) package is a fail-closed 409 at the API.
    """


class MaterialChangeBlockedError(RuntimeError):
    """The current package is fenced in ``MATERIAL_CHANGE_DETECTED``.

    Raised defensively when a write would proceed on a package whose terms no
    longer match the current decision; the API maps it to a 409 and records the
    consequence (the case must return to stage 6 for a new decision).
    """


@dataclass(frozen=True, slots=True)
class PermittingDecisionSnapshot:
    """The current APPROVED_* credit decision + its frozen approved terms.

    Only an approval decision that froze an ``ApprovedTermSnapshot`` for the
    case's current version is a permitting decision; anything else resolves to
    ``None`` at ``load_permitting_decision``.  ``terms`` is the snapshot's terms
    JSON object and ``snapshot_hash`` is the input to material-change detection.
    """

    decision_id: UUID
    case_id: UUID
    case_version: int
    decision_type: str
    rationale_vi: str
    conditions: tuple[str, ...]
    terms: Mapping[str, object]
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class RecordedContractPackage:
    """Durable read model for one persisted contract-package version."""

    id: UUID
    case_id: UUID
    case_version: int
    decision_id: UUID
    term_snapshot_hash: str
    content_vi: str
    content_hash: str
    package_version: int
    state: str
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CreatedContractPackage:
    """``create_package`` result.

    ``created`` is ``False`` when a package already existed for the case version
    and the current one was returned instead of writing a second draft -- the
    idempotent first-draft path.
    """

    package: RecordedContractPackage
    created: bool


@dataclass(frozen=True, slots=True)
class RecordedContractRedline:
    """Durable read model for one persisted versioned redline."""

    id: UUID
    package_id: UUID
    redline_version: int
    change_note_vi: str
    changed_content_vi: str
    changed_content_hash: str
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AddedRedline:
    """``add_redline`` result: the redline row plus the new REDLINED version it
    appended in the same transaction."""

    redline: RecordedContractRedline
    package: RecordedContractPackage


@dataclass(frozen=True, slots=True)
class RecordedSignatureEvidence:
    """Durable read model for one persisted MOCK signature-evidence row."""

    id: UUID
    package_id: UUID
    kind: str
    signer_names: tuple[str, ...]
    evidence_note_vi: str | None
    recorded_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SignedContractPackage:
    """``record_signature_evidence`` result: the appended READY_FOR_SIGNATURE
    version plus its 1:1 MOCK signature evidence."""

    package: RecordedContractPackage
    evidence: RecordedSignatureEvidence


@dataclass(frozen=True, slots=True)
class ContractPackageView:
    """Participant read model: the current package plus its full versioned
    redline history and its signature evidence (if signed)."""

    package: RecordedContractPackage
    redlines: tuple[RecordedContractRedline, ...]
    signature_evidence: RecordedSignatureEvidence | None


class ContractPackageRepository(Protocol):
    """The stage-8 contract package's full durable-state surface."""

    async def load_permitting_decision(
        self, case_id: UUID, case_version: int
    ) -> PermittingDecisionSnapshot | None: ...

    async def create_package(
        self,
        *,
        case_id: UUID,
        case_version: int,
        decision_id: UUID,
        term_snapshot_hash: str,
        content_vi: str,
        content_hash: str,
        actor_id: UUID,
    ) -> CreatedContractPackage: ...

    async def load_current_package(
        self, case_id: UUID, case_version: int
    ) -> RecordedContractPackage | None: ...

    async def load_package_view(
        self, case_id: UUID, case_version: int
    ) -> ContractPackageView | None: ...

    async def add_redline(
        self,
        *,
        case_id: UUID,
        case_version: int,
        change_note_vi: str,
        changed_content_vi: str,
        changed_content_hash: str,
        actor_id: UUID,
    ) -> AddedRedline: ...

    async def mark_material_change(
        self, *, case_id: UUID, case_version: int, actor_id: UUID
    ) -> RecordedContractPackage: ...

    async def record_signature_evidence(
        self,
        *,
        case_id: UUID,
        case_version: int,
        signer_names: tuple[str, ...],
        evidence_note_vi: str | None,
        actor_id: UUID,
    ) -> SignedContractPackage: ...
