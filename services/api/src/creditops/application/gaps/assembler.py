"""Deterministic gap-request-batch assembler (pure, no LLM).

Mirrors ``application/credit_ops/analysis.py``'s deterministic-first
discipline: one drafted request per CURRENT open gap, with request text built
only from the gap's own missing-information and suggested-evidence fields, plus
the order-insensitive snapshot hash that binds the batch to that exact open-gap
set.  Given the same open gaps and the same ``id_factory``, the assembled batch
is byte-for-byte identical.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from uuid import UUID, uuid4

from creditops.application.ports.gap_requests import OpenGap
from creditops.domain.gap_request_batches import (
    GapRequestBatch,
    GapRequestItem,
    compute_open_gap_snapshot_hash,
)
from creditops.domain.ids import CaseId


def _request_text(gap: OpenGap) -> str:
    text = f"Đề nghị khách hàng/đơn vị liên quan bổ sung: {gap.missing_information_vi}"
    if gap.suggested_evidence_vi:
        joined = "; ".join(gap.suggested_evidence_vi)
        text = f"{text} Tài liệu gợi ý: {joined}."
    return text


def assemble_gap_request_batch(
    open_gaps: Iterable[OpenGap],
    *,
    case_id: CaseId,
    case_version: int,
    id_factory: Callable[[], UUID] = uuid4,
) -> GapRequestBatch:
    """Assemble one drafted request per open gap plus the snapshot hash.

    Gaps are ordered by ``gap_id`` so item construction is deterministic; the
    snapshot hash is computed over the full unordered set (order-insensitive by
    construction).  The batch may be EMPTY when there are no open gaps -- that
    is a valid batch that still requires an explicit human
    ``NO_OUTBOUND_REQUESTS`` disposition to satisfy G2.
    """

    gaps = tuple(open_gaps)
    ordered = sorted(gaps, key=lambda gap: str(gap.gap_id))
    items = tuple(
        GapRequestItem(
            id=id_factory(),
            gap_id=gap.gap_id,
            request_text_vi=_request_text(gap),
            blocking_level=gap.blocking_level,
        )
        for gap in ordered
    )
    return GapRequestBatch(
        id=id_factory(),
        case_id=case_id,
        case_version=case_version,
        items=items,
        open_gap_snapshot_hash=compute_open_gap_snapshot_hash(gaps),
    )


__all__ = ["assemble_gap_request_batch"]
