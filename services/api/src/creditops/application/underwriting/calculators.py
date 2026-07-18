"""Deterministic financial calculators for the Credit Underwriting Agent.

Every material number in a maker assessment must come from these pure,
side-effect-free, ``Decimal``-based tools — never from the LLM (ADR-0001,
docs/AGENT_ARCHITECTURE.md "Agents versus deterministic tools").  Each result
carries the confirmed-fact / document-region references of its inputs so
citations survive into the assessment output, and a deterministic
``result_id`` (a hash of calculator name + canonical inputs) so redelivered
executions reproduce identical results.

A calculation that cannot be performed (missing input, zero denominator,
insufficient series) returns an explicit ``NOT_COMPUTABLE`` outcome with a
human-readable reason.  It never silently yields ``0`` — an absent number must
surface as an Evidence Gap, not a fabricated value.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_QUANT = Decimal("0.000001")
_MONEY = Decimal("0.01")
_DAYS_PER_YEAR = Decimal(365)


class CalculatorValidationError(ValueError):
    """A structurally invalid input was supplied.

    This is distinct from *missing evidence*: a missing (``None``) input yields
    a typed ``NOT_COMPUTABLE`` outcome recording which inputs were absent, never
    an exception.  A validation error means a value that *is* present is not a
    legal input at all — a negative amount, a zero or fractional loan term — and
    such inputs are rejected rather than silently coerced.
    """


class FactRef(BaseModel):
    """Reference to the evidence a calculator input was read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["CONFIRMED_FACT", "DOCUMENT_REGION"]
    ref_id: str = Field(min_length=1)


class CalculatorInput(BaseModel):
    """One named ``Decimal`` input plus the evidence references behind it.

    ``value`` may be ``None`` when the underlying evidence is missing; the
    calculator then reports NOT_COMPUTABLE instead of guessing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    value: Decimal | None = None
    fact_refs: tuple[FactRef, ...] = ()


class ComputedOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["COMPUTED"] = "COMPUTED"
    value: Decimal


class NotComputableOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["NOT_COMPUTABLE"] = "NOT_COMPUTABLE"
    reason: str = Field(min_length=1)


CalculatorOutcome = ComputedOutcome | NotComputableOutcome


class CalculatorResult(BaseModel):
    """A single deterministic calculation with full input provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: str = Field(min_length=1)
    inputs: tuple[CalculatorInput, ...]
    outcome: CalculatorOutcome

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        seen: dict[tuple[str, str], FactRef] = {}
        for calculator_input in self.inputs:
            for ref in calculator_input.fact_refs:
                seen.setdefault((ref.kind, ref.ref_id), ref)
        return tuple(seen.values())


def _canonical(value: Decimal | None) -> str:
    return "null" if value is None else format(value.normalize(), "f")


def _result_id(calculator: str, inputs: Sequence[CalculatorInput]) -> str:
    parts = [calculator]
    for calculator_input in sorted(inputs, key=lambda item: item.name):
        refs = ",".join(
            f"{ref.kind}:{ref.ref_id}"
            for ref in sorted(
                calculator_input.fact_refs, key=lambda ref: (ref.kind, ref.ref_id)
            )
        )
        parts.append(
            f"{calculator_input.name}={_canonical(calculator_input.value)}[{refs}]"
        )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"calc_{digest[:32]}"


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def _build(
    calculator: str,
    inputs: Sequence[CalculatorInput],
    outcome: CalculatorOutcome,
) -> CalculatorResult:
    return CalculatorResult(
        result_id=_result_id(calculator, inputs),
        calculator=calculator,
        inputs=tuple(inputs),
        outcome=outcome,
    )


def _missing(inputs: Sequence[CalculatorInput]) -> str | None:
    names = [item.name for item in inputs if item.value is None]
    if not names:
        return None
    return f"not computable: missing input {', '.join(sorted(names))}"


def _ratio(
    calculator: str,
    numerator: CalculatorInput,
    denominator: CalculatorInput,
    *,
    scale: Decimal = Decimal(1),
    extra_inputs: Sequence[CalculatorInput] = (),
) -> CalculatorResult:
    inputs = [numerator, denominator, *extra_inputs]
    missing = _missing(inputs)
    if missing is not None:
        return _build(calculator, inputs, NotComputableOutcome(reason=missing))
    assert numerator.value is not None and denominator.value is not None
    if denominator.value == 0:
        return _build(
            calculator,
            inputs,
            NotComputableOutcome(
                reason=f"not computable: division by zero ({denominator.name} is zero)"
            ),
        )
    value = _quantize(numerator.value * scale / denominator.value)
    return _build(calculator, inputs, ComputedOutcome(value=value))


def _difference_input(
    name: str, left: CalculatorInput, right: CalculatorInput
) -> CalculatorInput:
    """Derived input (left - right) that unions the provenance of both parts."""
    value: Decimal | None = None
    if left.value is not None and right.value is not None:
        value = left.value - right.value
    return CalculatorInput(
        name=name, value=value, fact_refs=(*left.fact_refs, *right.fact_refs)
    )


# --- Liquidity -----------------------------------------------------------


