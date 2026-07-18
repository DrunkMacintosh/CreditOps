"""Stage-12 post-credit monitoring: the deterministic, clock-free domain core.

Master design section 5 giai đoạn 12 ("Quản lý khoản vay và giám sát sau cấp tín
dụng").  A Post-Credit Monitoring Agent is a SUPPORT role only: it summarises and
explains, it never classifies debt and never applies a control.  This module
holds the pure, deterministic, model-free core of that stage:

- ``ObligationFrequency`` + ``ObligationSpec`` -- a frozen declarative spec, and
  ``generate_obligations(spec, from_date, count)`` -- a PURE schedule engine that
  produces monitoring obligations by DATE ARITHMETIC alone.  It reads NO clock:
  the anchor ``from_date`` is supplied by the caller and every due date is
  computed as ``anchor + k * period`` with correct month-end clamping, so the
  same inputs always yield the same schedule.

- ``ComparisonOperator`` + ``CovenantThreshold`` + ``evaluate_covenant`` -- a
  covenant's pass/fail is EXACTLY the declared comparison of supplied numeric
  inputs against a threshold that lives ON the covenant record (versioned
  synthetic data a human supplies when creating the covenant -- NEVER hard-coded
  here).  The comparison is done by exact-Decimal cross-multiplication
  (``numerator OP threshold*denominator``, ``denominator > 0``), so there is no
  division rounding: the verdict is exact and the arithmetic is echoed back on
  the ``CovenantEvaluation`` result.  ``evaluate_covenant`` renders NO judgement
  beyond that one declared comparison.

- ``AlertRule`` + ``AlertStatus`` + ``ALLOWED_ALERT_TRANSITIONS`` -- early-warning
  alert CANDIDATES are raised only by the two DETERMINISTIC rules below (never by
  a model): ``COVENANT_BREACH`` (a covenant test failed its declared comparison)
  and ``OVERDUE_OBLIGATION`` (a monitoring observation was recorded after its
  obligation's due date).  The pure predicates ``covenant_breach_detected`` /
  ``obligation_overdue`` are the whole of the "rule engine".  An alert's
  lifecycle is ``OPEN -> {ACKNOWLEDGED, ESCALATED, DISMISSED_BY_HUMAN}`` and on to
  ``DISMISSED_BY_HUMAN`` -- every disposition is a HUMAN-only act carrying a
  mandatory rationale (the human control of this stage; there is no gate).

NO OFFICIAL DEBT CLASSIFICATION: there is deliberately NO status, enum, column or
field anywhere in this stage that classifies a debt (no nhóm nợ / NPL / provision
bucket).  The spec forbids it -- the agent only surfaces early-warning signals for
a human, and formal classification stays OUT OF SCOPE.

TEMPORAL SEPARATION lives on the persisted observation (see
supabase/migrations/202607180022_monitoring.sql and the port): ``effectiveAt``
(when the observed fact holds in the world), ``observedAt`` (when a human/source
observed it) are caller data; ``recordedAt`` is the trusted database clock.  Only
``effectiveAt <= observedAt`` is a deterministic invariant (validated here and in
the DB); ``observedAt <= recordedAt`` is NOT enforced because it compares an
untrusted client clock to the DB clock.

PROPOSED / SYNTHETIC: the frequency set, the operator set, the alert taxonomy and
lifecycle, and every threshold value are a prototype configuration with NO
official SHB monitoring-policy mapping; all reconfigured when an official source
exists.

All customer data, covenants, and thresholds in this project are synthetic and
created solely for demonstration.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId

type MonitoringObligationId = UUID
type MonitoringObservationId = UUID
type CovenantId = UUID
type CovenantTestId = UUID
type EarlyWarningAlertId = UUID


# -- monitoring obligations (the deterministic schedule engine) ----------------


class ObligationFrequency(StrEnum):
    """The CLOSED set of monitoring-obligation frequencies (PROPOSED synthetic).

    Exactly the two cadences the schedule engine understands.  Each maps to a
    fixed number of months so ``generate_obligations`` is pure date arithmetic.
    """

    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


#: Months added per period for each frequency -- the only place the cadence is
#: turned into arithmetic.
_FREQUENCY_MONTHS: Mapping[ObligationFrequency, int] = {
    ObligationFrequency.MONTHLY: 1,
    ObligationFrequency.QUARTERLY: 3,
}


class ObligationSpec(BaseModel):
    """A frozen declarative monitoring-obligation spec.

    ``generate_obligations`` turns ONE spec plus an anchor date and a count into a
    deterministic run of obligations.  The spec itself carries no dates or clock
    -- it is pure configuration a human authors.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    frequency: ObligationFrequency
    requirement_text_vi: str = Field(min_length=1, max_length=4000)


