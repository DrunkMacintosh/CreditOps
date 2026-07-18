"""G3_RISK_DISPOSITION derivation tests (application/orchestration/gates.py).

Requirements exercised:

(e) maker-checker separation: the checker's own output can never satisfy G3
    -- every SATISFIED path requires a human disposition.
(f) human disposition required: G3 stays OPEN with zero dispositions even
    when the checker found nothing severe; an explicit assessment-level
    NOTED disposition is required before G3 may derive SATISFIED.
(g) disposition semantics: "đã disposition" is NOT "được tiếp tục" (master
    design section 6).  Only continue-authorizing types (NOTED,
    ACCEPTED_RISK) may satisfy G3; MAKER_MUST_REVISE demands a maker
    revision and ESCALATED awaits a higher authority -- both leave the gate
    OPEN, fail closed.  The latest disposition per challenge governs.
"""

from __future__ import annotations

from uuid import uuid4

from creditops.application.orchestration.gates import (
    G3_CONTINUE_DISPOSITION_TYPES,
    G3_SEVERITY_THRESHOLD,
    derive_g3_status,
)
from creditops.domain.orchestration import GateStatus
from creditops.domain.risk_review import ChallengeSeverity


def test_continue_authorizing_types_are_the_labelled_synthetic_config() -> None:
    # PROPOSED synthetic configuration: no official SHB disposition taxonomy
    # exists, so the continue set is pinned and reviewed here.
    assert G3_CONTINUE_DISPOSITION_TYPES == frozenset({"NOTED", "ACCEPTED_RISK"})


def test_no_assessment_yet_stays_open() -> None:
    assert (
        derive_g3_status(
            assessment_exists=False,
            challenge_severities={},
            latest_challenge_dispositions={},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )


def test_empty_challenge_case_requires_explicit_assessment_level_disposition() -> None:
    # (f) the checker found nothing severe, but zero dispositions were
    # recorded -- G3 must stay OPEN, never derive SATISFIED from silence.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={},
            latest_challenge_dispositions={},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )


def test_empty_challenge_case_satisfied_once_noted() -> None:
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={},
            latest_challenge_dispositions={},
            latest_assessment_level_disposition="NOTED",
        )
        is GateStatus.SATISFIED
    )


def test_severe_challenges_require_every_one_to_be_continue_disposed() -> None:
    severe_a, severe_b = uuid4(), uuid4()
    severities = {severe_a: ChallengeSeverity.HIGH, severe_b: ChallengeSeverity.CRITICAL}

    # Only one of two severe challenges disposed: still OPEN.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities=severities,
            latest_challenge_dispositions={severe_a: "ACCEPTED_RISK"},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )

    # Both continue-disposed: SATISFIED, WITHOUT an assessment-level record.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities=severities,
            latest_challenge_dispositions={
                severe_a: "ACCEPTED_RISK",
                severe_b: "NOTED",
            },
            latest_assessment_level_disposition=None,
        )
        is GateStatus.SATISFIED
    )


def test_maker_must_revise_never_satisfies_g3() -> None:
    # (g) MAKER_MUST_REVISE demands a maker revision; the case must NOT
    # continue to Credit Operations (master design sections 6 and 9).
    severe_id = uuid4()
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={severe_id: ChallengeSeverity.HIGH},
            latest_challenge_dispositions={severe_id: "MAKER_MUST_REVISE"},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )


def test_escalated_never_satisfies_g3() -> None:
    # (g) ESCALATED awaits a higher-authority outcome; fail closed.
    severe_id = uuid4()
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={severe_id: ChallengeSeverity.CRITICAL},
            latest_challenge_dispositions={severe_id: "ESCALATED"},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )


def test_latest_disposition_per_challenge_governs() -> None:
    # A challenge first sent back for revision and LATER accepted may
    # continue; the mapping carries the latest type per challenge.
    severe_id = uuid4()
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={severe_id: ChallengeSeverity.HIGH},
            latest_challenge_dispositions={severe_id: "ACCEPTED_RISK"},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.SATISFIED
    )
    # ...and the reverse (accepted, then sent back) leaves the gate OPEN.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={severe_id: ChallengeSeverity.HIGH},
            latest_challenge_dispositions={severe_id: "MAKER_MUST_REVISE"},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )


def test_non_continue_assessment_level_disposition_stays_open() -> None:
    # Fail closed even at the assessment level: only a continue-authorizing
    # type satisfies the empty/low-severity path.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={},
            latest_challenge_dispositions={},
            latest_assessment_level_disposition="ESCALATED",
        )
        is GateStatus.OPEN
    )


def test_low_and_medium_challenges_never_require_disposition_for_g3() -> None:
    low_id, medium_id = uuid4(), uuid4()
    severities = {low_id: ChallengeSeverity.LOW, medium_id: ChallengeSeverity.MEDIUM}
    # Nothing at/above the named threshold (HIGH): behaves like the
    # empty-challenge case and still needs the assessment-level disposition.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities=severities,
            latest_challenge_dispositions={},
            latest_assessment_level_disposition="NOTED",
        )
        is GateStatus.SATISFIED
    )


def test_disposing_a_non_severe_challenge_does_not_substitute_for_severe_ones() -> None:
    severe_id, low_id = uuid4(), uuid4()
    severities = {severe_id: ChallengeSeverity.HIGH, low_id: ChallengeSeverity.LOW}
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities=severities,
            latest_challenge_dispositions={low_id: "ACCEPTED_RISK"},
            latest_assessment_level_disposition="NOTED",
        )
        is GateStatus.OPEN
    )


def test_checker_output_alone_can_never_satisfy_g3() -> None:
    # (e) exhaustively: with an assessment present and severe challenges
    # raised but NO disposition of any kind recorded, G3 stays OPEN no
    # matter how the checker's own output is shaped.
    challenge_id = uuid4()
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={challenge_id: ChallengeSeverity.CRITICAL},
            latest_challenge_dispositions={},
            latest_assessment_level_disposition=None,
        )
        is GateStatus.OPEN
    )


def test_named_threshold_is_high() -> None:
    assert G3_SEVERITY_THRESHOLD is ChallengeSeverity.HIGH