def current_ratio(
    current_assets: CalculatorInput, current_liabilities: CalculatorInput
) -> CalculatorResult:
    return _ratio("current_ratio", current_assets, current_liabilities)


def quick_ratio(
    current_assets: CalculatorInput,
    inventory: CalculatorInput,
    current_liabilities: CalculatorInput,
) -> CalculatorResult:
    numerator = _difference_input(
        "current_assets_less_inventory", current_assets, inventory
    )
    if current_assets.value is None or inventory.value is None:
        inputs = [current_assets, inventory, current_liabilities]
        missing = _missing(inputs)
        assert missing is not None
        return _build("quick_ratio", inputs, NotComputableOutcome(reason=missing))
    return _ratio("quick_ratio", numerator, current_liabilities)


# --- Leverage ------------------------------------------------------------


def debt_to_equity(
    total_debt: CalculatorInput, total_equity: CalculatorInput
) -> CalculatorResult:
    return _ratio("debt_to_equity", total_debt, total_equity)


def debt_to_assets(
    total_debt: CalculatorInput, total_assets: CalculatorInput
) -> CalculatorResult:
    return _ratio("debt_to_assets", total_debt, total_assets)


# --- Profitability -------------------------------------------------------


def gross_margin(
    gross_profit: CalculatorInput, revenue: CalculatorInput
) -> CalculatorResult:
    return _ratio("gross_margin", gross_profit, revenue)


def operating_margin(
    operating_profit: CalculatorInput, revenue: CalculatorInput
) -> CalculatorResult:
    return _ratio("operating_margin", operating_profit, revenue)


def net_margin(
    net_profit: CalculatorInput, revenue: CalculatorInput
) -> CalculatorResult:
    return _ratio("net_margin", net_profit, revenue)


def return_on_assets(
    net_profit: CalculatorInput, total_assets: CalculatorInput
) -> CalculatorResult:
    return _ratio("return_on_assets", net_profit, total_assets)


def return_on_equity(
    net_profit: CalculatorInput, total_equity: CalculatorInput
) -> CalculatorResult:
    return _ratio("return_on_equity", net_profit, total_equity)


# --- Activity ------------------------------------------------------------


def receivable_days(
    accounts_receivable: CalculatorInput, revenue: CalculatorInput
) -> CalculatorResult:
    return _ratio(
        "receivable_days", accounts_receivable, revenue, scale=_DAYS_PER_YEAR
    )


def inventory_days(
    inventory: CalculatorInput, cost_of_goods_sold: CalculatorInput
) -> CalculatorResult:
    return _ratio("inventory_days", inventory, cost_of_goods_sold, scale=_DAYS_PER_YEAR)


def payable_days(
    accounts_payable: CalculatorInput, cost_of_goods_sold: CalculatorInput
) -> CalculatorResult:
    return _ratio("payable_days", accounts_payable, cost_of_goods_sold, scale=_DAYS_PER_YEAR)


def asset_turnover(
    revenue: CalculatorInput, total_assets: CalculatorInput
) -> CalculatorResult:
    return _ratio("asset_turnover", revenue, total_assets)


# --- Trend analysis ------------------------------------------------------


class TrendPoint(BaseModel):
    """One labelled period value in a series, with its evidence references."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: str = Field(min_length=1)
    value: Decimal | None = None
    fact_refs: tuple[FactRef, ...] = ()


class TrendStep(BaseModel):
    """Period-over-period delta and growth rate between adjacent points."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_period: str
    to_period: str
    delta: CalculatorOutcome
    growth_rate: CalculatorOutcome


class TrendResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["trend_analysis"] = "trend_analysis"
    metric: str = Field(min_length=1)
    points: tuple[TrendPoint, ...]
    steps: tuple[TrendStep, ...]

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        seen: dict[tuple[str, str], FactRef] = {}
        for point in self.points:
            for ref in point.fact_refs:
                seen.setdefault((ref.kind, ref.ref_id), ref)
        return tuple(seen.values())


def trend_analysis(metric: str, points: Sequence[TrendPoint]) -> TrendResult:
    """Deltas and growth rates over a chronologically ordered series."""
    inputs = [
        CalculatorInput(
            name=f"{metric}:{point.period}", value=point.value, fact_refs=point.fact_refs
        )
        for point in points
    ]
    steps: list[TrendStep] = []
    for previous, current in zip(points, points[1:], strict=False):
        delta: CalculatorOutcome
        growth: CalculatorOutcome
        if previous.value is None or current.value is None:
            missing_periods = [
                point.period
                for point in (previous, current)
                if point.value is None
            ]
            reason = (
                "not computable: missing value for period "
                + ", ".join(missing_periods)
            )
            delta = NotComputableOutcome(reason=reason)
            growth = NotComputableOutcome(reason=reason)
        else:
            delta = ComputedOutcome(value=_quantize(current.value - previous.value))
            if previous.value == 0:
                growth = NotComputableOutcome(
                    reason=(
                        "not computable: division by zero "
                        f"(base period {previous.period} is zero)"
                    )
                )
            else:
                growth = ComputedOutcome(
                    value=_quantize(
                        (current.value - previous.value) / abs(previous.value)
                    )
                )
        steps.append(
            TrendStep(
                from_period=previous.period,
                to_period=current.period,
                delta=delta,
                growth_rate=growth,
            )
        )
    return TrendResult(
        result_id=_result_id(f"trend_analysis:{metric}", inputs),
        metric=metric,
        points=tuple(points),
        steps=tuple(steps),
    )