class GeneratedObligation(BaseModel):
    """One obligation emitted by the pure schedule engine (no identity yet).

    ``sequence`` is 1-based within the generated run; ``due_date`` is the clamped
    anchor-plus-``k``-periods date.  The API assigns durable identity when it
    persists the run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=1)
    due_date: date
    frequency: ObligationFrequency
    requirement_text_vi: str = Field(min_length=1, max_length=4000)


def _add_months(anchor: date, months: int) -> date:
    """Return ``anchor`` shifted forward by ``months`` with month-end clamping.

    The day is clamped to the last valid day of the TARGET month (so 31 Jan + 1
    month is 28/29 Feb, and 31 Jan + 2 months is 31 Mar -- computed from the
    original anchor each time, never compounding a prior clamp).  Pure: no clock.
    """

    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor.day, last_day))


def generate_obligations(
    spec: ObligationSpec, from_date: date, count: int
) -> tuple[GeneratedObligation, ...]:
    """Deterministically generate ``count`` obligations from ``spec``.

    ``from_date`` is the anchor (e.g. the disbursement date); the ``k``-th
    obligation (``k`` = 1..count) is due ``from_date`` plus ``k`` periods, so the
    first report falls one whole period after the anchor.  Month-ends are clamped
    (see ``_add_months``).  Pure and clock-free: identical inputs always yield an
    identical schedule.
    """

    if count < 1:
        raise ValueError("count must be a positive integer")
    period = _FREQUENCY_MONTHS[spec.frequency]
    return tuple(
        GeneratedObligation(
            sequence=k,
            due_date=_add_months(from_date, k * period),
            frequency=spec.frequency,
            requirement_text_vi=spec.requirement_text_vi,
        )
        for k in range(1, count + 1)
    )


# -- covenant evaluation (exact-Decimal declared comparison) -------------------


class ComparisonOperator(StrEnum):
    """The CLOSED set of covenant comparison operators (PROPOSED synthetic).

    A covenant PASSES when ``ratio OP threshold`` holds, where ``ratio`` is the
    supplied ``numerator / denominator``.  The comparison is evaluated exactly by
    cross-multiplication (``denominator > 0``), never by floating division.
    """

    GTE = "GTE"
    GT = "GT"
    LTE = "LTE"
    LT = "LT"
    EQ = "EQ"


class CovenantThreshold(BaseModel):
    """The declared threshold that lives ON a covenant record.

    ``threshold_value`` and ``operator`` are VERSIONED SYNTHETIC data a human
    supplies when creating the covenant (``threshold_version`` bumps on every
    re-statement).  Nothing here is hard-coded: ``evaluate_covenant`` reads the
    comparison entirely from this record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_key: str = Field(min_length=1, max_length=200)
    operator: ComparisonOperator
    threshold_value: Decimal
    threshold_version: int = Field(ge=1)


