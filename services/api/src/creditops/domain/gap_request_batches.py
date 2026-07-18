"""Pre-Risk Evidence-Gap request batch: the G2 workflow's typed core.

This module is the single source of truth for ``G2_GAP_REQUEST_APPROVAL``
(the ``HG_OUTBOUND_REQUEST_APPROVED`` capability, master design section 9).
It sits BEFORE Independent Risk Review, entirely inside the shared Evidence
Gap workflow, and has nothing to do with the Credit Operations package: the
old ``credit_ops`` package-derived G2 path is gone.

The flow is deterministic-then-human:

1. After the specialist assessments, a pure assembler
   (``application/gaps/assembler.py``) snapshots every CURRENT open evidence
   gap, hashes that snapshot (``compute_open_gap_snapshot_hash``), and builds
   one drafted ``GapRequestItem`` per open gap into a versioned
   ``GapRequestBatch``.  The batch may be EMPTY (no open gaps).
2. An authorized human records exactly one ``GapRequestBatchDisposition`` --
   ``APPROVED_ALL``, ``APPROVED_WITH_CHANGES``, ``REJECTED`` or
   ``NO_OUTBOUND_REQUESTS`` -- against that batch.  A zero-item batch is NOT
   silently satisfied: it still requires an explicit ``NO_OUTBOUND_REQUESTS``.
3. ``derive_g2_from_batch`` decides whether the gate MAY become SATISFIED,
   binding the batch and disposition to the CURRENT case version and the
   CURRENT open-gap snapshot hash.  Any drift -- a new/resolved gap changing
   the hash, or a bumped case version -- makes the old batch stale and
   re-opens the gate for the new version.  A ``REJECTED`` disposition never
   satisfies.

No confidence, no LLM, no vacuous satisfaction: every SATISFIED path requires
a concrete human disposition bound to a still-current batch.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId, EvidenceGapId
from creditops.domain.orchestration import GateStatus
from creditops.domain.underwriting import GapBlockingLevel as GapBlockingLevel

type GapRequestBatchId = UUID
type GapRequestItemId = UUID
type GapRequestBatchDispositionId = UUID

#: The G2 gate's underlying human capability (master design section 9): the
#: authority to approve pre-Risk outbound evidence/document requests.
HG_OUTBOUND_REQUEST_APPROVED: Literal["HG_OUTBOUND_REQUEST_APPROVED"] = (
    "HG_OUTBOUND_REQUEST_APPROVED"
)

_SHA256_HEX = r"^[0-9a-f]{64}$"

#: The three per-item human decisions inside an ``APPROVED_WITH_CHANGES``
#: disposition.  ``EDITED`` additionally requires replacement text.
ItemDisposition = Literal["APPROVED", "REMOVED", "EDITED"]


class OpenGapSnapshotView(Protocol):
    """The minimal shape the snapshot hash reads from one open evidence gap.

    Any object exposing these three attributes (e.g. the port's ``OpenGap``
    read model) can be hashed; the hash intentionally ignores request text and
    ids-of-convenience so it is stable across re-assembly and depends ONLY on
    which gaps are open and how they are classified.
    """

    @property
    def gap_id(self) -> UUID: ...

    @property
    def status(self) -> str: ...

    @property
    def blocking_level(self) -> GapBlockingLevel: ...


def compute_open_gap_snapshot_hash(gaps: Iterable[OpenGapSnapshotView]) -> str:
    """Order-insensitive sha256 hex over the sorted open-gap triples.

    Canonical JSON over the SORTED list of ``(gap_id, status, blocking_level)``
    triples, so the same set of open gaps always hashes identically regardless
    of iteration order, and any added/removed/re-classified gap changes the
    hash.  Pure; no I/O.
    """

    triples = sorted(
        (str(gap.gap_id), str(gap.status), str(gap.blocking_level)) for gap in gaps
    )
    canonical = json.dumps(
        triples, separators=(",", ":"), ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GapRequestItem(BaseModel):
    """One drafted outbound request, one per open evidence gap.

    ``request_text_vi`` is assembled deterministically from the gap's missing
    information (and suggested evidence) -- never authored by an LLM.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: GapRequestItemId
    gap_id: EvidenceGapId
    request_text_vi: str = Field(min_length=1, max_length=2000)
    blocking_level: GapBlockingLevel