# --- Cash flow / working capital ----------------------------------------


def cash_conversion_cycle(
    receivable_days_result: CalculatorResult,
    inventory_days_result: CalculatorResult,
    payable_days_result: CalculatorResult,
) -> CalculatorResult:
    """CCC = receivable days + inventory days - payable days.

    Composes prior deterministic results; provenance is the union of the
    component results' inputs.
    """
    components = (
        receivable_days_result,
        inventory_days_result,
        payable_days_result,
    )
    inputs = [
        calculator_input for result in components for calculator_input in result.inputs
    ]
    not_computable = [
        result.calculator
        for result in components
        if isinstance(result.outcome, NotComputableOutcome)
    ]
    if not_computable:
        return _build(
            "cash_conversion_cycle",
            inputs,
            NotComputableOutcome(
                reason=(
                    "not computable: component not computable "
                    f"({', '.join(not_computable)})"
                )
            ),
        )
    total = Decimal(0)
    for result, sign in zip(components, (1, 1, -1), strict=True):
        assert isinstance(result.outcome, ComputedOutcome)
        total += result.outcome.value * sign
    return _build(
        "cash_conversion_cycle", inputs, ComputedOutcome(value=_quantize(total))
    )


def working_capital_need(
    annual_operating_outlay: CalculatorInput,
    cash_conversion_cycle_result: CalculatorResult,
) -> CalculatorResult:
    """Working-capital need = annual operating outlay x CCC / 365.

    ASSUMPTION: this is the standard textbook formula on synthetic data; no
    official SHB formula has been supplied (docs/OPEN_QUESTIONS.md).
    """
    inputs = [annual_operating_outlay, *cash_conversion_cycle_result.inputs]
    if isinstance(cash_conversion_cycle_result.outcome, NotComputableOutcome):
        return _build(
            "working_capital_need",
            inputs,
            NotComputableOutcome(
                reason="not computable: cash conversion cycle not computable"
            ),
        )
    if annual_operating_outlay.value is None:
        return _build(
            "working_capital_need",
            inputs,
            NotComputableOutcome(
                reason=(
                    "not computable: missing input "
                    f"{annual_operating_outlay.name}"
                )
            ),
        )
    value = _quantize(
        annual_operating_outlay.value
        * cash_conversion_cycle_result.outcome.value
        / _DAYS_PER_YEAR
    )
    return _build("working_capital_need", inputs, ComputedOutcome(value=value))


def working_capital_gap(
    working_capital_need_result: CalculatorResult,
    own_working_capital: CalculatorInput,
    other_funding_sources: CalculatorInput,
) -> CalculatorResult:
    """Gap = working-capital need - own working capital - other funding."""
    inputs = [
        *working_capital_need_result.inputs,
        own_working_capital,
        other_funding_sources,
    ]
    if isinstance(working_capital_need_result.outcome, NotComputableOutcome):
        return _build(
            "working_capital_gap",
            inputs,
            NotComputableOutcome(
                reason="not computable: working capital need not computable"
            ),
        )
    missing = _missing([own_working_capital, other_funding_sources])
    if missing is not None:
        return _build("working_capital_gap", inputs, NotComputableOutcome(reason=missing))
    assert own_working_capital.value is not None
    assert other_funding_sources.value is not None
    value = _quantize(
        working_capital_need_result.outcome.value
        - own_working_capital.value
        - other_funding_sources.value
    )
    return _build("working_capital_gap", inputs, ComputedOutcome(value=value))


# --- Scenario tool -------------------------------------------------------