class CovenantEvaluation(BaseModel):
    """The typed result of one covenant test with the exact arithmetic echoed.

    ``comparison_lhs`` (= numerator) and ``comparison_rhs`` (= threshold_value *
    denominator) are the exact terms actually compared, and ``passed`` is EXACTLY
    ``comparison_lhs OP comparison_rhs`` -- no verdict beyond the declared
    comparison.  Everything is Decimal, so the check is exact (no division).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_key: str
    operator: ComparisonOperator
    numerator: Decimal
    denominator: Decimal
    threshold_value: Decimal
    threshold_version: int
    comparison_lhs: Decimal
    comparison_rhs: Decimal
    passed: bool


def _compare(lhs: Decimal, operator: ComparisonOperator, rhs: Decimal) -> bool:
    match operator:
        case ComparisonOperator.GTE:
            return lhs >= rhs
        case ComparisonOperator.GT:
            return lhs > rhs
        case ComparisonOperator.LTE:
            return lhs <= rhs
        case ComparisonOperator.LT:
            return lhs < rhs
        case ComparisonOperator.EQ:
            return lhs == rhs
    assert_never(operator)


def evaluate_covenant(
    numerator: Decimal, denominator: Decimal, threshold: CovenantThreshold
) -> CovenantEvaluation:
    """Evaluate ``numerator / denominator`` against ``threshold``, exactly.

    Requires ``denominator > 0`` so the cross-multiplied comparison
    ``numerator OP threshold_value * denominator`` is equivalent to
    ``ratio OP threshold_value`` (a negative denominator would flip the
    inequality).  An absolute-value covenant uses ``denominator = 1``.  The
    verdict is EXACTLY the declared comparison and the arithmetic is echoed on the
    result; this function makes no other judgement.
    """

    if denominator <= 0:
        raise ValueError("denominator must be strictly positive")
    comparison_rhs = threshold.threshold_value * denominator
    return CovenantEvaluation(
        metric_key=threshold.metric_key,
        operator=threshold.operator,
        numerator=numerator,
        denominator=denominator,
        threshold_value=threshold.threshold_value,
        threshold_version=threshold.threshold_version,
        comparison_lhs=numerator,
        comparison_rhs=comparison_rhs,
        passed=_compare(numerator, threshold.operator, comparison_rhs),
    )


# -- monitoring observations (longitudinal, temporally separated) --------------


class MonitoringObservation(BaseModel):
    """One append-only longitudinal observation with separated timestamps.

    ``effective_at`` (fact-valid time) and ``observed_at`` (observation time) are
    CALLER data; ``recorded_at`` is the database clock, assigned on persistence
    (never carried here).  The only deterministic temporal invariant is
    ``effective_at <= observed_at`` -- ``observed_at <= recorded_at`` cannot be
    deterministically enforced against an untrusted client clock, so it is NOT a
    validation (documented, module docstring).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: MonitoringObservationId
    case_id: CaseId
    case_version: int = Field(ge=1)
    obligation_id: MonitoringObligationId | None = None
    observation_type_vi: str = Field(min_length=1, max_length=200)
    body_vi: str = Field(min_length=1, max_length=8000)
    effective_at: datetime
    observed_at: datetime
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_temporal_order(self) -> MonitoringObservation:
        if self.effective_at > self.observed_at:
            raise ValueError("effective_at must be <= observed_at")
        return self


# -- covenants -----------------------------------------------------------------


