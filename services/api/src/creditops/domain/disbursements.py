"""Stage-11 disbursement domain: proposed action, exact-decimal money, execution
state machine, and the labelled-mock execution receipt.

Master design section 5 giai đoạn 11 ("Giải ngân vốn vay").

SPEC CONTRACT faithfully encoded here (all pure, frozen value objects; NO I/O,
NO clock, NO randomness, NO LLM anywhere in this module):

- A ``ProposedDisbursementAction`` is DERIVED from approved terms.  Its ``amount``
  is an EXACT ``Decimal`` (money is never a float), positive and finite; its
  ``currency`` is required; ``beneficiary_ref_vi`` / ``account_ref_vi`` are
  SYNTHETIC references (nothing is a real bank account).  The action carries no
  execution capability itself -- execution happens only through the labelled mock
  adapter after BOTH human gates.
- ``validate_amount_against_terms`` is the currency-aware, cap-aware CHECK against
  the ``ApprovedTermSnapshot`` the decision froze: the proposed currency MUST
  equal the approved currency when the approval fixed one, and the proposed amount
  MUST NOT exceed the approved amount when the approval fixed one (a computation,
  documented PROPOSED).  Absent approved fields are simply not compared.
- ``ExecutionStatus`` is the CLOSED execution-lifecycle set with a deterministic
  ``ALLOWED_EXECUTION_TRANSITIONS`` map (the SAME map is re-encoded by the
  migration's trigger and re-checked by the adapter/application layers).  A
  simulated timeout / ambiguous result records ``EXECUTION_UNKNOWN``; such an
  action is NEVER blindly retried -- only a human reconciliation may resolve it,
  and only a ``CONFIRMED_NOT_EXECUTED`` outcome re-opens a NEW execution attempt.
- ``DisbursementExecutionReceipt`` is the labelled mock adapter's frozen return:
  it pins the ``idempotency_key`` the attempt used, the fixed adapter label, the
  adapter result status, and a deterministic ``receipt_ref`` that is present IFF
  the result is ``CONFIRMED_EXECUTED`` (a timeout / unknown carries no receipt).

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId

type ProposedDisbursementActionId = UUID
type DisbursementExecutionReceiptId = UUID

#: The fixed label of the ONLY execution adapter this project wires: a
#: deterministic mock.  Nothing is ever executed against a real core-banking
#: system.  The migration's CHECK mirrors this constant exactly.
MOCK_DISBURSEMENT_ADAPTER_LABEL: Final = "MOCK_DISBURSEMENT_EXECUTION_ADAPTER"

#: Exact-decimal amount format accepted from the (text) storage column and the
#: API: digits with an optional fractional part.  Money is stored as text and
#: carried as ``Decimal`` -- there is NO float arithmetic on money anywhere.
_AMOUNT_TEXT_PATTERN: Final = r"^[0-9]+(\.[0-9]+)?$"


class ExecutionStatus(StrEnum):
    """The CLOSED disbursement-execution lifecycle set (design giai đoạn 11).

    - ``PROPOSED``               the action exists but has never been executed.
    - ``EXECUTION_REQUESTED``    an attempt was durably recorded BEFORE the mock
      adapter was invoked; if the response is lost the action stays here and, like
      ``EXECUTION_UNKNOWN``, requires human reconciliation (never a blind retry).
    - ``EXECUTION_UNKNOWN``      the adapter timed out / returned an ambiguous
      result: whether money moved is UNKNOWN; only a human may resolve it.
    - ``CONFIRMED_EXECUTED``     terminal success (adapter receipt or a human
      reconciliation confirming the funds moved).
    - ``CONFIRMED_NOT_EXECUTED`` a human reconciliation confirmed NO funds moved;
      this is the ONLY outcome from which a NEW execution attempt may start.

    An agent can never WRITE any of these values -- a status is moved exclusively
    by the human-only API / mock adapter and only along an
    ``ALLOWED_EXECUTION_TRANSITIONS`` edge.
    """

    PROPOSED = "PROPOSED"
    EXECUTION_REQUESTED = "EXECUTION_REQUESTED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    CONFIRMED_EXECUTED = "CONFIRMED_EXECUTED"
    CONFIRMED_NOT_EXECUTED = "CONFIRMED_NOT_EXECUTED"


#: The deterministic execution transition map (PROPOSED synthetic edges).  A pair
#: absent here is FORBIDDEN -- there is no implicit edge.  Rationale per state:
#:
#: - ``PROPOSED`` -> only ``EXECUTION_REQUESTED`` (an attempt begins).
#: - ``EXECUTION_REQUESTED`` -> ``CONFIRMED_EXECUTED`` / ``EXECUTION_UNKNOWN`` (the
#:   adapter result) or ``CONFIRMED_NOT_EXECUTED`` (a human reconciliation of a
#:   stranded, lost-response attempt).
#: - ``EXECUTION_UNKNOWN`` -> ``CONFIRMED_EXECUTED`` / ``CONFIRMED_NOT_EXECUTED``
#:   (human reconciliation ONLY; never a blind retry).
#: - ``CONFIRMED_EXECUTED`` is terminal.
#: - ``CONFIRMED_NOT_EXECUTED`` -> ``EXECUTION_REQUESTED`` (the ONLY state that
#:   re-opens a new attempt, with a fresh idempotency key).
ALLOWED_EXECUTION_TRANSITIONS: Mapping[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.PROPOSED: frozenset({ExecutionStatus.EXECUTION_REQUESTED}),
    ExecutionStatus.EXECUTION_REQUESTED: frozenset(
        {
            ExecutionStatus.CONFIRMED_EXECUTED,
            ExecutionStatus.EXECUTION_UNKNOWN,
            ExecutionStatus.CONFIRMED_NOT_EXECUTED,
        }
    ),
    ExecutionStatus.EXECUTION_UNKNOWN: frozenset(
        {
            ExecutionStatus.CONFIRMED_EXECUTED,
            ExecutionStatus.CONFIRMED_NOT_EXECUTED,
        }
    ),
    ExecutionStatus.CONFIRMED_EXECUTED: frozenset(),
    ExecutionStatus.CONFIRMED_NOT_EXECUTED: frozenset(
        {ExecutionStatus.EXECUTION_REQUESTED}
    ),
}

#: The result statuses the labelled mock adapter may ever yield: a clean success
#: or an ambiguous timeout.  A clean "not executed" is NEVER an adapter result --
#: it can only come from a HUMAN reconciliation.
ADAPTER_RESULT_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {ExecutionStatus.CONFIRMED_EXECUTED, ExecutionStatus.EXECUTION_UNKNOWN}
)

#: The outcomes a human reconciliation may record for an unresolved execution.
RECONCILIATION_OUTCOMES: frozenset[ExecutionStatus] = frozenset(
    {ExecutionStatus.CONFIRMED_EXECUTED, ExecutionStatus.CONFIRMED_NOT_EXECUTED}
)

#: States from which a NEW execution attempt may begin: never-attempted, or a
#: human-confirmed non-execution.  ``EXECUTION_UNKNOWN`` / ``EXECUTION_REQUESTED``
#: are deliberately EXCLUDED -- they must be reconciled first (fail closed).
REATTEMPTABLE_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {ExecutionStatus.PROPOSED, ExecutionStatus.CONFIRMED_NOT_EXECUTED}
)

#: Unresolved states that require a human reconciliation before any further
#: execution -- a second execute here is REFUSED (never auto-retried).
RECONCILABLE_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {ExecutionStatus.EXECUTION_REQUESTED, ExecutionStatus.EXECUTION_UNKNOWN}
)


def is_execution_transition_allowed(
    from_status: ExecutionStatus, to_status: ExecutionStatus
) -> bool:
    """Whether ``from_status -> to_status`` is an explicit allowed edge.

    Self-transitions and any pair absent from ``ALLOWED_EXECUTION_TRANSITIONS``
    are rejected: the map is exhaustive and there is no implicit edge.
    """

    return to_status in ALLOWED_EXECUTION_TRANSITIONS.get(from_status, frozenset())


class CurrencyMismatchError(ValueError):
    """The proposed disbursement currency differs from the approved currency."""


class AmountExceedsApprovedError(ValueError):
    """The proposed disbursement amount exceeds the approved amount."""


def parse_exact_amount(text: str) -> Decimal:
    """Parse an exact-decimal money string into a positive ``Decimal``.

    Pure and total-or-raises: rejects a malformed literal, a non-finite value, a
    negative value, or zero (a disbursement moves a positive amount).  Never uses
    float arithmetic.
    """

    candidate = text.strip()
    try:
        amount = Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueError(f"{text!r} is not a valid exact-decimal amount") from exc
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if amount <= 0:
        raise ValueError("amount must be strictly positive")
    return amount


def validate_amount_against_terms(
    *,
    amount: Decimal,
    currency: str,
    approved_amount: Decimal | None,
    approved_currency: str | None,
) -> None:
    """Currency-aware, cap-aware validation against the frozen approved terms.

    Raises ``CurrencyMismatchError`` if the approval fixed a currency and the
    proposed currency differs, and ``AmountExceedsApprovedError`` if the approval
    fixed an amount and the proposed amount exceeds it (PROPOSED computation:
    a disbursement may be partial, so ``<=`` is allowed, ``>`` is not).  When an
    approved field is absent it is simply not compared (nothing to validate
    against) -- this never silently fabricates a limit.
    """

    if approved_currency is not None and currency != approved_currency:
        raise CurrencyMismatchError(
            f"proposed currency {currency!r} != approved currency {approved_currency!r}"
        )
    if approved_amount is not None and amount > approved_amount:
        raise AmountExceedsApprovedError(
            f"proposed amount {amount} exceeds approved amount {approved_amount}"
        )


class ProposedDisbursementAction(BaseModel):
    """One proposed disbursement, derived from approved terms + verified conditions.

    Frozen value object.  ``amount`` is an exact positive ``Decimal`` and
    ``currency`` is required; ``beneficiary_ref_vi`` / ``account_ref_vi`` are
    SYNTHETIC references.  ``status`` is the only field a later transition may
    change (trigger-enforced in the migration); everything else is bound at
    creation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ProposedDisbursementActionId
    case_id: CaseId
    case_version: int = Field(ge=1)
    decision_id: UUID
    amount: Decimal
    currency: str = Field(min_length=1, max_length=8)
    beneficiary_ref_vi: str = Field(min_length=1, max_length=400)
    account_ref_vi: str = Field(min_length=1, max_length=400)
    status: ExecutionStatus = ExecutionStatus.PROPOSED
    created_by: UUID

    @model_validator(mode="after")
    def _amount_is_exact_positive(self) -> Self:
        if not self.amount.is_finite():
            raise ValueError("amount must be finite")
        if self.amount <= 0:
            raise ValueError("amount must be strictly positive")
        return self

    @property
    def amount_text(self) -> str:
        """The canonical exact-decimal text used to store ``amount`` (no float)."""

        return format(self.amount, "f")