class ScenarioAdjustment(BaseModel):
    """A named, explicit downside adjustment to one base metric.

    ``relative_change`` is a signed fraction (-0.2 = 20% reduction) applied
    multiplicatively; ``absolute_change`` is added afterwards.  Nothing here is
    probabilistic — the maker may only recompute under adjustments a human can
    read and challenge.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str = Field(min_length=1)
    relative_change: Decimal = Decimal(0)
    absolute_change: Decimal = Decimal(0)


class ScenarioMetricOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    base: CalculatorOutcome
    adjusted: CalculatorOutcome


class ScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["scenario_projection"] = "scenario_projection"
    scenario_name: str = Field(min_length=1)
    adjustments: tuple[ScenarioAdjustment, ...]
    metrics: tuple[ScenarioMetricOutcome, ...]
    inputs: tuple[CalculatorInput, ...]

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        seen: dict[tuple[str, str], FactRef] = {}
        for calculator_input in self.inputs:
            for ref in calculator_input.fact_refs:
                seen.setdefault((ref.kind, ref.ref_id), ref)
        return tuple(seen.values())


def scenario_projection(
    scenario_name: str,
    base_metrics: Sequence[CalculatorInput],
    adjustments: Sequence[ScenarioAdjustment],
) -> ScenarioResult:
    """Recompute base metrics under explicit named downside adjustments.

    Deterministic: adjusted = base * (1 + relative_change) + absolute_change.
    A metric with no matching adjustment passes through unchanged; an
    adjustment naming an unknown or missing metric yields NOT_COMPUTABLE for
    that metric rather than inventing a base value.
    """
    by_metric = {item.name: item for item in base_metrics}
    adjustment_by_metric: dict[str, ScenarioAdjustment] = {}
    for adjustment in adjustments:
        adjustment_by_metric[adjustment.metric] = adjustment

    outcomes: list[ScenarioMetricOutcome] = []
    covered: set[str] = set()
    for base in base_metrics:
        covered.add(base.name)
        if base.value is None:
            missing: CalculatorOutcome = NotComputableOutcome(
                reason=f"not computable: missing input {base.name}"
            )
            outcomes.append(
                ScenarioMetricOutcome(metric=base.name, base=missing, adjusted=missing)
            )
            continue
        base_outcome = ComputedOutcome(value=_quantize(base.value))
        matched = adjustment_by_metric.get(base.name)
        if matched is None:
            outcomes.append(
                ScenarioMetricOutcome(
                    metric=base.name, base=base_outcome, adjusted=base_outcome
                )
            )
            continue
        adjusted_value = _quantize(
            base.value * (Decimal(1) + matched.relative_change)
            + matched.absolute_change
        )
        outcomes.append(
            ScenarioMetricOutcome(
                metric=base.name,
                base=base_outcome,
                adjusted=ComputedOutcome(value=adjusted_value),
            )
        )
    for metric_name in adjustment_by_metric:
        if metric_name not in by_metric:
            unknown = NotComputableOutcome(
                reason=f"not computable: missing input {metric_name}"
            )
            outcomes.append(
                ScenarioMetricOutcome(metric=metric_name, base=unknown, adjusted=unknown)
            )
    id_inputs = [
        *base_metrics,
        *[
            CalculatorInput(
                name=f"adjustment:{item.metric}",
                value=item.relative_change + item.absolute_change,
            )
            for item in adjustments
        ],
    ]
    return ScenarioResult(
        result_id=_result_id(f"scenario_projection:{scenario_name}", id_inputs),
        scenario_name=scenario_name,
        adjustments=tuple(adjustments),
        metrics=tuple(outcomes),
        inputs=tuple(base_metrics),
    )


# ==========================================================================
# Stage-4 underwriting groups 3-5 (docs/superpowers/specs/
# 2026-07-18-full-credit-lifecycle-agent-workflow-design.md, "Giai đoạn 4").
#
# The calculators below extend groups 3-5 of the maker's first pass:
#   - the working-capital *need* implied by the cash-conversion cycle;
#   - the reconciliation of that need against own funds, existing facilities
#     and the requested amount (surfaced as a signed difference, never a
#     verdict);
#   - an exact-decimal debt-service schedule;
#   - per-period repayment coverage; and
#   - declarative downside scenarios recomputing need/coverage.
#
# Every result model carries an ``inputs`` echo and a ``formula_version`` so
# the maker can cite the exact formula that produced each figure.  As with the
# ratio tools, thresholds (minimum DSCR, adequate coverage, acceptable gap)
# are OPEN QUESTIONS resolved by humans and versioned synthetic config, never
# hard-coded here: these functions COMPUTE, they do not JUDGE.
#
# NOTE ON NAMING: the earlier ``working_capital_need`` / ``working_capital_gap``
# above compose a pre-computed cash-conversion-cycle *result*.  The group-4
# calculator below (``working_capital_requirement``) instead derives the need
# directly from the operating drivers (day counts, projected revenue, COGS
# ratio) and carries a ``formula_version``; it is offered alongside — not in
# place of — the composed variant so existing callers stay intact.
# ==========================================================================


def _quantize_money(value: Decimal) -> Decimal:
    """Round a cash amount HALF_UP to the money quantum (2 decimal places)."""
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _union_fact_refs(inputs: Iterable[CalculatorInput]) -> tuple[FactRef, ...]:
    """De-duplicated union of the evidence references behind a set of inputs."""
    seen: dict[tuple[str, str], FactRef] = {}
    for calculator_input in inputs:
        for ref in calculator_input.fact_refs:
            seen.setdefault((ref.kind, ref.ref_id), ref)
    return tuple(seen.values())


def _reject_negative(calculator_input: CalculatorInput) -> None:
    """Reject a present-but-negative amount; ``None`` (missing) is left alone."""
    value = calculator_input.value
    if value is not None and value < 0:
        raise CalculatorValidationError(
            f"{calculator_input.name} must be non-negative, got {value}"
        )


def _validate_term(term_months: CalculatorInput) -> None:
    """Reject a present term that is not a whole, positive number of months."""
    value = term_months.value
    if value is None:
        return
    if value != value.to_integral_value():
        raise CalculatorValidationError(
            f"{term_months.name} must be a whole number of months, got {value}"
        )
    if value <= 0:
        raise CalculatorValidationError(
            f"{term_months.name} must be positive, got {value}"
        )


# --- Group 4: working-capital need --------------------------------------


class WorkingCapitalNeed(BaseModel):
    """Cash-conversion-cycle working-capital need with component provenance.

    ROUNDING POLICY: each component balance (receivables, inventory, payables)
    and the cash-conversion-cycle day count are rounded HALF_UP to 6 decimal
    places (the module ``_QUANT``, matching the existing working-capital
    tools).  The reported ``outcome`` need is the exact sum
    ``receivables + inventory - payables`` of those already-quantized
    components, so ``outcome`` reconciles to the components with no residual.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["working_capital_requirement"] = "working_capital_requirement"
    formula_version: Literal["wc-need-v1"] = "wc-need-v1"
    inputs: tuple[CalculatorInput, ...]
    outcome: CalculatorOutcome
    receivables: CalculatorOutcome
    inventory: CalculatorOutcome
    payables: CalculatorOutcome
    cash_conversion_days: CalculatorOutcome

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        return _union_fact_refs(self.inputs)


