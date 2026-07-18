"""Domain tests for the pre-Risk gap-request batch (the G2 workflow core).

Covers: snapshot-hash determinism and order-insensitivity; the disposition
validators for all four types (with wrong-shape rejections); the
``assert_disposition_matches_batch`` batch-relative rules; and the full
``derive_g2_from_batch`` decision matrix -- including that an empty batch is
never vacuously satisfied.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.application.ports.gap_requests import OpenGap
from creditops.domain.gap_request_batches import (
    BatchDispositionType,
    GapRequestBatch,
    GapRequestBatchDisposition,
    GapRequestItem,
    assert_disposition_matches_batch,
    compute_open_gap_snapshot_hash,
    derive_g2_from_batch,
)
from creditops.domain.orchestration import GateStatus
from creditops.domain.underwriting import GapBlockingLevel

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
HASH_A = "a" * 64
HASH_B = "b" * 64


def _gap(gap_id: UUID, *, status: str = "FORMAL") -> OpenGap:
    return OpenGap(
        gap_id=gap_id,
        status=status,
        blocking_level=GapBlockingLevel.CONDITIONAL,
        missing_information_vi="Thiếu báo cáo tài chính (mô phỏng).",
        suggested_evidence_vi=("Báo cáo tài chính năm gần nhất.",),
    )


def _item(gap_id: UUID | None = None) -> GapRequestItem:
    return GapRequestItem(
        id=uuid4(),
        gap_id=gap_id or uuid4(),
        request_text_vi="Đề nghị bổ sung (mô phỏng).",
        blocking_level=GapBlockingLevel.CONDITIONAL,
    )


def _batch(
    *, items: tuple[GapRequestItem, ...], case_version: int = 3, hash_: str = HASH_A
) -> GapRequestBatch:
    return GapRequestBatch(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=case_version,
        items=items,
        open_gap_snapshot_hash=hash_,
    )


# -- snapshot hash ------------------------------------------------------------


def test_snapshot_hash_is_64_hex_and_order_insensitive() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    forward = compute_open_gap_snapshot_hash([_gap(a), _gap(b), _gap(c)])
    shuffled = compute_open_gap_snapshot_hash([_gap(c), _gap(a), _gap(b)])
    assert forward == shuffled
    assert len(forward) == 64
    assert all(char in "0123456789abcdef" for char in forward)


def test_snapshot_hash_changes_when_the_open_gap_set_changes() -> None:
    a, b = uuid4(), uuid4()
    base = compute_open_gap_snapshot_hash([_gap(a), _gap(b)])
    # A resolved/removed gap changes the set.
    fewer = compute_open_gap_snapshot_hash([_gap(a)])
    # A re-classified status changes the triple.
    reclassified = compute_open_gap_snapshot_hash([_gap(a), _gap(b, status="PROVISIONAL")])
    assert base != fewer
    assert base != reclassified


def test_empty_open_gap_set_hashes_deterministically() -> None:
    assert compute_open_gap_snapshot_hash([]) == compute_open_gap_snapshot_hash(())


# -- disposition validators (internal shape) ----------------------------------


def _disposition(
    *,
    batch_id: UUID,
    dtype: BatchDispositionType,
    item_dispositions: dict[UUID, str] | None = None,
    edited_texts: dict[UUID, str] | None = None,
) -> GapRequestBatchDisposition:
    return GapRequestBatchDisposition(
        id=uuid4(),
        batch_id=batch_id,
        disposition_type=dtype,
        item_dispositions=item_dispositions or {},
        edited_texts=edited_texts or {},
        actor_id=uuid4(),
        actor_role="INTAKE_OFFICER",
        rationale_vi="Lý do (mô phỏng).",
    )


def test_no_outbound_requests_rejects_any_item_detail() -> None:
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.NO_OUTBOUND_REQUESTS,
            item_dispositions={uuid4(): "APPROVED"},
        )


def test_rejected_rejects_any_item_detail() -> None:
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.REJECTED,
            item_dispositions={uuid4(): "REMOVED"},
        )


def test_approved_all_rejects_non_approved_items_and_edits() -> None:
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.APPROVED_ALL,
            item_dispositions={uuid4(): "REMOVED"},
        )
    item_id = uuid4()
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.APPROVED_ALL,
            item_dispositions={item_id: "APPROVED"},
            edited_texts={item_id: "sửa"},
        )


def test_approved_with_changes_requires_item_dispositions() -> None:
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
            item_dispositions={},
        )


def test_edited_items_require_exactly_matching_edited_texts() -> None:
    edited_id = uuid4()
    # EDITED item without replacement text is invalid.
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
            item_dispositions={edited_id: "EDITED"},
        )
    # edited text for a non-EDITED item is invalid.
    other = uuid4()
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
            item_dispositions={other: "APPROVED"},
            edited_texts={other: "sửa"},
        )


def test_unknown_item_disposition_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _disposition(
            batch_id=uuid4(),
            dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
            item_dispositions={uuid4(): "MAYBE"},
        )


# -- assert_disposition_matches_batch (batch-relative) ------------------------


def test_no_outbound_requires_empty_batch() -> None:
    empty = _batch(items=())
    ok = _disposition(batch_id=empty.id, dtype=BatchDispositionType.NO_OUTBOUND_REQUESTS)
    assert_disposition_matches_batch(batch=empty, disposition=ok)  # does not raise

    non_empty = _batch(items=(_item(),))
    bad = _disposition(
        batch_id=non_empty.id, dtype=BatchDispositionType.NO_OUTBOUND_REQUESTS
    )
    with pytest.raises(ValueError):
        assert_disposition_matches_batch(batch=non_empty, disposition=bad)


def test_approved_all_requires_non_empty_batch() -> None:
    empty = _batch(items=())
    bad = _disposition(batch_id=empty.id, dtype=BatchDispositionType.APPROVED_ALL)
    with pytest.raises(ValueError):
        assert_disposition_matches_batch(batch=empty, disposition=bad)


def test_approved_with_changes_must_cover_every_item_exactly() -> None:
    item_a, item_b = _item(), _item()
    batch = _batch(items=(item_a, item_b))
    # Missing item_b -> invalid.
    partial = _disposition(
        batch_id=batch.id,
        dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
        item_dispositions={item_a.id: "APPROVED"},
    )
    with pytest.raises(ValueError):
        assert_disposition_matches_batch(batch=batch, disposition=partial)
    # Covering both -> valid.
    full = _disposition(
        batch_id=batch.id,
        dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
        item_dispositions={item_a.id: "APPROVED", item_b.id: "REMOVED"},
    )
    assert_disposition_matches_batch(batch=batch, disposition=full)


def test_disposition_for_a_different_batch_is_rejected() -> None:
    batch = _batch(items=(_item(),))
    foreign = _disposition(batch_id=uuid4(), dtype=BatchDispositionType.APPROVED_ALL)
    with pytest.raises(ValueError):
        assert_disposition_matches_batch(batch=batch, disposition=foreign)


def test_unknown_item_id_is_rejected_against_the_batch() -> None:
    batch = _batch(items=(_item(),))
    stray = _disposition(
        batch_id=batch.id,
        dtype=BatchDispositionType.APPROVED_WITH_CHANGES,
        item_dispositions={uuid4(): "APPROVED"},
    )
    with pytest.raises(ValueError):
        assert_disposition_matches_batch(batch=batch, disposition=stray)


# -- derive_g2_from_batch matrix ----------------------------------------------


def test_derive_satisfied_when_bound_current_and_approved() -> None:
    batch = _batch(items=(_item(),))
    disposition = _disposition(batch_id=batch.id, dtype=BatchDispositionType.APPROVED_ALL)
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=disposition,
            current_case_version=3,
            current_open_gap_hash=HASH_A,
        )
        is GateStatus.SATISFIED
    )


def test_derive_rejected_is_open() -> None:
    batch = _batch(items=(_item(),))
    disposition = _disposition(batch_id=batch.id, dtype=BatchDispositionType.REJECTED)
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=disposition,
            current_case_version=3,
            current_open_gap_hash=HASH_A,
        )
        is GateStatus.OPEN
    )


def test_derive_stale_hash_is_open() -> None:
    batch = _batch(items=(_item(),))
    disposition = _disposition(batch_id=batch.id, dtype=BatchDispositionType.APPROVED_ALL)
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=disposition,
            current_case_version=3,
            current_open_gap_hash=HASH_B,  # open gaps changed
        )
        is GateStatus.OPEN
    )


def test_derive_stale_case_version_is_open() -> None:
    batch = _batch(items=(_item(),), case_version=3)
    disposition = _disposition(batch_id=batch.id, dtype=BatchDispositionType.APPROVED_ALL)
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=disposition,
            current_case_version=4,  # case advanced
            current_open_gap_hash=HASH_A,
        )
        is GateStatus.OPEN
    )


def test_derive_empty_batch_with_no_outbound_requests_is_satisfied() -> None:
    batch = _batch(items=())
    disposition = _disposition(
        batch_id=batch.id, dtype=BatchDispositionType.NO_OUTBOUND_REQUESTS
    )
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=disposition,
            current_case_version=3,
            current_open_gap_hash=HASH_A,
        )
        is GateStatus.SATISFIED
    )


def test_derive_empty_batch_without_disposition_is_open_no_vacuous_satisfaction() -> None:
    batch = _batch(items=())
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=None,
            current_case_version=3,
            current_open_gap_hash=HASH_A,
        )
        is GateStatus.OPEN
    )


def test_derive_foreign_disposition_is_open() -> None:
    batch = _batch(items=(_item(),))
    foreign = _disposition(batch_id=uuid4(), dtype=BatchDispositionType.APPROVED_ALL)
    assert (
        derive_g2_from_batch(
            batch=batch,
            disposition=foreign,
            current_case_version=3,
            current_open_gap_hash=HASH_A,
        )
        is GateStatus.OPEN
    )