class Covenant(BaseModel):
    """A frozen covenant record carrying its own declared threshold.

    The threshold (metric, operator, value, version) IS on the record; a covenant
    test loads it and evaluates supplied inputs against it.  Covenants are
    append-only: re-stating a threshold writes a new row with a higher
    ``threshold_version``, never an in-place edit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: CovenantId
    case_id: CaseId
    case_version: int = Field(ge=1)
    name_vi: str = Field(min_length=1, max_length=400)
    threshold: CovenantThreshold


# -- early-warning alerts (deterministic rules + human dispositions) -----------


class AlertRule(StrEnum):
    """The CLOSED set of DETERMINISTIC rules that may raise an alert candidate.

    A model never raises an alert: only these two rules do, each keyed on
    caller-supplied data inside the endpoint transaction that produced it.
    """

    COVENANT_BREACH = "COVENANT_BREACH"
    OVERDUE_OBLIGATION = "OVERDUE_OBLIGATION"


class AlertStatus(StrEnum):
    """The CLOSED early-warning-alert lifecycle (PROPOSED synthetic).

    ``OPEN`` is the only status a deterministic rule may create; every move out of
    it is a HUMAN disposition (see ``ALLOWED_ALERT_TRANSITIONS``).
    """

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ESCALATED = "ESCALATED"
    DISMISSED_BY_HUMAN = "DISMISSED_BY_HUMAN"


#: The deterministic alert lifecycle map (PROPOSED synthetic edges).  A pair
#: absent here is FORBIDDEN -- there is no implicit edge and no self-transition.
#: All edges are HUMAN dispositions requiring a rationale:
#:
#: - ``OPEN`` -> a human ``ACKNOWLEDGED`` it (owns it), ``ESCALATED`` it
#:   (raised it to a higher authority) or ``DISMISSED_BY_HUMAN`` (ruled it not a
#:   concern).
#: - ``ACKNOWLEDGED`` -> may still be ``ESCALATED`` or ``DISMISSED_BY_HUMAN``.
#: - ``ESCALATED`` -> may only be ``DISMISSED_BY_HUMAN`` (closed after handling).
#: - ``DISMISSED_BY_HUMAN`` is fully terminal.
ALLOWED_ALERT_TRANSITIONS: Mapping[AlertStatus, frozenset[AlertStatus]] = {
    AlertStatus.OPEN: frozenset(
        {
            AlertStatus.ACKNOWLEDGED,
            AlertStatus.ESCALATED,
            AlertStatus.DISMISSED_BY_HUMAN,
        }
    ),
    AlertStatus.ACKNOWLEDGED: frozenset(
        {AlertStatus.ESCALATED, AlertStatus.DISMISSED_BY_HUMAN}
    ),
    AlertStatus.ESCALATED: frozenset({AlertStatus.DISMISSED_BY_HUMAN}),
    AlertStatus.DISMISSED_BY_HUMAN: frozenset(),
}


def is_alert_transition_allowed(
    from_status: AlertStatus, to_status: AlertStatus
) -> bool:
    """Whether ``from_status -> to_status`` is an explicit allowed disposition.

    Self-transitions and any pair absent from ``ALLOWED_ALERT_TRANSITIONS`` are
    rejected: the map is exhaustive and there is no implicit edge.
    """

    return to_status in ALLOWED_ALERT_TRANSITIONS.get(from_status, frozenset())


class EarlyWarningAlert(BaseModel):
    """One early-warning alert raised by a deterministic rule.

    Bound to exactly one source: a failed covenant test (``COVENANT_BREACH``,
    ``source_covenant_test_id``) or a late observation against an obligation
    (``OVERDUE_OBLIGATION``, ``source_obligation_id`` + ``source_observation_id``).
    ``detail_vi`` is a DETERMINISTIC one-line description built from the rule
    inputs -- not a model summary.  A new alert is always ``OPEN``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EarlyWarningAlertId
    case_id: CaseId
    case_version: int = Field(ge=1)
    rule: AlertRule
    status: AlertStatus = AlertStatus.OPEN
    detail_vi: str = Field(min_length=1, max_length=2000)
    source_covenant_test_id: CovenantTestId | None = None
    source_obligation_id: MonitoringObligationId | None = None
    source_observation_id: MonitoringObservationId | None = None


def covenant_breach_detected(evaluation: CovenantEvaluation) -> bool:
    """The COVENANT_BREACH rule: True iff the declared comparison FAILED."""

    return not evaluation.passed


def obligation_overdue(due_date: date, observed_at: datetime) -> bool:
    """The OVERDUE_OBLIGATION rule: True iff the observation date is past due.

    Deterministic and clock-free: compares the caller-supplied ``observed_at``
    calendar date to the obligation's ``due_date``.  A production sweep would use
    a clock to fence obligations that were never reported at all; here the rule
    fires on the deterministic event of a LATE observation being recorded.
    """

    return observed_at.date() > due_date


def build_breach_detail(evaluation: CovenantEvaluation) -> str:
    """Deterministic Vietnamese detail line for a covenant breach (no model)."""

    return (
        f"Vi phạm cam kết {evaluation.metric_key}: "
        f"{evaluation.numerator}/{evaluation.denominator} không thỏa điều kiện "
        f"{evaluation.operator.value} {evaluation.threshold_value} "
        f"(phiên bản ngưỡng {evaluation.threshold_version})."
    )


def build_overdue_detail(due_date: date, observed_at: datetime) -> str:
    """Deterministic Vietnamese detail line for an overdue obligation (no model)."""

    return (
        f"Nghĩa vụ giám sát quá hạn: quan sát ngày {observed_at.date().isoformat()} "
        f"sau hạn báo cáo {due_date.isoformat()}."
    )


__all__ = [
    "ALLOWED_ALERT_TRANSITIONS",
    "AlertRule",
    "AlertStatus",
    "ComparisonOperator",
    "Covenant",
    "CovenantEvaluation",
    "CovenantId",
    "CovenantTestId",
    "CovenantThreshold",
    "EarlyWarningAlert",
    "EarlyWarningAlertId",
    "GeneratedObligation",
    "MonitoringObligationId",
    "MonitoringObservation",
    "MonitoringObservationId",
    "ObligationFrequency",
    "ObligationSpec",
    "build_breach_detail",
    "build_overdue_detail",
    "covenant_breach_detected",
    "evaluate_covenant",
    "generate_obligations",
    "is_alert_transition_allowed",
    "obligation_overdue",
]
