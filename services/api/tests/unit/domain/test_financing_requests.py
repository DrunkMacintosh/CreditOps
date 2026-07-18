"""Invariants of the stage-2 FinancingRequest domain model.

All values here are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.financing_requests import (
    FinancingRequestDraft,
    FinancingRequestVersion,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _version(**overrides: object) -> FinancingRequestVersion:
    base: dict[str, object] = {
        "id": uuid4(),
        "case_id": uuid4(),
        "case_version": 1,
        "request_version": 1,
        "requested_amount": "5000000000",
        "purpose_vi": "Bổ sung vốn lưu động",
        "created_by": uuid4(),
        "created_at": NOW,
    }
    base.update(overrides)
    return FinancingRequestVersion(**base)  # type: ignore[arg-type]


def test_optional_stage_2_fields_default_to_none_not_provided() -> None:
    version = _version()
    # NULL == UNKNOWN / NOT_PROVIDED: nothing is invented for absent fields.
    assert version.currency is None
    assert version.product_vi is None
    assert version.term_months is None
    assert version.expected_use_date is None
    assert version.customer_own_funds is None
    assert version.working_capital_cycle_vi is None
    assert version.proposed_cash_flow_controls_vi is None


def test_request_version_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        _version(request_version=0)


def test_case_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _version(case_version=0)


def test_requested_amount_is_a_positive_decimal_string() -> None:
    # Amounts are exact decimal strings (no float): reject non-numeric and
    # non-positive values.
    with pytest.raises(ValidationError):
        _version(requested_amount="0")
    with pytest.raises(ValidationError):
        _version(requested_amount="12.5")
    with pytest.raises(ValidationError):
        _version(requested_amount="-3")


def test_customer_own_funds_may_be_zero_but_not_malformed() -> None:
    assert _version(customer_own_funds="0").customer_own_funds == "0"
    with pytest.raises(ValidationError):
        _version(customer_own_funds="1,000")


def test_term_months_must_be_positive_when_present() -> None:
    assert _version(term_months=12).term_months == 12
    with pytest.raises(ValidationError):
        _version(term_months=0)


def test_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _version(invented_field="model guessed this")


def test_draft_requires_amount_and_purpose_only() -> None:
    draft = FinancingRequestDraft(
        requested_amount="1000000000", purpose_vi="Nhu cầu vốn"
    )
    assert draft.currency is None
    assert draft.term_months is None
    # And it still validates amount shape.
    with pytest.raises(ValidationError):
        FinancingRequestDraft(requested_amount="abc", purpose_vi="x")
