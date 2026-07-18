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
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_QUANT = Decimal("0.000001")
_DAYS_PER_YEAR = Decimal(365)


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