def working_capital_requirement(
    inventory_days: CalculatorInput,
    receivable_days: CalculatorInput,
    payable_days: CalculatorInput,
    projected_revenue: CalculatorInput,
    cogs_ratio: CalculatorInput,
) -> WorkingCapitalNeed:
    """Working-capital need from the operating drivers of the cash cycle.

    Receivables scale with projected revenue; inventory and payables scale with
    projected COGS (``projected_revenue * cogs_ratio``).  Each leg is one day
    count times the relevant daily flow (annual flow / 365):

        receivables = receivable_days * revenue / 365
        inventory   = inventory_days  * (revenue * cogs_ratio) / 365
        payables    = payable_days    * (revenue * cogs_ratio) / 365
        need        = receivables + inventory - payables

    ASSUMPTION: this is the standard operating-working-capital formula on
    synthetic data; no official SHB formula has been supplied
    (docs/OPEN_QUESTIONS.md).  A missing input yields NOT_COMPUTABLE recording
    which inputs were absent; a present-but-negative amount is rejected.
    """
    inputs = (
        inventory_days,
        receivable_days,
        payable_days,
        projected_revenue,
        cogs_ratio,
    )
    for calculator_input in inputs:
        _reject_negative(calculator_input)

    result_id = _result_id("working_capital_requirement", inputs)
    missing = _missing(list(inputs))
    if missing is not None:
        not_computable = NotComputableOutcome(reason=missing)
        return WorkingCapitalNeed(
            result_id=result_id,
            inputs=inputs,
            outcome=not_computable,
            receivables=not_computable,
            inventory=not_computable,
            payables=not_computable,
            cash_conversion_days=not_computable,
        )

    assert receivable_days.value is not None
    assert inventory_days.value is not None
    assert payable_days.value is not None
    assert projected_revenue.value is not None
    assert cogs_ratio.value is not None

    cogs = projected_revenue.value * cogs_ratio.value
    receivables = _quantize(
        receivable_days.value * projected_revenue.value / _DAYS_PER_YEAR
    )
    inventory = _quantize(inventory_days.value * cogs / _DAYS_PER_YEAR)
    payables = _quantize(payable_days.value * cogs / _DAYS_PER_YEAR)
    need = receivables + inventory - payables
    cash_conversion_days = _quantize(
        receivable_days.value + inventory_days.value - payable_days.value
    )
    return WorkingCapitalNeed(
        result_id=result_id,
        inputs=inputs,
        outcome=ComputedOutcome(value=need),
        receivables=ComputedOutcome(value=receivables),
        inventory=ComputedOutcome(value=inventory),
        payables=ComputedOutcome(value=payables),
        cash_conversion_days=ComputedOutcome(value=cash_conversion_days),
    )


class FinancingInputs(BaseModel):
    """The own-funds / facilities / requested-amount side of the reconciliation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    own_funds: CalculatorInput
    existing_facilities: CalculatorInput
    requested_amount: CalculatorInput


class FinancingGap(BaseModel):
    """Reconciliation of the working-capital need against available funding.

    ROUNDING POLICY: both figures are rounded HALF_UP to 6 decimal places
    (``_QUANT``).  ``implied_external_financing`` is the need net of own funds
    and existing facilities; ``requested_vs_implied_difference`` is
    ``requested_amount - implied_external_financing`` — a SIGNED difference
    (positive = requested exceeds the implied need, negative = requested is
    below it).  It is a comparison, not a verdict: whether any gap is
    acceptable is an OPEN QUESTION for human judgement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["financing_gap"] = "financing_gap"
    formula_version: Literal["financing-gap-v1"] = "financing-gap-v1"
    inputs: tuple[CalculatorInput, ...]
    implied_external_financing: CalculatorOutcome
    requested_vs_implied_difference: CalculatorOutcome

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        return _union_fact_refs(self.inputs)