class GapRequestBatch(BaseModel):
    """A versioned snapshot of every current open gap plus its drafted requests.

    ``items`` MAY be empty (no open gaps).  ``open_gap_snapshot_hash`` binds
    the batch to the exact open-gap set it was assembled from; a mismatch with
    the current snapshot makes the batch stale.  ``case_version`` binds it to
    the exact case version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: GapRequestBatchId
    case_id: CaseId
    case_version: int = Field(ge=1)
    items: tuple[GapRequestItem, ...] = ()
    open_gap_snapshot_hash: str = Field(pattern=_SHA256_HEX)

    @model_validator(mode="after")
    def _item_and_gap_ids_are_unique(self) -> Self:
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("gap request item ids must be unique within a batch")
        gap_ids = [item.gap_id for item in self.items]
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("a batch may hold at most one request per gap")
        return self

    @property
    def item_ids(self) -> frozenset[UUID]:
        return frozenset(item.id for item in self.items)


class BatchDispositionType(StrEnum):
    """The closed set of human decisions on one gap-request batch.

    ``NO_OUTBOUND_REQUESTS`` is the explicit "there was nothing to send"
    decision required for an empty batch -- the spec forbids silent
    satisfaction.
    """

    APPROVED_ALL = "APPROVED_ALL"
    APPROVED_WITH_CHANGES = "APPROVED_WITH_CHANGES"
    REJECTED = "REJECTED"
    NO_OUTBOUND_REQUESTS = "NO_OUTBOUND_REQUESTS"


#: Only these disposition types authorize the gate to become SATISFIED.
#: ``REJECTED`` never does -- "đã disposition" is not "được tiếp tục".
_SATISFYING_DISPOSITIONS: frozenset[BatchDispositionType] = frozenset(
    {
        BatchDispositionType.APPROVED_ALL,
        BatchDispositionType.APPROVED_WITH_CHANGES,
        BatchDispositionType.NO_OUTBOUND_REQUESTS,
    }
)


class GapRequestBatchDisposition(BaseModel):
    """One human disposition of one gap-request batch.

    Internal-shape validators (below) enforce everything checkable WITHOUT the
    batch; ``assert_disposition_matches_batch`` enforces the batch-relative
    rules (empty-vs-non-empty batch, exact item coverage) where the batch is
    available.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: GapRequestBatchDispositionId
    batch_id: GapRequestBatchId
    disposition_type: BatchDispositionType
    item_dispositions: Mapping[UUID, ItemDisposition] = Field(default_factory=dict)
    edited_texts: Mapping[UUID, str] = Field(default_factory=dict)
    actor_id: UUID
    actor_role: str = Field(min_length=1, max_length=100)
    rationale_vi: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _shape_matches_disposition_type(self) -> Self:
        dtype = self.disposition_type
        has_items = bool(self.item_dispositions)
        if dtype in (
            BatchDispositionType.REJECTED,
            BatchDispositionType.NO_OUTBOUND_REQUESTS,
        ):
            if has_items or self.edited_texts:
                raise ValueError(
                    f"{dtype.value} takes no per-item dispositions or edited texts"
                )
            return self
        if dtype is BatchDispositionType.APPROVED_ALL:
            if self.edited_texts:
                raise ValueError("APPROVED_ALL cannot carry edited texts")
            if any(value != "APPROVED" for value in self.item_dispositions.values()):
                raise ValueError("APPROVED_ALL implies every listed item is APPROVED")
            return self
        # APPROVED_WITH_CHANGES
        if not has_items:
            raise ValueError(
                "APPROVED_WITH_CHANGES requires an explicit disposition for every item"
            )
        edited_ids = {
            item_id
            for item_id, value in self.item_dispositions.items()
            if value == "EDITED"
        }
        if set(self.edited_texts) != edited_ids:
            raise ValueError(
                "edited_texts must contain replacement text for exactly the EDITED items"
            )
        if any(not text.strip() for text in self.edited_texts.values()):
            raise ValueError("an EDITED item requires non-empty replacement text")
        return self


