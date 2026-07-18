"""Durable state contract for the officer intake evidence review surface
(master design P0 #3; spec sections 5 stage 3 and 15).

This port backs the four contract-pending endpoints the officer workspace
already calls: read one document's review payload, confirm every candidate on a
document version in one batch, and read the case-scoped confirmed evidence and
conflicts.  The confirmation write is the ONLY mutation here -- it appends
``fact_confirmations`` rows (the DB ``derive_and_protect_confirmed_fact`` trigger
derives ``confirmed_facts`` from candidate+confirmation), is idempotent per
candidate (``fact_confirmations_one_disposition`` unique key), and audits each
newly recorded confirmation.  Nothing here can satisfy a gate, resolve a
conflict/gap, complete intake, or record any credit decision; the completeness
verdict and every downstream decision stay with their own surfaces.

The reads are version-scoped projections of the Credit Case Digital Twin: the
confirmed evidence mirrors ``ConfirmedFactDto`` and the conflicts mirror the
normalized ``ConflictDto`` the frontend parser expects (camelCase is applied by
the API serialization layer, never here).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.enums import DocumentStage, FactDisposition
from creditops.domain.evidence import FactValue, PageRegion


class StaleDocumentVersionError(RuntimeError):
    """The target document version is superseded/stale (or no longer in its
    reviewable stage) between the officer's read and the confirmation write.

    The API maps this to 409 ``STALE_DOCUMENT_VERSION`` -- the write fails
    closed rather than confirm against a snapshot the officer never saw.
    """


class UnknownCandidateError(RuntimeError):
    """A confirmation references a candidate that is not part of the target
    document version.  Defense in depth behind the API's own batch check."""


@dataclass(frozen=True, slots=True)
class CandidateReview:
    """One candidate fact in a document review, with its page region and any
    confirmation already recorded against it (used for idempotency awareness;
    the review DTO itself never exposes the disposition)."""

    candidate_id: UUID
    field_key: str
    proposed_value: FactValue
    confidence: float
    source: PageRegion
    disposition: FactDisposition | None = None
    corrected_value: FactValue | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DocumentReviewView:
    """Frozen review projection for one document's current version.

    ``case_id`` lets the API enforce assignment (an unassigned/foreign document
    yields the same indistinguishable 404 as a missing one); ``document_version``
    is the ``expectedDocumentVersion`` the officer echoes back on confirm.
    """

    document_id: UUID
    case_id: UUID
    case_version: int
    document_version_id: UUID
    document_version: int
    stage: DocumentStage
    file_name: str | None
    page_count: int | None
    candidates: tuple[CandidateReview, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfirmationInput:
    """One per-candidate disposition in a confirmation batch.

    ``corrected_value`` and ``rationale`` are present iff ``disposition`` is
    ``CORRECTED`` (the domain invariant the API enforces before this reaches
    the adapter)."""

    candidate_id: UUID
    disposition: FactDisposition
    corrected_value: FactValue | None = None
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class RecordedConfirmations:
    """Result of one confirmation batch: the confirmation ids for every
    candidate (existing or freshly written) and the confirmed-fact ids derived
    for the supported dispositions.  ``created`` is ``False`` when the whole
    batch was already recorded (idempotent repeat)."""

    confirmation_ids: tuple[UUID, ...]
    confirmed_fact_ids: tuple[UUID, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class EvidenceFactView:
    """One confirmed fact projected for the case evidence list
    (``ConfirmedFactDto``)."""

    id: UUID
    case_id: UUID
    case_version: int
    candidate_id: UUID
    confirmation_id: UUID
    document_version_id: UUID
    field_key: str
    value: FactValue
    candidate_value: FactValue
    source: PageRegion
    confirmed_at: datetime
    stale: bool


@dataclass(frozen=True, slots=True)
class ConflictSourceView:
    """One side of a conflict: the confirmed value at a document version and
    its page region (``None`` when the region is unavailable)."""

    document_version_id: UUID
    value: FactValue
    source: PageRegion | None


@dataclass(frozen=True, slots=True)
class ConflictView:
    """One evidence conflict preserving every contributing source
    (``ConflictDto``; a conflict always carries >= 2 sources)."""

    id: UUID
    case_id: UUID
    case_version: int
    field_key: str
    sources: tuple[ConflictSourceView, ...]
    detected_at: datetime | None
    stale: bool


@dataclass(frozen=True, slots=True)
class EvidenceAuditEvent:
    """One append-only audit event for a human confirmation write.

    Like ``IntakeAuditEvent`` this is a HUMAN actor whose id is recorded for
    provenance (actor_type ``HUMAN:INTAKE_OFFICER``)."""

    case_id: UUID
    case_version: int
    event_type: str
    actor_id: UUID
    artifact_type: str
    artifact_id: UUID
    event_data: Mapping[str, object] = field(default_factory=dict)


class EvidenceReviewRepository(Protocol):
    async def load_document_review(
        self, document_id: UUID
    ) -> DocumentReviewView | None: ...

    async def record_confirmations(
        self,
        *,
        document_version_id: UUID,
        confirmations: tuple[ConfirmationInput, ...],
        actor_id: UUID,
        expected_document_stage: DocumentStage,
    ) -> RecordedConfirmations: ...

    async def load_case_evidence(
        self, case_id: UUID, case_version: int
    ) -> tuple[EvidenceFactView, ...]: ...

    async def load_case_conflicts(
        self, case_id: UUID, case_version: int
    ) -> tuple[ConflictView, ...]: ...