def financing_gap(
    working_capital_need_result: WorkingCapitalNeed,
    own_funds: CalculatorInput,
    existing_facilities: CalculatorInput,
    requested_amount: CalculatorInput,
) -> FinancingGap:
    """External financing implied by the need, compared to the requested amount.

        implied_external_financing = need - own_funds - existing_facilities
        requested_vs_implied       = requested_amount - implied_external_financing

    NOT_COMPUTABLE propagates from an un-computable need or any missing funding
    input; present-but-negative funding amounts are rejected.
    """
    _reject_negative(own_funds)
    _reject_negative(existing_facilities)
    _reject_negative(requested_amount)

    inputs = (
        *working_capital_need_result.inputs,
        own_funds,
        existing_facilities,
        requested_amount,
    )
    result_id = _result_id("financing_gap", inputs)

    need_outcome = working_capital_need_result.outcome
    if isinstance(need_outcome, NotComputableOutcome):
        not_computable = NotComputableOutcome(
            reason="not computable: working capital need not computable"
        )
        return FinancingGap(
            result_id=result_id,
            inputs=inputs,
            implied_external_financing=not_computable,
            requested_vs_implied_difference=not_computable,
        )

    missing = _missing([own_funds, existing_facilities, requested_amount])
    if missing is not None:
        not_computable = NotComputableOutcome(reason=missing)
        return FinancingGap(
            result_id=result_id,
            inputs=inputs,
            implied_external_financing=not_computable,
            requested_vs_implied_difference=not_computable,
        )

    assert own_funds.value is not None
    assert existing_facilities.value is not None
    assert requested_amount.value is not None
    implied = _quantize(
        need_outcome.value - own_funds.value - existing_facilities.value
    )
    difference = _quantize(requested_amount.value - implied)
    return FinancingGap(
        result_id=result_id,
        inputs=inputs,
        implied_external_financing=ComputedOutcome(value=implied),
        requested_vs_implied_difference=ComputedOutcome(value=difference),
    )


# --- Group 5: debt service and repayment capacity -----------------------


RepaymentStyle = Literal["EQUAL_PRINCIPAL", "BALLOON"]


class DebtServiceRow(BaseModel):
    """One period of a debt-service schedule (all amounts at the money quantum)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int = Field(ge=1)
    principal: Decimal
    interest: Decimal
    balance: Decimal


class DebtServiceSchedule(BaseModel):
    """Exact-decimal amortisation / balloon schedule.

    ROUNDING POLICY: every cash amount (principal, interest, closing balance) is
    rounded HALF_UP to the money quantum (2 decimal places, ``_MONEY``).
    Monthly interest = opening balance * (annual_rate_percent / 100 / 12).  For
    EQUAL_PRINCIPAL the level instalment is ``round(principal / term)`` and the
    FINAL row repays the exact residual opening balance, so the schedule
    reconciles with no rounding drift: ``Σ principal == principal`` and the
    final closing balance is exactly zero.  For BALLOON every non-final row is
    interest-only and the final row repays the whole principal.  ``outcome``
    is the total debt service (Σ principal + Σ interest).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["debt_service_schedule"] = "debt_service_schedule"
    formula_version: Literal["debt-service-v1"] = "debt-service-v1"
    repayment_style: RepaymentStyle
    inputs: tuple[CalculatorInput, ...]
    outcome: CalculatorOutcome
    rows: tuple[DebtServiceRow, ...] = ()

    @property
    def total_principal(self) -> Decimal | None:
        if not self.rows:
            return None
        return sum((row.principal for row in self.rows), Decimal(0))

    @property
    def total_interest(self) -> Decimal | None:
        if not self.rows:
            return None
        return sum((row.interest for row in self.rows), Decimal(0))

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        return _union_fact_refs(self.inputs)


def debt_service_schedule(
    principal: CalculatorInput,
    annual_rate_percent: CalculatorInput,
    term_months: CalculatorInput,
    repayment_style: RepaymentStyle,
) -> DebtServiceSchedule:
    """Build an exact-decimal monthly debt-service schedule.

    A missing principal, rate or term yields NOT_COMPUTABLE (empty rows,
    ``None`` totals — never a fabricated schedule).  A negative amount or a
    zero / fractional term is rejected as a validation error.
    """
    _reject_negative(principal)
    _reject_negative(annual_rate_percent)
    _validate_term(term_months)

    inputs = (principal, annual_rate_percent, term_months)
    result_id = _result_id(f"debt_service_schedule:{repayment_style}", inputs)

    missing = _missing(list(inputs))
    if missing is not None:
        return DebtServiceSchedule(
            result_id=result_id,
            repayment_style=repayment_style,
            inputs=inputs,
            outcome=NotComputableOutcome(reason=missing),
        )

    assert principal.value is not None
    assert annual_rate_percent.value is not None
    assert term_months.value is not None

    term = int(term_months.value)
    monthly_rate = annual_rate_percent.value / Decimal(100) / Decimal(12)
    balance = _quantize_money(principal.value)
    per_instalment = _quantize_money(balance / Decimal(term))

    rows: list[DebtServiceRow] = []
    for period in range(1, term + 1):
        opening = balance
        interest = _quantize_money(opening * monthly_rate)
        if repayment_style == "BALLOON":
            principal_paid = opening if period == term else _quantize_money(Decimal(0))
        else:  # EQUAL_PRINCIPAL — final row absorbs the rounding residual.
            principal_paid = opening if period == term else per_instalment
        balance = _quantize_money(opening - principal_paid)
        rows.append(
            DebtServiceRow(
                period=period,
                principal=principal_paid,
                interest=interest,
                balance=balance,
            )
        )

    total_service = sum((row.principal + row.interest for row in rows), Decimal(0))
    return DebtServiceSchedule(
        result_id=result_id,
        repayment_style=repayment_style,
        inputs=inputs,
        outcome=ComputedOutcome(value=total_service),
        rows=tuple(rows),
    )