def assert_disposition_matches_batch(
    *, batch: GapRequestBatch, disposition: GapRequestBatchDisposition
) -> None:
    """Enforce the batch-relative disposition rules; raise ``ValueError`` if wrong.

    - the disposition must reference this batch;
    - ``NO_OUTBOUND_REQUESTS`` is valid ONLY for an empty batch;
    - ``APPROVED_ALL`` is valid ONLY for a non-empty batch;
    - ``APPROVED_WITH_CHANGES`` must cover EVERY item id exactly (no missing,
      no unknown);
    - any per-item disposition / edited text must reference a real item id.

    ``REJECTED`` is valid for any batch and needs no per-item detail.
    """

    if disposition.batch_id != batch.id:
        raise ValueError("disposition does not reference this batch")
    known = batch.item_ids
    unknown = set(disposition.item_dispositions) - known
    if unknown:
        raise ValueError(f"disposition references unknown item ids: {sorted(map(str, unknown))}")
    dtype = disposition.disposition_type
    if dtype is BatchDispositionType.NO_OUTBOUND_REQUESTS:
        if batch.items:
            raise ValueError(
                "NO_OUTBOUND_REQUESTS is valid only for a batch with no drafted requests"
            )
    elif dtype is BatchDispositionType.APPROVED_ALL:
        if not batch.items:
            raise ValueError("APPROVED_ALL is valid only for a batch with drafted requests")
    elif dtype is BatchDispositionType.APPROVED_WITH_CHANGES:
        if set(disposition.item_dispositions) != set(known):
            raise ValueError(
                "APPROVED_WITH_CHANGES must dispose every item in the batch exactly once"
            )


def derive_g2_from_batch(
    *,
    batch: GapRequestBatch,
    disposition: GapRequestBatchDisposition | None,
    current_case_version: int,
    current_open_gap_hash: str,
) -> GateStatus:
    """Whether ``G2_GAP_REQUEST_APPROVAL`` MAY derive SATISFIED right now.

    SATISFIED requires ALL of:

    - a human disposition exists (``disposition is not None``) -- an
      un-dispositioned batch, even an empty one, never satisfies (no vacuous
      satisfaction);
    - the batch binds the CURRENT case version
      (``batch.case_version == current_case_version``);
    - the batch's snapshot hash still matches the CURRENT open-gap snapshot
      (``batch.open_gap_snapshot_hash == current_open_gap_hash``) -- any
      gap/evidence drift makes the batch stale;
    - the disposition is for THIS batch (``disposition.batch_id == batch.id``);
    - the disposition type authorizes continuation
      (``APPROVED_ALL`` / ``APPROVED_WITH_CHANGES`` / ``NO_OUTBOUND_REQUESTS``).

    Everything else -- ``REJECTED``, a stale hash, a stale case version, a
    foreign or absent disposition -- returns OPEN, fail closed.
    """

    if disposition is None:
        return GateStatus.OPEN
    if batch.case_version != current_case_version:
        return GateStatus.OPEN
    if batch.open_gap_snapshot_hash != current_open_gap_hash:
        return GateStatus.OPEN
    if disposition.batch_id != batch.id:
        return GateStatus.OPEN
    if disposition.disposition_type in _SATISFYING_DISPOSITIONS:
        return GateStatus.SATISFIED
    return GateStatus.OPEN


__all__ = [
    "HG_OUTBOUND_REQUEST_APPROVED",
    "BatchDispositionType",
    "GapBlockingLevel",
    "GapRequestBatch",
    "GapRequestBatchDisposition",
    "GapRequestItem",
    "ItemDisposition",
    "OpenGapSnapshotView",
    "assert_disposition_matches_batch",
    "compute_open_gap_snapshot_hash",
    "derive_g2_from_batch",
]
