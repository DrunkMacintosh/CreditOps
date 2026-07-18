"""Stage-2 financing-request domain model (master design section 5 stage 2).

The FinancingRequest is a VERSIONED, append-only aggregate: every edit is a new
``FinancingRequestVersion`` row, never an in-place mutation of a prior version
(the durable append-only guarantee lives in
supabase/migrations/202607170008_financing_requests.sql).  ``request_version``
starts at 1 and increments by one per edit.

Every structured field except the required amount and purpose is OPTIONAL and
carries ``UNKNOWN`` / ``NOT_PROVIDED`` semantics through ``None``: the spec
forbids model-invented values, so a field the customer did not supply MUST stay
``None`` rather than be defaulted or guessed.  Amounts are exact whole-currency
decimals carried AS STRINGS (mirroring how ``api/cases.py`` handles
``requested_amount``) so no float rounding can ever touch a monetary value.

Nothing in this module expresses or implies a credit decision; the financing
request only records the customer's stated need.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Exact positive whole-currency amount as a canonical digit string (no leading
#: zero), matching the ``requested_amount`` contract in ``api/cases.py`` and the
#: ``numeric(30, 0) > 0`` column check.
_POSITIVE_AMOUNT_PATTERN = r"^[1-9][0-9]*$"
#: A non-negative amount (own funds may legitimately be zero).
_NON_NEGATIVE_AMOUNT_PATTERN = r"^(0|[1-9][0-9]*)$"


class FinancingRequestDraft(BaseModel):
    """The writable stage-2 fields captured for one financing-request edit.

    ``requested_amount`` and ``purpose_vi`` are required (they mirror the
    pre-existing NOT NULL columns); every other field is optional and defaults
    to ``None`` == NOT_PROVIDED.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    requested_amount: str = Field(min_length=1, max_length=30, pattern=_POSITIVE_AMOUNT_PATTERN)
    purpose_vi: str = Field(min_length=1, max_length=500)
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    product_vi: str | None = Field(default=None, min_length=1, max_length=200)
    term_months: int | None = Field(default=None, gt=0, le=600)
    expected_use_date: date | None = None
    repayment_source_vi: str | None = Field(default=None, min_length=1, max_length=2000)
    repayment_plan_vi: str | None = Field(default=None, min_length=1, max_length=2000)
    proposed_security_vi: str | None = Field(default=None, min_length=1, max_length=2000)
    customer_own_funds: str | None = Field(
        default=None, min_length=1, max_length=30, pattern=_NON_NEGATIVE_AMOUNT_PATTERN
    )
    connected_trade_products_vi: str | None = Field(default=None, min_length=1, max_length=2000)
    working_capital_cycle_vi: str | None = Field(default=None, min_length=1, max_length=2000)
    key_suppliers_customers_vi: str | None = Field(default=None, min_length=1, max_length=2000)
    proposed_cash_flow_controls_vi: str | None = Field(default=None, min_length=1, max_length=2000)


class FinancingRequestVersion(BaseModel):
    """One immutable, persisted financing-request version.

    Mirrors the ``public.financing_requests`` row shape.  ``request_version`` is
    ``>= 1`` and ``case_version`` is ``> 0`` (the durable checks); the optional
    fields keep their ``None`` == NOT_PROVIDED meaning.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    case_id: UUID
    case_version: int = Field(gt=0)
    request_version: int = Field(ge=1)
    requested_amount: str = Field(min_length=1, max_length=30, pattern=_POSITIVE_AMOUNT_PATTERN)
    purpose_vi: str = Field(min_length=1, max_length=500)
    currency: str | None = None
    product_vi: str | None = None
    term_months: int | None = Field(default=None, gt=0)
    expected_use_date: date | None = None
    repayment_source_vi: str | None = None
    repayment_plan_vi: str | None = None
    proposed_security_vi: str | None = None
    customer_own_funds: str | None = Field(
        default=None, min_length=1, max_length=30, pattern=_NON_NEGATIVE_AMOUNT_PATTERN
    )
    connected_trade_products_vi: str | None = None
    working_capital_cycle_vi: str | None = None
    key_suppliers_customers_vi: str | None = None
    proposed_cash_flow_controls_vi: str | None = None
    created_by: UUID
    created_at: datetime