class RepaymentCapacityPeriod(BaseModel):
    """Per-period coverage: operating cash flow against scheduled debt service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: str = Field(min_length=1)
    operating_cashflow: CalculatorOutcome
    debt_service: CalculatorOutcome
    coverage_ratio: CalculatorOutcome
    shortfall: bool | None = None


class RepaymentCapacity(BaseModel):
    """Per-period repayment coverage with a shortfall tally — no policy judgement.

    ROUNDING POLICY: each coverage ratio (operating cash flow / debt service) is
    rounded HALF_UP to 6 decimal places (``_QUANT``).  A ``shortfall`` period is
    one where operating cash flow is strictly less than the scheduled debt
    service (coverage < 1) — an arithmetic fact about the projection, not a
    minimum-DSCR or adequacy threshold.  ``not_computable_period_count`` counts
    periods whose coverage could not be computed (missing value or zero debt
    service); those are excluded from the shortfall tally rather than guessed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["repayment_capacity"] = "repayment_capacity"
    formula_version: Literal["repayment-capacity-v1"] = "repayment-capacity-v1"
    inputs: tuple[CalculatorInput, ...]
    periods: tuple[RepaymentCapacityPeriod, ...]
    shortfall_period_count: int = Field(ge=0)
    not_computable_period_count: int = Field(ge=0)

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        return _union_fact_refs(self.inputs)


def repayment_capacity(
    projected_operating_cashflow_by_period: Sequence[CalculatorInput],
    debt_service_by_period: Sequence[CalculatorInput],
) -> RepaymentCapacity:
    """Per-period coverage ratios and a count of shortfall periods.

    The two series are aligned by index and MUST be the same length (a mismatch
    is a validation error).  Operating cash flow may be any sign (a period can
    burn cash); debt service must be non-negative.  Coverage is NOT_COMPUTABLE
    when either value is missing or debt service is zero — no shortfall verdict
    is issued for such periods.
    """
    if len(projected_operating_cashflow_by_period) != len(debt_service_by_period):
        raise CalculatorValidationError(
            "series length mismatch: "
            f"{len(projected_operating_cashflow_by_period)} cash-flow periods vs "
            f"{len(debt_service_by_period)} debt-service periods"
        )
    for service in debt_service_by_period:
        _reject_negative(service)

    periods: list[RepaymentCapacityPeriod] = []
    shortfall_count = 0
    not_computable_count = 0
    for cashflow, service in zip(
        projected_operating_cashflow_by_period, debt_service_by_period, strict=True
    ):
        cashflow_outcome: CalculatorOutcome = (
            ComputedOutcome(value=cashflow.value)
            if cashflow.value is not None
            else NotComputableOutcome(
                reason=f"not computable: missing input {cashflow.name}"
            )
        )
        service_outcome: CalculatorOutcome = (
            ComputedOutcome(value=service.value)
            if service.value is not None
            else NotComputableOutcome(
                reason=f"not computable: missing input {service.name}"
            )
        )

        coverage: CalculatorOutcome
        shortfall: bool | None
        if cashflow.value is None or service.value is None:
            missing_names = sorted(
                item.name
                for item in (cashflow, service)
                if item.value is None
            )
            coverage = NotComputableOutcome(
                reason=f"not computable: missing input {', '.join(missing_names)}"
            )
            shortfall = None
            not_computable_count += 1
        elif service.value == 0:
            coverage = NotComputableOutcome(
                reason=(
                    "not computable: division by zero "
                    f"({service.name} debt service is zero)"
                )
            )
            shortfall = None
            not_computable_count += 1
        else:
            coverage = ComputedOutcome(
                value=_quantize(cashflow.value / service.value)
            )
            shortfall = cashflow.value < service.value
            if shortfall:
                shortfall_count += 1

        periods.append(
            RepaymentCapacityPeriod(
                period=cashflow.name,
                operating_cashflow=cashflow_outcome,
                debt_service=service_outcome,
                coverage_ratio=coverage,
                shortfall=shortfall,
            )
        )

    inputs = (
        *projected_operating_cashflow_by_period,
        *debt_service_by_period,
    )
    return RepaymentCapacity(
        result_id=_result_id("repayment_capacity", inputs),
        inputs=inputs,
        periods=tuple(periods),
        shortfall_period_count=shortfall_count,
        not_computable_period_count=not_computable_count,
    )


# --- Group 5: declarative downside scenarios ----------------------------


