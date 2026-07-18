"""Unit tests for the stage-10 disbursement ConditionLedger domain.

Covers the deterministic transition map (every allowed edge, a sample of
forbidden edges, exhaustiveness) and the no-vacuous-confirmation predicate.
All data is synthetic.
"""

from __future__ import annotations

import pytest

from creditops.domain.conditions import (
    ALLOWED_TRANSITIONS,
    CONFIRMABLE_STATUSES,
    ConditionStatus,
    derive_conditions_confirmable,
    is_transition_allowed,
)

_ALLOWED_PAIRS = [
    (ConditionStatus.PENDING, ConditionStatus.EVIDENCE_SUBMITTED),
    (ConditionStatus.PENDING, ConditionStatus.WAIVER_REQUESTED),
    (ConditionStatus.PENDING, ConditionStatus.NOT_APPLICABLE_BY_HUMAN),
    (ConditionStatus.PENDING, ConditionStatus.SUPERSEDED),
    (ConditionStatus.EVIDENCE_SUBMITTED, ConditionStatus.VERIFIED),
    (ConditionStatus.EVIDENCE_SUBMITTED, ConditionStatus.FAILED),
    (ConditionStatus.EVIDENCE_SUBMITTED, ConditionStatus.SUPERSEDED),
    (ConditionStatus.FAILED, ConditionStatus.EVIDENCE_SUBMITTED),
    (ConditionStatus.FAILED, ConditionStatus.WAIVER_REQUESTED),
    (ConditionStatus.FAILED, ConditionStatus.SUPERSEDED),
    (ConditionStatus.WAIVER_REQUESTED, ConditionStatus.WAIVED_BY_HUMAN),
    (ConditionStatus.WAIVER_REQUESTED, ConditionStatus.FAILED),
    (ConditionStatus.WAIVER_REQUESTED, ConditionStatus.SUPERSEDED),
    (ConditionStatus.VERIFIED, ConditionStatus.SUPERSEDED),
    (ConditionStatus.WAIVED_BY_HUMAN, ConditionStatus.SUPERSEDED),
    (ConditionStatus.NOT_APPLICABLE_BY_HUMAN, ConditionStatus.SUPERSEDED),
]

_FORBIDDEN_PAIRS = [
    # No verification without submitted evidence -- never straight from PENDING.
    (ConditionStatus.PENDING, ConditionStatus.VERIFIED),
    (ConditionStatus.PENDING, ConditionStatus.WAIVED_BY_HUMAN),
    (ConditionStatus.PENDING, ConditionStatus.FAILED),
    # A submitted-evidence condition cannot be waived/ruled-NA in place.
    (ConditionStatus.EVIDENCE_SUBMITTED, ConditionStatus.WAIVED_BY_HUMAN),
    (ConditionStatus.EVIDENCE_SUBMITTED, ConditionStatus.NOT_APPLICABLE_BY_HUMAN),
    (ConditionStatus.EVIDENCE_SUBMITTED, ConditionStatus.PENDING),
    # A failure is not flipped to VERIFIED in place -- re-submit evidence first.
    (ConditionStatus.FAILED, ConditionStatus.VERIFIED),
    # A refused waiver becomes FAILED, never bounces back to PENDING/EVIDENCE.
    (ConditionStatus.WAIVER_REQUESTED, ConditionStatus.PENDING),
    (ConditionStatus.WAIVER_REQUESTED, ConditionStatus.EVIDENCE_SUBMITTED),
    # Satisfied terminals only ever supersede; never downgrade.
    (ConditionStatus.VERIFIED, ConditionStatus.FAILED),
    (ConditionStatus.VERIFIED, ConditionStatus.EVIDENCE_SUBMITTED),
    (ConditionStatus.WAIVED_BY_HUMAN, ConditionStatus.VERIFIED),
    (ConditionStatus.NOT_APPLICABLE_BY_HUMAN, ConditionStatus.VERIFIED),
    # SUPERSEDED is fully terminal.
    (ConditionStatus.SUPERSEDED, ConditionStatus.PENDING),
    (ConditionStatus.SUPERSEDED, ConditionStatus.SUPERSEDED),
]


@pytest.mark.parametrize(("frm", "to"), _ALLOWED_PAIRS)
def test_allowed_transitions_are_permitted(
    frm: ConditionStatus, to: ConditionStatus
) -> None:
    assert is_transition_allowed(frm, to) is True


@pytest.mark.parametrize(("frm", "to"), _FORBIDDEN_PAIRS)
def test_forbidden_transitions_are_rejected(
    frm: ConditionStatus, to: ConditionStatus
) -> None:
    assert is_transition_allowed(frm, to) is False


@pytest.mark.parametrize("status", list(ConditionStatus))
def test_no_self_transition_is_allowed(status: ConditionStatus) -> None:
    assert is_transition_allowed(status, status) is False


def test_transition_map_covers_every_status_exhaustively() -> None:
    # Every status is a key (no implicit source), and every target is a real
    # status value (no dangling edge).
    assert set(ALLOWED_TRANSITIONS) == set(ConditionStatus)
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= set(ConditionStatus)


def test_terminal_states_only_supersede() -> None:
    for terminal in (
        ConditionStatus.VERIFIED,
        ConditionStatus.WAIVED_BY_HUMAN,
        ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
    ):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset({ConditionStatus.SUPERSEDED})
    assert ALLOWED_TRANSITIONS[ConditionStatus.SUPERSEDED] == frozenset()


def test_empty_ledger_is_never_confirmable() -> None:
    # No vacuous satisfaction: an empty ledger cannot satisfy the gate.
    assert derive_conditions_confirmable([]) is False


@pytest.mark.parametrize("status", sorted(CONFIRMABLE_STATUSES))
def test_single_satisfied_terminal_is_confirmable(status: ConditionStatus) -> None:
    assert derive_conditions_confirmable([status]) is True


def test_all_satisfied_terminals_are_confirmable() -> None:
    assert (
        derive_conditions_confirmable(
            [
                ConditionStatus.VERIFIED,
                ConditionStatus.WAIVED_BY_HUMAN,
                ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
            ]
        )
        is True
    )


@pytest.mark.parametrize(
    "blocking",
    [
        ConditionStatus.PENDING,
        ConditionStatus.EVIDENCE_SUBMITTED,
        ConditionStatus.FAILED,
        ConditionStatus.WAIVER_REQUESTED,
        ConditionStatus.SUPERSEDED,
    ],
)
def test_any_non_terminal_condition_blocks_confirmation(
    blocking: ConditionStatus,
) -> None:
    assert (
        derive_conditions_confirmable([ConditionStatus.VERIFIED, blocking]) is False
    )


def test_confirmable_statuses_are_the_three_satisfied_terminals() -> None:
    assert CONFIRMABLE_STATUSES == frozenset(
        {
            ConditionStatus.VERIFIED,
            ConditionStatus.WAIVED_BY_HUMAN,
            ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
        }
    )
