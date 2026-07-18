"""G3_RISK_DISPOSITION derivation tests (application/orchestration/gates.py).

Requirements exercised:

(e) maker-checker separation: the checker's own output can never satisfy G3
    -- every SATISFIED path requires a human disposition.
(f) human disposition required: G3 stays OPEN with zero dispositions even
    when the checker found nothing severe; an explicit assessment-level
    NOTED disposition is required before G3 may derive SATISFIED.
"""

from __future__ import annotations

from uuid import uuid4

from creditops.application.orchestration.gates import (
    G3_SEVERITY_THRESHOLD,
    derive_g3_status,
)
from creditops.domain.orchestration import GateStatus
from creditops.domain.risk_review import ChallengeSeverity


def test_no_assessment_yet_stays_open() -> None:
    assert (
        derive_g3_status(
            assessment_exists=False,
            challenge_severities={},
            disposed_challenge_ids=set(),
            has_assessment_level_disposition=False,
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
            disposed_challenge_ids=set(),
            has_assessment_level_disposition=False,
        )
        is GateStatus.OPEN
    )


def test_empty_challenge_case_satisfied_once_noted() -> None:
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities={},
            disposed_challenge_ids=set(),
            has_assessment_level_disposition=True,
        )
        is GateStatus.SATISFIED
    )


def test_severe_challenges_require_every_one_to_be_disposed() -> None:
    severe_a, severe_b = uuid4(), uuid4()
    severities = {severe_a: ChallengeSeverity.HIGH, severe_b: ChallengeSeverity.CRITICAL}

    # Only one of two severe challenges disposed: still OPEN.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities=severities,
            disposed_challenge_ids={severe_a},
            has_assessment_level_disposition=False,
        )
        is GateStatus.OPEN
    )

    # Both disposed: SATISFIED, WITHOUT needing an assessment-level disposition.
    assert (
        derive_g3_status(
            assessment_exists=True,
            challenge_severities=severities,
            disposed_challenge_ids={severe_a, severe_b},
            has_assessment_level_disposition=False,
        )
        is GateStatus.SATISFIED
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
            disposed_challenge_ids=set(),
            has_assessment_level_disposition=True,
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
            disposed_challenge_ids={low_id},  # the severe one is untouched
            has_assessment_level_disposition=True,
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
            disposed_challenge_ids=set(),
            has_assessment_level_disposition=False,
        )
        is GateStatus.OPEN
    )


def test_named_threshold_is_high() -> None:
    assert G3_SEVERITY_THRESHOLD is ChallengeSeverity.HIGH