class WorkingCapitalBaseInputs(BaseModel):
    """The operating drivers a downside scenario shocks and recomputes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inventory_days: CalculatorInput
    receivable_days: CalculatorInput
    payable_days: CalculatorInput
    projected_revenue: CalculatorInput
    cogs_ratio: CalculatorInput


class ScenarioSpec(BaseModel):
    """A labelled, declarative downside shock — DATA, so it stays versioned.

    All three shocks are non-probabilistic and human-readable:
      - ``revenue_down_pct``: fractional revenue reduction (0.2 = -20%), 0..1;
      - ``cost_up_pct``: fractional increase applied to the COGS ratio (>= 0);
      - ``receivable_days_up``: absolute extra receivable days (>= 0).

    Keeping specs as frozen data (rather than code) lets synthetic downside
    configs stay versioned, labelled and auditable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    revenue_down_pct: Decimal = Field(default=Decimal(0), ge=0, le=1)
    cost_up_pct: Decimal = Field(default=Decimal(0), ge=0)
    receivable_days_up: Decimal = Field(default=Decimal(0), ge=0)


class ScenarioOutcome(BaseModel):
    """One scenario's recomputed working-capital need (and optional gap)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    spec: ScenarioSpec
    need: WorkingCapitalNeed
    financing_gap: FinancingGap | None = None


class DownsideScenarioSet(BaseModel):
    """Base case plus each labelled downside scenario, recomputed deterministically."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(min_length=1)
    calculator: Literal["downside_scenarios"] = "downside_scenarios"
    formula_version: Literal["downside-scenarios-v1"] = "downside-scenarios-v1"
    inputs: tuple[CalculatorInput, ...]
    base_need: WorkingCapitalNeed
    base_financing_gap: FinancingGap | None
    scenarios: tuple[ScenarioOutcome, ...]

    @property
    def fact_refs(self) -> tuple[FactRef, ...]:
        return _union_fact_refs(self.inputs)


def _shock(
    calculator_input: CalculatorInput,
    *,
    multiplier: Decimal | None = None,
    addend: Decimal | None = None,
) -> CalculatorInput:
    """Apply a declarative shock, preserving name and provenance.

    A missing (``None``) value propagates untouched so the downstream
    calculator reports NOT_COMPUTABLE rather than inventing a base figure.  The
    evidence references of the base input are retained: the shock itself is
    scenario config recorded on the labelled ``ScenarioSpec``.
    """
    value = calculator_input.value
    if value is not None:
        if multiplier is not None:
            value = value * multiplier
        if addend is not None:
            value = value + addend
    return CalculatorInput(
        name=calculator_input.name,
        value=value,
        fact_refs=calculator_input.fact_refs,
    )


def downside_scenarios(
    base: WorkingCapitalBaseInputs,
    scenario_specs: Sequence[ScenarioSpec],
    *,
    funding: FinancingInputs | None = None,
) -> DownsideScenarioSet:
    """Recompute working-capital need (and, if funding given, the financing gap)
    under each declarative downside scenario.

    Each scenario applies its shocks to the base drivers:
        revenue    -> revenue * (1 - revenue_down_pct)
        cogs_ratio -> cogs_ratio * (1 + cost_up_pct)
        rcv_days   -> receivable_days + receivable_days_up
    then re-runs ``working_capital_requirement`` (and ``financing_gap``).  No
    new rounding is introduced; the delegated calculators own their policies.
    Missing base inputs propagate to NOT_COMPUTABLE in every scenario.
    """

    def _need(
        receivable_days: CalculatorInput,
        projected_revenue: CalculatorInput,
        cogs_ratio: CalculatorInput,
    ) -> WorkingCapitalNeed:
        return working_capital_requirement(
            inventory_days=base.inventory_days,
            receivable_days=receivable_days,
            payable_days=base.payable_days,
            projected_revenue=projected_revenue,
            cogs_ratio=cogs_ratio,
        )

    def _gap(need: WorkingCapitalNeed) -> FinancingGap | None:
        if funding is None:
            return None
        return financing_gap(
            need,
            funding.own_funds,
            funding.existing_facilities,
            funding.requested_amount,
        )

    base_need = _need(base.receivable_days, base.projected_revenue, base.cogs_ratio)
    base_gap = _gap(base_need)

    scenarios: list[ScenarioOutcome] = []
    for spec in scenario_specs:
        need = _need(
            _shock(base.receivable_days, addend=spec.receivable_days_up),
            _shock(base.projected_revenue, multiplier=Decimal(1) - spec.revenue_down_pct),
            _shock(base.cogs_ratio, multiplier=Decimal(1) + spec.cost_up_pct),
        )
        scenarios.append(
            ScenarioOutcome(
                label=spec.label,
                spec=spec,
                need=need,
                financing_gap=_gap(need),
            )
        )

    inputs: tuple[CalculatorInput, ...] = (
        base.inventory_days,
        base.receivable_days,
        base.payable_days,
        base.projected_revenue,
        base.cogs_ratio,
    )
    if funding is not None:
        inputs = (
            *inputs,
            funding.own_funds,
            funding.existing_facilities,
            funding.requested_amount,
        )
    return DownsideScenarioSet(
        result_id=_result_id("downside_scenarios", inputs),
        inputs=inputs,
        base_need=base_need,
        base_financing_gap=base_gap,
        scenarios=tuple(scenarios),
    )
