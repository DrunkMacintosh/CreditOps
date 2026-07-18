"""Domain tests for the stage-9 security-perfection ledger.

Covers: the closed ``PerfectionStatus`` transition map; the ``COMPLETED``
evidence/completion invariant on ``SecurityPerfectionItem``; and the
``derive_perfection_confirmable`` / ``derive_perfection_blockers`` derivations,
including the no-vacuous-path rules (zero interests, an interest with zero
items, and ``EXPIRED`` are all NOT confirmable).  All identifiers are synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.security_interests import (
    ALLOWED_ITEM_TRANSITIONS,
    TERMINAL_SATISFIED_STATUSES,
    PerfectionStatus,
    SecurityAssetKind,
    SecurityInterest,
    SecurityInterestWithItems,
    SecurityPerfectionItem,
    derive_perfection_blockers,
    derive_perfection_confirmable,
    is_allowed_item_transition,
)

CASE_ID = UUID("10000000-0000-0000-0000-0000000000f1")
OWNER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _interest(interest_id: UUID | None = None) -> SecurityInterest:
    return SecurityInterest(
        id=interest_id or uuid4(),
        case_id=CASE_ID,
        case_version=1,
        asset_description_vi="Quyền sử dụng đất tại Vĩnh Phúc (mô phỏng).",
        asset_kind=SecurityAssetKind.REAL_ESTATE,
        owner_name_vi="Công ty TNHH Nông Sản Sạch Vĩnh Phúc Demo",
        valuation_reference="valuation-adapter://demo/asset-1",
        created_by=OWNER,
    )


def _item(
    *,
    interest_id: UUID,
    status: PerfectionStatus = PerfectionStatus.PENDING,
    item_id: UUID | None = None,
) -> SecurityPerfectionItem:
    completed = status is PerfectionStatus.COMPLETED
    return SecurityPerfectionItem(
        id=item_id or uuid4(),
        interest_id=interest_id,
        requirement_vi="Đăng ký biện pháp bảo đảm (mô phỏng).",
        status=status,
        evidence_refs=("storage://demo/receipt-1",) if completed else (),
        completed_by=OWNER if completed else None,
        completed_at=NOW if completed else None,
    )


# -- transition map -----------------------------------------------------------


def test_allowed_transition_map_is_the_closed_graph() -> None:
    assert ALLOWED_ITEM_TRANSITIONS[PerfectionStatus.PENDING] == frozenset(
        {PerfectionStatus.EVIDENCE_ATTACHED, PerfectionStatus.NOT_REQUIRED_BY_HUMAN}
    )
    assert ALLOWED_ITEM_TRANSITIONS[PerfectionStatus.EVIDENCE_ATTACHED] == frozenset(
        {PerfectionStatus.COMPLETED}
    )
    assert ALLOWED_ITEM_TRANSITIONS[PerfectionStatus.COMPLETED] == frozenset(
        {PerfectionStatus.EXPIRED}
    )
    assert ALLOWED_ITEM_TRANSITIONS[PerfectionStatus.NOT_REQUIRED_BY_HUMAN] == frozenset()
    assert ALLOWED_ITEM_TRANSITIONS[PerfectionStatus.EXPIRED] == frozenset()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PerfectionStatus.PENDING, PerfectionStatus.EVIDENCE_ATTACHED),
        (PerfectionStatus.EVIDENCE_ATTACHED, PerfectionStatus.COMPLETED),
        (PerfectionStatus.PENDING, PerfectionStatus.NOT_REQUIRED_BY_HUMAN),
        (PerfectionStatus.COMPLETED, PerfectionStatus.EXPIRED),
    ],
)
def test_allowed_transitions(
    current: PerfectionStatus, target: PerfectionStatus
) -> None:
    assert is_allowed_item_transition(current, target) is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PerfectionStatus.PENDING, PerfectionStatus.COMPLETED),
        (PerfectionStatus.PENDING, PerfectionStatus.EXPIRED),
        (PerfectionStatus.EVIDENCE_ATTACHED, PerfectionStatus.NOT_REQUIRED_BY_HUMAN),
        (PerfectionStatus.COMPLETED, PerfectionStatus.PENDING),
        (PerfectionStatus.NOT_REQUIRED_BY_HUMAN, PerfectionStatus.COMPLETED),
        (PerfectionStatus.EXPIRED, PerfectionStatus.COMPLETED),
    ],
)
def test_forbidden_transitions(
    current: PerfectionStatus, target: PerfectionStatus
) -> None:
    assert is_allowed_item_transition(current, target) is False


def test_terminal_satisfied_excludes_expired() -> None:
    assert PerfectionStatus.COMPLETED in TERMINAL_SATISFIED_STATUSES
    assert PerfectionStatus.NOT_REQUIRED_BY_HUMAN in TERMINAL_SATISFIED_STATUSES
    assert PerfectionStatus.EXPIRED not in TERMINAL_SATISFIED_STATUSES
    assert PerfectionStatus.PENDING not in TERMINAL_SATISFIED_STATUSES


# -- COMPLETED invariant ------------------------------------------------------


def test_completed_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        SecurityPerfectionItem(
            id=uuid4(),
            interest_id=uuid4(),
            requirement_vi="Thiếu chứng cứ.",
            status=PerfectionStatus.COMPLETED,
            evidence_refs=(),
            completed_by=OWNER,
            completed_at=NOW,
        )


def test_completed_requires_completed_by_and_at() -> None:
    with pytest.raises(ValidationError):
        SecurityPerfectionItem(
            id=uuid4(),
            interest_id=uuid4(),
            requirement_vi="Thiếu người/thời điểm hoàn tất.",
            status=PerfectionStatus.COMPLETED,
            evidence_refs=("storage://demo/receipt",),
        )


def test_completed_with_evidence_and_completion_is_valid() -> None:
    item = _item(interest_id=uuid4(), status=PerfectionStatus.COMPLETED)
    assert item.status is PerfectionStatus.COMPLETED
    assert item.evidence_refs and item.completed_by is not None


# -- confirmable derivation (no vacuous paths) --------------------------------


def test_zero_interests_is_not_confirmable() -> None:
    assert derive_perfection_confirmable([]) is False
    blockers = derive_perfection_blockers([])
    assert blockers.has_interests is False
    assert blockers.confirmable is False


def test_interest_with_zero_items_is_not_confirmable() -> None:
    interest = _interest()
    ledger = [SecurityInterestWithItems(interest=interest, items=())]
    assert derive_perfection_confirmable(ledger) is False
    blockers = derive_perfection_blockers(ledger)
    assert blockers.interests_without_items == (interest.id,)
    assert blockers.blocking_item_ids == ()


def test_all_terminal_satisfied_is_confirmable() -> None:
    interest_a = _interest()
    interest_b = _interest()
    ledger = [
        SecurityInterestWithItems(
            interest=interest_a,
            items=(
                _item(interest_id=interest_a.id, status=PerfectionStatus.COMPLETED),
                _item(
                    interest_id=interest_a.id,
                    status=PerfectionStatus.NOT_REQUIRED_BY_HUMAN,
                ),
            ),
        ),
        SecurityInterestWithItems(
            interest=interest_b,
            items=(
                _item(interest_id=interest_b.id, status=PerfectionStatus.COMPLETED),
            ),
        ),
    ]
    assert derive_perfection_confirmable(ledger) is True
    assert derive_perfection_blockers(ledger).confirmable is True


def test_a_pending_item_blocks_confirmation() -> None:
    interest = _interest()
    pending = _item(interest_id=interest.id, status=PerfectionStatus.PENDING)
    ledger = [
        SecurityInterestWithItems(
            interest=interest,
            items=(
                _item(interest_id=interest.id, status=PerfectionStatus.COMPLETED),
                pending,
            ),
        )
    ]
    assert derive_perfection_confirmable(ledger) is False
    assert derive_perfection_blockers(ledger).blocking_item_ids == (pending.id,)


def test_an_expired_item_blocks_confirmation() -> None:
    interest = _interest()
    expired = _item(interest_id=interest.id, status=PerfectionStatus.EXPIRED)
    ledger = [SecurityInterestWithItems(interest=interest, items=(expired,))]
    assert derive_perfection_confirmable(ledger) is False
    assert derive_perfection_blockers(ledger).blocking_item_ids == (expired.id,)


def test_blockers_report_both_channels() -> None:
    empty_interest = _interest()
    blocked_interest = _interest()
    pending = _item(interest_id=blocked_interest.id, status=PerfectionStatus.PENDING)
    ledger = [
        SecurityInterestWithItems(interest=empty_interest, items=()),
        SecurityInterestWithItems(interest=blocked_interest, items=(pending,)),
    ]
    blockers = derive_perfection_blockers(ledger)
    assert blockers.has_interests is True
    assert blockers.interests_without_items == (empty_interest.id,)
    assert blockers.blocking_item_ids == (pending.id,)
    assert blockers.confirmable is False


def test_no_priority_rank_field_on_interest() -> None:
    # The system never declares a final priority ranking: only free-text notes.
    assert "priority_rank" not in SecurityInterest.model_fields
    assert "notes_vi" in SecurityInterest.model_fields
