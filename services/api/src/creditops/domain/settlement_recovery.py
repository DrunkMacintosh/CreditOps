"""Stage-14 settlement (14A) and recovery-preparation (14B) domain.

Master design section 5 giai đoạn 14 ("Tất toán hoặc xử lý nợ").  Two mutually
exclusive branches, both opened ONLY by a deterministic ledger check plus a human
authority act -- never by a model score:

- 14A SETTLEMENT opens only when a deterministic ledger check confirms a ZERO
  outstanding balance (principal + interest + fees) AND zero still-open
  reconciliation exceptions.  ``derive_settlement_eligible`` is that pure check.
  The human gate is ``HG_SETTLEMENT_CONFIRMED``; closure / release receipts are
  LABELLED MOCK (``SettlementReceiptKind`` -- exactly like the stage-7
  communication_receipts MOCK_CHANNEL).  Real registry release / closure is OUT
  OF SCOPE.
- 14B RECOVERY opens only from a deterministic trigger (a supplied ledger
  snapshot showing SUSTAINED shortfall -- outstanding past ``SUSTAINED_SHORTFALL_
  PERIODS`` periods) PLUS an explicit HUMAN escalation record (an escalator +
  rationale).  ``derive_recovery_trigger`` is that pure deterministic rule.  A
  ``RecoveryCase`` carries an evidence pack of REFERENCES (uuid / text refs) and
  structured options whose consequences are structured text.  Only ONE human
  gate is in scope here -- ``HG_RECOVERY_STRATEGY_APPROVED``; separate gates for
  future security / legal / write-off actions are OUT OF SCOPE, and no real
  enforcement / litigation / write-off state exists.

STAGE-13 SEAM (concurrent stage).  This module NEVER imports a stage-13 module
(the deterministic ``RepaymentLedger`` may not exist yet).  Instead it defines
the minimal structural inputs it needs -- outstanding totals + open-exception
count for settlement, outstanding total + shortfall-period count for recovery --
as the frozen ``SettlementLedgerInputs`` / ``RecoveryTriggerInputs`` carriers and
the matching ``LedgerSettlementSnapshot`` / ``LedgerRecoverySnapshot`` Protocols.
The integrator adapts the real ledger to these shapes; nothing here couples to
its internals.  See ``derive_settlement_eligible`` / ``derive_recovery_trigger``.

PROPOSED / SYNTHETIC: ``SUSTAINED_SHORTFALL_PERIODS``, the receipt-kind set, the
recovery status set, and the eligibility / trigger rules are a prototype
configuration.  No official SHB settlement / recovery control code, materiality
threshold, or authority matrix has been supplied; every choice is documented as
PROPOSED and MUST be reconfigured when an official source exists.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from creditops.domain.ids import CaseId

#: PROPOSED synthetic constant (the labelled ``N``): the number of consecutive
#: repayment periods a shortfall must persist before the deterministic recovery
#: trigger MAY fire.  A single missed period is never sufficient; recovery
#: preparation follows SUSTAINED shortfall, not one late payment.  Reconfigure
#: when an official arrears / classification threshold is supplied.
SUSTAINED_SHORTFALL_PERIODS: int = 3


def _canonical_nonneg_amount(raw: str) -> str:
    """Validate a decimal amount string and return its canonical form.

    Rejects a non-decimal, non-finite, or NEGATIVE amount (``ValueError``).  The
    ONE canonicalization is that any decimal-zero amount (``'0'``, ``'0.00'``,
    ``'0E-9'``) is returned as exactly ``'0'``; every non-zero amount is returned
    verbatim (after stripping).  This is what makes the database text CHECK
    ``zero_balance_confirmed = (outstanding_* = '0')`` SOUND: a zero balance is
    always stored as the single token ``'0'`` regardless of how it was written.
    """

    text = raw.strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal amount: {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"non-finite decimal amount: {raw!r}")
    if value < 0:
        raise ValueError(f"amount must be non-negative: {raw!r}")
    if value == 0:
        return "0"
    return text


# -- stage-13 seam protocols --------------------------------------------------


@runtime_checkable
class LedgerSettlementSnapshot(Protocol):
    """Structural settlement input the integrator adapts the stage-13 ledger to.

    Attributes are canonical non-negative decimal-string outstanding totals plus
    the count of still-open reconciliation exceptions.  ``SettlementLedgerInputs``
    is the concrete in-tree carrier that satisfies this Protocol.
    """

    @property
    def outstanding_principal(self) -> str: ...
    @property
    def outstanding_interest(self) -> str: ...
    @property
    def outstanding_fees(self) -> str: ...
    @property
    def open_exception_count(self) -> int: ...


@runtime_checkable
class LedgerRecoverySnapshot(Protocol):
    """Structural recovery-trigger input the integrator adapts the ledger to.

    ``outstanding_total`` is a canonical non-negative decimal string;
    ``periods_in_shortfall`` is the count of consecutive periods a shortfall has
    persisted.  ``RecoveryTriggerInputs`` is the concrete in-tree carrier.
    """

    @property
    def outstanding_total(self) -> str: ...
    @property
    def periods_in_shortfall(self) -> int: ...


class SettlementLedgerInputs(BaseModel):
    """Frozen carrier for the settlement ledger snapshot (validates + canonicalizes).

    Conforms to ``LedgerSettlementSnapshot``.  Amounts are validated as
    non-negative decimals and canonicalized (zero -> ``'0'``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outstanding_principal: str
    outstanding_interest: str
    outstanding_fees: str
    open_exception_count: int = Field(ge=0)

    @field_validator(
        "outstanding_principal", "outstanding_interest", "outstanding_fees"
    )
    @classmethod
    def _canonical(cls, value: str) -> str:
        return _canonical_nonneg_amount(value)


class RecoveryTriggerInputs(BaseModel):
    """Frozen carrier for the recovery-trigger ledger snapshot.

    Conforms to ``LedgerRecoverySnapshot``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outstanding_total: str
    periods_in_shortfall: int = Field(ge=0)

    @field_validator("outstanding_total")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return _canonical_nonneg_amount(value)


# -- deterministic derivations (echo their inputs) ----------------------------


class SettlementEligibility(BaseModel):
    """The pure settlement-eligibility verdict, echoing the ledger snapshot.

    ``eligible`` is True IFF ``zero_balance`` (all three outstanding totals are
    decimal-zero) AND there are zero open reconciliation exceptions.  The echoed
    fields let the API return exactly what the deterministic check saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    zero_balance: bool
    outstanding_principal: str
    outstanding_interest: str
    outstanding_fees: str
    open_exception_count: int


class RecoveryTriggerAssessment(BaseModel):
    """The pure recovery-trigger verdict, echoing the ledger snapshot.

    ``triggered`` is True IFF the outstanding total is strictly positive AND the
    shortfall has persisted for at least ``threshold_periods`` periods.  A
    positive balance alone never triggers -- the shortfall must be SUSTAINED.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    triggered: bool
    outstanding_total: str
    periods_in_shortfall: int
    threshold_periods: int


def derive_settlement_eligible(
    inputs: LedgerSettlementSnapshot,
) -> SettlementEligibility:
    """Whether settlement (14A) MAY be opened for this ledger snapshot.

    Deterministic and pure: eligible IFF every outstanding total is decimal-zero
    (Decimal comparison, never string equality -- ``'0.00'`` equals ``'0'``) AND
    ``open_exception_count == 0``.  Echoes the inputs so the caller can surface
    exactly what was checked.
    """

    principal = Decimal(inputs.outstanding_principal)
    interest = Decimal(inputs.outstanding_interest)
    fees = Decimal(inputs.outstanding_fees)
    zero_balance = principal == 0 and interest == 0 and fees == 0
    eligible = zero_balance and inputs.open_exception_count == 0
    return SettlementEligibility(
        eligible=eligible,
        zero_balance=zero_balance,
        outstanding_principal=inputs.outstanding_principal,
        outstanding_interest=inputs.outstanding_interest,
        outstanding_fees=inputs.outstanding_fees,
        open_exception_count=inputs.open_exception_count,
    )


def derive_recovery_trigger(
    inputs: LedgerRecoverySnapshot,
    *,
    threshold_periods: int = SUSTAINED_SHORTFALL_PERIODS,
) -> RecoveryTriggerAssessment:
    """Whether the deterministic recovery (14B) trigger fires for this snapshot.

    Deterministic and pure: triggered IFF the outstanding total is strictly
    positive (Decimal comparison) AND the shortfall has persisted for at least
    ``threshold_periods`` consecutive periods.  This is ONLY the deterministic
    half of opening recovery; a ``RecoveryCase`` additionally requires an explicit
    human escalation record (never a model score).  Echoes the inputs.
    """

    outstanding = Decimal(inputs.outstanding_total)
    triggered = outstanding > 0 and inputs.periods_in_shortfall >= threshold_periods
    return RecoveryTriggerAssessment(
        triggered=triggered,
        outstanding_total=inputs.outstanding_total,
        periods_in_shortfall=inputs.periods_in_shortfall,
        threshold_periods=threshold_periods,
    )


# -- settlement (14A) models --------------------------------------------------


class SettlementReceiptKind(StrEnum):
    """The CLOSED set of LABELLED MOCK settlement receipts (design giai đoạn 14).

    An agent can never write these; both are produced by the human confirmation
    surface only, and neither performs any real closure or registry release.
    """

    MOCK_CLOSURE = "MOCK_CLOSURE"
    MOCK_RELEASE = "MOCK_RELEASE"


#: The two MOCK receipts a confirmed settlement produces, in order.
MOCK_SETTLEMENT_RECEIPTS: tuple[SettlementReceiptKind, ...] = (
    SettlementReceiptKind.MOCK_CLOSURE,
    SettlementReceiptKind.MOCK_RELEASE,
)


class SettlementCheck(BaseModel):
    """One append-only settlement ledger check bound to a case version.

    Frozen value object.  ``zero_balance_confirmed`` is COMPUTED by the domain
    (Decimal comparison of the three outstanding totals) and re-asserted here by
    a model validator; the database mirrors it with a text CHECK made sound by
    the canonical ``'0'`` amount form.  The check is recorded only when settlement
    is ELIGIBLE, so a persisted check always has ``zero_balance_confirmed`` True.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    case_id: CaseId
    case_version: int = Field(ge=1)
    outstanding_principal: str
    outstanding_interest: str
    outstanding_fees: str
    open_exception_count: int = Field(ge=0)
    zero_balance_confirmed: bool
    recorded_by: UUID
    created_at: datetime | None = None

    @field_validator(
        "outstanding_principal", "outstanding_interest", "outstanding_fees"
    )
    @classmethod
    def _canonical(cls, value: str) -> str:
        return _canonical_nonneg_amount(value)

    @model_validator(mode="after")
    def _zero_balance_is_consistent(self) -> SettlementCheck:
        computed = (
            Decimal(self.outstanding_principal) == 0
            and Decimal(self.outstanding_interest) == 0
            and Decimal(self.outstanding_fees) == 0
        )
        if computed != self.zero_balance_confirmed:
            raise ValueError(
                "zero_balance_confirmed must equal (all outstanding totals == 0)"
            )
        return self


# -- recovery (14B) models ----------------------------------------------------


class RecoveryStatus(StrEnum):
    """The CLOSED recovery-case status set (design giai đoạn 14).

    Only two states are in scope: ``PREPARING`` (evidence pack + options being
    assembled) and ``STRATEGY_APPROVED`` (a human authority approved the recovery
    strategy through ``HG_RECOVERY_STRATEGY_APPROVED``).  Real enforcement,
    litigation and write-off states are OUT OF SCOPE -- they do not exist here.
    """

    PREPARING = "PREPARING"
    STRATEGY_APPROVED = "STRATEGY_APPROVED"


class RecoveryOption(BaseModel):
    """One structured recovery option: a human-authored choice + consequences.

    Consequences and dependencies are STRUCTURED TEXT, not executable steps.
    Recording an option NEVER performs it; enforcement is out of scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label_vi: str = Field(min_length=1, max_length=400)
    description_vi: str = Field(min_length=1, max_length=4000)
    consequences_vi: str = Field(min_length=1, max_length=4000)
    dependencies_vi: str | None = Field(default=None, max_length=4000)


class RecoveryCase(BaseModel):
    """One append-only recovery case opened from a trigger + human escalation.

    Frozen value object.  ``evidence_refs`` is a NON-EMPTY pack of REFERENCES
    (uuid or text refs, never document bodies) and ``options`` is a NON-EMPTY
    tuple of structured options.  ``escalated_by`` + ``escalation_rationale_vi``
    are the mandatory human escalation record; the deterministic trigger alone
    can never open a case.  ``status`` starts ``PREPARING``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    case_id: CaseId
    case_version: int = Field(ge=1)
    trigger_summary_vi: str = Field(min_length=1, max_length=4000)
    escalated_by: UUID
    escalation_rationale_vi: str = Field(min_length=1, max_length=4000)
    status: RecoveryStatus = RecoveryStatus.PREPARING
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    options: tuple[RecoveryOption, ...] = Field(min_length=1)
    created_at: datetime | None = None

    @field_validator("evidence_refs")
    @classmethod
    def _non_blank_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(ref.strip() for ref in value)
        if any(not ref for ref in cleaned):
            raise ValueError("evidence reference must be a non-empty text/uuid ref")
        return cleaned


__all__ = [
    "MOCK_SETTLEMENT_RECEIPTS",
    "SUSTAINED_SHORTFALL_PERIODS",
    "LedgerRecoverySnapshot",
    "LedgerSettlementSnapshot",
    "RecoveryCase",
    "RecoveryOption",
    "RecoveryStatus",
    "RecoveryTriggerAssessment",
    "RecoveryTriggerInputs",
    "SettlementCheck",
    "SettlementEligibility",
    "SettlementLedgerInputs",
    "SettlementReceiptKind",
    "derive_recovery_trigger",
    "derive_settlement_eligible",
]