class DisbursementExecutionReceipt(BaseModel):
    """The labelled mock adapter's frozen execution receipt for one attempt.

    ``adapter_label`` is always ``MOCK_DISBURSEMENT_ADAPTER_LABEL`` (nothing runs
    against a real system); ``result_status`` is one of ``ADAPTER_RESULT_STATUSES``
    and ``receipt_ref`` is present IFF the result is ``CONFIRMED_EXECUTED`` -- a
    timeout / ``EXECUTION_UNKNOWN`` carries no receipt.  ``idempotency_key`` pins
    the attempt so a duplicate delivery can never move money twice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: DisbursementExecutionReceiptId
    action_id: ProposedDisbursementActionId
    idempotency_key: str = Field(min_length=1, max_length=200)
    adapter_label: str
    result_status: ExecutionStatus
    receipt_ref: str | None = Field(default=None, min_length=1, max_length=200)
    is_mock: bool = True

    @model_validator(mode="after")
    def _labelled_mock_result_is_consistent(self) -> Self:
        if self.adapter_label != MOCK_DISBURSEMENT_ADAPTER_LABEL:
            raise ValueError(
                f"adapter_label must be the labelled mock adapter "
                f"{MOCK_DISBURSEMENT_ADAPTER_LABEL!r}"
            )
        if not self.is_mock:
            raise ValueError("a disbursement execution receipt is always mock")
        if self.result_status not in ADAPTER_RESULT_STATUSES:
            raise ValueError(
                f"{self.result_status.value} is not an adapter result status"
            )
        if self.result_status is ExecutionStatus.CONFIRMED_EXECUTED:
            if self.receipt_ref is None:
                raise ValueError("a confirmed execution must carry a receipt_ref")
        elif self.receipt_ref is not None:
            raise ValueError("an unknown execution must not carry a receipt_ref")
        return self


__all__ = [
    "ADAPTER_RESULT_STATUSES",
    "ALLOWED_EXECUTION_TRANSITIONS",
    "MOCK_DISBURSEMENT_ADAPTER_LABEL",
    "REATTEMPTABLE_STATUSES",
    "RECONCILABLE_STATUSES",
    "RECONCILIATION_OUTCOMES",
    "AmountExceedsApprovedError",
    "CurrencyMismatchError",
    "DisbursementExecutionReceipt",
    "DisbursementExecutionReceiptId",
    "ExecutionStatus",
    "ProposedDisbursementAction",
    "ProposedDisbursementActionId",
    "is_execution_transition_allowed",
    "parse_exact_amount",
    "validate_amount_against_terms",
]
