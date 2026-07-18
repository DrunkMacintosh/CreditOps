"""G2/G4 derivation tests (application/orchestration/gates.py).

Requirements exercised: (c) missing approval blocks the action -- no
authorization record means G4 stays OPEN; the credit-ops worker's own output
can never satisfy either gate -- every SATISFIED path requires a human
record; G2 derives from document-request approvals (the G2 pattern).
"""

from __future__ import annotations

from uuid import uuid4

from creditops.application.orchestration.gates import derive_g2_status, derive_g4_status
from creditops.domain.orchestration import GateStatus


def test_g4_stays_open_without_a_package() -> None:
    assert (
        derive_g4_status(package_exists=False, action_ids=set(), authorized_action_ids=set())
        is GateStatus.OPEN
    )


def test_g4_stays_open_while_any_action_lacks_its_own_authorization() -> None:
    # (c) missing approval blocks the action: two drafted actions, one human
    # authorization -- G4 must stay OPEN.
    action_a, action_b = uuid4(), uuid4()
    assert (
        derive_g4_status(
            package_exists=True,
            action_ids={action_a, action_b},
            authorized_action_ids={action_a},
        )
        is GateStatus.OPEN
    )


def test_g4_satisfied_once_every_action_has_a_human_authorization() -> None:
    action_a, action_b = uuid4(), uuid4()
    assert (
        derive_g4_status(
            package_exists=True,
            action_ids={action_a, action_b},
            authorized_action_ids={action_a, action_b},
        )
        is GateStatus.SATISFIED
    )


def test_g4_ignores_authorizations_for_foreign_actions() -> None:
    # An authorization record naming an action outside the package can never
    # substitute for the real one.
    action = uuid4()
    assert (
        derive_g4_status(
            package_exists=True,
            action_ids={action},
            authorized_action_ids={uuid4()},
        )
        is GateStatus.OPEN
    )


def test_g4_with_zero_actions_is_vacuously_satisfied_only_with_a_package() -> None:
    assert (
        derive_g4_status(package_exists=True, action_ids=set(), authorized_action_ids=set())
        is GateStatus.SATISFIED
    )
    assert (
        derive_g4_status(package_exists=False, action_ids=set(), authorized_action_ids=set())
        is GateStatus.OPEN
    )


def test_agent_output_alone_can_never_satisfy_g4() -> None:
    # The worker persists a package with drafted actions and NO human
    # record of any kind: G4 stays OPEN no matter how the package is shaped.
    assert (
        derive_g4_status(
            package_exists=True,
            action_ids={uuid4(), uuid4(), uuid4()},
            authorized_action_ids=set(),
        )
        is GateStatus.OPEN
    )


def test_g2_stays_open_without_a_package() -> None:
    assert (
        derive_g2_status(package_exists=False, request_ids=set(), approved_request_ids=set())
        is GateStatus.OPEN
    )


def test_g2_requires_every_document_request_to_be_approved() -> None:
    request_a, request_b = uuid4(), uuid4()
    assert (
        derive_g2_status(
            package_exists=True,
            request_ids={request_a, request_b},
            approved_request_ids={request_a},
        )
        is GateStatus.OPEN
    )
    assert (
        derive_g2_status(
            package_exists=True,
            request_ids={request_a, request_b},
            approved_request_ids={request_a, request_b},
        )
        is GateStatus.SATISFIED
    )


def test_g2_with_zero_requests_is_vacuously_satisfied() -> None:
    # Nothing was drafted, so there is nothing a human must approve.
    assert (
        derive_g2_status(package_exists=True, request_ids=set(), approved_request_ids=set())
        is GateStatus.SATISFIED
    )
