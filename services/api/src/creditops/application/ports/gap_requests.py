"""Durable state contracts for the pre-Risk Evidence-Gap request workflow (G2).

The repository exposes only what the deterministic assembler and the
human-disposition API need: read the CURRENT open gaps for a case version,
idempotently persist an assembled ``GapRequestBatch``, load the current batch,
and append-only record / read human dispositions.  It exposes NO way to
resolve a gap, mutate a batch, or satisfy a gate directly -- gate satisfaction
is derived (``domain/gap_request_batches.derive_g2_from_batch``) and written
only through the orchestration repository.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.gap_request_batches import (
    BatchDispositionType,
    GapRequestBatch,
    ItemDisposition,
)
from creditops.domain.underwriting import GapBlockingLevel


@dataclass(frozen=True, slots=True)
class OpenGap:
    """One currently-visible (PROVISIONAL/FORMAL) evidence gap.

    Carries everything the assembler needs to draft a deterministic request
    (``missing_information_vi`` + ``suggested_evidence_vi``) and everything the
    snapshot hash reads (``gap_id`` + ``status`` + ``blocking_level``).
    """

    gap_id: UUID
    status: str
    blocking_level: GapBlockingLevel
    missing_information_vi: str
    suggested_evidence_vi: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersistedGapRequestBatch:
    """The canonical stored batch plus whether this call created it.

    ``created`` is ``False`` when an identical batch (same case, version, and
    open-gap snapshot hash) already existed and was re-selected -- idempotent
    assemble-or-get.
    """

    batch: GapRequestBatch
    created: bool


@dataclass(frozen=True, slots=True)
class GapRequestBatchDispositionRecord:
    """One append-only human disposition of one batch."""

    id: UUID
    batch_id: UUID
    disposition_type: BatchDispositionType
    item_dispositions: Mapping[UUID, ItemDisposition]
    edited_texts: Mapping[UUID, str]
    actor_id: UUID
    actor_role: str
    rationale_vi: str
    created_at: datetime


class GapRequestRepository(Protocol):
    async def load_open_gaps(
        self, case_id: UUID, case_version: int
    ) -> tuple[OpenGap, ...]: ...

    async def load_current_batch(
        self, case_id: UUID, case_version: int
    ) -> GapRequestBatch | None: ...

    async def persist_batch(self, batch: GapRequestBatch) -> PersistedGapRequestBatch: ...

    async def record_disposition(
        self,
        *,
        disposition_id: UUID,
        batch_id: UUID,
        case_id: UUID,
        case_version: int,
        disposition_type: BatchDispositionType,
        item_dispositions: Mapping[UUID, ItemDisposition],
        edited_texts: Mapping[UUID, str],
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> GapRequestBatchDispositionRecord: ...

    async def load_dispositions(
        self, batch_id: UUID
    ) -> tuple[GapRequestBatchDispositionRecord, ...]: ...
