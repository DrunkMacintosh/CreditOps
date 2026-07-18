"""Persistence contract for the concrete document-ingestion processor.

The pure stage functions in ``application/stages`` produce bounded, validated
value objects but never touch durable state.  This port is the *only* surface
through which the ``DocumentIngestionProcessor`` writes the stage outputs into
the approved evidence schema (migrations ``202607170004`` and ``202607170007``)
and advances ``document_versions.stage`` per ``domain/transitions.py``.

Design boundaries encoded here:

- The port persists **candidate** facts and retrieval passages only; nothing on
  this surface can confirm a fact, satisfy a gate, or record a credit decision.
  Extracted values are model output and stay ``candidate_facts`` until a human
  officer confirms them through a different, human-authority path.
- ``proposed_value`` / passage text originate in the customer document and are
  therefore *untrusted data*.  They are carried as opaque values and must never
  be interpreted as instructions; the adapter binds them only as SQL parameters.
- Every write is idempotent on a natural key so a redelivered task re-persists
  the identical rows without creating duplicates.
- Stage advancement is a guarded, transition-checked update fused into the same
  transaction as the rows it accompanies, so a committed row-write implies a
  committed stage advance (and vice versa) — partial processing can never leave
  the row and the stage disagreeing.

There is deliberately **no** ``persist_classification`` here: the approved
schema has no classification table, and this task may not add a migration.  The
deterministic classification is durable task-checkpoint state and is reflected
by ``document_versions.stage = CLASSIFIED`` instead.  See the processor's
``CHECKPOINT_CLASSIFIED`` handling and the final note for the fail-closed
rationale.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creditops.domain.enums import DocumentStage


class DocumentIngestionError(RuntimeError):
    """A durable document-ingestion persistence operation failed."""


class DocumentVersionNotFound(DocumentIngestionError):
    """The task's document version is not present for the given case scope."""


class DocumentStageConflict(DocumentIngestionError):
    """The document version is not in an expected stage for this transition.

    Raised when a guarded stage advance matches no row because the persisted
    stage is neither the expected ``from`` stage nor the idempotent ``to``
    stage.  The worker converts this into a bounded retry / manual review; it
    never silently skips the advance.
    """


@dataclass(frozen=True, slots=True)
class DocumentIngestionContext:
    """Immutable metadata the processor needs to drive the stage pipeline.

    Read from ``document_versions`` (identity/content columns are immutable, so
    this is a stable snapshot).  ``is_stale`` mirrors ``stale_at is not null`` so
    a superseded version is skipped rather than processed to READY.
    """

    case_id: UUID
    case_version: int
    document_version_id: UUID
    stage: DocumentStage
    original_filename: str
    declared_content_type: str
    detected_content_type: str | None
    storage_bucket: str
    storage_object_key: str
    byte_size: int
    content_sha256: str
    is_stale: bool


@dataclass(frozen=True, slots=True)
class PersistableRegion:
    """One parsed page region destined for ``public.page_regions``.

    ``page_region_id`` is a deterministic UUID derived from the document version
    and the region's normalized geometry, so re-persisting is a no-op.
    """

    page_region_id: UUID
    page_number: int
    x: float
    y: float
    width: float
    height: float
    extraction_method: str


@dataclass(frozen=True, slots=True)
class PersistableCandidate:
    """One extraction candidate destined for ``public.candidate_facts``.

    ``page_region_id`` references the already-persisted evidence region the
    value was grounded against (validated by the pure extract stage).
    ``proposed_value`` is untrusted document-derived data.
    """

    candidate_fact_id: UUID
    page_region_id: UUID
    field_key: str
    proposed_value: str | int | float | bool
    confidence: float
    extraction_method: str


@dataclass(frozen=True, slots=True)
class PersistablePassage:
    """One retrieval passage destined for ``public.retrieval_passages``.

    ``embedding`` is ``None`` only when no embedding was produced; when present
    the model/version metadata must accompany it (the table's check constraint
    requires all-or-nothing).  The processor never fabricates an embedding, so a
    ``None`` here is an explicit "no vector", never a fake zero vector.
    """

    passage_id: UUID
    page_region_id: UUID | None
    passage_text: str
    extraction_method: str
    embedding: tuple[float, ...] | None
    embedding_model: str | None
    embedding_version: str | None


class DocumentIngestionPort(Protocol):
    """Minimal durable surface the ingestion processor depends on.

    Every ``persist_*`` method fuses its idempotent inserts with the guarded
    stage advance that the completed stage implies, in a single transaction.
    ``advance_stage`` covers the row-less stages (security validation,
    classification, and the final READY handoff).
    """

    async def load_document(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
    ) -> DocumentIngestionContext | None: ...

    async def persist_parsed_regions(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        regions: Sequence[PersistableRegion],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None: ...

    async def persist_candidates(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        candidates: Sequence[PersistableCandidate],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None: ...

    async def persist_passages(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        passages: Sequence[PersistablePassage],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None: ...

    async def advance_stage(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None: ...
